"""Save the category the listing assistant chose onto each direct listing form.

The form reads one id per marketplace. A category counts only when it is the
last step in its branch: a Poshmark feature or None, a Depop product type, an
eBay leaf, a Mercari category with no children, or a Sellwild category. A
group the assistant named on the way there is not stored.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any

from vendoo_studio.services.chrome_catalog import (
    _depop_from_text,
    _depop_product,
    _poshmark_body,
    _poshmark_from_ids,
    name_key,
    poshmark_key,
)
from vendoo_studio.services.direct_categories import direct_category_id
from vendoo_studio.services.seller_overrides import is_locked
from vendoo_studio.services.sellwild_listing import sellwild_catalog, suggest_sellwild

log = logging.getLogger("vendoo_studio.agent_categories")

MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
_DIRECT = ("ebay", "poshmark", "mercari", "depop")
_LABELS = {
    "ebay": "eBay",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
    "depop": "Depop",
    "sellwild": "Sellwild",
}
_SPLIT = re.compile(r"\s*(?:>|›)\s*")
_NUMERIC = re.compile(r"[1-9]\d{0,14}")
# A general category often says "T-Shirts" where the marketplace list says "Tees".
_NAME_ALIASES = {"tshirt": "tees", "tshirts": "tees", "tee": "tees"}
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


def _name_keys(value: Any) -> set[str]:
    folded = name_key(value)
    keys = {folded} if folded else set()
    alias = _NAME_ALIASES.get(folded)
    if alias:
        keys.add(alias)
    return keys


def _names_match(left: Any, right: Any) -> bool:
    return bool(_name_keys(left) & _name_keys(right))


def _alias_last(path: str) -> str:
    parts = _segments(path)
    if not parts:
        return ""
    alias = _NAME_ALIASES.get(name_key(parts[-1]))
    if not alias:
        return ""
    parts[-1] = alias
    return " > ".join(parts)


def _candidate_paths(text: str) -> list[str]:
    """The named path, then each shorter tail. A T-shirt alias is tried with each tail."""
    parts = _segments(text)
    found: list[str] = []
    # A single trailing word, such as Tees, matches too many leaves. Keep it
    # only when that word is the whole category the seller named.
    for length in range(len(parts), 0, -1):
        if length == 1 and len(parts) > 1:
            continue
        tail = " > ".join(parts[-length:])
        found.append(tail)
        aliased = _alias_last(tail)
        if aliased:
            found.append(aliased)
    return _unique(found)


def _label(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    names = entry.get("name_i18n")
    if isinstance(names, dict) and names.get("en-US"):
        return str(names["en-US"])
    return str(entry.get("name") or entry.get("display") or entry.get("id") or "")


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


def _poshmark_row(rows: list[dict], segment: str) -> dict | None:
    return next((
        row for row in rows
        if _names_match(row.get("display"), segment) or _names_match(row.get("id"), segment)
    ), None)


def _poshmark_from_segments(catalog: dict, segments: list[str]) -> str:
    departments = [row for row in _poshmark_body(catalog).get("catalog", {}).get("departments") or [] if isinstance(row, dict)]
    for start, segment in enumerate(segments):
        department = _poshmark_row(departments, segment)
        if not department:
            continue
        categories = [row for row in department.get("categories") or [] if isinstance(row, dict)]
        for index in range(start + 1, len(segments)):
            category = _poshmark_row(categories, segments[index])
            if not category:
                continue
            features = [row for row in category.get("category_features") or [] if isinstance(row, dict)]
            rest = segments[index + 1:]
            dept_id = _text(department.get("id"))
            cat_id = _text(category.get("id"))
            if not dept_id or not cat_id:
                continue
            if not features and not rest:
                return f"{dept_id}/{cat_id}"
            if len(rest) != 1:
                continue
            if poshmark_key(rest[0]) in {"none", "nones"}:
                return f"{dept_id}/{cat_id}"
            feature = _poshmark_row(features, rest[0])
            feature_id = _text((feature or {}).get("id"))
            if feature_id:
                return f"{dept_id}/{cat_id}/{feature_id}"
    return ""


def _poshmark_choice(catalog: dict, listing: dict, requested: dict, *, explicit: str) -> str:
    if explicit:
        if not catalog:
            parts = [part for part in explicit.split("/") if part and not part.startswith("group:")]
            return explicit if 2 <= len(parts) <= 3 else ""
        found = _poshmark_from_ids(catalog, [part for part in explicit.split("/") if part])
        return _text((found or {}).get("id"))
    if not catalog:
        return _text(_direct(listing).get("poshmark"))
    for path in _search_paths(listing, requested, "poshmark"):
        found = _poshmark_from_segments(catalog, _segments(path))
        if found:
            return found
    return ""


def _depop_department(attributes: dict, segment: str) -> str:
    key = name_key(segment)
    for entry in attributes.get("department") or []:
        if isinstance(entry, dict) and (name_key(entry.get("id")) == key or name_key(_label(entry)) == key):
            return _text(entry.get("id"))
    text = segment or ""
    if re.search(r"kid", text, re.I):
        chosen = "kidswear"
    elif re.search(r"women|female", text, re.I):
        chosen = "womenswear"
    elif re.search(r"\bmen\b|male", text, re.I):
        chosen = "menswear"
    else:
        return ""
    if any(isinstance(entry, dict) and entry.get("id") == chosen for entry in attributes.get("department") or []):
        return chosen
    return ""


def _depop_hits(attributes: dict, leaf: str, department: str, group_name: str | None) -> list[str]:
    hits: list[str] = []
    leaf_keys = _name_keys(leaf)
    group_key = name_key(group_name) if group_name else ""
    for group in attributes.get("group") or []:
        if not isinstance(group, dict):
            continue
        if group_key and name_key(_label(group)) != group_key and name_key(group.get("id")) != group_key:
            continue
        for product in group.get("product_types") or []:
            if not isinstance(product, dict):
                continue
            if name_key(product.get("id")) not in leaf_keys and name_key(_label(product)) not in leaf_keys:
                continue
            for department_id in group.get("department") or []:
                if department and department_id != department:
                    continue
                found = _depop_product(attributes, str(department_id), _text(product.get("id")))
                if found and found not in hits:
                    hits.append(found)
    return hits


def _depop_from_path(attributes: dict, path: str) -> str:
    found = _depop_from_text(attributes, path)
    if found:
        return found
    parts = _segments(path)
    if len(parts) < 2:
        return ""
    department = ""
    for segment in parts:
        department = _depop_department(attributes, segment)
        if department:
            break
    group = parts[-2] if len(parts) >= 3 else None
    strict = _depop_hits(attributes, parts[-1], department, group)
    if len(strict) == 1:
        return strict[0]
    if group:
        loose = _depop_hits(attributes, parts[-1], department, None)
        if len(loose) == 1:
            return loose[0]
    return ""


def _depop_choice(attributes: dict, listing: dict, requested: dict, *, explicit: str) -> str:
    if explicit:
        if not attributes:
            return explicit
        return _depop_from_text(attributes, explicit)
    if not attributes:
        return _text(_direct(listing).get("depop"))
    for path in _search_paths(listing, requested, "depop"):
        found = _depop_from_path(attributes, path)
        if found:
            return found
    specifics = _mapping(listing.get("depop_specifics"))
    return _depop_from_text(attributes, _text(specifics.get("categoryId")))


def _leaf_rows(rows: Any) -> list[dict]:
    found = []
    for row in rows or []:
        if isinstance(row, dict) and _text(row.get("id")) and row.get("parent") is not True and row.get("has_children") is not True:
            found.append(row)
    return found


def _match_leaf_once(rows: list[dict], text: str) -> dict | None:
    key = _path_key(text)
    if not key:
        return None
    exact = [row for row in rows if _path_key(row.get("path") or row.get("label")) == key]
    if len(exact) == 1:
        return exact[0]
    suffix = [row for row in rows if _path_key(row.get("path") or row.get("label")).endswith(" > " + key)]
    return suffix[0] if len(suffix) == 1 else None


def _match_leaf(rows: list[dict], text: str) -> dict | None:
    """The longest tail of the named path that matches one leaf, including a T-shirt alias."""
    for candidate in _candidate_paths(text):
        hit = _match_leaf_once(rows, candidate)
        if hit:
            return hit
    return None


def _ebay_choice(leaves: list[dict], listing: dict, requested: dict, *, explicit: str) -> tuple[str, str]:
    current = _direct(listing)
    if explicit:
        if not _NUMERIC.fullmatch(explicit):
            return "", ""
        hit = next((row for row in leaves if _text(row.get("id")) == explicit), None)
        path = _text((requested.get("paths") or {}).get("ebay")) or _text((hit or {}).get("path")) or _text(current.get("ebay_path"))
        return explicit, path
    for path in _search_paths(listing, requested, "ebay"):
        if _NUMERIC.fullmatch(path):
            continue
        hit = _match_leaf(leaves, path)
        if hit:
            return _text(hit.get("id")), _text(hit.get("path") or hit.get("label"))
    return "", ""


def _mercari_rows(catalog: Any) -> list[dict]:
    if isinstance(catalog, list):
        return _leaf_rows(catalog)
    return _leaf_rows(_mapping(catalog).get("categories"))


def _mercari_choice(rows: list[dict], listing: dict, requested: dict, *, explicit: str) -> str:
    if explicit:
        if rows:
            return explicit if any(_text(row.get("id")) == explicit for row in rows) else ""
        return explicit if _NUMERIC.fullmatch(explicit) else ""
    if not rows:
        return ""
    for path in _search_paths(listing, requested, "mercari"):
        hit = _match_leaf(rows, path)
        if hit:
            return _text(hit.get("id"))
    return ""


def _sellwild_rows(catalogs: dict) -> list[dict]:
    supplied = catalogs.get("sellwild")
    catalog = supplied if isinstance(supplied, dict) and isinstance(supplied.get("categories"), list) else sellwild_catalog()
    return [row for row in catalog.get("categories") or [] if isinstance(row, dict)]


def _sellwild_known(rows: list[dict], raw: str) -> str:
    if not _NUMERIC.fullmatch(raw):
        return ""
    return raw if any(str(row.get("id")) == raw for row in rows) else ""


def _sellwild_choice(rows: list[dict], listing: dict, requested: dict, catalogs: dict, *, explicit: str) -> str:
    if explicit:
        return _sellwild_known(rows, explicit)
    for path in _search_paths(listing, requested, "sellwild"):
        hit = _match_leaf(rows, path)
        if hit:
            return str(hit.get("id"))
    if "sellwild" in catalogs:
        return ""
    probe = copy.deepcopy(listing)
    agent = _text(requested.get("category_path"))
    if agent:
        probe["category_path"] = agent
    specifics = dict(_mapping(probe.get("sellwild_specifics")))
    specifics.pop("nativeCategoryId", None)
    probe["sellwild_specifics"] = specifics
    suggested = suggest_sellwild(probe).get("category_id")
    return str(suggested) if suggested and _sellwild_known(rows, str(suggested)) else ""


def apply_agent_categories(
    listing: dict,
    catalogs: dict | None = None,
    *,
    requested: dict | None = None,
    markets: set[str] | None = None,
    explicit: set[str] | None = None,
) -> list[str]:
    """Write terminal category ids for `markets`. An explicit id is kept when it is terminal.

    Returns the category fields whose saved value changed. A market the assistant
    left alone keeps the id it already had when this pass cannot name a last step.
    """
    if not isinstance(listing, dict):
        return []
    catalogs = catalogs or {}
    requested = requested or {}
    wanted = set(markets or [])
    named = set(explicit or [])
    ids = _mapping(requested.get("ids"))
    written: list[str] = []

    def remember(market: str, changed: bool) -> None:
        if changed:
            written.append(_FIELD[market])

    if "poshmark" in wanted and not is_locked(listing, _FIELD["poshmark"]):
        before = _text(_direct(listing).get("poshmark"))
        choice = _poshmark_choice(
            _mapping(catalogs.get("poshmark")), listing, requested,
            explicit=_text(ids.get("poshmark")) if "poshmark" in named else "",
        )
        if choice or "poshmark" in named:
            _set_direct(listing, "poshmark", choice)
            if choice != before:
                _clear_dependents(listing, "poshmark")
            remember("poshmark", choice != before)
    if "depop" in wanted and not is_locked(listing, _FIELD["depop"]):
        before = _text(_direct(listing).get("depop"))
        choice = _depop_choice(
            _mapping(catalogs.get("depop")), listing, requested,
            explicit=_text(ids.get("depop")) if "depop" in named else "",
        )
        if choice or "depop" in named:
            _set_direct(listing, "depop", choice)
            if choice != before:
                _clear_dependents(listing, "depop")
            remember("depop", choice != before)
    if "mercari" in wanted and not is_locked(listing, _FIELD["mercari"]):
        before = _text(_direct(listing).get("mercari"))
        choice = _mercari_choice(
            _mercari_rows(catalogs.get("mercari")), listing, requested,
            explicit=_text(ids.get("mercari")) if "mercari" in named else "",
        )
        if choice or ("mercari" in named and _mercari_rows(catalogs.get("mercari"))):
            _set_direct(listing, "mercari", choice)
            if choice != before:
                _clear_dependents(listing, "mercari")
            remember("mercari", choice != before)
        elif "mercari" in named and not _NUMERIC.fullmatch(_text(ids.get("mercari"))):
            _set_direct(listing, "mercari", "")
            remember("mercari", before != "")
    if "ebay" in wanted and not is_locked(listing, _FIELD["ebay"]):
        before_id = _text(_direct(listing).get("ebay"))
        before_path = _text(_direct(listing).get("ebay_path"))
        choice_id, choice_path = _ebay_choice(
            _leaf_rows(catalogs.get("ebay")), listing, requested,
            explicit=_text(ids.get("ebay")) if "ebay" in named else "",
        )
        if choice_id or "ebay" in named:
            _set_direct(listing, "ebay", choice_id)
            if choice_path or "ebay" in named:
                _set_direct(listing, "ebay_path", choice_path)
            remember("ebay", choice_id != before_id or _text(_direct(listing).get("ebay_path")) != before_path)
    if "sellwild" in wanted and not is_locked(listing, _FIELD["sellwild"]):
        rows = _sellwild_rows(catalogs)
        specifics = listing.get("sellwild_specifics")
        before = _text(_mapping(specifics).get("nativeCategoryId"))
        choice = _sellwild_choice(
            rows, listing, requested, catalogs,
            explicit=_text(ids.get("sellwild")) if "sellwild" in named else "",
        )
        if choice or "sellwild" in named:
            block = specifics if isinstance(specifics, dict) else {}
            if choice != before or not isinstance(specifics, dict):
                block = dict(block)
                if choice:
                    block["nativeCategoryId"] = choice
                else:
                    block.pop("nativeCategoryId", None)
                if block:
                    listing["sellwild_specifics"] = block
                else:
                    listing.pop("sellwild_specifics", None)
            remember("sellwild", choice != before)
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


def _saved_message(markets: list[str]) -> str:
    names = [_LABELS[market] for market in MARKETS if market in markets]
    if not names:
        return ""
    if len(names) == 1:
        listed = names[0]
    elif len(names) == 2:
        listed = f"{names[0]} and {names[1]}"
    else:
        listed = ", ".join(names[:-1]) + f", and {names[-1]}"
    return f"Saved the category for {listed}."


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
    return field.rsplit(".", 1)[-1]


async def _fill_ebay_suggestion(listing: dict, requested: dict, *, markets: set[str], explicit: set[str], before_id: str) -> bool:
    if "ebay" not in markets or "ebay" in explicit or direct_category_id(listing, "ebay") != before_id:
        return False
    query = next(iter(_search_paths(listing, requested, "ebay")), "")
    if not query:
        return False
    try:
        from vendoo_studio.services.direct_categories import _ebay_leaf

        hit = await _ebay_leaf(query[:80])
    except Exception as exc:  # noqa: BLE001 - a synced leaf can still be chosen later
        log.info("No eBay category suggestion: %s", exc)
        return False
    if not isinstance(hit, dict) or not _text(hit.get("id")) or is_locked(listing, _FIELD["ebay"]):
        return False
    before_path = _text(_direct(listing).get("ebay_path"))
    _set_direct(listing, "ebay", _text(hit.get("id")))
    _set_direct(listing, "ebay_path", _text(hit.get("path")))
    return _text(hit.get("id")) != before_id or _text(_direct(listing).get("ebay_path")) != before_path


async def apply_native_categories(db, listing: dict, previous: dict | None, requested: dict | None) -> list[str]:
    """Write each marketplace's last-step id onto `listing`. Nothing is saved here."""
    previous = previous or {}
    requested = requested or {}
    markets, explicit = markets_for_choice(previous, listing, requested)
    if not markets:
        return []
    catalogs: dict[str, Any] = {}
    try:
        from vendoo_studio.services.chrome_catalog import ensure_catalogs

        loaded = await ensure_catalogs()
        if isinstance(loaded, dict):
            catalogs.update(loaded)
    except Exception as exc:  # noqa: BLE001 - the category already on the listing still stands
        log.info("No public category list for this category: %s", exc)
    catalogs["ebay"] = ebay_tree_leaves(db)
    before_ebay = direct_category_id(listing, "ebay")
    written = apply_agent_categories(listing, catalogs, requested=requested, markets=markets, explicit=explicit)
    if await _fill_ebay_suggestion(listing, requested, markets=markets, explicit=explicit, before_id=before_ebay):
        if "direct_category_ids.ebay" not in written:
            written.append("direct_category_ids.ebay")
    return written


def map_general_category(db, previous: dict | None, listing: dict) -> None:
    """A general category saved on the listing fills each marketplace form's own category."""
    if not isinstance(listing, dict):
        return
    previous = previous if isinstance(previous, dict) else {}
    path = _text(listing.get("category_path"))
    if not path or path == _text(previous.get("category_path")):
        return
    requested = {"category_path": path, "paths": {}, "ids": {}}
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(apply_native_categories(db, listing, previous, requested))
        except Exception:
            log.exception("Could not map the general category onto the marketplace forms")
        return
    log.info("Could not map the general category while another request is running")


async def apply_saved_agent_category(
    db,
    conv_id: str,
    *,
    operations: list[dict] | None = None,
    source_text: str = "",
) -> None:
    """After a chat save that chooses a category, store each marketplace's last-step id."""
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
    chosen = [market for market in (_changed_market(field) for field in written) if _stored_category(listing, market)]
    message = _saved_message(chosen) or "Choose the last category in that branch so the form can show its fields."
    ConversationRepo(db).add_message(conv_id, "system", message, provider="system", model="")


def _stored_category(listing: dict, market: str) -> str:
    if market == "sellwild":
        return _text(_mapping(listing.get("sellwild_specifics")).get("nativeCategoryId"))
    return _text(_direct(listing).get(market))


def _rejected_explicit(listing: dict, requested: dict) -> bool:
    named = _mapping(requested.get("ids"))
    return any(_text(value) and _stored_category(listing, market) != _text(value) for market, value in named.items())
