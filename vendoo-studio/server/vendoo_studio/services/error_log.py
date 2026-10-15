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


def list_entries() -> list[dict[str, Any]]:
    """Newest first."""
    with _lock:
        return [dict(entry) for entry in reversed(_entries)]


def clear() -> None:
    with _lock:
        _entries.clear()
