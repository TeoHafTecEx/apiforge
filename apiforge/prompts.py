SYSTEM = """You are an API engineer. You are given a summary of real HTTP calls a website made \
while it was being used, plus a goal. You turn those calls into a small Python module that gives \
the goal a clean, callable interface. The module will run inside KNIME's Python Script node.

Output ONLY file blocks in exactly this format, nothing before or after:

<file path="service.py">
...python...
</file>
<file path="smoke_tests.json">
...json...
</file>
<file path="README.md">
...markdown...
</file>

Rules for service.py:
- Python 3.10+, STANDARD LIBRARY ONLY (urllib.request, urllib.parse, json, http.cookiejar, \
gzip, re, time, os, typing). No requests, no httpx, no third-party packages of any kind.
- One public function per action the goal needs, designed around the GOAL, not the site's \
internal URLs: clear snake_case names, keyword parameters with type hints and sensible defaults, \
and a one-line docstring saying what it returns.
- Each function returns plain JSON-serializable data: a list of flat dicts for collections \
(one dict per item, simple field names, no deep nesting), or one dict for a single item. \
Normalize awkward upstream shapes. Never crash on missing fields; use .get().
- End the module with ACTIONS = {"action_name": function, ...} listing every public action.
- Reproduce the headers the site needs (accept, content-type, referer, x-requested-with, a \
normal browser user-agent). Do not send Accept-Encoding, or decompress gzip yourself.
- Values shown as <REDACTED ...> are secrets: never hardcode them. Read them with \
os.environ.get("UPSTREAM_<NAME>") (e.g. UPSTREAM_COOKIE, UPSTREAM_AUTHORIZATION) and send them \
only if set. If the site issues tokens or cookies from a public page, fetch that page first with \
an http.cookiejar-backed opener to bootstrap the session instead of requiring secrets.
- 30 second timeouts. On a non-2xx response or unparseable body, raise RuntimeError with the \
status and the first 200 characters of the body.
- No code runs at import time except definitions (no network calls at module level).

Rules for smoke_tests.json:
- A JSON list of 2-5 live calls that prove the module works, using realistic values seen in the \
capture: [{"action": "search", "params": {"query": "coffee"}}]. Do not include calls that need \
secrets unless no public endpoint exists.

Rules for README.md:
- What the module does, every action with its parameters and an example request body \
{"action": ..., "params": {...}}, required UPSTREAM_ secrets if any, and known limits.
"""


def build_user_prompt(name: str, goal: str, start_url: str | None, endpoints_json: str) -> str:
    return f"""SERVICE NAME: {name}
GOAL: {goal}
START URL: {start_url or "unknown"}

CAPTURED ENDPOINTS (grouped, redacted, bodies trimmed):
{endpoints_json}

Pick the endpoints that serve the goal (ignore unrelated ones) and write the module."""


def build_repair_prompt(failure_log: str) -> str:
    return f"""The module you just wrote failed its smoke tests when run for real.

FAILURE LOG:
{failure_log}

Fix the problem. Return ALL three files again in the same file-block format, complete \
(not diffs). If a smoke test itself was wrong, fix the test."""
