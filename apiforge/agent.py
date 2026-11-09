"""Agent capture: an AI model drives a headless browser to exercise a site's features.

The browser layer (BrowserSession) is plain Playwright and knows nothing about the model,
so it can be tested on its own. The loop (explore) gives the model a few browser tools and, after
every action, shows it the page plus which API calls that action triggered. That feedback is
what lets it find the endpoints that matter for the goal.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import har as harlib

SNAPSHOT_JS = r"""
() => {
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [role=option], [onclick], summary';
  const out = [];
  let i = 0;
  document.querySelectorAll('[data-af]').forEach(el => el.removeAttribute('data-af'));
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    if (el.disabled) continue;
    el.setAttribute('data-af', String(i));
    const label = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || el.name || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    const item = {id: i, tag: el.tagName.toLowerCase()};
    if (el.type && el.tagName !== 'BUTTON') item.type = el.type;
    if (label) item.label = label;
    if (el.tagName === 'A') item.href = el.getAttribute('href').slice(0, 120);
    if (el.tagName === 'SELECT') item.options = [...el.options].slice(0, 15).map(o => o.value);
    out.push(item);
    if (++i >= 120) break;
  }
  const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 2000);
  return {title: document.title, url: location.href, elements: out, text};
}
"""


class BrowserSession:
    """Headless Chromium with HAR recording and a live log of API-looking responses."""

    def __init__(self, har_path: Path, headless: bool = True, profile_dir: Path | None = None):
        from playwright.sync_api import sync_playwright

        har_path.parent.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        opts = dict(
            record_har_path=str(har_path),
            record_har_content="embed",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        if profile_dir:
            self.context = self._pw.chromium.launch_persistent_context(
                str(profile_dir), headless=headless, **opts)
            self._browser = None
        else:
            self._browser = self._pw.chromium.launch(headless=headless)
            self.context = self._browser.new_context(**opts)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._pending: list[str] = []
        self.api_calls_seen = 0
        self.context.on("response", self._on_response)
        self.context.on("page", self._on_new_page)

    def _on_new_page(self, page):  # follow links that open in new tabs
        self.page = page

    def _on_response(self, response):
        try:
            req = response.request
            entry = {
                "_resourceType": req.resource_type,
                "request": {"method": req.method, "url": req.url},
                "response": {"status": response.status,
                             "content": {"mimeType": response.headers.get("content-type", "")}},
            }
            if harlib.is_api_entry(entry):
                p = urlsplit(req.url)
                self.api_calls_seen += 1
                self._pending.append(f"{req.method} {p.netloc}{p.path}{'?' + p.query[:80] if p.query else ''}"
                                     f" -> {response.status}")
        except Exception:  # never let logging break browsing
            pass

    def drain_api_calls(self) -> list[str]:
        calls, self._pending = self._pending[:25], []
        return calls

    def _settle(self):
        # A click often fires fetch() after the page is already "loaded", so give requests a
        # moment to start; the sync API also only delivers response events while waiting.
        try:
            self.page.wait_for_timeout(1200)
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def snapshot(self) -> dict:
        self._settle()
        try:
            return self.page.evaluate(SNAPSHOT_JS)
        except Exception as exc:
            return {"error": f"could not read page: {exc}"}

    def _el(self, element_id: int):
        return self.page.locator(f'[data-af="{int(element_id)}"]').first

    # --- actions (each returns a short result string) ---
    def goto(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return "Refused: only http(s) URLs."
        self.page.goto(url, timeout=30000)
        return f"Opened {url}"

    def click(self, element_id: int) -> str:
        self._el(element_id).click(timeout=8000)
        return f"Clicked element {element_id}"

    def type(self, element_id: int, text: str, submit: bool = False) -> str:
        el = self._el(element_id)
        el.fill(text, timeout=8000)
        if submit:
            el.press("Enter")
        return f"Typed into element {element_id}" + (" and pressed Enter" if submit else "")

    def select(self, element_id: int, value: str) -> str:
        self._el(element_id).select_option(value, timeout=8000)
        return f"Selected {value!r} in element {element_id}"

    def scroll(self) -> str:
        self.page.mouse.wheel(0, 2500)
        return "Scrolled down"

    def back(self) -> str:
        self.page.go_back(timeout=15000)
        return "Went back"

    def close(self):
        try:
            self.context.close()  # writes the HAR
        finally:
            if self._browser:
                self._browser.close()
            self._pw.stop()


TOOLS = [
    {"name": "goto", "description": "Open a URL.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "click", "description": "Click an element by its id from the latest page snapshot.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "type", "description": "Fill a text input by id. Set submit=true to press Enter after.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "integer"}, "text": {"type": "string"}, "submit": {"type": "boolean"}},
         "required": ["id", "text"]}},
    {"name": "select", "description": "Choose an option in a <select> by id.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "integer"}, "value": {"type": "string"}}, "required": ["id", "value"]}},
    {"name": "scroll", "description": "Scroll down (triggers infinite scroll / lazy loading).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "back", "description": "Browser back.", "parameters": {"type": "object", "properties": {}}},
    {"name": "finish", "description": "Stop exploring. Call when the goal's features have triggered API calls, "
     "or if the site is blocked / needs a login you don't have.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]

AGENT_SYSTEM = """You explore a website in a browser so its internal API calls get recorded. \
Someone will turn those recorded calls into an API for this GOAL:

{goal}

Each turn you see the page (title, URL, visible text, interactive elements with ids) and the \
API calls your last action triggered. Your job is coverage, not reading content:
- Exercise every feature the goal needs: run 2-3 different searches with realistic values, open \
detail pages, change filters/sorting, paginate or scroll, switch tabs. Varied inputs let the \
API's parameters be inferred.
- Prefer actions that trigger API calls. If an action triggers none, try another route.
- Dismiss cookie banners and popups when they block the page.
- Read-only exploration ONLY. Never log in, sign up, buy, book, post, send messages, submit \
personal data, or change or delete anything. Never solve CAPTCHAs.
- Call finish when the goal's features are covered, or if you are blocked (CAPTCHA, login \
wall, access denied), and say which in the summary."""


def _observation(session: BrowserSession, action_result: str) -> str:
    snap = session.snapshot()
    calls = session.drain_api_calls()
    return json.dumps({
        "result": action_result,
        "api_calls_triggered": calls or "none",
        "page": snap,
    }, ensure_ascii=False)


def explore(url: str, goal: str, har_path: Path, *, max_steps: int = 30, headless: bool = True,
            profile_dir: Path | None = None, model: str | None = None,
            log: Callable[[str], None] = print) -> dict:
    """Run the agent; returns {"summary", "steps", "api_calls"}. The HAR is written to har_path."""
    from .llm import LLM, Turn

    llm = LLM(model=model)
    system = AGENT_SYSTEM.format(goal=goal)
    session = BrowserSession(har_path, headless=headless, profile_dir=profile_dir)
    summary, steps = "Stopped at step limit.", 0
    try:
        first = _observation(session, session.goto(url))
        history: list[Turn] = [Turn("user", text=f"Start. Current state:\n{first}")]
        for steps in range(1, max_steps + 1):
            _elide_old_observations(history)
            reply = llm.chat(system, history, tools=TOOLS, max_tokens=2000)
            history.append(reply)
            if not reply.calls:
                history.append(Turn("user", text="Use a tool, or call finish."))
                continue
            results, finished = [], False
            for call in reply.calls:
                if call.name == "finish":
                    summary, finished = call.input.get("summary", ""), True
                    results.append((call.id, "ok"))
                    continue
                try:
                    if call.name not in ACTIONS:
                        raise ValueError(f"unknown tool {call.name}")
                    args = dict(call.input)
                    if "id" in args:
                        args["element_id"] = args.pop("id")
                    outcome = getattr(session, call.name)(**args)
                except Exception as exc:
                    outcome = f"Action failed: {str(exc).splitlines()[0][:200] if str(exc) else exc!r}"
                log(f"  step {steps}: {call.name} {json.dumps(call.input)[:100]} -> {outcome}")
                results.append((call.id, _observation(session, outcome)))
            history.append(Turn("tool_results", results=results))
            if finished:
                break
        log(f"Agent finished after {steps} steps, {session.api_calls_seen} API calls seen. {summary}")
        return {"summary": summary, "steps": steps, "api_calls": session.api_calls_seen}
    finally:
        session.close()


ACTIONS = {"goto", "click", "type", "select", "scroll", "back"}


def _elide_old_observations(history: list, keep_last: int = 2) -> None:
    """Page snapshots are big; keep only the newest few in full to bound token use."""
    seen = 0
    for turn in reversed(history):
        if turn.role != "tool_results":
            continue
        for i in range(len(turn.results) - 1, -1, -1):
            cid, content = turn.results[i]
            if len(content) <= 400:
                continue
            seen += 1
            if seen > keep_last:
                try:
                    calls = json.loads(content).get("api_calls_triggered")
                except (ValueError, TypeError, AttributeError):
                    calls = None
                turn.results[i] = (cid, json.dumps({"elided": "old page snapshot",
                                                    "api_calls_triggered": calls}))
