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
    generate.write_service(out, files)

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
            generate.write_service(out, files)

    meta = {
        "name": slugify(name),
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
