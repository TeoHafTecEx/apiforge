"""Per-API access keys. Only SHA-256 hashes are stored; the plain key is shown once."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from pathlib import Path

_lock = threading.Lock()


def _file(apis_dir: Path) -> Path:
    return apis_dir / ".keys.json"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _load(apis_dir: Path) -> dict:
    f = _file(apis_dir)
    return json.loads(f.read_text()) if f.exists() else {}


def issue(apis_dir: Path, name: str) -> str:
    """Create (or rotate) the key for an API and return it in plain text."""
    key = f"af_{name.replace('-', '_')}_{secrets.token_urlsafe(24)}"
    with _lock:
        data = _load(apis_dir)
        data[name] = _hash(key)
        apis_dir.mkdir(parents=True, exist_ok=True)
        _file(apis_dir).write_text(json.dumps(data, indent=2))
    return key


def check(apis_dir: Path, name: str, key: str | None) -> bool:
    if not key:
        return False
    stored = _load(apis_dir).get(name)
    return bool(stored) and hmac.compare_digest(stored, _hash(key))
