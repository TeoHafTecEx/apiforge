"""Forge server: mounting generated APIs, per-API keys, and the /forge job flow."""
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient

UPSTREAM = "class UpstreamClient:\n    label = {label!r}\n    def search(self, q): return [{{'code': '0901', 'q': q, 'src': self.label}}]\n"
MAIN = (
    "from fastapi import FastAPI\nfrom upstream import UpstreamClient\napp = FastAPI()\n"
    "c = UpstreamClient()\n"
    "@app.get('/health')\ndef h(): return {'status': 'ok'}\n"
    "@app.get('/search')\ndef s(q: str = 'coffee'): return c.search(q)\n"
)


def make_service(apis, name, label):
    d = apis / name
    d.mkdir(parents=True)
    (d / "upstream.py").write_text(UPSTREAM.format(label=label))
    (d / "main.py").write_text(MAIN)
    (d / "apiforge.json").write_text(json.dumps({"name": name, "status": "verified", "goal": "g"}))


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("APIFORGE_HOME", str(tmp_path))
    monkeypatch.setenv("FORGE_ADMIN_KEY", "admin")
    make_service(tmp_path, "alpha", "A")
    make_service(tmp_path, "beta", "B")
    from apiforge import server as srv

    srv = importlib.reload(srv)
    with TestClient(srv.app) as client:
        yield srv, client, tmp_path


def test_existing_apis_mount_without_clashing(server):
    srv, client, apis = server
    from apiforge import keys

    ka, kb = keys.issue(apis, "alpha"), keys.issue(apis, "beta")
    a = client.get("/api/alpha/search", params={"q": "tea"}, headers={"x-api-key": ka}).json()
    b = client.get("/api/beta/search", headers={"x-api-key": kb}).json()
    assert a[0]["src"] == "A" and a[0]["q"] == "tea"
    assert b[0]["src"] == "B"  # each API got its own upstream module


def test_api_keys_are_enforced_per_api(server):
    srv, client, apis = server
    from apiforge import keys

    ka = keys.issue(apis, "alpha")
    assert client.get("/api/alpha/search").status_code == 401
    assert client.get("/api/beta/search", headers={"x-api-key": ka}).status_code == 401
    assert client.get("/api/alpha/health").status_code == 200  # health is public
    assert "af_alpha_" in ka and ka not in (apis / ".keys.json").read_text()  # only hashes stored


def test_admin_routes_need_admin_key(server):
    _, client, _ = server
    assert client.get("/apis").status_code == 401
    names = [a["name"] for a in client.get("/apis", headers={"x-admin-key": "admin"}).json()]
    assert names == ["alpha", "beta"]


def test_forge_job_returns_endpoint_and_key(server):
    srv, client, apis = server

    def fake_build(name, goal, url, **kw):
        kw["log"]("exploring...")
        make_service(apis, name, "NEW")
        return {"name": name, "status": "verified"}

    srv._build = fake_build
    r = client.post("/forge", headers={"x-admin-key": "admin"},
                    json={"name": "HTS Tariffs", "url": "https://example.com", "goal": "tariffs"})
    assert r.status_code == 202
    job_url = "/forge/jobs/" + r.json()["job_id"]
    for _ in range(50):
        job = client.get(job_url, headers={"x-admin-key": "admin"}).json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["endpoint"].endswith("/api/hts-tariffs")
    assert "GET /api/hts-tariffs/search" in res["routes"]
    data = client.get("/api/hts-tariffs/search", headers={"x-api-key": res["api_key"]}).json()
    assert data[0]["src"] == "NEW"
