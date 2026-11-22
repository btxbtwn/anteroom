"""Cache of Vendoo's per-category field schemas.

``/api/category/specifics`` needs the extension and a live Vendoo session, and
the answer for a given leaf only changes when Vendoo changes its taxonomy. So
every fetch is stored keyed by ``(marketplace, category_id)``, which lets the
Fields UI and listing generation read the full field list — including the
optional fields a category unlocks — without going through Chrome.

``vendoo_specifics`` stays pure; this is the only part that touches the store.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, and_, event, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anteroom.database import SessionLocal
from anteroom.models.catalog import CategoryFieldSchema
from anteroom.services.vendoo_specifics import (
    FieldSpec,
    specs_from_rows,
    specs_to_rows,
)

log = logging.getLogger("anteroom.category_fields")
SCHEMA_MAX_AGE = timedelta(days=7)
_MEMORY_TTL_SECONDS = 300.0
_MEMORY_CACHE_SIZE = 512
_SENTINEL = object()

_cache_lock = threading.Lock()
# Key: (id(bind), str(engine.url), user_id, marketplace, category_id)
# Value: (stored_monotonic, fetched_at_utc_or_none, rows_or_none)
_memory_cache: OrderedDict[tuple[Any, ...], tuple[float, datetime | None, list[dict[str, Any]] | None]] = OrderedDict()

__all__ = ["load_fields", "save_fields", "save_rows", "cached_marketplaces", "load_rows",
           "load_rows_many", "listing_category_ids", "clear_memory_cache"]


def _cache_scope(session: Session) -> tuple[int, str, str | None]:
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    return (id(bind), str(engine.url), getattr(session, "user_id", None))


def _session_has_uncommitted_schema_writes(session: Session) -> bool:
    if session.info.get("_category_fields_dirty"):
        return True
    return any(
        isinstance(obj, CategoryFieldSchema)
        for obj in (*session.new, *session.dirty, *session.deleted)
    )


def clear_memory_cache(scope: tuple[int, str, str | None] | None = None) -> None:
    """Drop cached schema rows for one seller/bind scope, or everything when scope is None."""
    with _cache_lock:
        if scope is None:
            _memory_cache.clear()
            return
        bind_id, url, user_id = scope
        stale = [
            key for key in _memory_cache
            if key[0] == bind_id and key[1] == url and (user_id is None or key[2] == user_id)
        ]
        for key in stale:
            _memory_cache.pop(key, None)


def _cache_get(scope: tuple[int, str, str | None], marketplace: str, category_id: str) -> Any:
    key = (*scope, marketplace, category_id)
    now_mono = time.monotonic()
    with _cache_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return _SENTINEL
        stored_at, fetched_at, rows = entry
        if now_mono - stored_at >= _MEMORY_TTL_SECONDS:
            _memory_cache.pop(key, None)
            return _SENTINEL
        if rows is not None and (fetched_at is None or datetime.now(UTC) - fetched_at > SCHEMA_MAX_AGE):
            _memory_cache.pop(key, None)
            return _SENTINEL
        _memory_cache.move_to_end(key)
        return list(rows) if rows is not None else None


def _cache_put(
    scope: tuple[int, str, str | None],
    marketplace: str,
    category_id: str,
    fetched_at: datetime | None,
    rows: list[dict[str, Any]] | None,
) -> None:
    key = (*scope, marketplace, category_id)
    stored_rows = list(rows) if rows is not None else None
    with _cache_lock:
        _memory_cache[key] = (time.monotonic(), fetched_at, stored_rows)
        _memory_cache.move_to_end(key)
        while len(_memory_cache) > _MEMORY_CACHE_SIZE:
            _memory_cache.popitem(last=False)


def _fresh_entry(row: CategoryFieldSchema | None) -> tuple[datetime | None, list[dict[str, Any]] | None]:
    if row is None or row.fetched_at is None:
        return None, None
    fetched_at = row.fetched_at.replace(tzinfo=UTC) if row.fetched_at.tzinfo is None else row.fetched_at
    if datetime.now(UTC) - fetched_at > SCHEMA_MAX_AGE:
        return None, None
    return fetched_at, list(row.fields or [])


def _fresh_rows(row: CategoryFieldSchema | None) -> list[dict[str, Any]] | None:
    return _fresh_entry(row)[1]


def _load_one_from_session(session: Session, marketplace: str, category_id: str) -> list[dict[str, Any]] | None:
    use_cache = not _session_has_uncommitted_schema_writes(session)
    scope = _cache_scope(session) if use_cache else None
    if scope is not None:
        hit = _cache_get(scope, marketplace, category_id)
        if hit is not _SENTINEL:
            return hit
    row = (
        session.query(CategoryFieldSchema)
        .filter_by(marketplace=marketplace, category_id=category_id)
        .one_or_none()
    )
    fetched_at, rows = _fresh_entry(row)
    if scope is not None:
        _cache_put(scope, marketplace, category_id, fetched_at, rows)
    return rows


def load_rows(marketplace: str, category_id: str, *, db: Session | None = None) -> list[dict[str, Any]] | None:
    """The stored rows for one leaf, or None when nothing is cached."""
    if not marketplace or not category_id:
        return None
    market, cat_id = str(marketplace), str(category_id)
    if db is not None:
        return _load_one_from_session(db, market, cat_id)
    with SessionLocal() as session:
        return _load_one_from_session(session, market, cat_id)


def load_rows_many(
    keys: Iterable[tuple[str, str]],
    *,
    db: Session | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]] | None]:
    """Batch-read cached schema rows for several ``(marketplace, category_id)`` pairs in one query."""
    pairs = list(dict.fromkeys((str(m), str(c)) for m, c in keys if m and c))
    out: dict[tuple[str, str], list[dict[str, Any]] | None] = {pair: None for pair in pairs}
    if not pairs:
        return out

    def query(session: Session) -> None:
        use_cache = not _session_has_uncommitted_schema_writes(session)
        scope = _cache_scope(session) if use_cache else None
        missing: list[tuple[str, str]] = []
        if scope is not None:
            for pair in pairs:
                hit = _cache_get(scope, pair[0], pair[1])
                if hit is not _SENTINEL:
                    out[pair] = hit
                else:
                    missing.append(pair)
        else:
            missing = pairs
        if not missing:
            return
        clauses = [
            and_(CategoryFieldSchema.marketplace == marketplace, CategoryFieldSchema.category_id == category_id)
            for marketplace, category_id in missing
        ]
        found: dict[tuple[str, str], tuple[datetime | None, list[dict[str, Any]] | None]] = {}
        for row in session.query(CategoryFieldSchema).filter(or_(*clauses)).all():
            pair = (str(row.marketplace), str(row.category_id))
            found[pair] = _fresh_entry(row)
        for pair in missing:
            fetched_at, rows = found.get(pair, (None, None))
            out[pair] = rows
            if scope is not None:
                _cache_put(scope, pair[0], pair[1], fetched_at, rows)

    if db is not None:
        query(db)
    else:
        _with_session(query)
    return out


def _with_session(work):
    with SessionLocal() as session:
        return work(session)


def load_fields(marketplace: str, category_id: str, *, db: Session | None = None) -> dict[str, FieldSpec] | None:
    rows = load_rows(marketplace, category_id, db=db)
    if rows is None:
        return None
    specs = specs_from_rows(rows)
    return specs


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _context) -> None:
    if any(isinstance(obj, CategoryFieldSchema) for obj in (*session.new, *session.dirty, *session.deleted)):
        session.info["_category_fields_dirty"] = True
        clear_memory_cache(_cache_scope(session))


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    if session.info.pop("_category_fields_dirty", False):
        clear_memory_cache(_cache_scope(session))


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    if session.info.pop("_category_fields_dirty", False):
        clear_memory_cache(_cache_scope(session))


@event.listens_for(Session, "do_orm_execute")
def _bulk_write(state) -> None:
    if not (state.is_update or state.is_delete):
        return
    tables = {getattr(mapper, "local_table", None) for mapper in state.all_mappers}
    if any(getattr(table, "name", None) == CategoryFieldSchema.__tablename__ for table in tables):
        state.session.info["_category_fields_dirty"] = True
        clear_memory_cache(_cache_scope(state.session))


def save_fields(marketplace: str, category_id: str, specs: dict[str, FieldSpec]) -> None:
    """Store one leaf's schema, replacing whatever was there."""
    save_rows(marketplace, category_id, specs_to_rows(specs))


def save_rows(marketplace: str, category_id: str, rows: list[dict[str, Any]]) -> None:
    """Store one leaf's rows as given. A marketplace's own schema (Etsy's
    category properties) is kept under its own ``marketplace`` key."""
    if not marketplace or not category_id:
        return
    with SessionLocal() as db:
        existing = (
            db.query(CategoryFieldSchema)
            .filter_by(marketplace=str(marketplace), category_id=str(category_id))
            .one_or_none()
        )
        if existing:
            existing.fields = rows
            # Refresh even when the server returned an unchanged schema.
            existing.fetched_at = datetime.now(UTC)
        else:
            db.add(CategoryFieldSchema(
                marketplace=str(marketplace), category_id=str(category_id), fields=rows
            ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(CategoryFieldSchema)
                .filter_by(marketplace=str(marketplace), category_id=str(category_id))
                .one_or_none()
            )
            if existing is None:
                raise
            existing.fields = rows
            existing.fetched_at = datetime.now(UTC)
            db.commit()


def cached_marketplaces(category_ids: dict[str, str]) -> dict[str, dict[str, FieldSpec]]:
    """Cached schemas for a ``{marketplace: category_id}`` map."""
    out: dict[str, dict[str, FieldSpec]] = {}
    for marketplace, category_id in (category_ids or {}).items():
        specs = load_fields(marketplace, category_id)
        if specs:
            out[marketplace] = specs
    return out


def listing_category_ids(listing: dict[str, Any]) -> dict[str, str]:
    """``{marketplace: leaf id}`` for a listing, without going near the network.

    Prefers ids already resolved onto the listing; otherwise looks the chosen
    breadcrumb up in the seeded tree. Categories only live there once they have
    been discovered, so an unknown path is simply absent rather than guessed.
    """
    if not isinstance(listing, dict):
        return {}
    out: dict[str, str] = {}
    known = listing.get("marketplace_category_ids")
    if isinstance(known, dict):
        for marketplace, value in known.items():
            text = str(value or "").strip()
            if text:
                out[str(marketplace).strip().lower()] = text
    paths = listing.get("marketplace_categories")
    if isinstance(paths, dict):
        from anteroom.services.leaf_schemas import tree_leaf

        for marketplace, path in paths.items():
            key = str(marketplace).strip().lower()
            if key in out or not str(path or "").strip():
                continue
            leaf = tree_leaf(key, str(path))
            if leaf and leaf.get("id"):
                out[key] = str(leaf["id"])
    return out
