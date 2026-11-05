"""A seller's marketplace account choices, kept so a form opens without waiting.

eBay's business policies and item locations come from the marketplace or the seller's
Chrome. A form shows the saved copy at once and, once it is a few minutes old,
reads a fresh one in the background. The Mac keeps a file; the web keeps the
seller's own preferences row, as sourcing does (``box_scout``). Only a
successful read is saved, and List checks every choice again before anything
is written.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from vendoo_studio.config import user_data_root

logger = logging.getLogger(__name__)

REFRESH_AFTER_S = 5 * 60
_lock = threading.Lock()
_refreshing: set[tuple[str, str, str]] = set()
_tasks: set[asyncio.Task[None]] = set()

Read = Callable[[], Awaitable[Any]]
Refresh = Callable[[], Coroutine[Any, Any, None]]
Background = Callable[[Refresh], None]


def _seller() -> str:
    from vendoo_studio.services.service_context import current

    context = current()
    return context.seller_id if context is not None else ""


def _path() -> Path:
    return user_data_root() / "marketplace-choices.json"


def _key(name: str) -> str:
    return f"choices:{name}"


def _entry(name: str) -> dict | None:
    if not _seller():
        with _lock:
            try:
                stored = json.loads(_path().read_text())
            except (OSError, json.JSONDecodeError):
                return None
        entry = stored.get(name) if isinstance(stored, dict) else None
        return entry if isinstance(entry, dict) else None
    from sqlalchemy import select

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.user_preference import UserPreference

    with SessionLocal() as db:
        entry = db.scalar(select(UserPreference.value).where(UserPreference.key == _key(name)))
    return entry if isinstance(entry, dict) else None


def _save(name: str, account: str, value: Any) -> None:
    entry = {"account": account, "at": time.time(), "value": value}
    if not _seller():
        with _lock:
            path = _path()
            try:
                stored = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                stored = {}
            stored = stored if isinstance(stored, dict) else {}
            stored[name] = entry
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(stored) + "\n")
            tmp.replace(path)
        return
    from sqlalchemy.dialects.postgresql import insert

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.user_preference import UserPreference

    with SessionLocal() as db:
        db.execute(insert(UserPreference).values(user_id=_seller(), key=_key(name), value=entry)
                   .on_conflict_do_update(index_elements=["user_id", "key"], set_={"value": entry}))
        db.commit()


def saved(name: str, account: str) -> Any | None:
    """The saved choices for this account, or None."""
    entry = _entry(name)
    return entry.get("value") if entry and entry.get("account") == account else None


def forget(name: str) -> None:
    """Drop the saved copy so the next open reads again (tests and an explicit reload)."""
    if not _seller():
        with _lock:
            path = _path()
            try:
                stored = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(stored, dict) or name not in stored:
                return
            stored.pop(name, None)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(stored) + "\n")
            tmp.replace(path)
        return
    from sqlalchemy import delete

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.user_preference import UserPreference

    with SessionLocal() as db:
        db.execute(delete(UserPreference).where(UserPreference.key == _key(name)))
        db.commit()


def in_thread(refresh: Refresh) -> None:
    """Refresh on a thread of its own, as this seller. For a form built on a short-lived loop."""
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(lambda: asyncio.run(refresh()),), daemon=True,
                     name="saved-choices-refresh").start()


def on_this_loop(refresh: Refresh) -> None:
    """Refresh on the running loop. For reads that must stay on the app's own loop (the web's Chrome relay)."""
    task = asyncio.get_running_loop().create_task(refresh())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _read_and_save(name: str, account: str, read: Read, usable: Callable[[Any], bool]) -> Any:
    value = await read()
    if usable(value):
        await asyncio.to_thread(_save, name, account, value)
    return value


def _refresh_later(name: str, account: str, read: Read, usable: Callable[[Any], bool], background: Background) -> None:
    job = (_seller(), name, account)
    with _lock:
        if job in _refreshing:
            return
        _refreshing.add(job)

    async def refresh() -> None:
        try:
            await _read_and_save(name, account, read, usable)
        except Exception as exc:  # noqa: BLE001 - the saved copy stays; the next open tries again
            logger.info("Refreshing saved %s choices failed: %s", name, type(exc).__name__)
        finally:
            with _lock:
                _refreshing.discard(job)

    background(refresh)


async def kept(name: str, account: str, read: Read, *, usable: Callable[[Any], bool] = lambda _value: True,
               background: Background = in_thread) -> Any:
    """The saved choices at once, refreshed behind the form once they are a few minutes old.

    With no saved copy for this account, read now. ``usable`` keeps a partial or
    signed-out answer from being saved, so the seller's reload still reads again.
    """
    entry = await asyncio.to_thread(_entry, name)
    if entry and entry.get("account") == account:
        if time.time() - float(entry.get("at") or 0) >= REFRESH_AFTER_S:
            _refresh_later(name, account, read, usable, background)
        return entry.get("value")
    return await _read_and_save(name, account, read, usable)
