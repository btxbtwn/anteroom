"""Cached marketplace spreadsheet cells and readiness for the inventory's marketplace tabs.

Reads only saved listing revisions, local bundled catalogs, and cached native
form contracts — never calls live marketplace APIs or Chrome. When eBay or Etsy
does not have a cached native form contract yet, core listing values are still
returned and ``needs_load`` is set so inline editing fetches the full form on
demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import listing_readiness, native_contract, native_marketplaces
from anteroom.services.direct_preflight import same_answer
from anteroom.services.job_snapshot import prepare_listing_snapshot
from anteroom.services.native_form import attach_form
from anteroom.services.seller_overrides import get_path, marketplace_text, price_for

MAX_LISTINGS = listing_readiness.MAX_LISTINGS
MARKETPLACES = ("ebay", "poshmark", "mercari", "depop", "etsy")

_EBAY_CONDITION_LABELS = {
    "NEW": "New",
    "NEW_OTHER": "New (other)",
    "NEW_WITH_DEFECTS": "New with defects",
    "LIKE_NEW": "Like new",
    "PRE_OWNED_EXCELLENT": "Pre-owned - Excellent",
    "USED_EXCELLENT": "Pre-owned - Good",
    "PRE_OWNED_FAIR": "Pre-owned - Fair",
    "USED_VERY_GOOD": "Very good",
    "USED_GOOD": "Good",
    "USED_ACCEPTABLE": "Acceptable",
    "FOR_PARTS_OR_NOT_WORKING": "For parts or not working",
}


@dataclass(frozen=True)
class SheetColumnSpec:
    key: str
    label: str
    default_required: bool = False
    editable: bool = True


# Canonical column order per marketplace, matching each marketplace's native form paths.
SHEET_COLUMNS: dict[str, tuple[SheetColumnSpec, ...]] = {
    "depop": (
        SheetColumnSpec("direct_category_ids.depop", "Category", default_required=True),
        SheetColumnSpec("depop_specifics.brand", "Brand"),
        SheetColumnSpec("depop_specifics.condition", "Condition", default_required=True),
        SheetColumnSpec("depop_specifics.size", "Size", default_required=True),
        SheetColumnSpec("depop_specifics.parcelSize", "Package size", default_required=True),
        SheetColumnSpec("price", "Price", default_required=True),
        SheetColumnSpec("depop_specifics.colour", "Color"),
        SheetColumnSpec("depop_specifics.description", "Description", default_required=True),
    ),
    "poshmark": (
        SheetColumnSpec("direct_category_ids.poshmark", "Category", default_required=True),
        SheetColumnSpec("poshmark_specifics.brand", "Brand"),
        SheetColumnSpec("poshmark_specifics.condition", "Condition", default_required=True),
        SheetColumnSpec("poshmark_specifics.inventoryRows", "Size", default_required=True, editable=False),
        SheetColumnSpec("poshmark_specifics.colors", "Color"),
        SheetColumnSpec("price", "Price", default_required=True),
        SheetColumnSpec("poshmark_specifics.title", "Title", default_required=True),
        SheetColumnSpec("poshmark_specifics.description", "Description", default_required=True),
    ),
    "mercari": (
        SheetColumnSpec("direct_category_ids.mercari", "Category", default_required=True),
        SheetColumnSpec("mercari_specifics.brand", "Brand"),
        SheetColumnSpec("mercari_specifics.condition", "Condition", default_required=True),
        SheetColumnSpec("mercari_specifics.nativeSizeId", "Size", default_required=True),
        SheetColumnSpec("mercari_specifics.shippingClassId", "Shipping", default_required=True, editable=False),
        SheetColumnSpec("price", "Price", default_required=True),
        SheetColumnSpec("mercari_specifics.title", "Title", default_required=True),
        SheetColumnSpec("mercari_specifics.description", "Description", default_required=True),
    ),
    "ebay": (
        SheetColumnSpec("direct_category_ids.ebay", "Category", default_required=True),
        SheetColumnSpec("ebay_specifics.condition", "Condition", default_required=True),
        SheetColumnSpec("ebay_specifics.Brand", "Brand"),
        SheetColumnSpec("ebay_specifics.Size", "Size"),
        SheetColumnSpec("price", "Price", default_required=True),
        SheetColumnSpec("quantity", "Qty"),
        SheetColumnSpec("ebay_specifics.title", "Title", default_required=True),
    ),
    "etsy": (
        SheetColumnSpec("direct_category_ids.etsy", "Category", default_required=True),
        SheetColumnSpec("etsy_specifics.whenMade", "When made", default_required=True),
        SheetColumnSpec("etsy_specifics.whoMade", "Who made it", default_required=True),
        SheetColumnSpec("etsy_specifics.whatIsIt", "What is it", default_required=True),
        SheetColumnSpec("price", "Price", default_required=True),
        SheetColumnSpec("quantity", "Qty", default_required=True),
        SheetColumnSpec("etsy_specifics.title", "Title", default_required=True),
    ),
}


def _option_label(raw: Any, field: dict[str, Any] | None) -> str:
    if raw is None or raw == "" or raw == [] or raw == {}:
        return ""
    option_values = (field or {}).get("option_values") or {}
    reverse = {str(option_id): str(label) for label, option_id in option_values.items()}
    if isinstance(raw, list):
        parts = [reverse.get(str(item), str(item)) for item in raw if item not in (None, "")]
        return ", ".join(parts)
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    text = str(raw)
    return reverse.get(text, text)


def _format_poshmark_inventory(rows: Any, field: dict[str, Any] | None) -> str:
    if not isinstance(rows, list) or not rows:
        return ""
    groups = (field or {}).get("inventory_groups") or []
    system = str((field or {}).get("inventory_system") or "us")
    size_labels: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        sizes = (group.get("systems") or {}).get(system, group.get("sizes") or [])
        for size in sizes or []:
            if isinstance(size, dict) and size.get("id") is not None:
                size_labels[str(size["id"])] = str(size.get("label") or size["id"])
    filled = [row for row in rows if isinstance(row, dict) and str(row.get("sizeId") or "").strip()]
    if not filled:
        return ""
    if len(filled) == 1:
        size_id = str(filled[0].get("sizeId") or "")
        label = size_labels.get(size_id, size_id)
        qty = filled[0].get("quantity")
        return f"{label} ({qty})" if qty not in (None, "", 1, "1") else label
    return f"{len(filled)} sizes"


def _format_mercari_shipping(prepared: dict[str, Any], field: dict[str, Any] | None) -> str:
    specifics = prepared.get("mercari_specifics")
    if not isinstance(specifics, dict):
        specifics = {}
    if specifics.get("shippingMethod") == "SOYO":
        return "Ship on your own"
    raw = (field or {}).get("value") if field else specifics.get("shippingClassId")
    if raw not in (None, ""):
        labeled = _option_label(raw, field)
        if labeled and labeled != str(raw):
            return labeled
        saved_label = ((prepared.get("form") or {}).get("mercari_shipping_label") or {}).get("label")
        if saved_label:
            return str(saved_label)
        return "Prepaid label"
    return ""


def _cached_form(listing_json: dict[str, Any], prepared: dict[str, Any], marketplace: str) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(form, needs_load)`` without making any network or Chrome calls."""
    if marketplace == "depop":
        from anteroom.services.depop_form import form as depop_form

        return depop_form(prepared), False
    if marketplace == "poshmark":
        from anteroom.services.poshmark_form import form as poshmark_form

        return poshmark_form(prepared), False
    if marketplace == "mercari":
        saved = native_contract.editor_form(listing_json, "mercari", values=prepared)
        if saved is not None:
            return saved, False
        from anteroom.services.mercari_form import form as mercari_form

        return mercari_form(prepared), False
    saved = native_contract.editor_form(listing_json, marketplace, values=prepared)
    if saved is not None:
        return saved, False
    return None, True


def _fallback_value(prepared: dict[str, Any], marketplace: str, path: str) -> tuple[str, str]:
    """Read a display/raw pair directly from ``prepared`` when eBay/Etsy has no cached form."""
    if path == "price":
        amount = price_for(prepared, marketplace)
        text = "" if amount is None else str(amount)
        return text, text
    if path == "quantity":
        qty = prepared.get("quantity")
        text = "" if qty is None else str(qty)
        return text, text
    if path == "direct_category_ids.ebay":
        ids = prepared.get("direct_category_ids") or {}
        raw = str(ids.get("ebay") or "")
        shown = str(ids.get("ebay_path") or raw)
        return shown, raw
    if path == "direct_category_ids.etsy":
        from anteroom.services.etsy_form import category_choices

        ids = prepared.get("direct_category_ids") or {}
        raw = str(ids.get("etsy") or "")
        shown = str(ids.get("etsy_path") or "")
        if not shown and raw:
            reverse = {str(v): k for k, v in category_choices()["option_values"].items()}
            shown = reverse.get(raw, raw)
        return shown, raw
    if path == "ebay_specifics.condition":
        from anteroom.services.ebay_listings import CONDITION_IDS, ebay_condition

        specifics = prepared.get("ebay_specifics")
        if not isinstance(specifics, dict):
            specifics = {}
        raw_text = str(specifics.get("condition") or prepared.get("condition") or "").strip()
        if not raw_text:
            return "", ""
        cond = ebay_condition(prepared)
        return _EBAY_CONDITION_LABELS.get(cond, raw_text), CONDITION_IDS.get(cond, raw_text)
    if path in {"ebay_specifics.Brand", "ebay_specifics.Size"}:
        name = path.split(".", 1)[1]
        specifics = prepared.get("ebay_specifics") or {}
        raw = specifics.get(name) or specifics.get(name.lower()) or prepared.get(name.lower()) or ""
        text = ", ".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        return text, text
    if path in {"ebay_specifics.title", "etsy_specifics.title"}:
        text = marketplace_text(prepared, marketplace, "title")
        return text, text
    if path == "etsy_specifics.whenMade":
        from anteroom.services.etsy_fields import etsy_when_options, etsy_when_raw, etsy_when_to_store

        etsy_spec = prepared.get("etsy_specifics")
        etsy_spec = etsy_spec if isinstance(etsy_spec, dict) else {}
        raw = etsy_when_raw(etsy_spec, prepared)
        if not raw:
            return "", ""
        shown = etsy_when_to_store(raw, prepared, etsy_when_options()) or raw
        return shown, raw
    if path == "etsy_specifics.whoMade":
        from anteroom.services.etsy_fields import etsy_who_raw

        etsy_spec = prepared.get("etsy_specifics")
        etsy_spec = etsy_spec if isinstance(etsy_spec, dict) else {}
        raw = etsy_who_raw(etsy_spec)
        return raw, raw
    if path == "etsy_specifics.whatIsIt":
        from anteroom.services.etsy_fields import etsy_what_raw

        etsy_spec = prepared.get("etsy_specifics")
        etsy_spec = etsy_spec if isinstance(etsy_spec, dict) else {}
        raw = etsy_what_raw(etsy_spec)
        return raw, raw
    raw = get_path(prepared, path)
    if raw is None:
        return "", ""
    if isinstance(raw, list):
        text = ", ".join(str(x) for x in raw if x not in (None, ""))
        return text, text
    text = str(raw)
    return text, text


def _answer_keys(path: str, marketplace: str) -> tuple[str, ...]:
    """Candidate preflight/rejected field keys that refer to this sheet column."""
    keys: list[str] = [same_answer(path), path]
    if path == f"direct_category_ids.{marketplace}":
        keys.extend((f"marketplace_category_ids.{marketplace}", "category"))
    elif path == "price":
        keys.append(f"{marketplace}_specifics.price")
    elif path == "mercari_specifics.nativeSizeId":
        keys.extend(("mercari_specifics.size", "size"))
    elif path == "poshmark_specifics.inventoryRows":
        keys.extend(("poshmark_specifics.size", "size"))
    elif path == "poshmark_specifics.colors":
        keys.append("primaryColor")
    elif path == "ebay_specifics.condition":
        keys.extend(("ebay_specifics.category_specifics.condition", "condition"))
    elif path == "ebay_specifics.Brand":
        keys.extend(("ebay_specifics.category_specifics.Brand", "brand"))
    elif path == "ebay_specifics.Size":
        keys.extend(("ebay_specifics.category_specifics.Size", "size"))
    elif path == "etsy_specifics.whenMade":
        keys.extend(("etsy_specifics.when_made", "when_made"))
    elif path == "etsy_specifics.whoMade":
        keys.extend(("etsy_specifics.who_made", "who_made"))
    elif path == "etsy_specifics.whatIsIt":
        keys.extend(("etsy_specifics.what_is", "what_is"))
    return tuple(dict.fromkeys(keys))


def _ebay_has_variations(prepared: dict[str, Any]) -> bool:
    specifics = prepared.get("ebay_specifics")
    if not isinstance(specifics, dict):
        return False
    variations = specifics.get("variations")
    return isinstance(variations, dict) and isinstance(variations.get("rows"), list) and len(variations["rows"]) > 0


def _ebay_is_auction(prepared: dict[str, Any]) -> bool:
    specifics = prepared.get("ebay_specifics")
    return isinstance(specifics, dict) and str(specifics.get("listingFormat") or "").upper() == "AUCTION"


def _build_cell(
    spec: SheetColumnSpec,
    marketplace: str,
    prepared: dict[str, Any],
    fields_by_path: dict[str, dict[str, Any]],
    missing_by_answer: dict[str, dict[str, Any]],
    rejected_by_answer: dict[str, str],
) -> dict[str, Any]:
    field = fields_by_path.get(spec.key)
    editable = spec.editable and not bool((field or {}).get("locked"))
    required = bool(field.get("required")) if field is not None else spec.default_required
    raw_value = ""
    display_value = ""

    if spec.key == "poshmark_specifics.inventoryRows":
        raw_rows = (field or {}).get("value") if field is not None else get_path(prepared, spec.key)
        display_value = _format_poshmark_inventory(raw_rows, field)
        raw_value = display_value
        editable = False
    elif spec.key == "mercari_specifics.shippingClassId":
        display_value = _format_mercari_shipping(prepared, field)
        raw_value = str((field or {}).get("value") or "")
        editable = False
    elif spec.key == "price":
        has_variations = marketplace == "ebay" and _ebay_has_variations(prepared)
        is_auction = marketplace == "ebay" and not has_variations and _ebay_is_auction(prepared)
        if has_variations:
            editable = False
            rows = ((prepared.get("ebay_specifics") or {}).get("variations") or {}).get("rows") or []
            display_value = f"{len(rows)} variations"
            raw_value = display_value
        elif is_auction:
            editable = False
            start = (prepared.get("ebay_specifics") or {}).get("auctionStartPrice")
            display_value = f"Auction ({start})" if start not in (None, "") else "Auction"
            raw_value = display_value
        else:
            amount = price_for(prepared, marketplace)
            text = "" if amount is None else str(amount)
            display_value = text
            raw_value = text
    elif field is not None:
        raw = field.get("value")
        if spec.key == "direct_category_ids.ebay":
            ids = prepared.get("direct_category_ids") or {}
            raw_value = str(ids.get("ebay") or raw or "")
            display_value = str(ids.get("ebay_path") or _option_label(raw, field))
        else:
            display_value = _option_label(raw, field)
            if isinstance(raw, list):
                raw_value = ", ".join(str(x) for x in raw if x not in (None, ""))
            elif raw is not None:
                raw_value = str(raw)
    else:
        display_value, raw_value = _fallback_value(prepared, marketplace, spec.key)

    keys = _answer_keys(spec.key, marketplace)
    missing_item = next((missing_by_answer[k] for k in keys if k in missing_by_answer), None)
    rejected_msg = next((rejected_by_answer[k] for k in keys if k in rejected_by_answer), "")
    is_rejected = bool(rejected_msg)
    is_missing = bool(missing_item)
    error = rejected_msg or (str(missing_item.get("message") or "") if missing_item else "")

    return {
        "value": display_value,
        "raw": raw_value,
        "required": required,
        "missing": is_missing,
        "rejected": is_rejected,
        "error": error,
        "editable": editable,
    }


def for_listings(db: Session, marketplace: str, conv_ids: list[str]) -> dict[str, Any]:
    """Build cached spreadsheet rows for ``marketplace`` across up to ``MAX_LISTINGS`` IDs.

    Synchronous and read-only: routes run it in the FastAPI threadpool. Listings
    this seller cannot read are omitted.
    """
    conv_repo = ConversationRepo(db)
    listing_repo = ListingRepo(db)
    market_repo = MarketplaceListingRepo(db)
    columns = SHEET_COLUMNS[marketplace]
    rows: dict[str, dict[str, Any]] = {}

    for conv_id in list(dict.fromkeys(conv_ids))[:MAX_LISTINGS]:
        conv = conv_repo.get(conv_id)
        if conv is None:
            continue
        listing = listing_repo.get_current(conv_id)
        revision = listing_repo.get_revision(listing.current_revision_id) if listing else None
        if not revision or not isinstance(revision.listing_json, dict):
            rows[conv_id] = {
                "revision_id": None,
                "ready": False,
                "needs_load": False,
                "cells": {},
                "missing": [],
            }
            continue

        prepared = prepare_listing_snapshot(db, conv, revision.listing_json)
        attached = attach_form(native_contract.copy_listing(prepared), marketplace, db=db)
        photos = conv_repo.get_all_photos(conv_id)
        records = market_repo.for_conversation(conv_id)
        preflight_result = native_marketplaces.preflight_for_marketplace(
            db, conv_id, prepared, photos, records, marketplace, attached=attached
        )

        rejected_by_answer: dict[str, str] = {}
        for item in preflight_result["rejected"]:
            if isinstance(item, dict) and item.get("field"):
                raw_key = str(item["field"])
                msg = str(item.get("message") or "")
                rejected_by_answer.setdefault(same_answer(raw_key), msg)
                rejected_by_answer.setdefault(raw_key, msg)

        missing_with_rejected = [
            {
                **item,
                "rejected": same_answer(item["field"]) in rejected_by_answer or item["field"] in rejected_by_answer,
            }
            for item in preflight_result["missing"]
        ]
        missing_by_answer: dict[str, dict[str, Any]] = {}
        for item in missing_with_rejected:
            missing_by_answer.setdefault(same_answer(item["field"]), item)
            missing_by_answer.setdefault(item["field"], item)

        form, needs_load = _cached_form(revision.listing_json, attached, marketplace)
        fields_by_path: dict[str, dict[str, Any]] = {}
        if form and isinstance(form.get("fields"), list):
            for field_row in form["fields"]:
                path = native_contract.path_of(field_row)
                if path:
                    fields_by_path[path] = field_row

        cells = {
            spec.key: _build_cell(spec, marketplace, attached, fields_by_path, missing_by_answer, rejected_by_answer)
            for spec in columns
        }

        rows[conv_id] = {
            "revision_id": revision.id,
            "ready": bool(preflight_result["ready"]),
            "needs_load": needs_load,
            "cells": cells,
            "missing": missing_with_rejected,
        }

    return {"marketplace": marketplace, "listings": rows}
