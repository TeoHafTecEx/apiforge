import json
from pathlib import Path

from apiforge import cli, generate

FIXTURE = Path(__file__).parent / "fixtures" / "sample.har"

GOOD = {
    "service.py": (
        "def search(query: str = 'coffee'):\n"
        "    \"\"\"Tariff lines.\"\"\"\n"
        "    return [{'code': '0901', 'q': query}]\n\n"
        "def get_code(code: str = '0901'):\n"
        "    return {'code': code}\n\n"
        "ACTIONS = {'search': search, 'code': get_code}\n"
    ),
    "smoke_tests.json": json.dumps([{"action": "search", "params": {"query": "coffee"}}]),
    "README.md": "# test\n",
}
BROKEN = dict(GOOD, **{"service.py": GOOD["service.py"].replace("[{'code': '0901', 'q': query}]", "[]")})


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
    rc = cli.main(["build", "HTS Tariffs", "--goal", "tariff lookup", "--har", str(FIXTURE)])
    assert rc == 0
    d = tmp_path / "hts-tariffs"
    meta = json.loads((d / "apiforge.json").read_text())
    assert meta["status"] == "verified"
    assert meta["actions"] == ["search", "code"]
    for f in ("service.py", "knime_node.py", "endpoints.json"):
        assert (d / f).exists()
    assert "_forge_handle" in (d / "knime_node.py").read_text()
    # `apiforge run` calls it exactly as KNIME would
    assert cli.main(["run", "hts-tariffs", "search", "--params", '{"query": "tea"}']) == 0
