"""Turn a raw HAR capture into a compact, redacted summary of the site's real API calls.

This is the step that decides what the LLM gets to see, so it does three jobs:
1. Drop noise (images, CSS, fonts, analytics, trackers).
2. Group repeated calls into endpoint templates (/items/123 -> /items/{id}).
3. Redact secrets (cookies, auth headers) and trim bodies so the prompt stays small.
"""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

STATIC_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif",
    ".css", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mp3", ".wav", ".map",
)
STATIC_MIME_PREFIXES = ("image/", "font/", "video/", "audio/", "text/css")
NOISE_HOSTS = (
    "google-analytics", "googletagmanager", "doubleclick", "googlesyndication",
    "facebook", "fbcdn", "hotjar", "segment", "sentry", "clarity.ms", "newrelic",
    "datadoghq", "intercom", "fonts.googleapis", "fonts.gstatic", "mixpanel",
    "amplitude", "cloudflareinsights", "linkedin.com/px", "bing.com", "tiktok",
)
DATA_MIME_HINTS = ("json", "xml", "graphql", "csv", "text/plain", "protobuf")
SENSITIVE_HEADERS = {
    "cookie", "authorization", "x-csrf-token", "x-xsrf-token", "csrf-token",
    "x-api-key", "api-key", "x-auth-token", "proxy-authorization",
}
USEFUL_HEADERS = SENSITIVE_HEADERS | {
    "content-type", "accept", "x-requested-with", "origin", "referer",
    "x-client-version", "x-app-version",
}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f]{16,}$", re.I)
_NUM = re.compile(r"^\d+$")


def load_entries(har_path: str | Path) -> list[dict]:
    with open(har_path, encoding="utf-8") as f:
        return json.load(f).get("log", {}).get("entries", [])


def _header_map(headers: list[dict]) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in headers or []}


def is_api_entry(entry: dict) -> bool:
    req, res = entry.get("request", {}), entry.get("response", {})
    method = req.get("method", "GET").upper()
    url = req.get("url", "")
    if method == "OPTIONS" or not url.startswith("http"):
        return False
    if any(h in url for h in NOISE_HOSTS):
        return False
    path = urlsplit(url).path.lower()
    if path.endswith(STATIC_EXTENSIONS):
        return False
    status = res.get("status", 0)
    if not (200 <= status < 400):
        return False
    mime = (res.get("content", {}).get("mimeType") or "").lower()
    if mime.startswith(STATIC_MIME_PREFIXES):
        return False
    rtype = (entry.get("_resourceType") or "").lower()
    if rtype in ("xhr", "fetch"):
        return True
    if method != "GET":
        return True
    return any(hint in mime for hint in DATA_MIME_HINTS)


def path_template(path: str) -> str:
    parts = []
    for seg in path.split("/"):
        if _NUM.match(seg) or _UUID.match(seg) or _HEX.match(seg):
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/".join(parts) or "/"


def _trim(value, max_list: int = 3, max_str: int = 300, depth: int = 0):
    """Shrink a JSON value while keeping its shape visible to the model."""
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        return {k: _trim(v, max_list, max_str, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        trimmed = [_trim(v, max_list, max_str, depth + 1) for v in value[:max_list]]
        if len(value) > max_list:
            trimmed.append(f"… ({len(value) - max_list} more items)")
        return trimmed
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str] + "…"
    return value


def _body_sample(text: str | None, mime: str, max_chars: int) -> object:
    if not text:
        return None
    if "json" in mime or text.lstrip()[:1] in ("{", "["):
        try:
            return _trim(json.loads(text))
        except (ValueError, TypeError):
            pass
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


def _response_text(res: dict) -> str | None:
    content = res.get("content", {})
    text = content.get("text")
    if text and content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return None
    return text


def _redact_value(name: str, value: str) -> str:
    if name in SENSITIVE_HEADERS:
        return f"<REDACTED {name}, len={len(value)}>"
    return value


def summarize(entries: list[dict], max_body: int = 2500, max_endpoints: int = 40) -> list[dict]:
    """Group API calls into endpoints and return redacted examples for each."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for entry in entries:
        if not is_api_entry(entry):
            continue
        req, res = entry["request"], entry.get("response", {})
        parts = urlsplit(req["url"])
        key = (req.get("method", "GET").upper(), parts.netloc, path_template(parts.path))
        g = groups.get(key)
        if g is None:
            if len(groups) >= max_endpoints:
                continue
            headers = _header_map(req.get("headers"))
            post = req.get("postData") or {}
            req_mime = (post.get("mimeType") or "").lower()
            res_mime = (res.get("content", {}).get("mimeType") or "").lower()
            g = groups[key] = {
                "method": key[0],
                "host": key[1],
                "path_template": key[2],
                "example_url": req["url"],
                "calls": 0,
                "query_params": {},
                "request_headers": {
                    k: _redact_value(k, v) for k, v in headers.items() if k in USEFUL_HEADERS
                },
                "uses_cookies": bool(headers.get("cookie")),
                "request_body_mime": req_mime or None,
                "request_body_example": _body_sample(post.get("text"), req_mime, max_body),
                "response_status": res.get("status"),
                "response_mime": res_mime or None,
                "response_example": _body_sample(_response_text(res), res_mime, max_body),
            }
        g["calls"] += 1
        for name, value in parse_qsl(parts.query, keep_blank_values=True):
            seen = g["query_params"].setdefault(name, [])
            if value not in seen and len(seen) < 3:
                seen.append(value[:120])
    return list(groups.values())


def summarize_har(har_path: str | Path, **kwargs) -> list[dict]:
    return summarize(load_entries(har_path), **kwargs)
