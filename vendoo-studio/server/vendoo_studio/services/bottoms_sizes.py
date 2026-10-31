"""Bottoms sizes as each marketplace lists them.

The general size for men's bottoms stays the waist. The inseam and the size
each marketplace actually accepts live on that marketplace's form. A
marketplace whose form has no size or size type field (Sellwild has neither,
Vinted has no size type) gets none.
"""

from __future__ import annotations

import re
from typing import Any

_BOTTOMS_RE = re.compile(
    r"(?i)\b(jeans|pants|trousers|chinos|slacks|shorts|joggers|sweatpants|"
    r"cargos|corduroys|leggings|skirts|culottes|capris|khakis|dungarees)\b"
)
_WOMEN_RE = re.compile(r"(?i)\b(women|ladies|female|womenswear)\b")
_MEN_RE = re.compile(r"(?i)\b(men|male|menswear)\b")
_PAIR_RE = re.compile(r"(?i)^(?:w(?:aist)?\s*)?(\d{2})\s*[x×/]\s*(?:l(?:ength)?\s*)?(\d{2})$")
_PAIR_IN_TEXT = re.compile(r"(?i)(?<![\dx])(\d{2})\s*[x×/]\s*(\d{2})(?![\dx])")
_W_L_RE = re.compile(r"(?i)^w(?:aist)?\s*(\d{2})\s*l(?:ength)?\s*(\d{2})$")
_WAIST_LABEL_RE = re.compile(r"(?i)^waist\s*(\d{2})$")
_TAG_RE = re.compile(r"(?i)^(?:us\s*)?(\d{1,2}|00)\s*([pwt])?$")
_INSEAM_RE = re.compile(r"(?i)\binseam\b\s*[:=]?\s*(\d{1,2})")

WOMENS_NUMERIC = ["00", "0", "2", "4", "6", "8", "10", "12", "14", "16", "18", "20", "22", "24"]
EBAY_WOMENS_SIZES = [*WOMENS_NUMERIC, *[f"{number}P" for number in WOMENS_NUMERIC]]
# eBay men's jeans list the waist by itself. Inseam is its own aspect.
EBAY_WAISTS = [str(n) for n in range(26, 61)]
EBAY_INSEAMS = [str(n) for n in range(26, 37)]
EBAY_SIZE_TYPES = ["Regular", "Petites", "Plus", "Tall", "Big & Tall", "Maternity"]
# Poshmark's live jeans catalog (catalog_v2, read 2026-10-25).
POSHMARK_WOMEN = [
    "23", "24", "25", "26", "27", "28", "29", "30", "31", "00", "0", "2", "4", "6", "8", "10", "12",
    "32", "33", "34", "14", "14W", "16", "16W", "18", "18W", "20", "20W", "22", "22W", "24", "24W",
    "26", "26W", "28", "28W", "30", "30W", "32W",
    "23P", "24P", "25P", "26P", "27P", "28P", "29P", "30P", "31P", "32P", "33P", "34P",
    "00P", "0P", "2P", "4P", "6P", "8P", "10P", "12P", "14P", "16P", "18P",
    "1", "3", "5", "7", "9", "11", "13", "15", "17",
]
POSHMARK_MEN = [f"Waist {n}" for n in list(range(28, 42)) + [42, 43, 44, 46, 48, 50, 52, 54, 56, 58, 60]]
POSHMARK_SIZE_TYPES = ["Regular", "Petite", "Plus", "Juniors", "Maternity", "Big & Tall"]
MERCARI_WAISTS = [str(n) for n in range(26, 51)]
MERCARI_SIZE_TYPES = ["Regular", "Petite", "Plus", "Tall"]
# Depop's US bottoms sets are the number. Petite, plus and tall are the size grouping.
DEPOP_WOMEN = [*WOMENS_NUMERIC, "Other"]
DEPOP_MEN = [str(n) for n in range(26, 45)] + ["Other"]
DEPOP_GROUPING = ["Petite", "Plus size", "Tall", "Maternity"]
ETSY_WAISTS = [str(n) for n in range(26, 51)]
ETSY_COMBINED = [f"{waist}x{inseam}" for waist in range(28, 45) for inseam in (28, 30, 32, 34)]
# Vinted's US women's buttons read "S / 8". Men's jeans buttons are the waist.
VINTED_WOMEN = {
    "00": "XXXS", "0": "XXS / 0", "2": "XS / 2", "4": "XS / 4", "6": "S / 6", "8": "S / 8",
    "10": "M / 10", "12": "M / 12", "14": "L / 14", "16": "L / 16", "18": "XL / 18",
    "20": "XL / 20", "22": "XXL / 22", "24": "XXL / 24",
}
VINTED_MEN = [str(n) for n in range(26, 51)] + [f"W{n}" for n in range(26, 51)]

_FIT = {
    "p": "Petite", "petite": "Petite", "petites": "Petites",
    "w": "Plus", "plus": "Plus", "plus size": "Plus",
    "t": "Tall", "tall": "Tall",
    "big & tall": "Big & Tall", "big and tall": "Big & Tall",
    "maternity": "Maternity", "juniors": "Juniors", "junior": "Juniors",
    "regular": "Regular", "standard": "Regular",
}


def is_bottoms(listing: dict | None) -> bool:
    if not isinstance(listing, dict):
        return False
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    parts = [
        listing.get("category_path"), listing.get("title"), listing.get("department"),
        ebay.get("type"), ebay.get("department"),
    ]
    categories = listing.get("marketplace_categories")
    if isinstance(categories, dict):
        parts.extend(categories.values())
    return bool(_BOTTOMS_RE.search(" ".join(str(part or "") for part in parts)))


def department_kind(listing: dict) -> str:
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    text = " ".join(str(part or "") for part in (
        listing.get("department"), ebay.get("department"), listing.get("category_path"), listing.get("title"),
    ))
    if _WOMEN_RE.search(text):
        return "women"
    if _MEN_RE.search(text):
        return "men"
    return ""


def _fit_name(raw: str) -> str:
    return _FIT.get(raw.strip().casefold(), "")


def _parse(listing: dict) -> dict[str, str]:
    """Waist, inseam, women's number and size type named by the draft."""
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    size = str(listing.get("size") or listing.get("size_us") or "").strip()
    fit = _fit_name(str(listing.get("sizeType") or ebay.get("sizeType") or ""))
    waist = inseam = numeric = ""
    pair = _PAIR_RE.match(size.replace("×", "x")) or _W_L_RE.match(size)
    label = _WAIST_LABEL_RE.match(size)
    tag = _TAG_RE.match(size)
    if pair:
        waist, inseam = pair.group(1), pair.group(2)
        if inseam == waist:
            inseam = ""
    elif label:
        waist = label.group(1)
    elif tag:
        number, suffix = tag.group(1), (tag.group(2) or "").casefold()
        if suffix:
            fit = fit or _fit_name(suffix)
        kind = department_kind(listing)
        if kind == "women" and (number in WOMENS_NUMERIC or suffix):
            numeric = number
        elif kind != "women" and len(number) == 2 and int(number) >= 26:
            waist = number
        elif kind == "women" and number.isdigit() and int(number) >= 23:
            waist = number
        elif number in WOMENS_NUMERIC:
            numeric = number
    if not inseam and waist:
        stored = re.match(r"^\s*(\d{1,2})", str(ebay.get("inseam") or ""))
        if stored and stored.group(1) != waist:
            inseam = stored.group(1)
        else:
            titled = _PAIR_IN_TEXT.search(str(listing.get("title") or "").replace("×", "x"))
            if titled and titled.group(1) == waist and titled.group(2) != waist:
                inseam = titled.group(2)
            else:
                measured = _INSEAM_RE.search(str(listing.get("description") or ""))
                if measured and measured.group(1) != waist:
                    inseam = measured.group(1)
    if fit == "Petites":
        fit = "Petite"
    combined = f"{waist}x{inseam}" if waist and inseam else ""
    return {"waist": waist, "inseam": inseam, "numeric": numeric, "fit": fit, "combined": combined}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold().replace("×", "x")


def _follows(current: Any, listing: dict, parsed: dict[str, str]) -> bool:
    text = str(current or "").strip()
    if not text:
        return True
    candidates = {
        str(listing.get("size") or "").strip(), parsed["waist"], parsed["numeric"], parsed["combined"],
    }
    if parsed["waist"] and parsed["inseam"]:
        candidates.add(f"{parsed['waist']} x {parsed['inseam']}")
    return _norm(text) in {_norm(item) for item in candidates if item}


def _write(listing: dict, path: str, value: str, *, parsed: dict[str, str], follow: bool) -> bool:
    if not value:
        return False
    from vendoo_studio.services.seller_overrides import get_path, is_locked, set_path

    if is_locked(listing, path):
        return False
    current = get_path(listing, path)
    if str(current or "").strip() == value:
        return False
    if current not in (None, "", []) and not (follow and _follows(current, listing, parsed)):
        return False
    set_path(listing, path, value)
    return True


def _poshmark_size(parsed: dict[str, str], kind: str) -> str:
    fit = parsed["fit"]
    if kind == "men" and parsed["waist"]:
        label = f"Waist {parsed['waist']}"
        return label if label in POSHMARK_MEN else parsed["waist"]
    if kind == "women":
        number = parsed["numeric"] or parsed["waist"]
        if not number:
            return ""
        if fit == "Petite":
            petite = f"{number}P"
            return petite if petite in POSHMARK_WOMEN else number
        if fit == "Plus" and f"{number}W" in POSHMARK_WOMEN and number not in WOMENS_NUMERIC:
            return f"{number}W"
        return number
    return parsed["waist"] or parsed["numeric"]


def _targets(listing: dict, parsed: dict[str, str]) -> dict[str, str]:
    kind = department_kind(listing) or ("men" if parsed["waist"] else "women" if parsed["numeric"] else "")
    fit = parsed["fit"]
    targets: dict[str, str] = {}
    if parsed["inseam"]:
        targets["ebay_specifics.inseam"] = parsed["inseam"]
        targets["etsy_specifics.inseam"] = parsed["inseam"]
    ebay_size = parsed["numeric"] if kind == "women" and parsed["numeric"] else parsed["waist"]
    if ebay_size:
        targets["ebay_specifics.size"] = ebay_size
    if fit:
        targets["ebay_specifics.sizeType"] = "Petites" if fit == "Petite" else fit
        targets["poshmark_specifics.sizeType"] = "Petite" if fit == "Petite" else fit
        targets["mercari_specifics.sizeType"] = fit if fit in {"Petite", "Plus", "Tall"} else ""
        if not str(listing.get("sizeType") or "").strip():
            targets["sizeType"] = "Petites" if fit == "Petite" else fit
    poshmark = _poshmark_size(parsed, kind)
    if poshmark:
        targets["poshmark_specifics.size"] = poshmark
    mercari = parsed["numeric"] if kind == "women" and parsed["numeric"] else parsed["waist"]
    if mercari:
        targets["mercari_specifics.size"] = mercari
    depop = parsed["numeric"] if kind == "women" and parsed["numeric"] else parsed["waist"]
    if depop:
        # Depop's own list writes a man's waist with inches (36") and a kid's as an age.
        from vendoo_studio.models.depop_fields import depop_size_for

        targets["depop_specifics.size"] = depop_size_for(depop, listing)
    grouping = {"Petite": "Petite", "Plus": "Plus size", "Tall": "Tall", "Big & Tall": "Tall", "Maternity": "Maternity"}.get(fit, "")
    if grouping:
        targets["depop_specifics.sizeGrouping"] = grouping
    etsy = parsed["numeric"] if kind == "women" and parsed["numeric"] else parsed["waist"]
    if etsy:
        targets["etsy_specifics.size"] = etsy
    if kind == "women" and parsed["numeric"] in VINTED_WOMEN:
        targets["vinted_specifics.size"] = VINTED_WOMEN[parsed["numeric"]]
    elif parsed["waist"]:
        targets["vinted_specifics.size"] = parsed["waist"]
    return {path: value for path, value in targets.items() if value}


def apply_bottoms_sizes(listing: dict) -> bool:
    """Fill each marketplace's bottoms size from the draft. A size the seller set stays."""
    if not is_bottoms(listing):
        return False
    parsed = _parse(listing)
    if not any(parsed.values()):
        return False
    changed = False
    for path, value in _targets(listing, parsed).items():
        follow = path.endswith(".size") or path == "size"
        if _write(listing, path, value, parsed=parsed, follow=follow):
            changed = True
    return changed


def bottoms_options(listing: dict | None, marketplace: str) -> dict[str, list[str]]:
    """The size choices to show for a bottoms listing, or nothing for other items."""
    if not is_bottoms(listing):
        return {}
    listing = listing or {}
    kind = department_kind(listing)
    women = kind != "men"
    men = kind != "women"
    if marketplace == "ebay":
        sizes = (EBAY_WOMENS_SIZES if women else []) + (EBAY_WAISTS if men else [])
        return {"size": sizes, "sizeType": EBAY_SIZE_TYPES, "inseam": EBAY_INSEAMS}
    if marketplace == "poshmark":
        sizes = (POSHMARK_WOMEN if women else []) + (POSHMARK_MEN if men else [])
        return {"size": sizes, "sizeType": POSHMARK_SIZE_TYPES}
    if marketplace == "mercari":
        sizes = (WOMENS_NUMERIC if women else []) + (MERCARI_WAISTS if men else [])
        return {"size": sizes, "sizeType": MERCARI_SIZE_TYPES}
    if marketplace == "depop":
        sizes = (DEPOP_WOMEN if women else []) + (DEPOP_MEN if men else [])
        return {"size": sizes, "sizeGrouping": DEPOP_GROUPING}
    if marketplace == "etsy":
        sizes = (WOMENS_NUMERIC if women else []) + (ETSY_WAISTS if men else []) + (ETSY_COMBINED if men else [])
        return {"size": sizes, "inseam": EBAY_INSEAMS, "sizeType": ["Regular", "Petite", "Plus", "Tall"]}
    if marketplace == "vinted":
        sizes = (list(VINTED_WOMEN.values()) if women else []) + (VINTED_MEN if men else [])
        return {"size": sizes}
    return {}
