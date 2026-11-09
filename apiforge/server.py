"""The forge server: send a URL and a goal, get back a live endpoint and an API key.

  POST /forge                {"name", "url", "goal"}         (admin key)  -> job id
  GET  /forge/jobs/{id}      status, log, and when done: endpoint + api_key
  GET  /apis                 list of live APIs                (admin key)
  POST /apis/{name}/rotate-key                                (admin key)
  ANY  /api/{name}/...       the generated APIs               (that API's key)

Every API built here (or already in apis/) is mounted into this one process, so you deploy
the server once and new APIs go live without redeploying.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.routing import Mount

from . import keys, pipeline

APIS_DIR = Path(os.getenv("APIFORGE_HOME", "apis"))
ADMIN_KEY = os.getenv("FORGE_ADMIN_KEY")
PUBLIC_URL = os.getenv("PUBLIC_URL")  # e.g. https://forge.example.com

# Generated services check SERVICE_API_KEY themselves when run standalone; the gateway does
# auth per API instead, so that variable must not leak into mounted apps.
os.environ.pop("SERVICE_API_KEY", None)

jobs: dict[str, dict] = {}
_build_lock = threading.Lock()  # one browser + build at a time keeps memory predictable
_build = pipeline.build  # swapped in tests


# ---------- loading generated services ----------
def load_service_app(d: Path):
    """Import a generated service's main.py without module-name clashes between services."""
    tag = d.name.replace("-", "_")

    def _load(mod_name: str, path: Path):
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    saved = sys.modules.get("upstream")
    try:
        if (d / "upstream.py").exists():
            sys.modules["upstream"] = _load(f"apiforge_svc_{tag}_upstream", d / "upstream.py")
        return _load(f"apiforge_svc_{tag}_main", d / "main.py").app
    finally:
        if saved is not None:
            sys.modules["upstream"] = saved
        else:
            sys.modules.pop("upstream", None)


def mount(name: str) -> None:
    sub = load_service_app(APIS_DIR / name)
    path = f"/api/{name}"
    app.router.routes[:] = [r for r in app.router.routes
                            if not (isinstance(r, Mount) and r.path == path)]
    app.router.routes.append(Mount(path, app=sub))


def mounted() -> list[str]:
    return sorted(r.path.removeprefix("/api/") for r in app.router.routes
                  if isinstance(r, Mount) and r.path.startswith("/api/"))


def _mount_existing():
    if not APIS_DIR.exists():
        return
    for d in sorted(APIS_DIR.iterdir()):
        if (d / "main.py").exists() and (d / "apiforge.json").exists():
            try:
                mount(d.name)
            except Exception as exc:  # one broken service must not take down the rest
                print(f"[apiforge] could not mount {d.name}: {exc}")


@asynccontextmanager
async def _lifespan(_app):
    _mount_existing()
    yield


app = FastAPI(title="apiforge", version="0.2.0", lifespan=_lifespan)


# ---------- auth ----------
@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    parts = request.url.path.split("/")
    if len(parts) >= 3 and parts[1] == "api" and parts[2]:
        name = parts[2]
        if parts[3:] != ["health"] and not keys.check(APIS_DIR, name, request.headers.get("x-api-key")):
            return JSONResponse({"error": "missing or invalid x-api-key"}, status_code=401)
    return await call_next(request)


def require_admin(x_admin_key: str | None = Header(default=None)):
    if not ADMIN_KEY:
        raise HTTPException(503, "Server has no FORGE_ADMIN_KEY set.")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key.")


# ---------- forge ----------
class ForgeRequest(BaseModel):
    name: str = Field(examples=["hts-tariffs"])
    url: str = Field(examples=["https://hts.usitc.gov"])
    goal: str = Field(examples=["Search HTS codes by keyword or number and return duty rates"])
    max_steps: int = Field(30, ge=5, le=80)
    recapture: bool = True


def _base_url(request: Request) -> str:
    return (PUBLIC_URL or str(request.base_url)).rstrip("/")


def _run_job(job_id: str, req: ForgeRequest, base_url: str) -> None:
    job = jobs[job_id]

    def log(line: str):
        job["log"].append(line)
        del job["log"][:-300]

    with _build_lock:
        job["status"] = "running"
        try:
            name = pipeline.slugify(req.name)
            meta = _build(name, req.goal, req.url, mode="agent", recapture=req.recapture,
                          max_steps=req.max_steps, apis_dir=APIS_DIR, log=log)
            mount(name)
            endpoints = _routes(name)
            job["result"] = {
                "endpoint": f"{base_url}/api/{name}",
                "api_key": keys.issue(APIS_DIR, name),
                "auth_header": "x-api-key",
                "docs": f"{base_url}/api/{name}/docs",
                "routes": endpoints,
                "verified": meta["status"] == "verified",
                "note": "Store the api_key now; it is not shown again (rotate it via /apis/{name}/rotate-key).",
            }
            job["status"] = "done" if meta["status"] != "failing" else "done_with_failing_tests"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            log(traceback.format_exc()[-2000:])
        finally:
            job["finished_at"] = _now()


def _routes(name: str) -> list[str]:
    hidden = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    for r in app.router.routes:
        if isinstance(r, Mount) and r.path == f"/api/{name}":
            return sorted(f"{','.join(sorted(getattr(x, 'methods', None) or ['*']))} /api/{name}{x.path}"
                          for x in r.app.routes if getattr(x, "path", "") not in hidden)
    return []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.post("/forge", dependencies=[Depends(require_admin)], status_code=202)
def forge(req: ForgeRequest, request: Request):
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "name": req.name, "status": "queued", "log": [],
                    "created_at": _now(), "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, req, _base_url(request)), daemon=True).start()
    return {"job_id": job_id, "status_url": f"{_base_url(request)}/forge/jobs/{job_id}"}


@app.get("/forge/jobs/{job_id}", dependencies=[Depends(require_admin)])
def job_status(job_id: str, log_lines: int = 40):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No such job (jobs are kept in memory and reset on restart).")
    return {**job, "log": job["log"][-log_lines:]}


@app.get("/apis", dependencies=[Depends(require_admin)])
def list_apis(request: Request):
    out = []
    for name in mounted():
        meta_file = APIS_DIR / name / "apiforge.json"
        meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        out.append({"name": name, "endpoint": f"{_base_url(request)}/api/{name}",
                    "status": meta.get("status"), "goal": meta.get("goal"),
                    "built_at": meta.get("built_at")})
    return out


@app.post("/apis/{name}/rotate-key", dependencies=[Depends(require_admin)])
def rotate_key(name: str):
    if name not in mounted():
        raise HTTPException(404, "No such API.")
    return {"name": name, "api_key": keys.issue(APIS_DIR, name), "auth_header": "x-api-key"}


@app.get("/health")
def health():
    return {"status": "ok", "apis": len(mounted())}
