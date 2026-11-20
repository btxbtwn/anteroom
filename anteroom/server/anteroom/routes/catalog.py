from datetime import datetime
import hashlib
import json
import threading
from typing import Any, cast
from weakref import WeakKeyDictionary

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.models.catalog import (
    CategoryNode,
    CategorySchema,
    CategoryTree,
    CategoryTreeNode,
)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# Complete category trees (eBay alone has ~20.5k rows) only change when an
# operator imports a new seed, which updates CategoryTree.updated_at. Cache the
# encoded JSON per engine and marketplace so repeated picker loads do not scan
# and serialize 20k ORM rows on every request.
_TREE_CACHE_LOCK = threading.Lock()
_TREE_CACHE: WeakKeyDictionary[Any, dict[str, tuple[tuple[datetime, str | None], bytes, str]]] = WeakKeyDictionary()


def _etag_matches(header: str | None, etag: str) -> bool:
    if not isinstance(header, str) or not header.strip():
        return False
    target = etag.removeprefix("W/")
    for candidate in header.split(","):
        cleaned = candidate.strip()
        if cleaned == "*" or cleaned.removeprefix("W/") == target:
            return True
    return False


def _serialize_tree_nodes(db: Session, marketplace: str, *, complete: bool) -> tuple[bytes, str]:
    rows = (
        db.query(
            CategoryTreeNode.category_id,
            CategoryTreeNode.parent_id,
            CategoryTreeNode.path,
            CategoryTreeNode.label,
            CategoryTreeNode.is_leaf,
            CategoryTreeNode.has_children,
        )
        .filter(CategoryTreeNode.marketplace == marketplace)
        .order_by(CategoryTreeNode.path)
        .all()
    )
    body = json.dumps(
        {
            "coverage": "tree",
            "complete": complete,
            "nodes": [
                {
                    "id": category_id,
                    "parent_id": parent_id,
                    "path": path,
                    "label": label,
                    "is_leaf": is_leaf,
                    "has_children": has_children,
                }
                for category_id, parent_id, path, label, is_leaf, has_children in rows
            ],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    return body, etag


@router.get("/categories")
def categories(
    marketplace: str = "general",
    if_none_match: str | None = Header(default=None),
    db: Session = Depends(get_db, scope="function"),
):
    tree = db.get(CategoryTree, marketplace)
    if tree:
        bind = db.get_bind()
        engine = getattr(bind, "engine", bind)
        version = (tree.updated_at, tree.source) if tree.status == "complete" and tree.updated_at is not None else None
        if version is not None:
            with _TREE_CACHE_LOCK:
                cached = _TREE_CACHE.get(engine, {}).get(marketplace)
            if cached is not None and cached[0] == version:
                _, body, etag = cached
                headers = {"Cache-Control": "private, no-cache", "ETag": etag}
                if _etag_matches(if_none_match, etag):
                    return Response(status_code=304, headers=headers)
                return Response(body, media_type="application/json", headers=headers)
        body, etag = _serialize_tree_nodes(db, marketplace, complete=tree.status == "complete")
        if version is not None:
            with _TREE_CACHE_LOCK:
                by_marketplace = _TREE_CACHE.get(engine)
                if by_marketplace is None:
                    by_marketplace = {}
                    _TREE_CACHE[engine] = by_marketplace
                by_marketplace[marketplace] = (version, body, etag)
            headers = {"Cache-Control": "private, no-cache", "ETag": etag}
            if _etag_matches(if_none_match, etag):
                return Response(status_code=304, headers=headers)
            return Response(body, media_type="application/json", headers=headers)
        return Response(body, media_type="application/json")
    rows = db.query(CategoryNode).filter_by(marketplace=marketplace).order_by(CategoryNode.path).all()
    return {"coverage": "observed", "complete": False, "nodes": [{
        "path": row.path, "parent_path": row.parent_path, "label": row.label,
        "observed_at": cast(datetime, row.observed_at).isoformat(),
    } for row in rows]}


@router.get("/search")
def search(
    q: str,
    marketplace: str | None = None,
    kind: str = "all",
    top_k: int = 15,
    db: Session = Depends(get_db, scope="function"),
):
    from anteroom.services.catalog_index import search_catalog

    query = str(q or "").strip()
    if not query:
        raise HTTPException(400, "q is required")
    if kind not in {"category", "schema", "option", "skill", "fill", "all"}:
        raise HTTPException(400, "kind must be category, schema, option, skill, fill, or all")
    top_k = max(1, min(int(top_k or 15), 50))
    try:
        hits = search_catalog(db, query, marketplace=marketplace or None, kind=kind, top_k=top_k)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"query": query, "kind": kind, "marketplace": marketplace, "results": hits}


@router.get("/dropdown-options")
def dropdown_options(db: Session = Depends(get_db, scope="function")):
    """Static Vendoo dropdown lists from the skill reference scrape.

    Used by the Forms editor when a category schema has not been cached yet, so
    sellers can pick valid condition / style / shipping values without guessing.
    Depop product types come from the category tree: each leaf id's suffix.
    """
    from anteroom.models.listing_values import marketplace_dropdown_forms
    from anteroom.services.category_tree import depop_product_types

    forms = {marketplace: dict(fields) for marketplace, fields in marketplace_dropdown_forms().items()}
    product_types = depop_product_types(db)
    if product_types:
        forms.setdefault("depop", {})["productType"] = product_types
    return {"forms": forms}


@router.get("/fields")
def fields(marketplace: str, category_id: str):
    """Vendoo's field list for one marketplace category.

    Every field that category renders, including the optional ones it unlocks,
    with ``required``, ``multi`` and the coded options each accepts. Served from
    the cache the create path fills, so this needs no Chrome session.
    """
    from anteroom.services.category_fields import load_rows

    rows = load_rows(marketplace, category_id)
    if rows is None:
        raise HTTPException(
            404,
            f"No cached field schema for {marketplace} category {category_id}. "
            "Create or refresh a listing in that category to fetch it.",
        )
    return {
        "marketplace": marketplace,
        "category_id": category_id,
        "required": [row["key"] for row in rows if row.get("required")],
        "fields": rows,
    }


@router.get("/schema")
def schema(category_path: str, db: Session = Depends(get_db, scope="function")):
    rows = db.query(CategorySchema).filter_by(general_path=category_path).all()
    return {row.marketplace: {"category_path": row.category_path, "fields": row.fields,
                             "observed_at": cast(datetime, row.observed_at).isoformat()} for row in rows}


@router.get("/choice-lists/{name}")
def choice_list(name: str, v: str = ""):
    """A marketplace choice list a form names. Its URL carries the version, so the browser keeps it."""
    from anteroom.services.choice_lists import choice_list as read

    listed = read(name)
    if listed is None:
        raise HTTPException(404, "Unknown choice list")
    body, version = listed
    cache = "private, max-age=31536000, immutable" if v == version else "no-cache"
    return Response(body, media_type="application/json", headers={"Cache-Control": cache})
