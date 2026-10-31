"""In-memory ring of Vendoo HTTP calls reported by the extension.

Each entry keeps what a browser's network panel shows: the full URL, request
and response headers, and both bodies. The extension masks credentials before
sending; this module masks them again — Bearer and Firebase tokens, refresh
tokens, API keys, cookies, and signed-upload signatures — and caps every field,
so nothing secret reaches the Settings page even from a misbehaving sender.
The list lives only in this process: restarting Studio clears it.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

MAX_ENTRIES = 200
MAX_BODY_CHARS = 64 * 1024 + 200
MAX_HEADERS = 60
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,120}(:\d{1,5})?$")
_MASKED_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-goog-api-key"})
_SECRET_KEYS = (
    "id_?token|refresh_?token|access_?token|api_?key|key|token|password|secret"
    "|X-Goog-Signature|X-Goog-Credential|signature|sig"
)
_SECRET_PAIR = re.compile(
    rf"""(["']?\b(?:{_SECRET_KEYS})["']?\s*[:=]\s*["']?)([^"'&\s,}}]{{4,}})""",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[\w-]{6,}\.[\w-]+\.[\w-]+")
_BEARER = re.compile(r"\b(Bearer\s+)(?!…\(masked\))[\w.~+/=-]+", re.IGNORECASE)
_MASK = "…(masked)"

_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_seq = 0


def _mask(value: str) -> str:
    if value.endswith(_MASK):
        return value
    return f"{value[:4]}{_MASK}"


def redact(text: str) -> str:
    text = _BEARER.sub(lambda m: f"{m.group(1)}{_MASK}", text)
    text = _JWT.sub(lambda m: _mask(m.group(0)), text)
    return _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", text)


def _at(value: object) -> str:
    text = str(value or "").strip()
    if len(text) > 40:
        text = ""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, bytes, bytearray)):
        return None
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if 100 <= code <= 599:
        return code
    return None


def _text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    return redact(str(value)[:limit])


def _headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for name, raw in list(value.items())[:MAX_HEADERS]:
        key = str(name)[:200]
        text = str(raw if raw is not None else "")
        if key.lower() in _MASKED_HEADERS:
            scheme = re.match(r"^(\w+)\s+\S", text)
            out[key] = f"{scheme.group(1)} {_MASK}" if scheme else _MASK
        else:
            out[key] = redact(text[:2000])
    return out


def sanitize_entry(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()[:4000]
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.lower()
    if parts.scheme not in {"http", "https"} or "@" in host or not _HOST.fullmatch(host):
        return None
    url = redact(parts._replace(fragment="").geturl())
    method = str(raw.get("method") or "GET").strip().upper()
    if method not in _METHODS:
        method = "GET"
    status = _status(raw.get("status"))
    ok = bool(raw.get("ok")) and status is not None and status < 400
    try:
        duration = int(raw.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration = 0
    duration = min(max(duration, 0), 600_000)
    error = None
    if not ok:
        error = _text(raw.get("error"), 2000) or (f"HTTP {status}" if status is not None else "failed")
    return {
        "at": _at(raw.get("at")),
        "method": method,
        "url": url,
        "host": host,
        "path": parts.path or "/",
        "status": status,
        "duration_ms": duration,
        "ok": ok,
        "error": error,
        "request_headers": _headers(raw.get("request_headers")),
        "request_body": _text(raw.get("request_body"), MAX_BODY_CHARS),
        "response_headers": _headers(raw.get("response_headers")),
        "response_body": _text(raw.get("response_body"), MAX_BODY_CHARS),
    }


_DETAIL_FIELDS = ("request_headers", "request_body", "response_headers", "response_body")


def record(raw_entries: object) -> int:
    """Keep sanitized lines. Returns how many were stored."""
    global _seq
    if isinstance(raw_entries, dict):
        items = [raw_entries]
    elif isinstance(raw_entries, list):
        items = raw_entries[-MAX_ENTRIES:]
    else:
        return 0
    stored = 0
    with _lock:
        for raw in items:
            clean = sanitize_entry(raw)
            if clean is None:
                continue
            _seq += 1
            clean["id"] = _seq
            _entries.append(clean)
            stored += 1
    return stored


def ingest(payload: object) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return record(payload["entries"])
    return record(payload)


def list_entries() -> list[dict[str, Any]]:
    """Newest first, without headers or bodies: the page polls this every second."""
    with _lock:
        return [
            {key: value for key, value in entry.items() if key not in _DETAIL_FIELDS}
            for entry in reversed(_entries)
        ]


def get_entry(entry_id: int) -> dict[str, Any] | None:
    with _lock:
        for entry in _entries:
            if entry["id"] == entry_id:
                return dict(entry)
    return None


def clear() -> None:
    with _lock:
        _entries.clear()
