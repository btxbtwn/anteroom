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
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import Connection, event
from sqlalchemy.orm import Session

TTL_SECONDS = 120.0
_CACHE_SIZE = 16
# What the snapshot is built from. Writes to anything else (jobs, logs,
# assistant threads) leave the copy alone.
WATCHED_TABLES = frozenset({
    "conversations", "messages", "listings", "listing_revisions", "source_boxes", "ad_spend",
    "sale_events", "scout_checks", "sale_snapshots", "business_expenses", "marketplace_sales",
})

_lock = threading.Lock()
_global_generation = 0
_seller_generations: dict[str | None, int] = {}
# Key: (id(bind), str(engine.url), day, user_id)
# Value: ((global_gen, seller_gen), stored_monotonic, value)
_entries: OrderedDict[tuple[Any, ...], tuple[tuple[int, int], float, Any]] = OrderedDict()


def invalidate_business_context(user_id: str | None = None) -> None:
    """Drop the kept copy for one seller, or for every seller when ``user_id`` is None."""
    global _global_generation
    with _lock:
        if user_id is None:
            _global_generation += 1
            _seller_generations.clear()
            _entries.clear()
            return
        _seller_generations[user_id] = _seller_generations.get(user_id, 0) + 1
        stale = [key for key in _entries if key[3] == user_id]
        for key in stale:
            _entries.pop(key, None)


def cached(db: Session, day: date, build: Callable[[], Any]) -> Any:
    """``build()``'s result, reused while nothing it reads has changed."""
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    user_id = getattr(db, "user_id", None)
    key = (id(bind), str(engine.url), day, user_id)
    with _lock:
        generation = (_global_generation, _seller_generations.get(user_id, 0))
        hit = _entries.get(key)
        if hit is not None and hit[0] == generation and time.monotonic() - hit[1] < TTL_SECONDS:
            _entries.move_to_end(key)
            return hit[2]
    value = build()
    with _lock:
        # A write that landed while this was being built makes it stale already.
        if (_global_generation, _seller_generations.get(user_id, 0)) == generation:
            _entries[key] = (generation, time.monotonic(), value)
            _entries.move_to_end(key)
            while len(_entries) > _CACHE_SIZE:
                _entries.popitem(last=False)
    return value


def _touches_watched(session: Session) -> bool:
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = getattr(obj, "__tablename__", None)
        # The first read can archive Vendoo sales it already includes.
        if table == "marketplace_sales" and session.info.get("archiving_vendoo_sales"):
            continue
        if table in WATCHED_TABLES:
            return True
    return False


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _context) -> None:
    if _touches_watched(session):
        session.info["business_context_dirty"] = True
        invalidate_business_context(user_id=getattr(session, "user_id", None))


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _after_transaction(session: Session) -> None:
    if session.info.pop("business_context_dirty", False):
        invalidate_business_context(user_id=getattr(session, "user_id", None))


@event.listens_for(Session, "do_orm_execute")
def _bulk_write(state) -> None:
    # query(...).update() and .delete() never pass through a flush.
    if not (state.is_update or state.is_delete):
        return
    tables = {getattr(mapper, "local_table", None) for mapper in state.all_mappers}
    if any(getattr(table, "name", None) in WATCHED_TABLES for table in tables):
        state.session.info["business_context_dirty"] = True
        invalidate_business_context(user_id=getattr(state.session, "user_id", None))
