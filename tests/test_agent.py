"""Agent capture against a tiny local site, with the model replaced by a script."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
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


def _block(name, **inp):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=f"t_{name}_{len(inp)}")


class ScriptedClient:
    """Stands in for anthropic.Anthropic: plays a fixed sequence of tool calls."""
    script = [
        [_block("type", id=0, text="coffee")],
        [_block("click", id=1)],
        [_block("type", id=0, text="steel pipe", submit=False), ],
        [_block("click", id=1)],
        [_block("finish", summary="searched twice")],
    ]

    def __init__(self, *a, **k):
        self.seen = []
        self.messages = SimpleNamespace(create=self._create)
        self._i = 0

    def _create(self, **kw):
        self.seen.append(kw["messages"][-1])
        step = self.script[self._i]
        self._i += 1
        return SimpleNamespace(content=step)


def test_agent_explores_and_captures(site, tmp_path, monkeypatch):
    import anthropic

    client = ScriptedClient()
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    har_path = tmp_path / "capture.har"
    result = agent.explore(site, "search tariffs", har_path, max_steps=10, log=lambda s: None)

    assert result["summary"] == "searched twice"
    assert result["api_calls"] >= 2
    # The model was told which API call its click triggered.
    click_feedback = json.loads(client.seen[2]["content"][0]["content"])
    assert any("/api/search" in c for c in click_feedback["api_calls_triggered"])
    # The HAR has the endpoint with both queries, and it survives summarization.
    eps = har.summarize_har(har_path)
    search = next(e for e in eps if e["path_template"] == "/api/search")
    assert search["query_params"]["q"] == ["coffee", "steel pipe"]
    assert search["response_example"][0]["rate"] == "Free"


def test_old_snapshots_are_elided():
    big = json.dumps({"result": "x", "api_calls_triggered": ["GET a"], "page": {"text": "y" * 1000}})
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(i), "content": big}]}
            for i in range(4)]
    agent._elide_old_observations(msgs, keep_last=2)
    sizes = [len(m["content"][0]["content"]) for m in msgs]
    assert sizes[0] < 200 and sizes[1] < 200 and sizes[2] > 1000 and sizes[3] > 1000
    assert json.loads(msgs[0]["content"][0]["content"])["api_calls_triggered"] == ["GET a"]
