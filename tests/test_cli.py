import json
from pathlib import Path

from apiforge import cli, generate

FIXTURE = Path(__file__).parent / "fixtures" / "sample.har"

GOOD = {
    "upstream.py": "class UpstreamClient: pass\n",
    "main.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/health')\ndef h(): return {'status': 'ok'}\n"
        "@app.get('/search')\ndef s(q: str = 'coffee'): return [{'code': '0901', 'q': q}]\n"
    ),
    "smoke_tests.json": json.dumps([{"path": "/search", "params": {"q": "coffee"}}]),
    "README.md": "# test\n",
}
BROKEN = dict(GOOD, **{"main.py": GOOD["main.py"].replace("/search", "/serch")})


class FakeGenerator:
    model = "fake"

    def __init__(self, *a, **k):
        self.repairs = 0

    def generate(self, *a):
        return BROKEN

    def repair(self, log):
        assert "FAIL" in log
        self.repairs += 1
        return GOOD


def test_build_with_repair_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "APIS_DIR", tmp_path)
    monkeypatch.setattr(generate, "Generator", FakeGenerator)
    rc = cli.main(["build", "HTS Tariffs", "--goal", "tariff lookup", "--har", str(FIXTURE), "--no-key"])
    assert rc == 0
    d = tmp_path / "hts-tariffs"
    meta = json.loads((d / "apiforge.json").read_text())
    assert meta["status"] == "verified"
    for f in ("main.py", "Dockerfile", "requirements.txt", "endpoints.json"):
        assert (d / f).exists()
