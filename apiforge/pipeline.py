"""The build pipeline as a plain function, shared by the CLI and the forge server.

capture (human or agent) -> summarize -> generate -> verify/repair -> write metadata
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import har

APIS_DIR = Path(os.getenv("APIFORGE_HOME", "apis"))
Log = Callable[[str], None]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError("Name must contain letters or numbers.")
    return slug


def service_dir(name: str, apis_dir: Path | None = None) -> Path:
    return (apis_dir or APIS_DIR) / slugify(name)


def build(
    name: str,
    goal: str,
    url: str | None = None,
    *,
    mode: str = "agent",  # "agent" (AI drives a headless browser), "manual" (you browse), "har"
    har_file: Path | None = None,
    recapture: bool = False,
    profile: Path | None = None,
    headless: bool = True,
    max_steps: int = 30,
    model: str | None = None,
    max_repairs: int = 2,
    verify: bool = True,
    apis_dir: Path | None = None,
    log: Log = print,
) -> dict:
    out = service_dir(name, apis_dir)
    out.mkdir(parents=True, exist_ok=True)
    har_path = out / "capture.har"

    # 1. Capture
    if mode == "har":
        if not har_file:
            raise ValueError("mode='har' needs har_file")
        har_path = Path(har_file)
        log(f"Using existing capture: {har_path}")
    elif har_path.exists() and not recapture:
        log(f"Reusing previous capture: {har_path}")
    else:
        if not url:
            raise ValueError("A URL is required to capture.")
        if mode == "agent":
            from .agent import explore

            explore(url, goal, har_path, max_steps=max_steps, headless=headless,
                    profile_dir=profile, model=model, log=log)
        else:
            from .capture import capture_manual

            capture_manual(url, har_path, profile)

    # 2. Summarize
    endpoints = har.summarize_har(har_path)
    if not endpoints:
        raise RuntimeError("No API calls found in the capture. The site may render server-side, "
                           "block headless browsers, or need a login.")
    endpoints_json = json.dumps(endpoints, indent=1, ensure_ascii=False)
    (out / "endpoints.json").write_text(endpoints_json, encoding="utf-8")
    log(f"Found {len(endpoints)} API endpoints:")
    for e in endpoints:
        log(f"  {e['method']:6} {e['host']}{e['path_template']}  ({e['calls']} calls)")

    # 3. Generate
    from . import generate

    gen = generate.Generator(model=model) if model else generate.Generator()
    log(f"Generating service with {gen.model}...")
    files = gen.generate(slugify(name), goal, url, endpoints_json)
    generate.write_service(out, files, name=slugify(name), url=url, goal=goal)

    # 4. Verify + repair
    status = "unverified"
    if verify:
        from .verify import run_smoke_tests

        for attempt in range(max_repairs + 1):
            log(f"Running smoke tests (attempt {attempt + 1})...")
            ok, result = run_smoke_tests(out)
            log(result)
            if ok:
                status = "verified"
                break
            if attempt == max_repairs:
                status = "failing"
                break
            log("Asking the model to fix it...")
            files = gen.repair(result)
            generate.write_service(out, files, name=slugify(name), url=url, goal=goal)

    actions = _list_actions(out / "service.py")
    action_details = _describe_actions(out / "knime_node.py")
    meta = {
        "name": slugify(name),
        "actions": actions,
        "action_details": action_details,
        "goal": goal,
        "start_url": url,
        "capture_mode": mode,
        "endpoints": len(endpoints),
        "status": status,
        "model": gen.model,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "apiforge.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"Done: {out}/  status={status}")
    return meta


def _list_actions(service_py: Path) -> list[str]:
    """Action names from the ACTIONS dict, without importing (and so running) the code."""
    import ast

    try:
        tree = ast.parse(service_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ACTIONS" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]


_DESCRIBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("forge_service", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps(mod._forge_help(), default=str))
"""


def _describe_actions(knime_py: Path) -> list[dict]:
    """Each action's description and parameters, for the web page's request examples."""
    import subprocess
    import sys

    try:
        proc = subprocess.run([sys.executable, "-S", "-c", _DESCRIBE, str(knime_py)],
                              capture_output=True, text=True, timeout=30)
        return json.loads(proc.stdout) if proc.returncode == 0 else []
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return []
