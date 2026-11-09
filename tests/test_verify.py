import json

from apiforge.generate import write_service
from apiforge.verify import run_smoke_tests

GOOD = '''
def search(query: str = "coffee", limit: int = 5):
    """Matching tariff lines."""
    return [{"code": "0901.11", "query": query}][:limit]

def nothing():
    """Always empty."""
    return []

ACTIONS = {"search": search, "nothing": nothing}
'''


def _service(tmp_path, code, tests):
    write_service(tmp_path, {"service.py": code, "smoke_tests.json": json.dumps(tests), "README.md": "x"})
    return tmp_path


def test_passing_service(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, GOOD, [{"action": "search", "params": {"query": "tea"}}]))
    assert ok, log
    assert "PASS search" in log and '"query": "tea"' in log


def test_empty_result_fails(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, GOOD, [{"action": "nothing"}]))
    assert not ok and "no data" in log


def test_unknown_action_and_bad_params_fail(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, GOOD, [
        {"action": "nope"}, {"action": "search", "params": {"bogus": 1}}]))
    assert not ok and "Unknown action" in log and "TypeError" in log


def test_third_party_imports_are_caught(tmp_path):
    code = "import httpx\n" + GOOD
    ok, log = run_smoke_tests(_service(tmp_path, code, [{"action": "search"}]))
    assert not ok and "httpx" in log and "importing" in log


def test_crash_on_import(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, "raise RuntimeError('boom')\nACTIONS = {}", [{"action": "x"}]))
    assert not ok and "boom" in log
