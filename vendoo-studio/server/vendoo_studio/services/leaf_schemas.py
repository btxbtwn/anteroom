"""Marketplace leaf schemas Studio can answer without a browser.

A leaf's field schema comes from the cache (``category_field_schemas``), from
the seeded category trees, or, for Mercari, from the public sizes file. eBay
and Etsy forms add their live API schema on top in ``direct_categories``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from vendoo_studio.config import DATA_DIR
from vendoo_studio.services.vendoo_specifics import MERCARI_STATIC_URL, FieldSpec, mercari_specifics

log = logging.getLogger("vendoo_studio.leaf_schemas")

LOOKUP_TIMEOUT_SEC = 10.0
# Condition and colour encodings learned from the seller's own items. Direct
# listing reads it to encode a marketplace form; nothing writes it any more.
SCHEMA_FILE = "vendoo-item-schema.json"


def schema_path() -> Path:
    return Path(DATA_DIR) / SCHEMA_FILE


def load_schema() -> dict[str, Any] | None:
    path = schema_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def tree_leaf(marketplace: str, path: str) -> dict[str, Any] | None:
    """Exact leaf from the seeded ``category_tree_nodes`` table, when present.

    Vendoo's Mercari tree marks some parents as leaves ("Kids > Boys coats &
    jackets" has Boys 0-24 mos, 2T-5T and 4+ under it). Mercari lists such a
    parent with no size, so for Mercari a node with children is never a leaf.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.catalog import CategoryTreeNode
    from vendoo_studio.services.vendoo_categories import path_parts

    parts = path_parts(path)
    wanted = " > ".join(parts)
    if not wanted:
        return None
    try:
        with SessionLocal() as db:
            query = db.query(CategoryTreeNode).filter(
                CategoryTreeNode.marketplace == marketplace,
                CategoryTreeNode.path == wanted,
                CategoryTreeNode.is_leaf.is_(True),
            )
            if marketplace == "mercari":
                query = query.filter(CategoryTreeNode.has_children.is_(False))
            node = query.first()
            if node is None:
                return None
            cat_id, node_path, label, has_children = node.category_id, node.path, node.label, node.has_children
            ancestors = _tree_ancestors(db, marketplace, str(cat_id))
    except SQLAlchemyError:
        return None
    # Shaped like a ``category_search`` hit so one mapper handles both. The
    # tree has no ``payload_text``, so ``extras`` only comes from a live hit.
    return {
        "id": str(cat_id),
        "is_leaf": True,
        "has_children": bool(has_children),
        "last_subcategory_label": label,
        "all_category_label": path_parts(node_path),
        "parent_category_id_path": ancestors,
    }


def _tree_ancestors(db, marketplace: str, category_id: str) -> list[str]:
    """Ancestor ids root-first, which ``categoryV2.path`` lists before the leaf."""
    from vendoo_studio.models.catalog import CategoryTreeNode

    chain: list[str] = []
    seen: set[str] = set()
    current = category_id
    while current and current not in seen:
        seen.add(current)
        parent = db.query(CategoryTreeNode.parent_id).filter(
            CategoryTreeNode.marketplace == marketplace,
            CategoryTreeNode.category_id == current,
        ).scalar()
        if not parent:
            break
        parent = str(parent)
        # The seeded tree hangs every root off a ``__root`` sentinel Vendoo
        # does not store.
        if parent.startswith("__") or parent in ("", "root", "0"):
            break
        chain.append(parent)
        current = parent
    return list(reversed(chain))


async def mercari_fields(category_id: str) -> dict[str, FieldSpec]:
    """Mercari's size options for one category, from Vendoo's static asset.

    Needs no Vendoo session and no extension — the file is public — so this is
    a plain server-side fetch rather than a browser round trip.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT_SEC) as client:
            res = await client.get(MERCARI_STATIC_URL, headers={"Accept-Encoding": "gzip"})
            res.raise_for_status()
            master = res.json()
    except Exception as exc:  # noqa: BLE001 - a missing schema is not fatal
        log.info("No Mercari size data: %s", exc)
        return {}
    return mercari_specifics(master, category_id)


async def leaf_specifics(marketplace: str, category_id: str) -> dict[str, FieldSpec]:
    """One marketplace leaf's schema: cached, else Mercari's public file, else empty."""
    from vendoo_studio.services.category_fields import load_fields, save_fields

    if not category_id:
        return {}
    cached = load_fields(marketplace, category_id)
    if cached is not None or marketplace != "mercari":
        return cached or {}
    specs = await mercari_fields(category_id)
    if specs:
        save_fields(marketplace, category_id, specs)
    return specs
