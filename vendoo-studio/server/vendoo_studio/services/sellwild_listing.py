"""Sellwild's own catalog, the choice of category and condition, and what a listing needs there.

Sellwild has no drafts and no per-category fields. Its sell page checks the
title, price, package and (for vehicles) VIN and mileage before it creates a
listing, and its moderation holds any listing with a phone number or outside
contact. The same checks run here before List, so the seller hears about them
before confirming rather than from Chrome afterwards.
"""
from __future__ import annotations

import re
from functools import cache

from vendoo_studio.services.direct_preflight import Context, gap, requirement


@cache
def sellwild_catalog() -> dict:
    """Sellwild's own categories and conditions. Vendoo's Sellwild IDs are retired there."""
    import json

    from vendoo_studio.config import skills_dir

    return json.loads((skills_dir() / "list-this" / "references" / "sellwild-native-catalog.json").read_text(encoding="utf-8"))


# Marketplace and general category words that name the same Sellwild leaf.
_SELLWILD_SYNONYMS = {"tee": "shirt", "tshirt": "shirt", "tees": "shirt", "blouse": "top", "tank": "top", "cami": "top",
                      "jumper": "sweater", "sweatshirt": "sweater", "cardigan": "sweater", "pullover": "sweater",
                      "vest": "jacket", "gilet": "jacket", "parka": "coat", "trouser": "pant", "legging": "activewear",
                      "sneaker": "sneaker", "trainer": "sneaker", "bag": "handbag", "purse": "handbag"}


def _sellwild_words(text: str) -> set[str]:
    text = re.sub(r"\b(short|long|3/4)[ -]sleeves?\b", " ", text.lower()).replace("t-shirt", "tshirt")
    words = set()
    for word in re.findall(r"[a-z0-9]+", text):
        word = word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        words.add(_SELLWILD_SYNONYMS.get(word, word))
    return words


def _sellwild_text_sources(listing: dict) -> list[str]:
    """Where the listing already says what it is, most specific first."""
    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    depop = str((listing.get("marketplace_category_ids") or {}).get("depop") or "")
    # eBay's top level names every clothing branch; it says nothing about the item.
    general = re.sub(r"clothing,? shoes (&|and) accessories", " ", str(listing.get("category_path") or ""), flags=re.I)
    return [" ".join(map(str, poshmark[1:])), depop.split("_", 1)[-1].replace("-", " "),
            " ".join(general.replace(">", "/").split("/")[-2:]), str(listing.get("title") or "")]


def suggest_sellwild(listing: dict) -> dict:
    """Sellwild's own category and condition, worked out from what the listing already says."""
    catalog = sellwild_catalog()
    ids = {option["id"] for option in catalog["categories"]}
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    explicit = specifics.get("nativeCategoryId")
    category_id = int(explicit) if str(explicit or "").isdigit() and int(explicit) in ids else None
    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    everything = " ".join([str(listing.get("category_path") or ""), " ".join(map(str, poshmark)),
                           str((listing.get("marketplace_category_ids") or {}).get("depop") or "")]).lower()
    if category_id is None and re.search(r"\bbaby\b|infant|newborn", everything):
        category_id = 66
    elif category_id is None and re.search(r"\bkids?\b|kidswear|\bboys?\b|\bgirls?\b", everything):
        category_id = 246
    # Vendoo's (retired) Sellwild category still names the right top-level group.
    vendoo_group = str((((listing.get("marketplace_category_objects") or {}).get("sellwild") or {}).get("displayPath") or [""])[0] or "")
    clothing = bool(re.search(r"clothing|menswear|womenswear|\bmen\b|\bwomen\b|shoes|accessories", everything))
    group = vendoo_group or ("Fashion" if clothing else "")
    if category_id is None:
        for text in _sellwild_text_sources(listing):
            words = _sellwild_words(text)
            best, score = None, 0
            for option in catalog["categories"]:
                parent, _, leaf = option["label"].partition(" > ")
                if group and parent != group:
                    continue
                hits = len(_sellwild_words(leaf.replace("&", " ").replace(",", " ")) & words)
                if hits > score:
                    best, score = option["id"], hits
            if best is not None:
                category_id = best
                break
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
    """The category and condition List will send: the seller's pick, else Anteroom's match."""
    automatic = suggest_sellwild(listing)
    return {"category_id": choices.get("sellwild_category_id") or automatic["category_id"],
            "condition_id": choices.get("sellwild_condition_id") or automatic["condition_id"]}


def package_pounds(listing: dict) -> float | None:
    try:
        pounds = float(listing.get("weight_lb") or 0) + float(listing.get("weight_oz") or 0) / 16
    except (TypeError, ValueError):
        return None
    return pounds if pounds > 0 else None


def package_sides(listing: dict) -> list[float] | None:
    sides = [float(side) for side in re.findall(r"\d+(?:\.\d+)?", str(listing.get("package_dimensions_in") or ""))]
    return sides if len(sides) == 3 and min(sides) > 0 else None


def contact_details(text: str) -> list[str]:
    """What in the text Sellwild's moderation would hold the listing for."""
    return [label for pattern, label in _CONTACT if pattern.search(text or "")]


@requirement("sellwild")
def requirements(listing: dict, context: Context) -> list[dict]:
    found = []
    title = str(listing.get("title") or "").strip()
    if not 3 <= len(title) <= 255:
        found.append(gap("title", "Sellwild titles need 3 to 255 characters."))
    if not str(listing.get("description") or "").strip():
        found.append(gap("description", "Sellwild needs a description."))
    try:
        price = float(listing.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if not 5 <= price <= MAX_PRICE:
        found.append(gap("price", "Sellwild prices run from $5 to $999,999."))
    choice = chosen(listing, context.choices)
    catalog = sellwild_catalog()
    if choice["category_id"] not in {option["id"] for option in catalog["categories"]}:
        found.append(gap("sellwild.category", "Choose a Sellwild category."))
    if choice["condition_id"] not in {option["id"] for option in catalog["conditions"]}:
        found.append(gap("sellwild.condition", "Choose a Sellwild condition."))
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    if choice["category_id"] in VEHICLE_CATEGORIES:
        if not str(specifics.get("vin") or "").strip():
            found.append(gap("sellwild_specifics.vin", "Sellwild needs the vehicle's VIN."))
        if not str(specifics.get("mileage") or "").strip():
            found.append(gap("sellwild_specifics.mileage", "Sellwild needs the vehicle's mileage."))
    else:
        if package_pounds(listing) is None:
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
