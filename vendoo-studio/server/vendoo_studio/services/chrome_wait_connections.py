"""Notice code that holds a pooled database connection while it waits on Chrome.

A Chrome reply can take minutes. A connection held for that wait serves no
one, and a few held at once leave the hosted pool nothing for sellers' own page
requests, which then fail with QueuePool timeouts. Each wait therefore starts
with its caller's transaction ended (commit or rollback), as a listing job does.

Connections are attributed to the asyncio task that checked them out. Code that
hands a blocking read to a worker thread is not counted against the waiting task.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import weakref
from pathlib import Path

from sqlalchemy import event

log = logging.getLogger(__name__)

_TASK_KEY = "chrome_wait_task"
_held: weakref.WeakKeyDictionary[asyncio.Task, int] = weakref.WeakKeyDictionary()
_seen: set[str] = set()
# Tests turn this on so that a new hold fails where it is introduced.
strict = False


class ConnectionHeldDuringChromeWait(RuntimeError):
    pass


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _checkout(_dbapi_connection, record, _proxy) -> None:
    task = _current_task()
    if task is None:
        return
    record.info[_TASK_KEY] = weakref.ref(task)
    _held[task] = _held.get(task, 0) + 1


def _checkin(_dbapi_connection, record) -> None:
    if record is None:
        return
    ref = record.info.pop(_TASK_KEY, None)
    task = ref() if ref is not None else None
    if task is None:
        return
    remaining = _held.get(task, 0) - 1
    if remaining > 0:
        _held[task] = remaining
    else:
        _held.pop(task, None)


def watch(engine) -> None:
    if not event.contains(engine, "checkout", _checkout):
        event.listen(engine, "checkout", _checkout)
        event.listen(engine, "checkin", _checkin)


def unwatch(engine) -> None:
    if event.contains(engine, "checkout", _checkout):
        event.remove(engine, "checkout", _checkout)
        event.remove(engine, "checkin", _checkin)


def check_released() -> None:
    """Called as a Chrome wait starts. Reports a connection the waiting task still holds."""
    task = _current_task()
    if task is None or not _held.get(task):
        return
    frames = [frame for frame in traceback.extract_stack()[:-1] if "/vendoo_studio/" in frame.filename]
    site = " < ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in reversed(frames[-5:]))
    if strict:
        raise ConnectionHeldDuringChromeWait(f"A database connection is held while waiting on Chrome: {site}")
    if site not in _seen:
        _seen.add(site)
        log.warning("Database connection held while waiting on Chrome: %s", site)
