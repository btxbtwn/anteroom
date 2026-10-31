"""Fill each direct listing form's category from that form's own options.

The form reads one id per marketplace, and only one of its own options counts:
a Poshmark feature or None, a Depop product type, an eBay leaf, a Mercari
category with no children, or a Sellwild category. The options come from the
same lists the forms show (see ``native_category_match``), never Vendoo's tree.
When the general category changes, a marketplace no option matches is cleared
and the seller is asked to pick, rather than keeping the old item's category.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import logging
import re
from typing import Any

from vendoo_studio.services.direct_categories import direct_category_id
from vendoo_studio.services.native_category_match import evidence, match_row, native_rows
from vendoo_studio.services.seller_overrides import is_locked

log = logging.getLogger("vendoo_studio.agent_categories")

MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
_DIRECT = ("ebay", "poshmark", "mercari", "depop")
# Etsy and Vinted follow a general category change only; their lists are read live.
_ALL = (*MARKETS, "etsy", "vinted")
_LABELS = {
    "ebay": "eBay",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
    "depop": "Depop",
    "sellwild": "Sellwild",
    "etsy": "Etsy",
    "vinted": "Vinted",
}
_SPLIT = re.compile(r"\s*(?:>|›)\s*")
_NUMERIC = re.compile(r"[1-9]\d{0,14}")
_CLEARS = {
    "poshmark": (
        "poshmark_specifics.subcategory",
        "poshmark_specifics.sizeSetId",
        "poshmark_specifics.nativeSizeId",
        "poshmark_specifics.inventoryRows",
    ),
    "depop": (
        "depop_specifics.nativeSizeId",
        "depop_specifics.type",
        "depop_specifics.attributes",
    ),
    "mercari": ("mercari_specifics.nativeSizeId",),
}
_FIELD = {
    "ebay": "direct_category_ids.ebay",
    "poshmark": "direct_category_ids.poshmark",
    "mercari": "direct_category_ids.mercari",
    "depop": "direct_category_ids.depop",
    "sellwild": "sellwild_specifics.nativeCategoryId",
    "etsy": "direct_category_ids.etsy",
    "vinted": "vinted_specifics.category",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _segments(value: Any) -> list[str]:
    return [part.strip() for part in _SPLIT.split(_text(value)) if part.strip()]


def _path_key(value: Any) -> str:
    return " > ".join(part.casefold() for part in _segments(value))


def _unique(values: list[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in found:
            found.append(text)
    return found


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _direct(listing: dict) -> dict:
    return _mapping(listing.get("direct_category_ids"))


def _set_direct(listing: dict, key: str, value: str) -> None:
    raw = listing.get("direct_category_ids")
    if not isinstance(raw, dict):
        raw = {}
        listing["direct_category_ids"] = raw
    if value:
        raw[key] = value
    else:
        raw.pop(key, None)
    if not raw:
        listing.pop("direct_category_ids", None)


def _clear_dependents(listing: dict, market: str) -> None:
    for path in _CLEARS.get(market, ()):
        if is_locked(listing, path):
            continue
        parent: Any = listing
        parts = path.split(".")
        for part in parts[:-1]:
            parent = parent.get(part) if isinstance(parent, dict) else None
            if not isinstance(parent, dict):
                parent = None
                break
        if not isinstance(parent, dict) or parts[-1] not in parent:
            continue
        if path.endswith(".inventoryRows"):
            parent[parts[-1]] = []
        elif path.endswith(".attributes"):
            parent[parts[-1]] = {}
        else:
            parent.pop(parts[-1], None)


def _search_paths(listing: dict, requested: dict, market: str) -> list[str]:
    """Paths to match, most specific first. A category named in this save wins over an older one."""
    paths = _mapping(requested.get("paths"))
    found = [_text(paths.get(market))]
    agent = _text(requested.get("category_path"))
    if agent:
        found.append(agent)
        return _unique(found)
    listed = _mapping(listing.get("marketplace_categories"))
    objects = _mapping(_mapping(listing.get("marketplace_category_objects")).get(market))
    display = objects.get("displayPath")
    found.append(_text(listed.get(market)))
    if isinstance(display, list) and display:
        found.append(" > ".join(str(part) for part in display))
    found.append(_text(listing.get("category_path")))
    if market == "ebay" and not _text(paths.get("ebay")):
        found.append(_text(_direct(listing).get("ebay_path")))
    return _unique(found)


def _rows(rows: Any) -> list[dict]:
    return [row for row in rows or [] if isinstance(row, dict) and _text(row.get("id"))]


def _choose(rows: list[dict], listing: dict, requested: dict, market: str, *, explicit: str) -> dict | None:
    """The form's own option for this market: a named id or path when the form lists it, else the best match."""
    if explicit:
        by_id = next((row for row in rows if _text(row.get("id")) == explicit), None)
        by_path = next((row for row in rows if _path_key(row.get("path")) == _path_key(explicit)), None)
        if by_id or by_path:
            return by_id or by_path
        # eBay's synced tree can be missing; its numeric ids still stand.
        if market == "ebay" and not rows and _NUMERIC.fullmatch(explicit):
            return {"id": explicit, "path": _text(_mapping(requested.get("paths")).get("ebay"))}
        return None
    hint = evidence(listing)
    for path in _search_paths(listing, requested, market):
        if _NUMERIC.fullmatch(path):
            continue
        hit = match_row(rows, path, hint)
        if hit:
            return hit
    return None


def _stored_category(listing: dict, market: str) -> str:
    if market == "sellwild":
        return _text(_mapping(listing.get("sellwild_specifics")).get("nativeCategoryId"))
    if market == "vinted":
        return _text(_mapping(listing.get("vinted_specifics")).get("category"))
    return _text(_direct(listing).get(market))


def _store(listing: dict, market: str, choice: dict | None) -> bool:
    """Write this market's category, or clear it when there is none. True when it changed."""
    value = _text((choice or {}).get("id"))
    before = _stored_category(listing, market)
    if market == "sellwild":
        block = dict(_mapping(listing.get("sellwild_specifics")))
        if value:
            block["nativeCategoryId"] = value
        else:
            block.pop("nativeCategoryId", None)
        if block:
            listing["sellwild_specifics"] = block
        else:
            listing.pop("sellwild_specifics", None)
        return value != before
    changed = value != before
    _set_direct(listing, market, value)
    if market == "ebay":
        path = _text((choice or {}).get("path")) if value else ""
        before_path = _text(_direct(listing).get("ebay_path"))
        if path or not value:
            _set_direct(listing, "ebay_path", path)
        changed = changed or _text(_direct(listing).get("ebay_path")) != before_path
    if value != before:
        _clear_dependents(listing, market)
    return changed


def apply_agent_categories(
    listing: dict,
    catalogs: dict | None = None,
    *,
    requested: dict | None = None,
    markets: set[str] | None = None,
    explicit: set[str] | None = None,
) -> list[str]:
    """Write each market's category from that form's own options. Returns the fields that changed.

    `catalogs` holds each market's options as {"id", "path"} rows. A market this
    save names that no option matches is cleared, so a category chosen for the
    old item is never kept for the new one. A category the seller locked stays.
    """
    if not isinstance(listing, dict):
        return []
    catalogs = catalogs or {}
    requested = requested or {}
    named = set(explicit or [])
    ids = _mapping(requested.get("ids"))
    written: list[str] = []
    for market in MARKETS:
        if market not in (markets or ()) or is_locked(listing, _FIELD[market]):
            continue
        choice = _choose(
            _rows(catalogs.get(market)), listing, requested, market,
            explicit=_text(ids.get(market)) if market in named else "",
        )
        if _store(listing, market, choice):
            written.append(_FIELD[market])
    return written


def _norm_path(path: str) -> str:
    return _text(path).replace("~1", "/").replace("~0", "~").strip("/").replace("/", ".").lower()


def _take_choice(requested: dict, path: str, value: Any) -> None:
    text = _text(value)
    if not text:
        return
    key = _norm_path(path)
    if key in {"category_path", "categorypath"}:
        requested["category_path"] = text
        return
    for market in MARKETS:
        if key in {f"marketplace_categories.{market}", f"direct_category_ids.{market}_path"}:
            requested["paths"][market] = text
            return
    if key == "direct_category_ids.ebay_path":
        requested["paths"]["ebay"] = text
        return
    for market in _DIRECT:
        if key == f"direct_category_ids.{market}":
            requested["ids"][market] = text
            return
    if key in {"sellwild_specifics.nativecategoryid"}:
        requested["ids"]["sellwild"] = text
    elif key == "depop_specifics.categoryid":
        requested["ids"]["depop"] = text
    elif key == "ebay_specifics.categoryid" and _NUMERIC.fullmatch(text):
        requested["ids"]["ebay"] = text


def requested_choices(operations: list[dict] | None, source_text: str = "") -> dict:
    """The category words and ids the assistant wrote, before a saved path is normalized."""
    requested: dict[str, Any] = {"category_path": "", "paths": {}, "ids": {}}
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        if op.get("op") not in {None, "replace", "add"}:
            continue
        if op.get("clear") is True or op.get("value") in ("", None, []):
            continue
        _take_choice(requested, str(op.get("path") or ""), op.get("value"))
    if requested["category_path"] or requested["paths"] or requested["ids"]:
        return requested
    if not source_text:
        return requested
    from vendoo_studio.services.listing_generate import extract_listing_json

    parsed = extract_listing_json(source_text)
    if not isinstance(parsed, dict):
        return requested
    _take_choice(requested, "category_path", parsed.get("category_path"))
    for market, path in _mapping(parsed.get("marketplace_categories")).items():
        _take_choice(requested, f"marketplace_categories.{market}", path)
    for market in _DIRECT:
        _take_choice(requested, f"direct_category_ids.{market}", _mapping(parsed.get("direct_category_ids")).get(market))
    _take_choice(requested, "direct_category_ids.ebay_path", _mapping(parsed.get("direct_category_ids")).get("ebay_path"))
    _take_choice(requested, "sellwild_specifics.nativeCategoryId", _mapping(parsed.get("sellwild_specifics")).get("nativeCategoryId"))
    _take_choice(requested, "depop_specifics.categoryId", _mapping(parsed.get("depop_specifics")).get("categoryId"))
    return requested


def _market_path(listing: dict, market: str) -> str:
    paths = _mapping(listing.get("marketplace_categories"))
    if _text(paths.get(market)):
        return _text(paths.get(market))
    objects = _mapping(_mapping(listing.get("marketplace_category_objects")).get(market))
    display = objects.get("displayPath")
    if isinstance(display, list) and display:
        return " > ".join(str(part) for part in display if _text(part))
    return ""


def markets_for_choice(previous: dict | None, current: dict | None, requested: dict | None) -> tuple[set[str], set[str]]:
    """Markets this save should resolve, and the ones given their own id."""
    previous = previous or {}
    current = current or {}
    requested = requested or {}
    markets: set[str] = set()
    explicit: set[str] = set()
    ids = _mapping(requested.get("ids"))
    for market, value in ids.items():
        if _text(value):
            explicit.add(market)
            markets.add(market)
    agent_path = _text(requested.get("category_path"))
    if agent_path and agent_path != _text(previous.get("category_path")):
        markets.update(MARKETS)
    for market, path in _mapping(requested.get("paths")).items():
        if _text(path) and _text(path) != _market_path(previous, market):
            markets.add(market)
    for market in MARKETS:
        current_path = _market_path(current, market)
        if current_path and current_path != _market_path(previous, market):
            markets.add(market)
            requested.setdefault("paths", {})
            if isinstance(requested["paths"], dict):
                requested["paths"].setdefault(market, current_path)
    previous_direct = _direct(previous)
    current_direct = _direct(current)
    for market in _DIRECT:
        current_id = _text(current_direct.get(market))
        if current_id and current_id != _text(previous_direct.get(market)):
            markets.add(market)
            explicit.add(market)
            ids = requested.setdefault("ids", {})
            if isinstance(ids, dict):
                ids.setdefault(market, current_id)
    previous_sellwild = _text(_mapping(previous.get("sellwild_specifics")).get("nativeCategoryId"))
    current_sellwild = _text(_mapping(current.get("sellwild_specifics")).get("nativeCategoryId"))
    if current_sellwild and current_sellwild != previous_sellwild:
        markets.add("sellwild")
        explicit.add("sellwild")
        ids = requested.setdefault("ids", {})
        if isinstance(ids, dict):
            ids.setdefault("sellwild", current_sellwild)
    return markets, explicit


def _listed(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _saved_message(saved: list[str], missing: list[str]) -> str:
    parts = []
    names = [_LABELS[market] for market in _ALL if market in saved]
    if names:
        parts.append(f"Saved the category for {_listed(names)}.")
    names = [_LABELS[market] for market in _ALL if market in missing]
    if names:
        parts.append(f"Pick the category on {_listed(names)}: none of its own categories matches this one.")
    return " ".join(parts)


def ebay_tree_leaves(db) -> list[dict]:
    """eBay leaves already synced into Studio. Empty when that tree has not been stored."""
    from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode

    if db.get(CategoryTree, "ebay") is None:
        return []
    rows = db.query(CategoryTreeNode).filter_by(marketplace="ebay").all()
    return [
        {"id": row.category_id, "path": row.path}
        for row in rows
        if row.is_leaf or not row.has_children
    ]


def _changed_market(field: str) -> str:
    if field == "sellwild_specifics.nativeCategoryId":
        return "sellwild"
    if field == "vinted_specifics.category":
        return "vinted"
    return field.rsplit(".", 1)[-1]


async def _fill_ebay_suggestion(listing: dict, requested: dict, *, markets: set[str], explicit: set[str]) -> bool:
    """Ask eBay for its leaf when the synced tree has none for this category."""
    if "ebay" not in markets or "ebay" in explicit or direct_category_id(listing, "ebay") or is_locked(listing, _FIELD["ebay"]):
        return False
    query = next(iter(_search_paths(listing, requested, "ebay")), "")
    if not query:
        return False
    try:
        from vendoo_studio.services.direct_categories import _ebay_leaf

        hit = await _ebay_leaf(query[:80])
    except Exception as exc:  # noqa: BLE001 - the seller picks the eBay category instead
        log.info("No eBay category suggestion: %s", exc)
        return False
    if not isinstance(hit, dict) or not _text(hit.get("id")):
        return False
    return _store(listing, "ebay", hit)


async def _fill_etsy(listing: dict, path: str) -> bool:
    """Etsy's own leaf for a new general category, or none so the seller picks it."""
    if is_locked(listing, _FIELD["etsy"]):
        return False
    try:
        from vendoo_studio.services.direct_categories import _etsy_leaf

        hit = await _etsy_leaf(path)
    except Exception as exc:  # noqa: BLE001 - the seller picks the Etsy category instead
        log.info("No Etsy category: %s", exc)
        hit = None
    before = (_text(_direct(listing).get("etsy")), _text(_direct(listing).get("etsy_path")))
    _set_direct(listing, "etsy", _text((hit or {}).get("id")))
    _set_direct(listing, "etsy_path", _text((hit or {}).get("path")) if hit else "")
    return (_text(_direct(listing).get("etsy")), _text(_direct(listing).get("etsy_path"))) != before


def _fill_vinted(listing: dict) -> bool:
    """Vinted's breadcrumb for a new general category, or none so the seller picks it."""
    if is_locked(listing, _FIELD["vinted"]):
        return False
    from vendoo_studio.services.vinted_listing import map_vinted_category_path

    specifics = listing.get("vinted_specifics")
    if not isinstance(specifics, dict):
        specifics = {}
    before = _text(specifics.get("category"))
    path = map_vinted_category_path(listing)
    if path == before:
        return False
    if path:
        specifics["category"] = path
    else:
        specifics.pop("category", None)
    if specifics:
        listing["vinted_specifics"] = specifics
    else:
        listing.pop("vinted_specifics", None)
    return True


def _catalogs(db) -> dict[str, list[dict]]:
    return {**native_rows(), "ebay": ebay_tree_leaves(db)}


async def apply_native_categories(
    db, listing: dict, previous: dict | None, requested: dict | None, *, catalogs: dict | None = None,
) -> list[str]:
    """Write each marketplace form's category onto `listing`. Nothing is saved here."""
    previous = previous or {}
    requested = requested or {}
    markets, explicit = markets_for_choice(previous, listing, requested)
    general = _text(requested.get("category_path"))
    if general == _text(previous.get("category_path")):
        general = ""
    if not markets and not general:
        return []
    catalogs = catalogs if catalogs is not None else _catalogs(db)
    written = apply_agent_categories(listing, catalogs, requested=requested, markets=markets, explicit=explicit)
    if await _fill_ebay_suggestion(listing, requested, markets=markets, explicit=explicit):
        if _FIELD["ebay"] not in written:
            written.append(_FIELD["ebay"])
    if general:
        if await _fill_etsy(listing, general):
            written.append(_FIELD["etsy"])
        if _fill_vinted(listing):
            written.append(_FIELD["vinted"])
    return written


def _run(coroutine):
    """Run a coroutine to the end from sync code, including code called inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def map_general_category(db, previous: dict | None, listing: dict) -> None:
    """A new general category fills each marketplace form's own category."""
    if not isinstance(listing, dict):
        return
    previous = previous if isinstance(previous, dict) else {}
    path = _text(listing.get("category_path"))
    if not path or path == _text(previous.get("category_path")):
        return
    requested = {"category_path": path, "paths": {}, "ids": {}}
    try:
        # Read the database here: the coroutine may run on another thread.
        _run(apply_native_categories(None, listing, previous, requested, catalogs=_catalogs(db)))
    except Exception:
        log.exception("Could not map the general category onto the marketplace forms")


async def apply_saved_agent_category(
    db,
    conv_id: str,
    *,
    operations: list[dict] | None = None,
    source_text: str = "",
) -> None:
    """After a chat save that chooses a category, store each marketplace's own category."""
    try:
        await _apply_saved_agent_category(db, conv_id, operations=operations, source_text=source_text)
    except Exception:
        log.exception("Could not save the assistant's category")


async def _apply_saved_agent_category(db, conv_id: str, *, operations, source_text: str) -> None:
    from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
    from vendoo_studio.services.chat_listing import save_listing_revision

    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return
    current = revisions[0].listing_json
    parent = {}
    if revisions[0].parent_revision_id:
        previous = ListingRepo(db).get_revision(revisions[0].parent_revision_id)
        if previous is not None and isinstance(previous.listing_json, dict):
            parent = previous.listing_json
    requested = requested_choices(operations, source_text)
    listing = copy.deepcopy(current)
    written = await apply_native_categories(db, listing, parent, requested)
    if not written:
        if _rejected_explicit(listing, requested):
            ConversationRepo(db).add_message(
                conv_id, "system",
                "Choose the last category in that branch so the form can show its fields.",
                provider="system", model="",
            )
        return
    save_listing_revision(db, conv_id, listing)
    changed = {_changed_market(field) for field in written}
    saved = [market for market in changed if _stored_category(listing, market)]
    missing = [market for market in changed if not _stored_category(listing, market)]
    message = _saved_message(saved, missing) or "Choose the last category in that branch so the form can show its fields."
    ConversationRepo(db).add_message(conv_id, "system", message, provider="system", model="")


def _rejected_explicit(listing: dict, requested: dict) -> bool:
    named = _mapping(requested.get("ids"))
    return any(_text(value) and _stored_category(listing, market) != _text(value) for market, value in named.items())
