"""In-memory ring of Studio's warnings and errors for Settings → Logs.

Failures such as an AI provider rejecting a key were only visible in the log
file on the seller's Mac. A logging handler keeps the recent ones here so the
Logs page can show them, and a stderr handler stamps them with the time so the
log file shows when each happened. Messages pass through the same redaction as
the Vendoo API log. The list lives only in this process: restarting Studio
clears it.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from vendoo_studio.services.vendoo_api_log import redact

MAX_ENTRIES = 200
MAX_MESSAGE_CHARS = 4000
MAX_DETAIL_CHARS = 16000
# Studio's own loggers, and uvicorn's, which logs a route's unhandled exception.
LOGGERS = ("vendoo_studio", "uvicorn.error")
_PREFIX = "vendoo_studio."

_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_seq = 0


class ErrorLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            message = redact(record.getMessage()[:MAX_MESSAGE_CHARS])
            detail = None
            if record.exc_info:
                detail = redact(logging.Formatter().formatException(record.exc_info)[-MAX_DETAIL_CHARS:])
            name = record.name
            source = name[len(_PREFIX):] if name.startswith(_PREFIX) else name
            with _lock:
                _seq += 1
                _entries.append({
                    "id": _seq,
                    "at": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
                    "level": record.levelname.lower(),
                    "source": source,
                    "message": message,
                    "detail": detail,
                })
        except Exception:
            self.handleError(record)


def install() -> None:
    """Attach the handlers once; safe to call again."""
    studio = logging.getLogger("vendoo_studio")
    # Info has to reach the disk handler. The stderr and warning-ring handlers
    # keep their own WARNING threshold, so the terminal stays warnings and errors.
    if studio.level == logging.NOTSET or studio.level > logging.INFO:
        studio.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in studio.handlers):
        # Without a handler of its own, Studio's warnings reached the log file
        # only through logging's last-resort printer, which the ring handler
        # below would switch off.
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        studio.addHandler(stream)
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(h, ErrorLogHandler) for h in logger.handlers):
            handler = ErrorLogHandler(level=logging.WARNING)
            logger.addHandler(handler)
        if not any(isinstance(h, AppLogHandler) for h in logger.handlers):
            logger.addHandler(AppLogHandler(level=logging.INFO))


def list_entries() -> list[dict[str, Any]]:
    """Newest first."""
    with _lock:
        return [dict(entry) for entry in reversed(_entries)]


def clear() -> None:
    with _lock:
        _entries.clear()


# A bounded on-disk ring so `anteroom logs` can see info, warnings, and errors
# after a restart. The in-memory list above stays warnings and errors for Settings.
_DISK_MAX = 2000
_disk_lock = threading.Lock()


def _disk_path():
    from vendoo_studio.config import user_data_root

    return user_data_root() / "logs" / "anteroom.jsonl"


class AppLogHandler(logging.Handler):
    """Append info and above to the on-disk ring. Secrets are redacted."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact(record.getMessage()[:MAX_MESSAGE_CHARS])
            name = record.name
            source = name[len(_PREFIX):] if name.startswith(_PREFIX) else name
            entry = {
                "at": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
                "level": record.levelname.lower(),
                "source": source,
                "message": message,
            }
            _append_disk(entry)
        except Exception:
            self.handleError(record)


def _append_disk(entry: dict[str, Any]) -> None:
    import json

    path = _disk_path()
    line = json.dumps(entry, ensure_ascii=False)
    with _disk_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if path.stat().st_size <= 512_000:
            return
        rows = path.read_text(encoding="utf-8").splitlines()
        if len(rows) > _DISK_MAX:
            path.write_text("\n".join(rows[-_DISK_MAX:]) + "\n", encoding="utf-8")


def _read_disk() -> list[dict[str, Any]]:
    import json

    path = _disk_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


_LEVEL_RANK = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def query_entries(*, level: str | None = None, since: str | None = None, grep: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Newest matching disk entries. ``level`` is a minimum: warning includes errors."""
    minimum = _LEVEL_RANK.get((level or "info").lower(), 20)
    needle = (grep or "").casefold()
    matched = []
    for entry in _read_disk():
        rank = _LEVEL_RANK.get(str(entry.get("level") or "").lower(), 20)
        if rank < minimum:
            continue
        if since and str(entry.get("at") or "") < since:
            continue
        if needle and needle not in str(entry.get("message") or "").casefold():
            continue
        matched.append(entry)
    return list(reversed(matched[-max(1, min(limit, 1000)):]))
