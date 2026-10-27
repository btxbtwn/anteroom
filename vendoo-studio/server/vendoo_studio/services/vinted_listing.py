"""Vinted category and item fields for a direct listing.

Paths are Vinted's own catalog breadcrumbs (Women/Men > Clothing > …).
List on Vinted publishes them on vinted.com. They are not a Vendoo form
and not a Vendoo category id.
"""

from __future__ import annotations

from vendoo_studio.services.direct_preflight import Context, gap, requirement
from vendoo_studio.services.garment_categories import family_kind
from vendoo_studio.services.registry import (
    _department,
    _garment_family,
    _listing_garment_haystack,
    _listing_style,
    _top_kind,
)

_CONDITIONS = {
    "New With Tags/Box": "New with tags",
    "New Without Tags/Box": "New without tags",
    "New With Imperfections": "Very good",
    "Pre-Owned - Excellent": "Very good",
    "Pre-Owned - Good": "Good",
    "Pre-Owned - Fair": "Satisfactory",
    "Poor (Major flaws)": "Satisfactory",
}
_VINTED_CONDITIONS = frozenset({
    "New with tags",
    "New without tags",
    "New",
    "Like new",
    "Very good",
    "Good",
    "Satisfactory",
})


def _clothing(department: str, *parts: str) -> str:
    root = "Women" if department == "women" else "Men"
    return " > ".join((root, "Clothing", *parts))


def _tops(department: str) -> dict[str, str]:
    tee = _clothing(department, "Tops & t-shirts", "T-shirts")
    shirt = _clothing(department, "Tops & t-shirts", "Shirts")
    if department == "women":
        return {
            "tee": tee,
            "long_tee": tee,
            "polo": shirt,
            "button_up": _clothing(department, "Tops & t-shirts", "Shirts"),
            "blouse": _clothing(department, "Tops & t-shirts", "Blouses"),
            "cami": _clothing(department, "Tops & t-shirts", "Camis"),
            "tank": _clothing(department, "Tops & t-shirts", "Vest tops & tank tops"),
            "muscle": _clothing(department, "Tops & t-shirts", "Vest tops & tank tops"),
            "crop": _clothing(department, "Tops & t-shirts", "Crop tops"),
            "tunic": _clothing(department, "Tops & t-shirts", "Tunics"),
            "bodysuit": _clothing(department, "Tops & t-shirts", "Bodysuits"),
            "turtleneck": _clothing(department, "Tops & t-shirts", "Turtlenecks"),
            "halter": _clothing(department, "Tops & t-shirts", "Halterneck tops"),
            "hoodie": _clothing(department, "Jumpers & sweaters", "Hoodies & sweatshirts"),
            "sweatshirt": _clothing(department, "Jumpers & sweaters", "Hoodies & sweatshirts"),
            "cardigan": _clothing(department, "Jumpers & sweaters", "Cardigans"),
            "sweater": _clothing(department, "Jumpers & sweaters", "Jumpers"),
            "sweater_v_neck": _clothing(department, "Jumpers & sweaters", "Jumpers", "V-neck jumpers"),
            "sweater_turtleneck": _clothing(department, "Jumpers & sweaters", "Jumpers", "Turtleneck jumpers"),
            "sweater_crewneck": _clothing(department, "Jumpers & sweaters", "Jumpers"),
            "jersey": _clothing(department, "Activewear", "Team shirts & jerseys"),
        }
    return {
        "tee": tee,
        "long_tee": tee,
        "polo": shirt,
        "button_up": shirt,
        "blouse": shirt,
        "tank": tee,
        "hoodie": _clothing(department, "Jumpers & sweaters", "Hoodies & sweaters"),
        "sweatshirt": _clothing(department, "Jumpers & sweaters", "Hoodies & sweaters"),
        "cardigan": _clothing(department, "Jumpers & sweaters", "Cardigans"),
        "sweater": _clothing(department, "Jumpers & sweaters", "Jumpers"),
        "sweater_v_neck": _clothing(department, "Jumpers & sweaters", "V-neck jumpers"),
        "sweater_turtleneck": _clothing(department, "Jumpers & sweaters", "Turtleneck jumpers"),
        "sweater_crewneck": _clothing(department, "Jumpers & sweaters", "Crew neck jumpers"),
        "jersey": _clothing(department, "Activewear", "Team shirts & jerseys"),
    }


def _family_paths(department: str) -> dict[str, dict[str, str]]:
    if department == "women":
        return {
            "dress": {
                "dress": _clothing(department, "Dresses"),
                "dress_mini": _clothing(department, "Dresses", "Mini-dresses"),
                "dress_midi": _clothing(department, "Dresses", "Midi-dresses"),
                "dress_maxi": _clothing(department, "Dresses", "Long dresses"),
            },
            "skirt": {
                "skirt": _clothing(department, "Skirts"),
                "skirt_mini": _clothing(department, "Skirts", "Mini skirts"),
                "skirt_midi": _clothing(department, "Skirts", "Midi skirts"),
                "skirt_maxi": _clothing(department, "Skirts", "Maxi skirts"),
                "skirt_asymmetrical": _clothing(department, "Skirts", "Asymmetric skirts"),
            },
            "jeans": {
                "jeans": _clothing(department, "Jeans", "Other"),
                "jeans_skinny": _clothing(department, "Jeans", "Skinny jeans"),
                "jeans_straight": _clothing(department, "Jeans", "Straight jeans"),
                "jeans_flare": _clothing(department, "Jeans", "Flared jeans"),
                "jeans_wide_leg": _clothing(department, "Jeans", "Flared jeans"),
                "jeans_boyfriend": _clothing(department, "Jeans", "Boyfriend jeans"),
                "jeans_cropped": _clothing(department, "Jeans", "Cropped jeans"),
                "jeans_high_rise": _clothing(department, "Jeans", "High waisted jeans"),
            },
            "pants": {
                "pants": _clothing(department, "Trousers & leggings"),
                "pants_wide_leg": _clothing(department, "Trousers & leggings", "Wide-leg trousers"),
                "pants_skinny": _clothing(department, "Trousers & leggings", "Skinny trousers"),
                "pants_straight": _clothing(department, "Trousers & leggings", "Straight-leg trousers"),
                "pants_cropped": _clothing(department, "Trousers & leggings", "Cropped trousers & chinos"),
                "leggings": _clothing(department, "Trousers & leggings", "Leggings"),
            },
            "leggings": {"leggings": _clothing(department, "Trousers & leggings", "Leggings")},
            "shorts": {
                "shorts": _clothing(department, "Shorts & cropped trousers"),
                "shorts_cargo": _clothing(department, "Shorts & cropped trousers", "Cargo shorts"),
                "shorts_denim": _clothing(department, "Shorts & cropped trousers", "Denim shorts"),
                "shorts_high_waist": _clothing(department, "Shorts & cropped trousers", "High-waisted shorts"),
            },
            "jumpsuit": {
                "jumpsuit": _clothing(department, "Jumpsuits & playsuits", "Jumpsuits"),
            },
            "jacket": {
                "jacket": _clothing(department, "Outerwear", "Jackets"),
                "jacket_jean": _clothing(department, "Outerwear", "Jackets", "Denim jackets"),
                "jacket_bomber": _clothing(department, "Outerwear", "Jackets", "Bomber jackets"),
                "jacket_varsity": _clothing(department, "Outerwear", "Jackets", "Varsity jackets"),
                "jacket_puffer": _clothing(department, "Outerwear", "Jackets", "Puffer jackets"),
                "jacket_utility": _clothing(department, "Outerwear", "Jackets", "Field & utility jackets"),
                "jacket_trench": _clothing(department, "Outerwear", "Coats", "Trench coats"),
                "coat": _clothing(department, "Outerwear", "Coats"),
                "coat_pea": _clothing(department, "Outerwear", "Coats", "Peacoats"),
                "vest": _clothing(department, "Outerwear", "Gilets & body warmers"),
            },
        }
    return {
        "jeans": {
            "jeans": _clothing(department, "Jeans"),
            "jeans_skinny": _clothing(department, "Jeans", "Skinny jeans"),
            "jeans_straight": _clothing(department, "Jeans", "Straight fit jeans"),
        },
        "pants": {
            "pants": _clothing(department, "Trousers"),
            "pants_skinny": _clothing(department, "Trousers", "Skinny trousers"),
            "pants_cropped": _clothing(department, "Trousers", "Cropped trousers"),
            "pants_wide_leg": _clothing(department, "Trousers", "Wide-legged trousers"),
            "pants_joggers": _clothing(department, "Trousers", "Joggers & sweatpants"),
        },
        "shorts": {
            "shorts": _clothing(department, "Shorts"),
            "shorts_cargo": _clothing(department, "Shorts", "Cargo shorts"),
            "shorts_denim": _clothing(department, "Shorts", "Denim shorts"),
        },
        "jacket": {
            "jacket": _clothing(department, "Outerwear", "Jackets"),
            "jacket_jean": _clothing(department, "Outerwear", "Jackets", "Denim jackets"),
            "jacket_bomber": _clothing(department, "Outerwear", "Jackets", "Bomber jackets"),
            "jacket_varsity": _clothing(department, "Outerwear", "Jackets", "Varsity jackets"),
            "jacket_puffer": _clothing(department, "Outerwear", "Jackets", "Puffer jackets"),
            "jacket_utility": _clothing(department, "Outerwear", "Jackets", "Field & utility jackets"),
            "jacket_trench": _clothing(department, "Outerwear", "Coats", "Trench coats"),
            "coat": _clothing(department, "Outerwear", "Coats"),
            "coat_pea": _clothing(department, "Outerwear", "Coats", "Peacoats"),
            "vest": _clothing(department, "Outerwear", "Gilets & body warmers"),
        },
    }


def map_vinted_category_path(listing: dict | None) -> str:
    """Vinted breadcrumb for this garment, or "" when the listing does not name one."""
    listing = listing if isinstance(listing, dict) else {}
    raw = str(listing.get("category_path") or "")
    garment = f"{_listing_garment_haystack(listing)} {_listing_style(listing)}"
    department = _department(listing, raw, garment)
    if department not in {"women", "men"}:
        return ""
    family = _garment_family(listing)
    if family in {"", "top"}:
        kind = _top_kind(garment) or ("tee" if family == "top" else "")
        if not kind:
            return ""
        return _tops(department).get(kind) or _tops(department).get("tee") or ""
    kind = family_kind(family, garment) or family
    table = _family_paths(department).get(family) or {}
    return table.get(kind) or table.get(family) or ""


def vinted_condition(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _CONDITIONS:
        return _CONDITIONS[text]
    if text in _VINTED_CONDITIONS:
        return text
    low = text.lower()
    if "without" in low and "tag" in low:
        return "New without tags"
    if "tag" in low or low == "new":
        return "New with tags"
    if "excellent" in low or "like new" in low:
        return "Very good"
    if "fair" in low or "poor" in low or "satisfactory" in low:
        return "Satisfactory"
    if low == "good" or low.endswith(" good"):
        return "Good"
    return ""


def vinted_package_size(listing: dict) -> str:
    try:
        pounds = float(listing.get("weight_lb") or 0)
    except (TypeError, ValueError):
        pounds = 0
    try:
        ounces = float(listing.get("weight_oz") or 0)
    except (TypeError, ValueError):
        ounces = 0
    total = pounds + ounces / 16
    if total <= 0:
        return "Medium"
    if total <= 1:
        return "Small"
    if total <= 5:
        return "Medium"
    return "Large"


def _materials(listing: dict) -> list[str]:
    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    found: list[str] = []
    for source in (
        listing.get("material"),
        ebay.get("material"),
        ebay.get("outerShellMaterial"),
        ebay.get("fabricType"),
    ):
        values = source if isinstance(source, list) else [source]
        for value in values:
            text = str(value or "").strip()
            if not text or text.casefold() in {"does not apply", "n/a"}:
                continue
            if text not in found:
                found.append(text)
            if len(found) >= 3:
                return found
    return found


def apply_vinted_listing(listing: dict) -> bool:
    """Fill a Vinted category and the fields Vinted's sell form asks for.

    Leaves a value the seller already set. Does not publish anything.
    """
    if not isinstance(listing, dict):
        return False
    specifics = listing.get("vinted_specifics")
    if not isinstance(specifics, dict):
        specifics = {}
    changed = False
    # The breadcrumb belongs on the direct listing. marketplace_categories.vinted
    # is Vendoo's category, and writing it here makes Send fill that form.
    path = str(specifics.get("category") or "").strip() or map_vinted_category_path(listing)

    def fill(key: str, value: object) -> None:
        nonlocal changed
        if value in (None, "", []):
            return
        current = specifics.get(key)
        if current not in (None, "", []):
            return
        specifics[key] = value
        changed = True

    fill("category", path)
    fill("condition", vinted_condition(specifics.get("condition") or listing.get("condition")))
    from vendoo_studio.services.seller_overrides import field_locked, followed_entry, marketplace_text

    if followed_entry(listing, "vinted", "brand") is None and not field_locked(listing, "vinted", "brand"):
        fill("brand", marketplace_text(listing, "vinted", "brand").strip())
    if followed_entry(listing, "vinted", "size") is None and not field_locked(listing, "vinted", "size"):
        fill("size", str(listing.get("size") or "").strip())
    fill("color", str(listing.get("primaryColor") or "").strip())
    fill("packageSize", vinted_package_size(listing))
    materials = _materials(listing)
    fill("material", ", ".join(materials) if materials else "")
    if specifics != listing.get("vinted_specifics"):
        listing["vinted_specifics"] = specifics
        changed = True
    elif specifics:
        listing["vinted_specifics"] = specifics
    return changed


def _price(snapshot: dict):
    specifics = snapshot.get("vinted_specifics") if isinstance(snapshot.get("vinted_specifics"), dict) else {}
    from vendoo_studio.services.seller_overrides import price_for

    raw = price_for(snapshot, "vinted")
    if raw is None:
        raw = specifics.get("price", snapshot.get("price"))
    try:
        amount = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return amount if amount == amount else None


@requirement("vinted")
def vinted_requirements(snapshot: dict, context: Context) -> list[dict]:
    """What List on Vinted refuses before the extension writes a draft."""
    del context
    apply_vinted_listing(snapshot)
    found: list[dict] = []
    from vendoo_studio.services.seller_overrides import marketplace_text

    title = marketplace_text(snapshot, "vinted", "title").strip()
    if not title:
        found.append(gap("title", "Vinted needs a title."))
    elif len(title) > 100:
        found.append(gap("title", "Vinted titles are at most 100 characters."))
    if not marketplace_text(snapshot, "vinted", "description").strip():
        found.append(gap("description", "Vinted needs a description."))
    amount = _price(snapshot)
    if amount is None or amount <= 0:
        found.append(gap("price", "Vinted needs a price."))
    specifics = snapshot.get("vinted_specifics") if isinstance(snapshot.get("vinted_specifics"), dict) else {}
    if not str(specifics.get("category") or "").strip():
        found.append(gap("vinted_specifics.category", "Vinted needs a clothing category. Say whether this is for women or men."))
    if not str(specifics.get("condition") or "").strip():
        found.append(gap("vinted_specifics.condition", "Vinted needs a condition."))
    return found
