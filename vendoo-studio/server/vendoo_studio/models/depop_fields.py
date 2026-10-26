"""Depop listing attributes: parcel size, tags, materials, and category optional defaults."""

from __future__ import annotations

import re
from typing import Any

from vendoo_studio.models.ebay_fields import (
    ebay_optional_blank,
    ebay_season_haystack,
)
from vendoo_studio.models.listing_values import (
    as_list,
    canonical_option,
    infer_known_options,
    text_value,
)
from vendoo_studio.models.schema import (
    VALID_DEPOP_OCCASION,
    VALID_DEPOP_STYLE,
)

DEPOP_CATEGORY_OPTIONAL_KEYS = (
    "source",
    "age",
    "style",
    "occasion",
    "parcelSize",
    "sizeGrouping",
    "material",
)
DEPOP_OPTIONAL_DNA_KEYS = frozenset({
    "sizeGrouping",  # Regular sizing — omit / Does Not Apply
})
DEPOP_OPTIONAL_EVIDENCE_KEYS = frozenset({"material"})
# Keys a model sometimes copies from Depop's shipping form. No code reads them.
DEPOP_UNREAD_KEYS = ("nationalShipping", "shippingmethod", "shippingMethod")
DEPOP_OPTIONAL_MUST_FILL_LOOKUPS = frozenset({
    "source",
    "age",
    "style",
    "occasion",
    "parcel size",
    "size grouping",
    "material",
})
DEPOP_OPTIONAL_DNA_LOOKUPS = frozenset({
    "size grouping",
})
# Last-resort pads when listing text yields fewer than 3 style cues.
DEPOP_STYLE_FALLBACKS = ("Casual", "Minimalist", "Indie")
DEPOP_OCCASION_FALLBACKS = ("Casual", "Going out", "Vacation")
DEPOP_DEFAULT_OCCASIONS = DEPOP_OCCASION_FALLBACKS
DEPOP_OCCASION_EBAY_ALIASES = {
    "workwear": "Work", "activewear": "Workout", "formal": "Special Occasion",
    "party/cocktail": "Party", "everyday": "Casual", "travel": "Vacation",
    "business": "Work", "casual": "Casual",
}
DEPOP_OCCASION_CUES = {
    "Festival": (r"\bfestival\b", r"\brave\b"),
    "Going out": (r"\bgoing[\s-]*out\b", r"\bnight[\s-]*out\b", r"\bcocktail\b", r"\bblouse\b"),
    "Outdoors": (r"\boutdoor(?:s)?\b", r"\bhiking\b", r"\bcamping\b", r"\bpatagonia\b"),
    "Party": (r"\bparty\b", r"\bsequin\b", r"\bglam\b"),
    "Relaxation": (r"\brelax(?:ation|ing)?\b", r"\blounge(?:wear)?\b", r"\bpajamas?\b"),
    "School": (r"\bschool\b", r"\bcampus\b", r"\bcollegiate\b"),
    "Ski": (r"\bski(?:ing)?\b", r"\bsnowboard\b"),
    "Special Occasion": (r"\bspecial\s*occasion\b", r"\bwedding\b", r"\bformal\b", r"\bprom\b"),
    "Summer": (r"\bsummer\b", r"\bbeach\b", r"\btropical\b", r"\bshorts?\b"),
    "Vacation": (r"\bvacation\b", r"\btravel\b", r"\bresort\b"),
    "Winter": (r"\bwinter\b", r"\bsnow\b", r"\bparka\b", r"\bpuffer\b"),
    "Work": (r"\bwork(?:wear)?\b", r"\boffice\b", r"\bbusiness\b"),
    "Workout": (r"\bworkout\b", r"\bgym\b", r"\bactivewear\b", r"\bathletic\b", r"\bleggings?\b"),
    "Casual": (r"\bcasual\b", r"\beveryday\b", r"\btee\b", r"\bt[\s-]?shirt\b", r"\brelaxed\b"),
}

# Keyword cues → Depop style labels. Scores accumulate; top hits win.
DEPOP_STYLE_CUES: dict[str, tuple[str, ...]] = {
    "Streetwear": (
        r"\bstreetwear\b", r"\bstreet\s*style\b",
        r"\bgraphic\s*(?:tee|t[\s-]?shirt|top)\b", r"\bhype(?:beast)?\b",
        r"\boversized\s+(?:hoodie|tee|sweat)\b",
    ),
    "Sportswear": (
        r"\bsportswear\b", r"\bathletic\b", r"\bactivewear\b", r"\bworkout\b",
        r"\bleggings?\b", r"\btrack\s*(?:pant|jacket|suit)\b", r"\bjoggers?\b",
        r"\bsporty\b",
    ),
    "Loungewear": (
        r"\bloungewear\b", r"\blounge\b", r"\bpajamas?\b", r"\bpyjamas?\b",
        r"\bsweatpants?\b", r"\brobe\b", r"\bsleepwear\b",
    ),
    "Goth": (r"\bgoth(?:ic)?\b", r"\bvampire\b", r"\bbatwing\b"),
    "Retro": (
        r"\bretro\b", r"\bvintage\b", r"\bdeadstock\b",
        r"\b(?:50|60|70|80|90)s\b", r"\bthrowback\b",
    ),
    "Boho": (
        r"\bboho\b", r"\bbohemian\b", r"\bpeasant\b", r"\bfloral\b",
        r"\bflower(?:ed|s)?\b", r"\bfringe\b", r"\bembroider(?:ed|y)\b",
        r"\btassels?\b",
    ),
    "Western": (
        r"\bwestern\b", r"\bcowboy\b", r"\bcowgirl\b", r"\brodeo\b",
        r"\bfringe\s+(?:jacket|vest)\b",
    ),
    "Indie": (r"\bindie\b", r"\balternative\b", r"\bthrift(?:ed|y)?\b"),
    "Skater": (r"\bskater\b", r"\bskate\b", r"\bskateboard\b"),
    "Rave": (r"\brave\b", r"\bfestival\b", r"\bneon\b", r"\bedm\b"),
    "Costume": (r"\bcostume\b", r"\bhalloween\b", r"\bdress[\s-]*up\b"),
    "Cosplay": (r"\bcosplay\b", r"\banime\b", r"\bmanga\b"),
    "Grunge": (r"\bgrunge\b", r"\bdistressed\b", r"\bflannel\b"),
    "Emo": (r"\bemo\b",),
    "Minimalist": (
        r"\bminimalist\b", r"\bminimal\b", r"\bclean\s*line\b",
        r"\bbasic\b", r"\bplain\b",
    ),
    "Preppy": (
        r"\bpreppy\b", r"\bprep\b", r"\bcollegiate\b", r"\bplaid\b",
        r"\btweed\b", r"\bpolo\b", r"\bargyle\b",
    ),
    "Avant Garde": (r"\bavant[\s-]*garde\b", r"\bexperimental\b", r"\basymmetric\b"),
    "Punk": (r"\bpunk\b", r"\bstudded\b", r"\bspiked\b", r"\banarchy\b"),
    "Glam": (
        r"\bglam\b", r"\bglitter\b", r"\bsequin\b", r"\bmetallic\b",
        r"\brhinestone\b", r"\bparty\s*dress\b",
    ),
    "Regency": (r"\bregency\b", r"\bbridgerton\b", r"\bempire\s*waist\b"),
    "Casual": (
        r"\bcasual\b", r"\beveryday\b", r"\brelaxed\b", r"\btunic\b",
        r"\bblouse\b", r"\btee\b", r"\bt[\s-]?shirt\b",
        r"\bjeans?\b", r"\bdenim\b",
    ),
    "Utility": (
        r"\butility\b", r"\btechwear\b", r"\bcargo\b", r"\bworkwear\b",
        r"\btactical\b",
    ),
    "Futuristic": (r"\bfuturistic\b", r"\bcyber\b", r"\bmetallic\s*foil\b"),
    "Cottage": (r"\bcottage(?:core)?\b", r"\brural\b"),
    "Fairy": (r"\bfairy(?:core)?\b", r"\bethereal\b", r"\bwhimsical\b"),
    "Kidcore": (r"\bkidcore\b", r"\bchildlike\b"),
    "Y2K": (r"\by2k\b", r"\b2000s\b", r"\blow[\s-]*rise\b"),
    "Biker": (
        r"\bbiker\b", r"\bmoto(?:rcycle)?\b", r"\bleather\s*jacket\b",
        r"\bmoto\s*jacket\b",
    ),
    "Gorpcore": (
        r"\bgorpcore\b", r"\bhiking\b", r"\bpatagonia\b",
        r"\bnorth\s*face\b",
    ),
    "Twee": (r"\btwee\b", r"\bquirky\b", r"\bkitschy\b"),
    "Coquette": (r"\bcoquette\b", r"\bballetcore\b"),
    "Whimsygoth": (r"\bwhimsygoth\b", r"\bwhimsy\s*goth\b"),
}

VALID_DEPOP_GROUPING = frozenset({"Maternity", "Petite", "Plus size", "Tall"})
VALID_DEPOP_PARCEL = (
    "Extra extra small",
    "Extra small",
    "Small",
    "Medium",
    "Large",
    "Extra large",
)


def _pad_depop_tags(values: list[str], defaults: tuple[str, ...], *, limit: int = 3) -> list[str]:
    kept: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in kept:
            kept.append(text)
        if len(kept) >= limit:
            return kept[:limit]
    for default in defaults:
        if default not in kept:
            kept.append(default)
        if len(kept) >= limit:
            break
    return kept[:limit]


def infer_depop_styles(
    listing: dict | None = None,
    *,
    ebay: dict | None = None,
    limit: int = 3,
    exclude: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Pick up to ``limit`` Depop styles from listing text (never a fixed Casual/Retro/Boho trio)."""
    listing = listing if isinstance(listing, dict) else {}
    if not isinstance(ebay, dict):
        raw = listing.get("ebay_specifics")
        ebay = raw if isinstance(raw, dict) else {}
    hay = ebay_season_haystack(listing, ebay)
    blocked = {str(item) for item in (exclude or ()) if item}
    scores: dict[str, int] = {name: 0 for name in VALID_DEPOP_STYLE}

    # Exact Depop label in the haystack is a strong signal.
    for style in VALID_DEPOP_STYLE:
        if style in blocked:
            continue
        if re.search(rf"\b{re.escape(style)}\b", hay, flags=re.I):
            scores[style] += 3

    for style, patterns in DEPOP_STYLE_CUES.items():
        if style in blocked or style not in scores:
            continue
        for pattern in patterns:
            if re.search(pattern, hay, flags=re.I):
                scores[style] += 1

    # eBay style / theme / pattern fields often carry the best single cue.
    for key in ("style", "theme", "pattern", "features", "type"):
        for part in as_list(ebay.get(key)):
            text = text_value(part)
            if not text:
                continue
            hit = canonical_option(text, VALID_DEPOP_STYLE)
            if hit and hit not in blocked:
                scores[hit] += 2
            folded = text.casefold()
            for style, patterns in DEPOP_STYLE_CUES.items():
                if style in blocked:
                    continue
                if any(re.search(pattern, folded, flags=re.I) for pattern in patterns):
                    scores[style] += 1

    ranked = sorted(
        (name for name, score in scores.items() if score > 0 and name not in blocked),
        key=lambda name: (-scores[name], name),
    )
    picked = ranked[:limit]
    if len(picked) >= limit:
        return picked
    fallbacks = tuple(name for name in DEPOP_STYLE_FALLBACKS if name not in blocked)
    return _pad_depop_tags(picked, fallbacks, limit=limit)


def _map_ebay_occasion_to_depop(value: str) -> str | None:
    folded = str(value or "").casefold()
    if not folded:
        return None
    for key, mapped in DEPOP_OCCASION_EBAY_ALIASES.items():
        if key in folded:
            return mapped
    return canonical_option(str(value), VALID_DEPOP_OCCASION)


def infer_depop_occasions(
    listing: dict | None = None,
    *,
    ebay: dict | None = None,
    limit: int = 3,
    exclude=None,
) -> list[str]:
    """Pick up to ``limit`` Depop occasions from listing text and known dropdown options."""
    listing = listing if isinstance(listing, dict) else {}
    if not isinstance(ebay, dict):
        raw = listing.get("ebay_specifics")
        ebay = raw if isinstance(raw, dict) else {}
    hay = ebay_season_haystack(listing, ebay)
    blocked = {str(item) for item in (exclude or ()) if item}
    seeded: list[str] = []
    for part in as_list(ebay.get("occasion")):
        hit = _map_ebay_occasion_to_depop(str(part))
        if hit and hit not in blocked and hit not in seeded:
            seeded.append(hit)
    type_boosts = {
        "t-shirt": ("Casual", "School", "Summer"),
        "tee": ("Casual", "School", "Summer"),
        "hoodie": ("Casual", "School"),
        "blouse": ("Casual", "Going out", "Work"),
        "dress": ("Going out", "Party", "Special Occasion"),
        "leggings": ("Workout", "Casual"),
        "joggers": ("Workout", "Casual"),
        "shorts": ("Casual", "Summer", "Vacation"),
        "parka": ("Winter", "Outdoors"),
        "coat": ("Winter", "Work"),
        "puffer": ("Winter", "Outdoors"),
    }
    type_hay = " ".join(text_value(part) for key in ("type", "features", "style") for part in as_list(ebay.get(key))).casefold()
    for needle, boosts in type_boosts.items():
        if needle in type_hay:
            for occasion in boosts:
                if occasion not in blocked and occasion not in seeded:
                    seeded.append(occasion)
    season = text_value(ebay.get("season"))
    if season in {"Summer", "Winter"} and season not in blocked and season not in seeded:
        seeded.append(season)
    depop = listing.get("depop_specifics") if isinstance(listing.get("depop_specifics"), dict) else {}
    style_boosts = {
        "Streetwear": ("Casual", "Going out"),
        "Sportswear": ("Workout", "Casual"),
        "Loungewear": ("Relaxation", "Casual"),
        "Rave": ("Festival", "Party"),
        "Gorpcore": ("Outdoors", "Vacation"),
        "Preppy": ("School", "Work"),
    }
    for style in as_list(depop.get("style")):
        for occasion in style_boosts.get(str(style), ()):
            if occasion not in blocked and occasion not in seeded:
                seeded.append(occasion)
    scored = infer_known_options(
        hay, VALID_DEPOP_OCCASION, cues=DEPOP_OCCASION_CUES,
        fallbacks=DEPOP_OCCASION_FALLBACKS, limit=limit, exclude=blocked,
    )
    return _pad_depop_tags(seeded, tuple(scored) + DEPOP_OCCASION_FALLBACKS, limit=limit)


def _infer_depop_parcel_size(listing: dict) -> str:
    """Depop's parcel tier is priced by weight — empty when the listing has no weight."""
    try:
        lb = float(listing.get("weight_lb") or 0)
        oz = float(listing.get("weight_oz") or 0)
    except (TypeError, ValueError):
        lb, oz = 0.0, 0.0
    total_oz = max(0.0, lb * 16 + oz)
    if total_oz <= 0:
        return ""
    if total_oz < 4:
        return "Extra extra small"
    if total_oz < 8:
        return "Extra small"
    if total_oz < 12:
        return "Small"
    if total_oz < 16:
        return "Medium"
    if total_oz < 32:
        return "Large"
    return "Extra large"


def ensure_depop_category_optionals(listing: dict) -> bool:
    """Fill Depop Show-Optional-Fields rows; omit size grouping for Regular."""
    if not isinstance(listing, dict):
        return False
    raw = listing.get("depop_specifics")
    if not isinstance(raw, dict):
        return False
    depop = dict(raw)
    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    changed = False
    hay = ebay_season_haystack(listing, ebay)

    def set_key(key: str, value: Any) -> None:
        nonlocal depop, changed
        current = depop.get(key)
        if key in {"style", "occasion", "material"}:
            if as_list(current):
                return
        elif not ebay_optional_blank(current):
            return
        depop[key] = value
        changed = True

    if ebay_optional_blank(depop.get("source")):
        source = "Vintage" if re.search(r"\b(?:vintage|deadstock|y2k|90s|80s|70s)\b", hay) else "Preloved"
        set_key("source", source)

    if ebay_optional_blank(depop.get("age")):
        age = "Modern"
        for decade in ("50s", "60s", "70s", "80s", "90s"):
            if re.search(rf"\b{decade}\b", hay):
                age = decade
                break
        if age == "Modern" and re.search(r"\by2k\b", hay):
            age = "y2k"
        set_key("age", age)

    from vendoo_studio.services.seller_overrides import is_locked

    styles = as_list(depop.get("style"))
    mapped: list[str] = []
    for style in styles:
        hit = canonical_option(style, VALID_DEPOP_STYLE)
        if hit and hit not in mapped:
            mapped.append(hit)
    # A seller who cleared or chose style owns that list, including fewer than three.
    if not is_locked(listing, "depop_specifics.style") and len(mapped) < 3:
        inferred = infer_depop_styles(listing, ebay=ebay, limit=3, exclude=mapped)
        padded = _pad_depop_tags(mapped, tuple(inferred) + DEPOP_STYLE_FALLBACKS, limit=3)
        if padded != styles:
            depop["style"] = padded
            changed = True

    working = {**listing, "depop_specifics": depop}
    occasions = as_list(depop.get("occasion"))
    occasion_hits = [canonical_option(occasion, VALID_DEPOP_OCCASION) for occasion in occasions]
    if not occasions:
        mapped = []
        for occasion in as_list(ebay.get("occasion") if isinstance(ebay, dict) else None):
            hit = _map_ebay_occasion_to_depop(str(occasion))
            if hit and hit not in mapped:
                mapped.append(hit)
        inferred = infer_depop_occasions(working, ebay=ebay, limit=3, exclude=mapped)
        set_key("occasion", _pad_depop_tags(mapped, tuple(inferred) + DEPOP_OCCASION_FALLBACKS, limit=3))
    elif all(occasion_hits) and len({hit for hit in occasion_hits if hit}) < 3:
        mapped = []
        for hit in occasion_hits:
            if hit and hit not in mapped:
                mapped.append(hit)
        inferred = infer_depop_occasions(working, ebay=ebay, limit=3, exclude=mapped)
        padded = _pad_depop_tags(mapped, tuple(inferred) + DEPOP_OCCASION_FALLBACKS, limit=3)
        if padded != occasions:
            depop["occasion"] = padded
            changed = True

    # Depop prices the parcel by weight, so the weight on the listing wins over any
    # tier the model picked on its own.
    parcel = text_value(depop.get("parcelSize") or depop.get("parcel_size"))
    from_weight = _infer_depop_parcel_size(listing)
    if from_weight:
        if parcel != from_weight or "parcel_size" in depop:
            depop["parcelSize"] = from_weight
            depop.pop("parcel_size", None)
            changed = True
    elif not parcel:
        set_key("parcelSize", "Medium")
        depop.pop("parcel_size", None)

    size_type = text_value(listing.get("sizeType") or ebay.get("sizeType")).casefold()
    grouping = text_value(depop.get("sizeGrouping") or depop.get("size_grouping"))
    wanted = {
        "petite": "Petite",
        "plus": "Plus size",
        "plus size": "Plus size",
        "tall": "Tall",
        "maternity": "Maternity",
    }.get(size_type)
    if wanted and grouping != wanted:
        depop["sizeGrouping"] = wanted
        depop.pop("size_grouping", None)
        changed = True

    # Studio's own section builder writes Depop shipping from parcelSize. A
    # model's copy of Depop's shipping form is read by nothing, so it goes.
    for key in DEPOP_UNREAD_KEYS:
        if key in depop:
            depop.pop(key)
            changed = True

    if changed:
        listing["depop_specifics"] = depop
    return changed


def depop_category_id(listing: dict) -> str:
    """Depop's own category id for the listing (``kidswear_jackets``), read the way the extension reads it."""
    direct = listing.get("direct_category_ids")
    specifics = listing.get("depop_specifics")
    ids = listing.get("marketplace_category_ids")
    for source in (direct, specifics, ids):
        if not isinstance(source, dict):
            continue
        value = source.get("categoryId") if source is specifics else source.get("depop")
        if text_value(value):
            return text_value(value)
    return ""


def depop_is_kids(listing: dict) -> bool:
    category = depop_category_id(listing)
    if category:
        return category.startswith("kidswear_")
    paths = listing.get("marketplace_categories")
    path = text_value(paths.get("depop")) if isinstance(paths, dict) else ""
    return path.casefold().startswith("kid")


# Depop sizes kids' clothes by age. A US youth letter is the lower age it is
# made for: XS 4/5, S 6/7, M 10/12, L 14/16, and XL 18/20 is past Depop's
# oldest, 16 years.
_KIDS_LETTER_AGES = {"xxs": 3, "xs": 4, "s": 6, "m": 10, "l": 14, "xl": 16, "xxl": 16, "2xl": 16, "xxxl": 16, "3xl": 16}
_KIDS_MONTHS = ((0, "0-3 months"), (3, "3-6 months"), (6, "6-9 months"), (9, "9-12 months"),
                (12, "12-18 months"), (18, "18-24 months"))
DEPOP_KIDS_OLDEST = 16


def _kids_age_label(age: int) -> str:
    return f"{min(age, DEPOP_KIDS_OLDEST)} years"


def depop_kids_size(size: Any) -> str:
    """A kids' tag size as the age Depop lists it under; anything else unchanged.

    ``XL (18/20)`` and ``18/20`` are 16 years, ``M`` is 10 years, ``4T`` is
    4 years, ``12M`` is 12-18 months. A number on the tag wins over its letter.
    """
    text = text_value(size)
    folded = text.casefold()
    if folded in {"os", "one size", "onesize"}:
        return "One size"
    months = re.fullmatch(r"(\d{1,2})\s*(?:-\s*\d{1,2}\s*)?(?:m|mo|mos|months?)", folded)
    if months:
        start = int(months.group(1))
        if start >= 24:
            return _kids_age_label(start // 12)
        return next(label for floor, label in reversed(_KIDS_MONTHS) if start >= floor)
    numbers = [int(number) for number in re.findall(r"\d{1,2}", folded)]
    if numbers and re.fullmatch(r"[a-z\s]*\(?\s*\d{1,2}\s*t?\s*(?:[/-]\s*\d{1,2}\s*)?\)?\s*(?:t|years?|yrs?|y)?", folded):
        age = min(numbers)
        return _kids_age_label(age) if age >= 2 else text
    letter = _KIDS_LETTER_AGES.get(re.sub(r"[\s.]+", "", folded))
    return _kids_age_label(letter) if letter else text


def depop_size_for(size: Any, listing: dict) -> str:
    """The general tag size as Depop's size for this listing's category.

    Petite tags keep the letter Depop lists (PM is M) and the grouping is Petite.
    """
    if depop_is_kids(listing):
        return depop_kids_size(size)
    text = text_value(size)
    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    size_type = text_value(listing.get("sizeType") or ebay.get("sizeType")).casefold()
    petite = re.fullmatch(r"p([sml])", text, re.I)
    if size_type in {"petite", "petites"} and petite:
        return petite.group(1).upper()
    return text


def depop_offers_sizes(listing: dict) -> bool:
    """Depop's category has real sizes, not only Other and One size.

    Read from the category's cached schema; a clothing department with no
    schema cached yet is taken to be sized.
    """
    category = depop_category_id(listing)
    if not category:
        return False
    from vendoo_studio.services.category_fields import load_fields

    try:
        specs = load_fields("depop", category)
    except Exception:  # noqa: BLE001 - no cached schema reads as unknown
        specs = None
    spec = (specs or {}).get("size")
    if spec is None:
        return category.split("_", 1)[0] in {"kidswear", "menswear", "womenswear"}
    return any(label.strip().casefold() not in {"other", "one size"} for label in spec.options.values())
