"""A short-lived copy of what the business assistant reads about the shop.

Reading the shop takes most of a second on a real inventory (every listing,
eight sales ranges, boxes, sourcing), and a follow-up a minute later usually
reads exactly the same rows. The copy is kept for ``TTL_SECONDS`` and dropped
as soon as anything it was built from is written: a flush that touches one of
``WATCHED_TABLES``, or a sourcing refresh (``box_scout`` calls
``invalidate_business_context`` when it saves). What the seller says about the
business and what the assistant remembers are read fresh every time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import Connection, event
from sqlalchemy.orm import Session

TTL_SECONDS = 120.0
# What the snapshot is built from. Writes to anything else (jobs, logs,
# assistant threads) leave the copy alone.
WATCHED_TABLES = frozenset({
    "conversations", "messages", "listings", "listing_revisions", "source_boxes", "ad_spend",
    "sale_events", "scout_checks", "sale_snapshots",
})

_lock = threading.Lock()
_generation = 0
_entry: tuple[tuple[Any, ...], float, Any] | None = None


def invalidate_business_context() -> None:
    """Drop the kept copy so the next question reads the shop again."""
    global _generation, _entry
    with _lock:
        _generation += 1
        _entry = None


def cached(db: Session, day: date, build: Callable[[], Any]) -> Any:
    """``build()``'s result, reused while nothing it reads has changed."""
    global _entry
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    with _lock:
        key = (id(bind), str(engine.url), day, _generation, getattr(db, 'user_id', None))
        if _entry is not None and _entry[0] == key and time.monotonic() - _entry[1] < TTL_SECONDS:
            return _entry[2]
    value = build()
    with _lock:
        # A write that landed while this was being built makes it stale already.
        if key[3] == _generation:
            _entry = (key, time.monotonic(), value)
    return value


def _touches_watched(session: Session) -> bool:
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = getattr(obj, "__tablename__", None)
        if table in WATCHED_TABLES:
            return True
    return False


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _context) -> None:
    if _touches_watched(session):
        invalidate_business_context()


@event.listens_for(Session, "do_orm_execute")
def _bulk_write(state) -> None:
    # query(...).update() and .delete() never pass through a flush.
    if not (state.is_update or state.is_delete):
        return
    tables = {getattr(mapper, "local_table", None) for mapper in state.all_mappers}
    if any(getattr(table, "name", None) in WATCHED_TABLES for table in tables):
        invalidate_business_context()
