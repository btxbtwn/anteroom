"""The fields a saved listing still needs on each marketplace.

One count for every place that shows readiness: the listing editor's
marketplace tabs, List everywhere, and the inventory, on the web and the
iPhone. A field is missing when the marketplace's form requires it and the
listing leaves it empty or unusable (the pre-flight's blocking and required
gaps), or when the marketplace rejected it on the last List and the seller has
not edited it since. A field named both ways counts once.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from vendoo_studio.services.direct_preflight import same_answer

# How the seller knows a field whose report carries no form label.
_NAMES = {"direct_category_ids": "Category", "marketplace_category_ids": "Category",
          "package_dimensions_in": "Package size", "weight_oz": "Weight", "sku": "SKU",
          "marketplace_photos": "Photos", "when_made": "When made", "parcelSize": "Parcel size",
          "shippingClassId": "Shipping", "shipsFrom": "Ships from", "nativeSizeId": "Size"}
# Most listings a single readiness request reads; the inventory asks for the rows on screen.
MAX_LISTINGS = 50


def label(field: str) -> str:
    """A readable name for a field path: its own name, not its place in the listing."""
    parts = [part for part in str(field).split(".") if part]
    if not parts:
        return "Field"
    for part in (parts[-1], parts[0]):
        if part in _NAMES:
            return _NAMES[part]
    leaf = parts[-1]
    if leaf.endswith("_specifics"):
        return "Details"
    if " " in leaf:
        return leaf  # Already the marketplace's own label, such as an eBay aspect.
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", leaf).replace("_", " ").strip().lower()
    return words[:1].upper() + words[1:]


def missing(gaps: list[dict], rejected: list[dict] | None = None) -> list[dict]:
    """Each field the marketplace still needs, once: ``{"field", "label", "message"}``.

    Pre-flight gaps that refuse List or name an empty required row count;
    advice that does not (an account default, a photo left off) does not.
    """
    out: list[dict] = []
    seen: set[str] = set()
    candidates = [item for item in gaps if item.get("blocking") or item.get("required")]
    candidates += [item for item in rejected or [] if isinstance(item, dict) and item.get("field")]
    for item in candidates:
        field = str(item["field"])
        # A form's own label can end in a colon ("Ships from:"); the name does not.
        name = str(item.get("label") or "").strip().rstrip(":").strip() or label(field)
        keys = {same_answer(field), f"label:{name.casefold()}"}
        if keys & seen:
            continue
        seen |= keys
        out.append({"field": field, "label": name, "message": str(item.get("message") or "")})
    return out


def rejected_fields(record: dict) -> list[dict]:
    """The fields the last List was refused for, while that refusal still stands."""
    fields = record.get("error_fields")
    return fields if record.get("error") and isinstance(fields, list) else []


def for_listings(db: Session, conv_ids: list[str]) -> dict[str, dict]:
    """Each listing's readiness per marketplace, as the editor's pre-flight reports it.

    Listings this seller cannot read are left out. Synchronous: routes call it
    from the threadpool.
    """
    from vendoo_studio.services import native_marketplaces

    out: dict[str, dict] = {}
    for conv_id in list(dict.fromkeys(conv_ids))[:MAX_LISTINGS]:
        try:
            check = native_marketplaces.preflight(conv_id, db)
        except native_marketplaces.NativeMarketplaceError:
            continue
        out[conv_id] = {
            "revision_id": check["revision_id"],
            "marketplaces": {market: {"missing": result["missing"]} for market, result in check["marketplaces"].items()},
        }
    return out
