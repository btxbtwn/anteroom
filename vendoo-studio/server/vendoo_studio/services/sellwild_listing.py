"""Sellwild's own catalog, the choice of category and condition, and what a listing needs there.

Sellwild has no drafts and no per-category fields. Its sell page checks the
title, price, package and (for vehicles) VIN and mileage before it creates a
listing, and its moderation holds any listing with a phone number or outside
contact. The same checks run here before List, so the seller hears about them
before confirming rather than from Chrome afterwards.
"""
from __future__ import annotations

import re
from math import ceil
from functools import cache

from vendoo_studio.services.direct_preflight import Context, gap, requirement


@cache
def sellwild_catalog() -> dict:
    """Sellwild's own categories and conditions. Vendoo's Sellwild IDs are retired there."""
    import json

    from vendoo_studio.config import skills_dir

    return json.loads((skills_dir() / "list-this" / "references" / "sellwild-native-catalog.json").read_text(encoding="utf-8"))


def suggest_sellwild(listing: dict) -> dict:
    """Sellwild's own category and condition, worked out from the general category and the listing."""
    from vendoo_studio.services.native_category_match import evidence, match_row

    catalog = sellwild_catalog()
    ids = {option["id"] for option in catalog["categories"]}
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    explicit = specifics.get("nativeCategoryId")
    category_id = int(explicit) if str(explicit or "").isdigit() and int(explicit) in ids else None
    general = str(listing.get("category_path") or "")
    # Sellwild keeps children's clothes in two groups of its own, by age.
    everything = f"{general} {listing.get('department') or ''}".lower()
    if category_id is None and re.search(r"\bbaby\b|infant|newborn", everything):
        category_id = 66
    elif category_id is None and re.search(r"\bkids?\b|\bboys?\b|\bgirls?\b", everything):
        category_id = 246
    if category_id is None:
        rows = [{"id": option["id"], "path": option["label"]} for option in catalog["categories"]]
        hit = match_row(rows, general, evidence(listing))
        category_id = hit["id"] if hit else None
    native_condition = specifics.get("nativeConditionId")
    condition = str(listing.get("condition") or "").lower()
    # Sellwild's own scale: 5 Brand New, 4 Like New, 3 Good, 2 Gently Used, 1 Quite Used.
    # Excellent is Like New and Good is Good. A plain pre-owned or used item is Gently Used.
    condition_id = (int(native_condition) if str(native_condition or "") in {"1", "2", "3", "4", "5"}
                    else 1 if re.search(r"poor|major flaw|\bfair\b|acceptable", condition)
                    else 4 if re.search(r"new without tags|nwot|like new|excellent", condition)
                    else 3 if re.search(r"new with defects|imperfection|\bgood\b", condition)
                    else 5 if re.search(r"new with tags|nwt|brand new|^new\b", condition)
                    else 2 if re.search(r"pre-owned|\bused\b", condition) else None)
    return {"category_id": category_id, "condition_id": condition_id}


# Sellwild's vehicle leaves: they take a VIN and mileage instead of a package.
VEHICLE_CATEGORIES = frozenset({43, 95, 157, 158, 159, 160, 161, 162, 163, 164, 291, 292})
MAX_PRICE = 999999
MAX_SIDE_IN = 108
_CONTACT = (
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "a phone number"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|shop|store|co|io|me|ly)\b", re.I), "a link"),
    (re.compile(r"\b(?:whats\s?app|telegram|signal|venmo|cash\s?app|zelle|paypal\.me|text me|call me|dm me)\b", re.I),
     "an outside contact or payment"),
)


def chosen(listing: dict, choices: dict) -> dict:
    """The saved category and condition List will send. Category must be chosen."""
    automatic = suggest_sellwild(listing)
    specifics = listing.get("sellwild_specifics") or {}

    def pick(key: str, field: str):
        if field in specifics:
            value = str(specifics[field] or "")
            return int(value) if re.fullmatch(r"[1-9]\d*", value) else None
        return choices.get(f"sellwild_{key}_id") or (automatic["condition_id"] if key == "condition" else None)

    return {"category_id": pick("category", "nativeCategoryId"), "condition_id": pick("condition", "nativeConditionId")}


def _own_package(listing: dict) -> dict:
    """Weight and sides typed on the Sellwild tab. They start from Item Details."""
    package = (listing.get("sellwild_specifics") or {}).get("package")
    return package if isinstance(package, dict) else {}


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def package_pounds(listing: dict) -> float | None:
    if (own := _positive(_own_package(listing).get("weight"))) is not None:
        return own
    try:
        pounds = float(listing.get("weight_lb") or 0) + float(listing.get("weight_oz") or 0) / 16
    except (TypeError, ValueError):
        return None
    return pounds if pounds > 0 else None


def package_sides(listing: dict) -> list[float] | None:
    """Length (Sellwild's depth), width and height in inches."""
    selected = str((listing.get("sellwild_specifics") or {}).get("parcelTemplate") or "")
    preset = next((row for row in sellwild_catalog()["parcel_templates"] if row["id"] == selected), None)
    if preset and all(preset[side] > 0 for side in ("length", "width", "height")):
        # The native sell page rounds each template side up to a whole inch.
        return [ceil(preset[side]) for side in ("length", "width", "height")]
    own = [_positive(_own_package(listing).get(side)) for side in ("depth", "width", "height")]
    if all(side is not None for side in own):
        return own
    sides = [float(side) for side in re.findall(r"\d+(?:\.\d+)?", str(listing.get("package_dimensions_in") or ""))]
    return sides if len(sides) == 3 and min(sides) > 0 else None


def contact_details(text: str) -> list[str]:
    """What in the text Sellwild's moderation would hold the listing for."""
    return [label for pattern, label in _CONTACT if pattern.search(text or "")]


@requirement("sellwild")
def requirements(listing: dict, context: Context) -> list[dict]:
    found = []
    from vendoo_studio.services.seller_overrides import marketplace_text

    title = marketplace_text(listing, "sellwild", "title").strip()
    if not 3 <= len(title) <= 255:
        found.append(gap("title", "Sellwild titles need 3 to 255 characters."))
    try:
        price = float(listing.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if not 5 <= price <= MAX_PRICE:
        found.append(gap("price", "Sellwild prices run from $5 to $999,999."))
    choice = chosen(listing, context.choices)
    catalog = sellwild_catalog()
    if choice["category_id"] not in {option["id"] for option in catalog["categories"]}:
        found.append(gap("sellwild_specifics.nativeCategoryId", "Choose a Sellwild category."))
    if choice["condition_id"] not in {option["id"] for option in catalog["conditions"]}:
        found.append(gap("sellwild_specifics.nativeConditionId", "Choose a Sellwild condition."))
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    template = str(specifics.get("parcelTemplate") or "")
    preset = next((row for row in catalog["parcel_templates"] if row["id"] == template), None)
    # Sellwild's page asks for a weight with every package except the flat-rate ones.
    fixed_package = bool(preset and preset.get("flat_rate"))
    if choice["category_id"] not in VEHICLE_CATEGORIES and template and not preset:
        found.append(gap("sellwild_specifics.parcelTemplate", "Choose packaging from Sellwild’s current catalog."))
    if choice["category_id"] in VEHICLE_CATEGORIES:
        if not str(specifics.get("vin") or "").strip():
            found.append(gap("sellwild_specifics.vin", "Sellwild needs the vehicle's VIN."))
        if not str(specifics.get("mileage") or "").strip():
            found.append(gap("sellwild_specifics.mileage", "Sellwild needs the vehicle's mileage."))
    else:
        if not fixed_package and package_pounds(listing) is None:
            found.append(gap("weight_oz", "Sellwild needs the packed weight."))
        sides = package_sides(listing)
        if sides is None:
            found.append(gap("package_dimensions_in", "Sellwild needs the package length, width and height."))
        elif max(sides) >= MAX_SIDE_IN:
            found.append(gap("package_dimensions_in", f"Sellwild takes packages under {MAX_SIDE_IN} inches on every side."))
    for name in ("title", "description"):
        details = contact_details(str(listing.get(name) or ""))
        if details:
            found.append(gap(name, f"Sellwild holds a listing for review when its {name} has {' or '.join(details)}. Remove it."))
    return found
