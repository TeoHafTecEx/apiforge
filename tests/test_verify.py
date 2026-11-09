import json

from apiforge.verify import run_smoke_tests

APP = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/echo")
def echo(q: str = "x"):
    return {"q": q}

@app.get("/empty")
def empty():
    return []
'''


def _service(tmp_path, tests):
    (tmp_path / "main.py").write_text(APP)
    (tmp_path / "smoke_tests.json").write_text(json.dumps(tests))
    return tmp_path


def test_passing_service(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, [{"path": "/echo", "params": {"q": "hi"}}]))
    assert ok, log


def test_empty_response_fails(tmp_path):
    ok, log = run_smoke_tests(_service(tmp_path, [{"path": "/empty"}]))
    assert not ok and "empty" in log.lower()


def test_crash_on_startup(tmp_path):
    (tmp_path / "main.py").write_text("raise RuntimeError('boom')")
    (tmp_path / "smoke_tests.json").write_text("[]")
    ok, log = run_smoke_tests(tmp_path)
    assert not ok and "boom" in log
