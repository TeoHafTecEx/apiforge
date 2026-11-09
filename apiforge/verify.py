"""Boot the generated service for real and hit it. Failures feed the repair loop."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_smoke_tests(service_dir: Path, boot_timeout: float = 20.0) -> tuple[bool, str]:
    """Return (passed, log). The log is what the model sees when asked to repair."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port)],
        cwd=service_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log: list[str] = []
    passed = True
    try:
        deadline = time.time() + boot_timeout
        while True:
            if proc.poll() is not None:
                return False, "Service crashed on startup:\n" + (proc.stdout.read() if proc.stdout else "")
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() > deadline:
                return False, "Service did not answer /health within the timeout."
            time.sleep(0.4)

        try:
            tests = json.loads((service_dir / "smoke_tests.json").read_text())
        except (OSError, ValueError) as exc:
            return False, f"smoke_tests.json is missing or not valid JSON: {exc}"

        for t in tests:
            method = t.get("method", "GET").upper()
            path = t.get("path", "/")
            expect = t.get("expect_status", 200)
            try:
                r = httpx.request(
                    method, base + path, params=t.get("params"), json=t.get("body"), timeout=60
                )
            except httpx.HTTPError as exc:
                passed = False
                log.append(f"FAIL {method} {path}: request error {exc!r}")
                continue
            body = r.text[:800]
            ok = r.status_code == expect
            if ok and "json" in r.headers.get("content-type", ""):
                try:
                    data = r.json()
                    ok = data not in (None, [], {})  # empty output usually means a parsing bug
                    if not ok:
                        body = "Response was empty: " + body
                except ValueError:
                    ok = False
            passed &= ok
            log.append(f"{'PASS' if ok else 'FAIL'} {method} {path} {t.get('params') or ''} "
                       f"-> {r.status_code} (expected {expect})\n  {body}")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        if not passed and out:
            log.append("SERVER LOG (tail):\n" + out[-4000:])
    return passed, "\n".join(log)
