"""MCP reads an agent needs before it fills a marketplace's fields.

Categories, the fields a category asks for and the values each accepts, the
form a listing shows for one marketplace, and the seller's region and connections.
Catalog reads use data Anteroom already keeps. An explicit form refresh reads
the marketplace after the MCP database session closes and returns the form
without saving a listing revision. A form reload does what Reload choices does
in the editor: it reads the form the same way and saves it to the listing.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import copy
import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from anteroom.models.catalog import CategoryFieldSchema
from anteroom.services.hosted_listing_contract import MARKETS
from anteroom.services.text_retrieval import rank_texts

_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

NAMES = {"ebay": "eBay", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "etsy": "Etsy"}
_CATEGORY_MARKETS: tuple[str, ...] = ("general", *sorted(MARKETS))
# Longer option lists are left out of a field summary: the agent asks for the ones it needs.
MAX_INLINE_OPTIONS = 50
# eBay and Etsy sign-ins live in the Keychain on the Mac. Its own deadline is as long as the
# whole MCP call, so the settings tool stops waiting sooner and says the state is unknown.
CONNECTIONS_READ_TIMEOUT_S = 5.0
_FORM_SOURCES = {
    "catalog": "Built from the marketplace's form as Anteroom's bundled catalog records it",
    "saved": "The form Anteroom saved the last time it read this marketplace's fields for this listing",
}
_FORM_PLACEHOLDER = "_anteroom_form_refresh"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_categories",
        "title": "Search marketplace categories",
        "description": (
            "Find a marketplace's own categories by words (\"women's denim jacket\"). Returns each "
            "category's path and id. Write a marketplace's id to direct_category_ids.<marketplace> "
            "(create_draft_listing / edit_listing); for marketplace 'general' write the path to "
            "category_path. Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": list(_CATEGORY_MARKETS)},
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["marketplace", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_category_attributes",
        "title": "Get category fields",
        "description": (
            "List the fields a marketplace category asks for: path (where the value goes in "
            "edit_listing fields; dots are nested keys), label, required, type, multi, and the allowed "
            "values. Where an option has a value, save the value, not the label. Long lists are left "
            "out with a hint to use search_attribute_options. eBay and Etsy return the category's item "
            "specifics as Anteroom last read them; Poshmark, Mercari and Depop return every field their "
            "form shows for that category. Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
                "category_id": {"type": "string", "description": "An id from search_categories for this marketplace."},
            },
            "required": ["marketplace", "category_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_attribute_options",
        "title": "Search allowed field values",
        "description": (
            "Search the values one field of a marketplace category accepts (brands, sizes, "
            "materials…). field is a path or label from get_category_attributes. Without a query, "
            "returns the first values in the marketplace's order. Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
                "category_id": {"type": "string"},
                "field": {"type": "string", "maxLength": 300},
                "query": {"type": "string", "maxLength": 200},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
            },
            "required": ["marketplace", "category_id", "field"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_marketplace_form",
        "title": "Get a listing's marketplace form",
        "description": (
            "Read the fields and current values one marketplace's form shows for a listing in the "
            "Anteroom editor, with which are required and what each accepts. Says where the form came "
            "from and when its fields were read. By default uses saved data. Set refresh=true and supply "
            "expected_revision_id to read the current choices on that marketplace, including Mercari "
            "prepaid quotes for the saved package. Uses the seller's marketplace connection when needed. "
            "Never saves a listing revision or changes a marketplace listing."
        ),
        "annotations": {**_READ_ANNOTATIONS, "openWorldHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
                "refresh": {"type": "boolean", "default": False},
                "expected_revision_id": {"type": "string", "description": "Current revision ID from get_listing, required for refresh."},
                "field": {"type": "string", "description": "With refresh, return choices for this field path or label."},
                "query": {"type": "string", "description": "Filter a refreshed field's choices by label."},
            },
            "required": ["conversation_id", "marketplace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reload_marketplace_form",
        "title": "Reload a listing's marketplace form",
        "description": (
            "Read one marketplace's current form for a listing and save it to the listing, exactly like "
            "Reload choices in the Anteroom editor. Use it when a listing's validation says \"Reload its "
            "fields before listing\" after its category, weight or another choice changed. Pass "
            "expected_revision_id from get_listing; refused if the listing changed since. Returns the new "
            "revision_id and what that marketplace's form still needs. Saves only Anteroom's listing: never "
            "publishes, saves a marketplace draft or changes a live listing."
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
                "expected_revision_id": {"type": "string", "description": "current_revision_id from get_listing."},
            },
            "required": ["conversation_id", "marketplace", "expected_revision_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_auto_relist_status",
        "title": "Preview Auto-Relist readiness",
        "description": (
            "Read Auto-Relist and eBay Auto-Recreate rules and preview due candidates from Anteroom's "
            "records. Shows the remaining cap, proposed prices, pending attempts, retry backoff, and known "
            "listing requirements. Does not run relists, reconcile attempts, or verify live connections."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": ["poshmark", "ebay", "mercari", "depop"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25,
                          "description": "Maximum candidate details to include per marketplace."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_account_settings",
        "title": "Get account settings",
        "description": (
            "Read the seller's region, currency and eBay site, which marketplaces Anteroom offers in "
            "that region, and which are connected. Never returns tokens, cookies or account names. "
            "Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

# Reloading a form saves it to the listing, so it needs the same access as edit_listing.
_WRITE_TOOLS = {"reload_marketplace_form"}
PERMISSIONS: dict[str, tuple[str, str]] = {
    tool["name"]: ("listings", "write" if tool["name"] in _WRITE_TOOLS else "read") for tool in TOOLS
}

# Appended to the create and edit tool descriptions in mcp_http.
WRITE_GUIDANCE = (
    " Before filling categories or <marketplace>_specifics, look them up: search_categories, "
    "get_category_attributes and search_attribute_options give each marketplace's categories, "
    "fields and allowed values."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _text(args: dict[str, Any], key: str, *, required: bool = True, max_len: int = 300) -> str:
    value = str(args.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"{key} is required.")
    if len(value) > max_len:
        raise ValueError(f"{key} must be {max_len} characters or fewer.")
    return value


def _market(args: dict[str, Any], allowed: Collection[str] = MARKETS) -> str:
    market = _text(args, "marketplace").lower()
    if market not in allowed:
        raise ValueError(f"marketplace must be one of: {', '.join(sorted(allowed))}.")
    return market


def _limit(args: dict[str, Any], *, default: int, maximum: int) -> int:
    raw = args.get("limit")
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()


def _ranked(query: str, labels: list[str]) -> list[int]:
    """Indexes of ``labels`` matching ``query``: whole phrase first, then by shared words."""
    needle = query.casefold()
    exact = [i for i, label in enumerate(labels) if label.casefold() == needle]
    starts = [i for i, label in enumerate(labels) if i not in exact and label.casefold().startswith(needle)]
    contains = [i for i, label in enumerate(labels)
                if i not in exact and i not in starts and needle in label.casefold()]
    seen = {*exact, *starts, *contains}
    words = [i for i, _score in rank_texts(query, labels) if i not in seen]
    return [*exact, *starts, *contains, *words]


# ---------------------------------------------------------------------------
# Field rows
# ---------------------------------------------------------------------------


def _row_options(row: dict) -> list:
    """A row's allowed values: labels, or {label, value} where the form saves a different value."""
    if row.get("input_type") == "inventory":
        options = []
        for group in row.get("inventory_groups") or []:
            for size in group.get("sizes") or []:
                options.append({"label": f"{group.get('label')}: {size.get('label')}",
                                "value": {"sizeSetId": group.get("id"), "sizeId": size.get("id")}})
        return options
    labels = [str(label) for label in row.get("options") or []]
    values = row.get("option_values") if isinstance(row.get("option_values"), dict) else {}
    if values and any(str(values.get(label, label)) != label for label in labels):
        return [{"label": label, "value": values.get(label, label)} for label in labels]
    return labels


def _field_type(row: dict) -> str:
    kind = str(row.get("input_type") or "")
    if kind:
        return kind
    return "select" if row.get("selection_only") or row.get("options") else "text"


def _summary(row: dict, *, with_value: bool) -> dict:
    path = str(row.get("path") or row.get("key") or "")
    out: dict[str, Any] = {
        "path": path,
        "label": row.get("label") or path.rsplit(".", 1)[-1],
        "section": row.get("section"),
        "type": _field_type(row),
        "required": bool(row.get("required")),
        "multi": bool(row.get("multi")),
        "selection_only": bool(row.get("selection_only")),
    }
    for key in ("max_values", "max_length", "help", "locked"):
        if row.get(key) not in (None, "", False):
            out[key] = row[key]
    if row.get("category_specific"):
        out["category_specific"] = True
    if with_value:
        out["value"] = copy.deepcopy(row.get("value"))
    options = _row_options(row)
    if options:
        out["option_count"] = len(options)
        if len(options) <= MAX_INLINE_OPTIONS:
            out["options"] = options
        else:
            out["options_truncated"] = True
            out["options_hint"] = (
                "search_categories lists these categories." if path.startswith("direct_category_ids.")
                else f"Call search_attribute_options with field '{path}' and a query to find a value."
            )
    return out


def _photo_or_note(row: dict) -> bool:
    return row.get("input_type") in {"photos", "note"}


# ---------------------------------------------------------------------------
# Category options and field definitions
# ---------------------------------------------------------------------------


def _category_rows(db: Session, market: str) -> list[dict]:
    """Every category the marketplace's own picker offers, as {"id", "path"}."""
    if market == "etsy":
        from anteroom.services import etsy_form

        choices = etsy_form.category_choices()
        return [{"id": str(choices["option_values"][path]), "path": path} for path in choices["options"]]
    from anteroom.services.native_category_match import native_rows

    return native_rows()[market]


def _search_tree(db: Session, market: str, query: str, limit: int) -> list[dict]:
    """The synced category tree's verified leaves (general and eBay US)."""
    from anteroom.services.catalog_index import search_catalog

    # Never build the index inside an MCP call: an unbuilt index falls back to
    # whole-word ranking over the stored tree.
    hits = search_catalog(db, query, marketplace=market, kind="category", top_k=limit, ensure=False)
    return [{"id": hit["id"], "path": hit["path"]} for hit in hits if hit.get("marketplace") == market]


def _ebay_site(db: Session) -> str:
    from anteroom.services.seller_region import seller_region

    return seller_region(db=db).ebay_marketplace


def _saved_schema(db: Session, key: str, category_id: str) -> tuple[list[dict] | None, datetime | None]:
    row = db.query(CategoryFieldSchema).filter_by(marketplace=key, category_id=category_id).one_or_none()
    if row is None or not isinstance(row.fields, list):
        return None, None
    return list(row.fields), row.fetched_at


def _schema_rows(fields: list[dict], prefix: str, *, ebay: bool) -> list[dict]:
    """eBay aspects or Etsy properties as form rows, under the paths the forms save them to."""
    rows = []
    for item in fields:
        name = str(item.get("name") or "")
        if not name or item.get("id") == "condition" or "." in name:
            continue
        values = item.get("values") or []
        labels = list(dict.fromkeys(str(value.get("label") or "") for value in values if value.get("label")))
        rows.append({
            "path": f"{prefix}.{name}", "label": name, "required": bool(item.get("required")),
            "multi": bool(item.get("multi")), "max_values": item.get("max_values"), "max_length": item.get("max_length"),
            # eBay's page offers only its listed sizes, though its API takes others.
            "selection_only": item.get("mode") == "selection" or (ebay and name == "Size" and bool(labels)),
            # Both save the label: Etsy matches a saved answer to its choices by label, not ID.
            "options": labels, "category_specific": True,
            "section": ("Item specifics: Required" if item.get("required") else "Item specifics: Additional (optional)")
            if ebay else "About this item",
        })
    return rows


def _not_read(market: str) -> dict:
    return {
        "available": False,
        "message": (
            f"Anteroom has not read {NAMES[market]}'s fields for this category yet. They are read when a "
            f"listing in this category opens its {NAMES[market]} tab in Anteroom (or the seller chooses "
            "Reload choices there). Ask again after that."
        ),
    }


def _category_fields(db: Session, market: str, category_id: str) -> dict:
    """The fields a category's form asks for, without contacting the marketplace."""
    if market == "ebay":
        from anteroom.services import marketplace_schemas

        site = _ebay_site(db)
        fields, read_at = _saved_schema(
            db, marketplace_schemas.ebay_schema_cache_key(marketplace_schemas.EBAY_ASPECTS, site), category_id)
        if fields is None:
            return _not_read(market)
        conditions, _ = _saved_schema(
            db, marketplace_schemas.ebay_schema_cache_key(marketplace_schemas.EBAY_CONDITIONS, site), category_id)
        result: dict[str, Any] = {"available": True, "rows": _schema_rows(fields, "ebay_specifics", ebay=True),
                                  "read_at": _iso(read_at), "source": f"eBay ({site}) item specifics"}
        policy = (conditions or [{}])[0] if conditions else {}
        if policy.get("conditions"):
            result["condition"] = {
                "required": bool(policy.get("required")),
                "options": [{"label": item.get("label"), "value": str(item.get("id"))} for item in policy["conditions"]],
                "help": "Set the listing's general condition; Anteroom sends the matching eBay condition.",
            }
        return result
    if market == "etsy":
        from anteroom.services import etsy_form, marketplace_schemas

        if category_id not in {row["id"] for row in _category_rows(db, "etsy")}:
            raise ValueError("Unknown Etsy category. Use an id from search_categories.")
        fields, read_at = _saved_schema(db, marketplace_schemas.ETSY_PROPERTIES, category_id)
        if fields is None:
            return _not_read(market)
        return {"available": True, "rows": _schema_rows(fields, etsy_form.PROPERTIES_PATH, ebay=False),
                "read_at": _iso(read_at), "source": "Etsy category properties"}
    if category_id not in {str(row["id"]) for row in _category_rows(db, market)}:
        raise ValueError(f"Unknown {NAMES[market]} category. Use an id from search_categories.")
    form = _catalog_form({"direct_category_ids": {market: category_id}}, market)
    return {"available": True, "rows": [row for row in form.get("fields") or [] if not _photo_or_note(row)],
            "source": _catalog_source(market)}


def _catalog_form(snapshot: dict, market: str) -> dict:
    """Poshmark's, Depop's and Mercari's forms, built from the bundled catalogs (no network)."""
    if market == "poshmark":
        from anteroom.services.poshmark_form import form

        return form(snapshot)
    if market == "depop":
        from anteroom.services.depop_form import form as depop

        return depop(snapshot)
    from anteroom.services.mercari_form import form as mercari

    # Shipping labels are quoted by Mercari when the editor opens; every product field is here.
    return mercari(snapshot, [], notes=["Mercari's shipping labels are offered when the listing's Mercari tab opens in Anteroom."])


def _catalog_source(market: str) -> str:
    from anteroom.services import depop_form, mercari_form, poshmark_form

    catalog = {"poshmark": poshmark_form, "depop": depop_form, "mercari": mercari_form}[market].catalog()
    source = catalog.get("source")
    return str(source) if isinstance(source, str) else f"{NAMES[market]}'s form as Anteroom's catalog records it"


def _find_row(rows: list[dict], field: str) -> dict | None:
    wanted = field.strip().casefold()
    for match in (
        lambda row: str(row.get("path") or "").casefold() == wanted,
        lambda row: str(row.get("path") or "").rsplit(".", 1)[-1].casefold() == wanted,
        lambda row: str(row.get("label") or "").casefold() == wanted,
    ):
        found = next((row for row in rows if match(row)), None)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _tool_search_categories(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    market = _market(args, _CATEGORY_MARKETS)
    query = _text(args, "query", max_len=200)
    limit = _limit(args, default=10, maximum=50)
    notes: list[str] = []
    if market in {"general", "ebay"}:
        site = _ebay_site(db)
        if market == "ebay" and site != "EBAY_US":
            return {"marketplace": market, "query": query, "categories": [], "write_to": "direct_category_ids.ebay",
                    "note": (f"Anteroom's eBay category list is eBay US's, and this seller sells on {site}. "
                             "Choose the eBay category in the listing's eBay tab in Anteroom.")}
        hits = _search_tree(db, market, query, limit)
        if not hits:
            notes.append("No matching category in the synced tree. Try fewer or broader words.")
    else:
        rows = _category_rows(db, market)
        hits = [rows[i] for i in _ranked(query, [row["path"] for row in rows])[:limit]]
    write_to = "category_path (write the path)" if market == "general" else f"direct_category_ids.{market}"
    out: dict[str, Any] = {"marketplace": market, "query": query, "write_to": write_to,
                           "categories": [{"id": str(hit["id"]), "path": hit["path"]} for hit in hits]}
    if market != "general":
        from anteroom.services.seller_region import seller_region

        if market not in seller_region(db=db).marketplaces:
            notes.append(f"{NAMES[market]} is not offered in this seller's region.")
    if notes:
        out["note"] = " ".join(notes)
    return out


def _tool_get_category_attributes(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    market = _market(args)
    category_id = _text(args, "category_id", max_len=200)
    found = _category_fields(db, market, category_id)
    out: dict[str, Any] = {"marketplace": market, "category_id": category_id, "available": found["available"]}
    if not found["available"]:
        out["message"] = found["message"]
        return out
    out["source"] = found.get("source")
    if found.get("read_at"):
        out["read_at"] = found["read_at"]
    if found.get("condition"):
        out["condition"] = found["condition"]
    out["fields"] = [_summary(row, with_value=False) for row in found["rows"]]
    out["required"] = [row["path"] for row in out["fields"] if row["required"]]
    return out


def _tool_search_attribute_options(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    market = _market(args)
    category_id = _text(args, "category_id", max_len=200)
    field = _text(args, "field", max_len=300)
    query = _text(args, "query", required=False, max_len=200)
    limit = _limit(args, default=25, maximum=200)
    found = _category_fields(db, market, category_id)
    if not found["available"]:
        raise ValueError(found["message"])
    row = _find_row(found["rows"], field)
    if row is None:
        raise ValueError(f"{NAMES[market]} category {category_id} has no field '{field}'. "
                         "get_category_attributes lists its fields.")
    options = _row_options(row)
    labels = [str(option["label"]) if isinstance(option, dict) else option for option in options]
    order = _ranked(query, labels) if query else list(range(len(options)))
    summary = _summary(row, with_value=False)
    return {
        "marketplace": market,
        "category_id": category_id,
        "path": summary["path"],
        "label": summary["label"],
        "selection_only": summary["selection_only"],
        "multi": summary["multi"],
        "query": query or None,
        "total_matches": len(order),
        "options": [options[i] for i in order[:limit]],
        **({} if summary["selection_only"] or summary["type"] not in {"text", "select"}
           else {"note": "This field also takes free text."}),
    }


def _tool_get_marketplace_form(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services import native_contract
    from anteroom.services.native_editor import form_snapshot
    from anteroom.services.native_marketplaces import NativeMarketplaceError

    conv_id = _text(args, "conversation_id", max_len=500)
    market = _market(args)
    refresh = args.get("refresh", False)
    if not isinstance(refresh, bool):
        raise ValueError("refresh must be true or false.")
    revision_id = args.get("expected_revision_id")
    if revision_id is not None and (not isinstance(revision_id, str) or not 1 <= len(revision_id) <= 100):
        raise ValueError("Use the current revision ID from get_listing as expected_revision_id.")
    if refresh and not revision_id:
        raise ValueError("To refresh choices, pass expected_revision_id from get_listing.")
    field = _text(args, "field", required=False)
    query = _text(args, "query", required=False, max_len=200)
    if (field and not refresh) or (query and not field):
        raise ValueError("Use field with refresh=true and optionally pass a query for its choices.")
    try:
        # Reads the listing the way the editor does; rolls back before returning.
        inputs = form_snapshot(db, conv_id, market, revision_id, preload=not refresh)
    except NativeMarketplaceError as exc:
        raise ValueError("Listing not found." if exc.status_code == 404 else str(exc)) from None
    out: dict[str, Any] = {"conversation_id": conv_id, "marketplace": market}
    if refresh:
        from anteroom.services import cloud_sessions
        from anteroom.services.seller_region import seller_region

        region = seller_region(db=db)
        if market not in region.marketplaces:
            return {**out, "available": False, "source": "unavailable", "reason": "unsupported_region",
                    "message": f"{NAMES[market]} is unavailable in the seller's Anteroom region ({region.country})."}
        use_cloud = market == "poshmark" and cloud_sessions.has_active_session(market, db)
        return {"_after": [{_FORM_PLACEHOLDER: {
            **out, "revision_id": revision_id, "snapshot": {**inputs.snapshot, "_seller_region": region.country}, "use_cloud": use_cloud,
            "field": field, "query": query,
        }}]}
    if market in {"ebay", "etsy"}:
        # These forms are read from eBay's and Etsy's APIs. Use the copy Anteroom saved for this
        # listing; reading them again here would hold this call's database session open.
        form = native_contract.editor_form(inputs.listing_json, market, values=inputs.snapshot)
        if form is None:
            return {**out, "available": False, "message": (
                f"Anteroom has not read {NAMES[market]}'s form for this listing's current category yet. Open the "
                f"listing's {NAMES[market]} tab in Anteroom (or choose Reload choices there) and ask again. "
                "get_category_attributes lists the category's fields meanwhile.")}
        out.update(available=True, source="saved", source_detail=_FORM_SOURCES["saved"])
        category_id = str(form.get("category_id") or "")
        if category_id:
            from anteroom.services import marketplace_schemas

            key = (marketplace_schemas.ebay_schema_cache_key(marketplace_schemas.EBAY_ASPECTS, _ebay_site(db))
                   if market == "ebay" else marketplace_schemas.ETSY_PROPERTIES)
            _fields, read_at = _saved_schema(db, key, category_id)
            if read_at is not None:
                out["category_fields_read_at"] = _iso(read_at)
    else:
        form = _catalog_form(inputs.snapshot, market)
        out.update(available=True, source="catalog", source_detail=_FORM_SOURCES["catalog"],
                   catalog_read=_catalog_source(market))
    return _form_result(out, form)


def _tool_reload_marketplace_form(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.repositories.queries import ListingRepo
    from anteroom.services.seller_region import seller_region

    conv_id = _text(args, "conversation_id", max_len=500)
    market = _market(args)
    revision_id = args.get("expected_revision_id")
    if not isinstance(revision_id, str) or not 1 <= len(revision_id) <= 100:
        raise ValueError("Use the current revision ID from get_listing as expected_revision_id.")
    current = ListingRepo(db).get_current(conv_id)
    if current is None:
        raise ValueError("Listing not found.")
    if current.current_revision_id != revision_id:
        raise ValueError("The listing changed. Read its current revision with get_listing and try again.")
    region = seller_region(db=db)
    if market not in region.marketplaces:
        raise ValueError(f"{NAMES[market]} is unavailable in the seller's Anteroom region ({region.country}).")
    # The form is read and saved after this call's session closes, like Reload choices in the editor.
    return {"_after": [{_FORM_PLACEHOLDER: {
        "conversation_id": conv_id, "marketplace": market, "revision_id": revision_id, "save": True,
    }}]}


def _remaining_form_gaps(db: Session, conv_id: str, market: str) -> list[dict[str, Any]]:
    from anteroom.repositories.queries import ListingRepo
    from anteroom.services import native_contract

    repo = ListingRepo(db)
    current = repo.get_current(conv_id)
    revision = repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    listing = revision.listing_json if revision else {}
    return [{"field": gap["field"], "message": gap["message"]}
            for gap in native_contract.gaps(listing, market) if gap.get("blocking", True)]


def _save_form_in_time(db: Session, expired: Callable[[], bool], conv_id: str, market: str,
                       revision_id: str, form: dict) -> dict:
    from anteroom.services import native_editor

    def refuse_late_commit(*_args: Any) -> None:
        # The caller was already told this call timed out and nothing more was saved.
        if expired():
            raise TimeoutError

    event.listen(db, "before_commit", refuse_late_commit)
    # A commit flushes after before_commit, and a flush can wait on a locked database.
    event.listen(db, "after_flush", refuse_late_commit)
    return native_editor.save_refreshed_form(db, conv_id, market, revision_id, form)


async def _save_reloaded_form(spec: dict, form: dict, expired: Callable[[], bool]) -> dict:
    """Save a form read by Reload: the editor's own fenced save, then what that marketplace still needs."""
    from anteroom.database import in_worker_session

    market, conv_id = spec["marketplace"], spec["conversation_id"]
    saved = await in_worker_session(_save_form_in_time, expired, conv_id, market, spec["revision_id"], form)
    remaining = await in_worker_session(_remaining_form_gaps, conv_id, market)
    return {"conversation_id": conv_id, "marketplace": market, "saved": True, "revision_id": saved["revision_id"],
            "remaining": remaining}


def _form_result(out: dict[str, Any], form: dict) -> dict[str, Any]:
    out["category_id"] = form.get("category_id")
    out["fields"] = [_summary(row, with_value=True) for row in form.get("fields") or [] if not _photo_or_note(row)]
    out["missing_required"] = [row["path"] for row in out["fields"]
                               if row["required"] and row.get("value") in (None, "", [], {})]
    notes = [str(note) for note in form.get("notes") or [] if note]
    if notes:
        out["notes"] = notes
    return out


def pending_form_reads(body: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    reads = []
    for response in body if isinstance(body, list) else [body]:
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            for item in result.get("content", []):
                if isinstance(item, dict) and _FORM_PLACEHOLDER in item:
                    reads.append((result, item[_FORM_PLACEHOLDER]))
    return reads


class _FormUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _browser_form(market: str, snapshot: dict, use_cloud: bool, timeout: float) -> tuple[dict, str]:
    from anteroom.services import browser_bridge, cloud_marketplace, hosted_browser_relay, service_context

    keys = ("title", "description", "brand", "condition", "category_path", "category", "department", "form",
            "price", "cost", "quantity", "sku", "size", "colors", "color", "primaryColor", "secondaryColor",
            "direct_category_ids", "seller_fields", "_seller_region", f"{market}_specifics")
    listing = {key: snapshot[key] for key in keys if key in snapshot}
    if isinstance(listing.get("form"), dict):
        listing["form"] = {key: listing["form"][key] for key in ("overrides", "specifics", "aspects") if key in listing["form"]}
    payload = {"marketplace": market, "listing": listing, "refresh": True}
    if use_cloud:
        if timeout < 5:
            raise TimeoutError
        transport = "poshmark_cloud"
        reply = await cloud_marketplace.execute(market, {"type": "marketplace.form", "payload": payload},
                                                timeout=timeout)
    else:
        transport = "chrome_bridge"
        try:
            ctx = service_context.current()
            if ctx is not None:
                relay = hosted_browser_relay.relay_for(ctx.seller_id)
                reply = await relay.read("marketplace.form", payload, timeout)
            else:
                reply = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.form", payload, timeout=timeout)
        except (browser_bridge.BrowserBridgeError, hosted_browser_relay.ChromeReadError) as exc:
            if str(exc).startswith("Update Anteroom Bridge"):
                raise _FormUnavailable("refresh_unsupported", str(exc)) from None
            if "connect chrome first" in str(exc).lower():
                raise _FormUnavailable("connection_required", f"Connect Chrome with Anteroom Bridge and sign in to {NAMES[market]}.") from None
            if "in time" in str(exc) or "cancelled" in str(exc):
                raise TimeoutError from None
            raise _FormUnavailable("connection_unavailable", "Anteroom Bridge did not answer. Check the Chrome connection.") from None
    form = reply.get("form")
    if not reply.get("ok") or not isinstance(form, dict) or form.get("marketplace") != market:
        code = reply.get("error_code")
        if code == "signed_out":
            where = "in Anteroom Settings → Connections" if use_cloud else "in the connected Chrome browser"
            raise _FormUnavailable("connection_required", f"Sign in to {NAMES[market]} {where}.")
        if code == "timeout":
            raise TimeoutError
        if code in ("challenge", "rate_limited", "schema_drift"):
            raise _FormUnavailable(code, f"{NAMES[market]} could not return its current choices ({code}).")
        raise _FormUnavailable("unavailable", f"Could not read {NAMES[market]}'s choices. Check its connection and try again.")
    if reply.get("refreshed") is not True:
        raise _FormUnavailable("refresh_unsupported", f"Update the {NAMES[market]} connection or Anteroom Bridge to refresh choices.")
    return form, transport


async def _refresh_marketplace_form(spec: dict, timeout: float) -> dict:
    from anteroom.services import etsy_form, marketplace_schemas, mercari_form, native_editor

    market, snapshot = spec["marketplace"], spec["snapshot"]
    out = {key: spec[key] for key in ("conversation_id", "marketplace", "revision_id")}
    out.update(available=True, source="live", choice_sources={"listing_values": "saved"})
    choices = out["choice_sources"]
    if market == "ebay":
        form = await native_editor.ebay_form(snapshot, fresh_choices=True)
        out["fetched_via"] = "ebay_api"
        choices.update(category_fields="live" if form.get("category_id") else "unavailable", account_choices="live")
    elif market == "etsy":
        with marketplace_schemas.schema_session(fresh=True):
            form = await etsy_form.editor_form(snapshot, fresh_choices=True)
        out.update(fetched_via="etsy_api", shop_profiles=form["shop_profiles"])
        choices.update(category_fields="live" if form.get("category_id") else "unavailable", account_choices="live",
                       category_picker="bundled_catalog")
    elif market == "mercari":
        status, classes = await mercari_form.prepaid_shipping_quotes(snapshot, refresh=True)
        form = mercari_form.form(snapshot, classes)
        out.update(shipping_quote_status=status, shipping_quotes=[{
            "id": str(row["id"]), "carrier": row["carrierDisplayName"], "fee_cents": row["fee"],
            "currency": "USD", "max_ounces": row["weightRange"]["max"], "eta": row.get("etaForSeller"),
        } for row in classes])
        choices["product_fields"] = "bundled_catalog"
        if status in ("quoted", "no_options"):
            choices["shipping_quotes"] = "live"
            out["fetched_via"] = "mercari_guest_api"
        else:
            choices["shipping_quotes"] = "unavailable"
            out.update(source="catalog", catalog_read=_catalog_source(market))
    else:
        form, out["fetched_via"] = await _browser_form(market, snapshot, spec["use_cloud"], timeout)
        choices.update(category_fields="live", shipping_choices="unavailable" if form.get("notes") else "live")
        if market == "depop":
            form = {**form, "fields": [row for row in form.get("fields", []) if row.get("path") != "depop_specifics.shipFrom"]}
            if snapshot.get("_seller_region") == "CA":
                choices["shipping_choices"] = "seller_entered"
    if out["source"] == "live":
        out["fetched_at"] = _iso(datetime.now(UTC))
    return await asyncio.to_thread(_refreshed_form_result, out, form, spec["field"], spec["query"])


def _refreshed_form_result(out: dict, form: dict, path: str, query: str) -> dict:
    result = _form_result(out, form)
    if path:
        row = _find_row([row for row in form.get("fields") or [] if not _photo_or_note(row)], path)
        if row is None:
            raise _FormUnavailable("field_not_found", "The marketplace did not return that field. Refresh the form to see its fields.")
        options = _row_options(row)
        labels = [str(option["label"]) if isinstance(option, dict) else option for option in options]
        order = _ranked(query, labels) if query else range(len(options))
        field = _summary(row, with_value=True)
        field.pop("options_hint", None)
        field.update(options=[options[index] for index in order[:MAX_INLINE_OPTIONS]], option_count=len(order),
                     options_truncated=len(order) > MAX_INLINE_OPTIONS)
        result["fields"] = [field]
    else:
        for row in result["fields"]:
            if row.get("options_truncated"):
                row["options_hint"] = "Refresh again with this field and a query to find a current choice."
    return result


def _refresh_error(market: str, exc: Exception) -> tuple[str, str]:
    import httpx

    from anteroom.services.ebay_client import EbayFailure
    from anteroom.services.marketplace_accounts import SignInRequired
    from anteroom.services.native_marketplaces import NativeMarketplaceError

    name = NAMES[market]
    if isinstance(exc, _FormUnavailable):
        return exc.code, str(exc)
    if isinstance(exc, NativeMarketplaceError):
        # The editor's own reload wording: the listing moved on, or the form could not be read.
        return ("listing_changed" if exc.status_code == 409 else "not_found" if exc.status_code == 404
                else "unavailable"), str(exc)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout", f"{name} did not return its choices in time. Try again."
    if isinstance(exc, SignInRequired) or (market == "etsy" and str(exc).startswith("Connect Etsy in Settings")):
        return "connection_required", f"Connect {name} in Anteroom Settings → Connections."
    status = exc.status if isinstance(exc, EbayFailure) else exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    if status == 403 and market in ("ebay", "etsy"):
        return "permission_denied", f"{name} denied access to these choices. Check the marketplace connection."
    if status == 429:
        return "rate_limited", f"{name} is limiting requests. Try again shortly."
    return "unavailable", f"Could not read {name}'s choices. Try again."


def _revision_matches(db: Session, conversation_id: str, revision_id: str) -> bool:
    from anteroom.repositories.queries import ListingRepo

    current = ListingRepo(db).get_current(conversation_id)
    return current is not None and current.current_revision_id == revision_id


async def finish_form_reads(reads: list[tuple[dict[str, Any], dict[str, Any]]], *, deadline: float,
                            expired: Callable[[], bool]) -> None:
    """Complete explicit reads off the MCP database session, within the shared request budget.

    ``expired`` is the request's own check: a reload's save is refused once the caller was told it timed out.
    """
    from anteroom.database import in_worker_session

    async def finish(result: dict, spec: dict) -> None:
        market, conv_id, revision_id = (spec[key] for key in ("marketplace", "conversation_id", "revision_id"))
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            if spec.get("save"):
                from anteroom.services import native_editor

                # Only the marketplace read is under the deadline. The save that follows is a short,
                # fenced database write: cancelling it part-way could commit after "Nothing was saved",
                # so it refuses its own commit instead once the request has timed out.
                async with asyncio.timeout(min(25.0, remaining)):
                    form = await native_editor.editor_form(conv_id, market, revision_id, refresh=True)
                data = await _save_reloaded_form(spec, form, expired)
            else:
                async with asyncio.timeout(remaining):
                    data = await _refresh_marketplace_form(spec, min(25.0, remaining))
                    if not await in_worker_session(_revision_matches, conv_id, revision_id):
                        raise _FormUnavailable("listing_changed", "The listing changed while choices loaded. Read its revision and try again.")
        except Exception as exc:  # noqa: BLE001 - never expose provider errors or the internal snapshot
            code, message = _refresh_error(market, exc)
            data = {"marketplace": market, "conversation_id": conv_id, "revision_id": revision_id,
                    "available": False, "source": "unavailable", "reason": code, "message": message}
            if spec.get("save"):
                data = {"marketplace": market, "conversation_id": conv_id, "revision_id": revision_id,
                        "saved": False, "reason": code, "message": f"{message} Nothing was saved."}
        result["content"] = [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]

    await asyncio.gather(*(finish(result, spec) for result, spec in reads))


def _tool_get_auto_relist_status(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services import auto_relist

    market = _market(args, auto_relist.RELIST_MARKETPLACES) if "marketplace" in args else None
    return auto_relist.preview_status(db, marketplace=market, limit=_limit(args, default=25, maximum=100))


def _tool_get_account_settings(db: Session, _args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services import cloud_sessions, marketplace_accounts
    from anteroom.services.seller_region import seller_region

    region = seller_region(db=db)
    unreadable: list[str] = []
    # Read on a thread of its own (with this call's seller context) so a slow Keychain cannot
    # use up the call's deadline. A read that is still running finishes on its own deadline.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-connections")
    try:
        pending = pool.submit(contextvars.copy_context().run, marketplace_accounts.status)
        accounts = pending.result(timeout=CONNECTIONS_READ_TIMEOUT_S).get("accounts") or {}
    except Exception:  # noqa: BLE001 - the rest of the settings still answer; say which part did not
        accounts, unreadable = {}, ["eBay and Etsy connections"]
    finally:
        pool.shutdown(wait=False)
    try:
        with db.begin_nested():
            cloud = cloud_sessions.public_sessions(db)
    except Exception:  # noqa: BLE001
        cloud, unreadable = {}, [*unreadable, "cloud marketplace sessions"]
    markets: dict[str, dict[str, Any]] = {}
    for market in sorted(MARKETS):
        entry: dict[str, Any] = {"name": NAMES[market], "available_in_region": market in region.marketplaces}
        api = accounts.get(market)
        if isinstance(api, dict):
            # eBay and Etsy: Anteroom's own sign-in. Only whether it works and since when.
            connected_at = api.get("connectedAt")
            entry.update(
                connection="api",
                connected=bool(api.get("connected")),
                needs_sign_in=bool(api.get("error")),
                connected_at=(datetime.fromtimestamp(connected_at, UTC).isoformat()
                              if isinstance(connected_at, (int, float)) else None),
            )
        elif market in cloud:
            session = cloud[market]
            entry.update(connection="cloud", connected=bool(session.get("cloud_connected")),
                         needs_sign_in=session.get("state") in {"expired", "signed_out"})
        else:
            entry.update(connection="chrome", connected=None, note=(
                "Signed in through the seller's Chrome; Settings → Connections in Anteroom checks it."))
        markets[market] = entry
    out: dict[str, Any] = {
        "region": region.country,
        "currency": region.currency,
        "ebay_site": region.ebay_marketplace,
        "marketplaces": markets,
    }
    if unreadable:
        out["note"] = f"Could not read {' or '.join(unreadable)} just now; Settings → Connections in Anteroom shows them."
    return out


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_categories": _tool_search_categories,
    "get_category_attributes": _tool_get_category_attributes,
    "search_attribute_options": _tool_search_attribute_options,
    "get_marketplace_form": _tool_get_marketplace_form,
    "reload_marketplace_form": _tool_reload_marketplace_form,
    "get_auto_relist_status": _tool_get_auto_relist_status,
    "get_account_settings": _tool_get_account_settings,
}
