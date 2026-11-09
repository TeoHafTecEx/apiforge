"""Agent capture against a tiny local site, with the model replaced by a script."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from apiforge import agent, har

PAGE = b"""<!doctype html><html><head><title>Tariff demo</title></head><body>
<input id=q placeholder="Search products">
<button id=go onclick="run()">Search</button>
<ul id=out></ul>
<script>
async function run(){
  const r = await fetch('/api/search?q=' + encodeURIComponent(document.getElementById('q').value));
  const data = await r.json();
  document.getElementById('out').innerHTML = data.map(d => '<li>' + d.code + ' ' + d.rate + '</li>').join('');
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/api/search":
            q = parse_qs(u.query).get("q", [""])[0]
            body = json.dumps([{"code": "0901.11", "desc": q, "rate": "Free"}]).encode()
            ctype = "application/json"
        else:
            body, ctype = PAGE, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


class ScriptedLLM:
    """Stands in for llm.LLM: plays a fixed sequence of tool calls, records what it was shown."""

    def __init__(self, *a, **k):
        from apiforge.llm import ToolCall

        self.seen = []
        self.script = [
            [ToolCall("t1", "type", {"id": 0, "text": "coffee"})],
            [ToolCall("t2", "click", {"id": 1})],
            [ToolCall("t3", "type", {"id": 0, "text": "steel pipe"})],
            [ToolCall("t4", "click", {"id": 1})],
            [ToolCall("t5", "finish", {"summary": "searched twice"})],
        ]

    def chat(self, system, history, tools=None, max_tokens=0):
        from apiforge.llm import Turn

        self.seen.append(history[-1])
        return Turn("assistant", calls=self.script.pop(0))


def test_agent_explores_and_captures(site, tmp_path, monkeypatch):
    from apiforge import llm

    fake = ScriptedLLM()
    monkeypatch.setattr(llm, "LLM", lambda *a, **k: fake)
    har_path = tmp_path / "capture.har"
    result = agent.explore(site, "search tariffs", har_path, max_steps=10, log=lambda s: None)

    assert result["summary"] == "searched twice"
    assert result["api_calls"] >= 2
    # The model was told which API call its click triggered.
    click_feedback = json.loads(fake.seen[2].results[0][1])
    assert any("/api/search" in c for c in click_feedback["api_calls_triggered"])
    # The HAR has the endpoint with both queries, and it survives summarization.
    eps = har.summarize_har(har_path)
    search = next(e for e in eps if e["path_template"] == "/api/search")
    assert search["query_params"]["q"] == ["coffee", "steel pipe"]
    assert search["response_example"][0]["rate"] == "Free"


def test_old_snapshots_are_elided():
    from apiforge.llm import Turn

    big = json.dumps({"result": "x", "api_calls_triggered": ["GET a"], "page": {"text": "y" * 1000}})
    history = [Turn("tool_results", results=[(str(i), big)]) for i in range(4)]
    agent._elide_old_observations(history, keep_last=2)
    sizes = [len(t.results[0][1]) for t in history]
    assert sizes[0] < 200 and sizes[1] < 200 and sizes[2] > 1000 and sizes[3] > 1000
    assert json.loads(history[0].results[0][1])["api_calls_triggered"] == ["GET a"]
