"""Run the generated KNIME script for real and call its actions. Failures feed the repair loop.

It runs exactly the file that gets pasted into KNIME (knime_node.py), in a separate Python
started with -S, so site-packages are invisible: any third-party import fails here just as it
would in a bare KNIME environment.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RUNNER = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("forge_service", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for t in json.load(open(sys.argv[2], encoding="utf-8")):
    res = mod._forge_handle({"action": t.get("action"), "params": t.get("params") or {}})
    print("@@RESULT " + json.dumps({"test": t, "response": res}, default=str), flush=True)
"""


def run_smoke_tests(service_dir: Path, timeout: float = 300.0) -> tuple[bool, str]:
    """Return (passed, log). The log is what the model sees when asked to repair."""
    script = service_dir / "knime_node.py"
    tests_file = service_dir / "smoke_tests.json"
    try:
        tests = json.loads(tests_file.read_text(encoding="utf-8"))
        if not isinstance(tests, list) or not tests:
            return False, "smoke_tests.json must be a non-empty JSON list."
    except (OSError, ValueError) as exc:
        return False, f"smoke_tests.json is missing or not valid JSON: {exc}"
    try:
        proc = subprocess.run(
            [sys.executable, "-S", "-c", RUNNER, str(script), str(tests_file)],
            capture_output=True, text=True, timeout=timeout, cwd=service_dir,
        )
    except subprocess.TimeoutExpired:
        return False, f"The smoke tests took longer than {timeout:.0f}s. Add timeouts or reduce page sizes."

    log, passed, seen = [], True, 0
    for line in proc.stdout.splitlines():
        if not line.startswith("@@RESULT "):
            continue
        seen += 1
        item = json.loads(line[len("@@RESULT "):])
        t, res = item["test"], item["response"]
        label = f"{t.get('action')} {json.dumps(t.get('params') or {})}"
        if not res.get("ok"):
            passed = False
            log.append(f"FAIL {label}\n  {res.get('error')}")
            continue
        data = res.get("data")
        if data in (None, [], {}, ""):
            passed = False
            log.append(f"FAIL {label}\n  Returned no data (usually a parsing or parameter bug).")
            continue
        sample = data[0] if isinstance(data, list) else data
        what = f"{len(data)} items" if isinstance(data, list) else "1 item"
        log.append(f"PASS {label} -> {what}\n  {json.dumps(sample, default=str)[:300]}")

    if proc.returncode != 0 or seen < len(tests):
        passed = False
        err = (proc.stderr or "").strip()[-3000:]
        log.append("The script crashed" + (" while importing" if seen == 0 else "") + ":\n" + err)
    return passed, "\n".join(log)
