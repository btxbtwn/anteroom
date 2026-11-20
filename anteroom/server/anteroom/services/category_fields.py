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
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
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

__all__ = ["load_fields", "save_fields", "save_rows", "cached_marketplaces", "load_rows",
           "load_rows_many", "listing_category_ids"]


def _fresh_rows(row: CategoryFieldSchema | None) -> list[dict[str, Any]] | None:
    if row is None or row.fetched_at is None:
        return None
    fetched_at = row.fetched_at.replace(tzinfo=UTC) if row.fetched_at.tzinfo is None else row.fetched_at
    if datetime.now(UTC) - fetched_at > SCHEMA_MAX_AGE:
        return None
    return list(row.fields or [])


def load_rows(marketplace: str, category_id: str, *, db: Session | None = None) -> list[dict[str, Any]] | None:
    """The stored rows for one leaf, or None when nothing is cached."""
    if not marketplace or not category_id:
        return None
    if db is not None:
        row = (
            db.query(CategoryFieldSchema)
            .filter_by(marketplace=str(marketplace), category_id=str(category_id))
            .one_or_none()
        )
        return _fresh_rows(row)
    with SessionLocal() as session:
        row = (
            session.query(CategoryFieldSchema)
            .filter_by(marketplace=str(marketplace), category_id=str(category_id))
            .one_or_none()
        )
        return _fresh_rows(row)


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

    def query(session: Session) -> list[CategoryFieldSchema]:
        clauses = [
            and_(CategoryFieldSchema.marketplace == marketplace, CategoryFieldSchema.category_id == category_id)
            for marketplace, category_id in pairs
        ]
        return list(session.query(CategoryFieldSchema).filter(or_(*clauses)).all())

    rows = query(db) if db is not None else _with_session(query)
    for row in rows:
        out[(str(row.marketplace), str(row.category_id))] = _fresh_rows(row)
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
