"""MCP reads an agent needs before it fills a marketplace's fields.

Categories, the fields a category asks for and the values each accepts, the
form a listing shows for one marketplace, the seller's region and connections,
and the list-this rules. Each comes from what Anteroom already keeps: the
bundled marketplace catalogs, the synced category trees, and the eBay and Etsy
category fields Anteroom saved when it last read them. None of them waits on
Chrome or a marketplace (they run inside the MCP call's database session), and
none of them changes anything.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import copy
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import Any

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
MAX_GUIDELINE_CHARS = 20000
# eBay and Etsy sign-ins live in the Keychain on the Mac. Its own deadline is as long as the
# whole MCP call, so the settings tool stops waiting sooner and says the state is unknown.
CONNECTIONS_READ_TIMEOUT_S = 5.0
_DEFAULT_GUIDELINE_CHARS = 12000
_FORM_SOURCES = {
    "catalog": "Built from the marketplace's form as Anteroom's bundled catalog records it",
    "saved": "The form Anteroom saved the last time it read this marketplace's fields for this listing",
}

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
            "from and when its fields were read. Never opens Chrome or contacts the marketplace: when "
            "Anteroom has not read eBay's or Etsy's fields for this listing yet, it says so. Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
            },
            "required": ["conversation_id", "marketplace"],
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
    {
        "name": "get_listing_guidelines",
        "title": "Get listing guidelines",
        "description": (
            "Read the rules Anteroom follows when it writes a listing: the title, description and "
            "pricing formulas (including the seller's own), evidence rules, and the sections most "
            "relevant to a marketplace, category or field. Read before writing or editing a "
            "listing's text. Read-only."
        ),
        "annotations": _READ_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
                "category": {"type": "string", "maxLength": 300, "description": "Optional category path or words."},
                "field": {"type": "string", "maxLength": 200, "description": "Optional field, such as title or size."},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": MAX_GUIDELINE_CHARS,
                              "default": _DEFAULT_GUIDELINE_CHARS},
            },
            "additionalProperties": False,
        },
    },
]

PERMISSIONS: dict[str, tuple[str, str]] = {tool["name"]: ("listings", "read") for tool in TOOLS}

# Appended to the create and edit tool descriptions in mcp_http.
WRITE_GUIDANCE = (
    " Before filling categories or <marketplace>_specifics, look them up: search_categories, "
    "get_category_attributes and search_attribute_options give each marketplace's categories, "
    "fields and allowed values, and get_listing_guidelines how to write the title and description."
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
        option_values = {} if ebay else {str(value["label"]): value.get("id") for value in values if value.get("label")}
        rows.append({
            "path": f"{prefix}.{name}", "label": name, "required": bool(item.get("required")),
            "multi": bool(item.get("multi")), "max_values": item.get("max_values"), "max_length": item.get("max_length"),
            # eBay's page offers only its listed sizes, though its API takes others.
            "selection_only": item.get("mode") == "selection" or (ebay and name == "Size" and bool(labels)),
            "options": labels, "option_values": option_values, "category_specific": True,
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
        **({} if summary["selection_only"] or not summary["type"] in {"text", "select"}
           else {"note": "This field also takes free text."}),
    }


def _tool_get_marketplace_form(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services import native_contract
    from anteroom.services.native_editor import form_snapshot
    from anteroom.services.native_marketplaces import NativeMarketplaceError

    conv_id = _text(args, "conversation_id", max_len=500)
    market = _market(args)
    try:
        # Reads the listing the way the editor does; rolls back before returning.
        inputs = form_snapshot(db, conv_id, market, None)
    except NativeMarketplaceError as exc:
        raise ValueError("Listing not found." if exc.status_code == 404 else str(exc)) from None
    out: dict[str, Any] = {"conversation_id": conv_id, "marketplace": market}
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
    out["category_id"] = form.get("category_id")
    out["fields"] = [_summary(row, with_value=True) for row in form.get("fields") or [] if not _photo_or_note(row)]
    out["missing_required"] = [row["path"] for row in out["fields"]
                               if row["required"] and row.get("value") in (None, "", [], {})]
    notes = [str(note) for note in form.get("notes") or [] if note]
    if notes:
        out["notes"] = notes
    return out


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


def _tool_get_listing_guidelines(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services.catalog_index import relevant_skill_rules

    market = _text(args, "marketplace", required=False, max_len=20).lower()
    if market and market not in MARKETS:
        raise ValueError(f"marketplace must be one of: {', '.join(sorted(MARKETS))}.")
    category = _text(args, "category", required=False, max_len=300)
    field = _text(args, "field", required=False, max_len=200)
    try:
        max_chars = int(args.get("max_chars") or _DEFAULT_GUIDELINE_CHARS)
    except (TypeError, ValueError):
        max_chars = _DEFAULT_GUIDELINE_CHARS
    max_chars = max(1000, min(max_chars, MAX_GUIDELINE_CHARS))
    query = " ".join(part for part in (NAMES.get(market, ""), category, field, "listing") if part)
    return {
        "marketplace": market or None,
        "category": category or None,
        "field": field or None,
        "guidelines": relevant_skill_rules(db, query, max_chars=max_chars),
    }


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_categories": _tool_search_categories,
    "get_category_attributes": _tool_get_category_attributes,
    "search_attribute_options": _tool_search_attribute_options,
    "get_marketplace_form": _tool_get_marketplace_form,
    "get_account_settings": _tool_get_account_settings,
    "get_listing_guidelines": _tool_get_listing_guidelines,
}
