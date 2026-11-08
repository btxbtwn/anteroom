"""Every General size, condition, color and a broad set of categories, through
each marketplace's production mapping, checked against that marketplace's own
option lists as captured from its live forms (``skills/list-this/references/``).

An answer is either one of the marketplace's options (an id with the label it
belongs to) or an explicit "no equivalent": an empty answer the seller sees and
fills. Never free text in a choice-only row, never a number read as an id, and
never another option when the list spells the value exactly.

This file covers what the server maps: the eBay and Etsy sends, the Poshmark,
Mercari and Depop editor rows, and every marketplace's category match. What the
extension sends to Poshmark, Mercari and Depop is checked by
``vendoo-extension/tests/marketplace-value-matrix.test.js`` with the same
families (``vendoo-extension/tests/fixtures/marketplace-value-matrix.json``).

Known gaps are listed one value per entry and can only shrink: a fixed value
fails here until its entry is removed. Each historical regression is pinned by
name. A refreshed form sweep updates the captured lists this test reads.
"""
from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

from vendoo_studio.config import skills_dir
from vendoo_studio.models.schema import VALID_CONDITIONS
from vendoo_studio.services import depop_form, ebay_listings, etsy_listings, listing_generate, mercari_form, poshmark_form
from vendoo_studio.services.direct_categories import pick_etsy_leaf
from vendoo_studio.services.native_category_match import department, match_row, native_rows
from vendoo_studio.services.native_contract import shared_answer, value_error
from vendoo_studio.services.registry import map_vendoo_category_path

ROOT = Path(__file__).resolve().parents[3]
REFERENCES = skills_dir() / "list-this" / "references"
MATRIX = json.loads((ROOT / "vendoo-extension/tests/fixtures/marketplace-value-matrix.json").read_text())
DROPDOWNS = json.loads((REFERENCES / "vendoo-dropdown-options.json").read_text())["forms"]
GENERAL = DROPDOWNS["vendoo"]
APPAREL = "Clothing, Shoes & Accessories"


def _tailored(size: str) -> bool:
    return bool(re.match(r"(IT|EU|FR) ", size))


SIZE_GROUPS = {
    **MATRIX["size_groups"],
    "general_letters": [size for size in GENERAL["usSize"] if not size.isdigit() and not _tailored(size)],
    "general_numbers": [size for size in GENERAL["usSize"] if size.isdigit()],
    "general_tailored": [size for size in GENERAL["usSize"] if _tailored(size)],
}
FAMILIES = MATRIX["families"]


def family_sizes(family: dict) -> list[str]:
    return list(dict.fromkeys(size for group in family["sizes"] for size in SIZE_GROUPS[group]))


def short(path: str) -> str:
    return " > ".join(path.split(" > ")[-2:])


def same(left, right) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


@lru_cache(maxsize=1)
def _seed() -> dict:
    return json.loads(gzip.open(ROOT / "vendoo-studio/data/category-trees-seed.json.gz").read())


@lru_cache(maxsize=1)
def apparel_leaves() -> tuple[str, ...]:
    """Every General apparel leaf: clothing, shoes and accessories for every department."""
    return tuple(node["path"] for node in _seed()["category_tree_nodes"]
                 if node["marketplace"] == "general" and node.get("is_leaf") and node["path"].startswith(APPAREL))


@lru_cache(maxsize=1)
def etsy_leaves() -> tuple[dict, ...]:
    return tuple({"id": node["category_id"], "path": node["path"]} for node in _seed()["category_tree_nodes"]
                 if node["marketplace"] == "etsy" and node.get("is_leaf"))


def compare_gaps(found: dict[str, str], known: dict[str, str], what: str) -> None:
    unexpected = {key: value for key, value in found.items() if key not in known}
    fixed = sorted(key for key in known if key not in found)
    assert not unexpected, f"New {what} mismatches: {unexpected}"
    assert not fixed, f"Fixed {what} gaps; remove them from the known gaps: {fixed}"


def test_every_general_size_is_in_a_matrix_family():
    covered = {size for family in FAMILIES for size in family_sizes(family)}
    assert [size for size in GENERAL["usSize"] if size not in covered] == []
    assert set(GENERAL["condition"]) == set(VALID_CONDITIONS)


# --- Categories ---------------------------------------------------------------

@pytest.mark.parametrize("market", ["poshmark", "mercari", "depop"])
def test_family_categories_resolve_to_the_captured_native_rows(market):
    rows = native_rows()[market]
    for family in FAMILIES:
        hit = match_row(rows, family["general"])
        expected = family["native"][market]
        assert (hit and {"id": hit["id"], "path": hit["path"]}) == (expected or None), short(family["general"])


# A department's apparel lands in the marketplace's apparel branches or nowhere
# (the seller picks). Department-free care items, like garment bags, may land in Home.
APPAREL_ROOTS = {
    "poshmark": {"women", "men", "kids"},
    "depop": {"women", "men", "kids", "everything else"},
    "mercari": {"women", "men", "kids", "vintage & collectibles", "sports & outdoors"},
}
# Apparel the matcher puts outside apparel. A department's clothing needs a
# branch that names something it says too, so kids' sets no longer land in
# Fragrance or gloves in Cleaning. The closest-match fallback is checked in
# test_closest_category.py.
KNOWN_CATEGORY_GAPS: dict[str, dict[str, str]] = {"mercari": {}, "poshmark": {}, "depop": {}}


@pytest.mark.parametrize("market", ["poshmark", "mercari", "depop"])
def test_every_apparel_category_lands_in_apparel_or_nowhere(market):
    rows = native_rows()[market]
    ids = {row["id"] for row in rows}
    found = {}
    for path in apparel_leaves():
        hit = match_row(rows, path)
        if hit is None:
            continue
        assert hit["id"] in ids
        native = hit["path"]
        general = path.split(" > ", 1)[1]
        if department(path) and department(native) and department(path) != department(native):
            found[general] = native
        elif department(path) and native.split(" > ")[0].casefold() not in APPAREL_ROOTS[market]:
            found[general] = native
    compare_gaps(found, KNOWN_CATEGORY_GAPS[market], f"{market} category")


# Etsy's category is read from its taxonomy by ``pick_etsy_leaf`` when the General
# category changes. General's root words choose nothing, and a pet or wedding
# leaf needs the category to say so, so no family lands in Pet Supplies.
ETSY_FAMILY_GAPS: dict[str, str] = {}
ETSY_ROOTS = {"Clothing", "Shoes", "Accessories"}


@pytest.mark.parametrize("family", FAMILIES, ids=[short(family["general"]) for family in FAMILIES])
def test_etsy_category_for_each_family_stays_in_apparel(family, request):
    name = short(family["general"])
    if name in ETSY_FAMILY_GAPS:
        request.applymarker(pytest.mark.xfail(strict=True, reason=f"Etsy picks {ETSY_FAMILY_GAPS[name]}"))
    hit = pick_etsy_leaf(list(etsy_leaves()), family["general"])
    assert hit is None or hit["path"].split(" > ")[0] in ETSY_ROOTS, hit


def test_category_regressions():
    rows = native_rows()
    tee = f"{APPAREL} > Men > Men's Clothing > Shirts > T-Shirts"
    # #586 / #295: a short-sleeve tee is a tee, never shorts or a tank top.
    assert match_row(rows["poshmark"], tee, "Nike short sleeve tee")["path"] == "Men > Shirts > Tees - Short Sleeve"
    for market in ("depop", "mercari"):
        assert match_row(rows[market], tee, "short sleeve")["path"].casefold() == "men > tops > t-shirts"
    # #454: a cropped jacket is a jacket, not a crop top.
    jacket = f"{APPAREL} > Women > Women's Clothing > Coats, Jackets & Vests"
    for market in ("poshmark", "depop", "mercari"):
        assert re.search(r"jacket", match_row(rows[market], jacket, "Cropped trucker jacket")["path"], re.I)
    # #392: a button-up blouse is not an Etsy tunic.
    blouse = pick_etsy_leaf(list(etsy_leaves()), "Clothing > Women's Clothing > Tops & Tees > Blouses")
    assert "Tunic" not in blouse["path"]
    # #501: a men's T-shirt path saved without separators is the T-shirts leaf again.
    assert map_vendoo_category_path("Clothing, Shoes & AccessoriesMen's ClothingShirtsT-Shirts", {
        "title": "Hanes XL Casual Graphic Tee Black", "department": "Men", "ebay_specifics": {"type": "T-Shirt"}}) == tee


# --- Conditions ---------------------------------------------------------------

# eBay's apparel condition ladder: the ids its condition policy gives a clothing
# leaf, in the order its form lists the captured labels.
EBAY_APPAREL_CONDITIONS = dict(zip(("1000", "1500", "1750", "2990", "3000", "3010"), DROPDOWNS["ebay"]["condition"], strict=True))


@pytest.mark.parametrize("condition", GENERAL["condition"])
def test_ebay_condition_is_one_of_its_apparel_grades(condition):
    code = ebay_listings.ebay_condition({"condition": condition})
    assert ebay_listings.CONDITION_IDS[code] in EBAY_APPAREL_CONDITIONS


@pytest.mark.parametrize("condition", GENERAL["condition"])
def test_mercari_editor_condition_is_one_of_its_five(condition):
    form = mercari_form.form({"condition": condition, "price": 20})
    row = next(row for row in form["fields"] if row["path"] == "mercari_specifics.condition")
    assert str(row["value"]) in {str(value) for value in row["option_values"].values()}
    assert value_error(row, row["value"]) is None


def test_condition_regressions():
    row = {"option_values": {label: native for native, label in EBAY_APPAREL_CONDITIONS.items()}}
    # #910: General's Pre-Owned - Good is eBay's Pre-owned - Good grade, by id.
    assert ebay_listings.CONDITION_IDS[ebay_listings.ebay_condition({"condition": "Pre-Owned - Good"})] == "3000"
    assert EBAY_APPAREL_CONDITIONS["3000"] == "Pre-owned - Good"
    # #929: eBay's id, its code and its label are one condition.
    for said in ("3000", "USED_EXCELLENT"):
        assert ebay_listings.ebay_condition({"ebay_specifics": {"condition": said}}) == "USED_EXCELLENT"
    assert shared_answer({"condition": "Pre-owned - Good"}, row, "ebay", "ebay_specifics.condition") == "3000"
    # Poor has no apparel grade of its own: it is eBay's Fair, not a free-text grade.
    assert ebay_listings.CONDITION_IDS[ebay_listings.ebay_condition({"condition": "Poor (Major flaws)"})] == "3010"
    # #370 / #385: Mercari's own condition numbers.
    assert mercari_form._condition("New With Tags/Box") == "1"
    assert mercari_form._condition("Pre-Owned - Excellent") == "3"


# --- Colors -------------------------------------------------------------------

@pytest.mark.parametrize("color", GENERAL["primaryColor"])
def test_poshmark_editor_color_is_its_own_or_left_off(color):
    form = poshmark_form.form({"primaryColor": color, "price": 20})
    row = next(row for row in form["fields"] if row["path"] == "poshmark_specifics.colors")
    assert value_error(row, row["value"]) is None, row["value"]
    if color in row["options"]:
        assert row["value"] == [color]


@pytest.mark.parametrize("color", GENERAL["primaryColor"])
def test_ebay_color_is_its_own(color):
    schema = [{"name": "Color", "multi": True, "mode": "selection", "required": False,
               "values": [{"label": label} for label in DROPDOWNS["ebay"]["color"]]}]
    aspects, problems = ebay_listings._match_aspects(schema, ebay_listings.ebay_aspects({"primaryColor": color}, {}))
    assert aspects["Color"] == [color] and color in DROPDOWNS["ebay"]["color"]
    assert problems == []


def _etsy_color_properties() -> list[dict]:
    return [{"id": index, "name": name, "multi": False, "values": [{"id": f"{name}:{label}", "label": label}
                                                                   for label in DROPDOWNS["etsy"][key]]}
            for index, (name, key) in enumerate((("Primary color", "primaryColor"), ("Secondary color", "secondaryColor")))]


@pytest.mark.parametrize("color", GENERAL["primaryColor"])
def test_etsy_color_is_its_own_or_left_off(color):
    rows = etsy_listings.property_values({"primaryColor": color, "secondaryColor": color}, _etsy_color_properties())
    for row in rows:
        assert row["value_ids"] == [f"{row['name']}:{label}" for label in row["values"]]
        assert all(label in DROPDOWNS["etsy"]["primaryColor"] for label in row["values"])
    if color in DROPDOWNS["etsy"]["primaryColor"]:
        assert [row["values"] for row in rows] == [[color], [color]]
    else:
        assert rows == []


@pytest.mark.xfail(strict=True, reason="#390: Etsy has no Multicolor; the secondary color is not promoted to "
                                       "Primary color, so a Multicolor listing sends no primary color")
def test_etsy_promotes_the_secondary_color_when_primary_has_no_equivalent():
    rows = etsy_listings.property_values({"primaryColor": "Multicolor", "secondaryColor": "Blue"}, _etsy_color_properties())
    assert {row["name"]: row["values"] for row in rows}["Primary color"] == ["Blue"]


# --- Sizes --------------------------------------------------------------------

def _listing(family: dict, market: str, size: str) -> dict:
    """A listing in this family's native category, with General's size propagated as saving does."""
    listing = {"size": size, "quantity": 1, "price": 20, "category_path": family["general"],
               "direct_category_ids": {market: family["native"][market]["id"]}, f"{market}_specifics": {}}
    listing_generate.propagate_general_size(listing)
    return listing


def _poshmark_size(listing: dict) -> tuple[str | None, list[str]]:
    form = poshmark_form.form(listing)
    row = next(row for row in form["fields"] if row["path"] == "poshmark_specifics.inventoryRows")
    item = row["value"][0]
    # The groups List can pick without a named fit; another group's same label is another size.
    usable = {"Standard", "Plus", "Big & Tall", "Baby",
              *(["Boys"] if "Boys'" in listing["category_path"] else ["Girls"] if "Girls'" in listing["category_path"] else [])}
    labels = [size["label"] for group in row["inventory_groups"] if group["label"] in usable for size in group.get("sizes") or []]
    if not item["sizeSetId"] and not item["sizeId"]:
        return None, labels
    if value_error(row, row["value"]):
        return f"invalid {item}", labels
    group = next(group for group in row["inventory_groups"] if group["id"] == item["sizeSetId"])
    return next(size["label"] for size in group["sizes"] if size["id"] == item["sizeId"]), labels


def _mercari_size(listing: dict) -> tuple[str | None, list[str]]:
    form = mercari_form.form(listing)
    row = next((row for row in form["fields"] if row["path"] == "mercari_specifics.nativeSizeId"), None)
    if row is None:
        return None, []
    labels = list(row["option_values"])
    if row["value"] == "":
        return None, labels
    label = next((label for label, native in row["option_values"].items() if native == row["value"]), None)
    return label or f"invalid id {row['value']}", labels


def _depop_size(listing: dict) -> tuple[str | None, list[str]]:
    form = depop_form.form(listing)
    row = next((row for row in form["fields"] if row["path"] == "depop_specifics.size"), None)
    if row is None:
        return None, []
    if row["value"] == "":
        return None, row["options"]
    return (row["value"] if row["value"] in row["options"] else f"free text {row['value']}"), row["options"]


EDITOR_SIZES = {"poshmark": _poshmark_size, "mercari": _mercari_size, "depop": _depop_size}

# The Depop editor shows a size its category does not list as the tag wrote it,
# not as an empty row. Nothing invalid is sent (List stops on "a size Depop
# offers for this category"), and the row reads as a refused choice. Emptying it
# instead changes what a saved form writes back, so it is left for its own change.
# Each family lists the sizes it shows that way.
_NOT_ON_DEPOP = ["7XL", "8XL", "S/M", "M/L", "L/XL", "XS/S", "Big 1X", "Big 2X", "Big 3X", "Big 4X", "Big 5X", "Big 6X",
                 "ST", "MT", "LT", "XLT", "2XLT", "3XLT", "4XLT", "5XLT", "6XLT"]
DEPOP_SIZE_GAPS = {
    "Shirts > T-Shirts": _NOT_ON_DEPOP,
    "Shirts > Casual Button-Down Shirts": [*_NOT_ON_DEPOP, "14.5", "15.5", "16 34/35"],
    "Men's Clothing > Coats, Jackets & Vests": _NOT_ON_DEPOP,
    "Men's Clothing > Jeans": [*_NOT_ON_DEPOP, "62", "32x30", "W32 L30", "34x32"],
    "Men's Clothing > Shorts": [*_NOT_ON_DEPOP, "62"],
    "Men's Clothing > Suits & Suit Separates": [*SIZE_GROUPS["general_tailored"], "38R", "40", "40R", "42L"],
    "Women's Clothing > Tops": _NOT_ON_DEPOP,
    "Women's Clothing > Dresses": _NOT_ON_DEPOP,
    "Women's Clothing > Jeans": ["00"],
    "Girls' Clothing (Sizes 4 & Up) > Dresses": ["6X"],
    "Men's Shoes > Athletic Shoes": ["5.5"],
}
KNOWN_SIZE_GAPS = {
    "poshmark": {},
    "mercari": {},
    "depop": {f"{family} | {size}": "shown as typed; Depop's list has no such size"
              for family, sizes in DEPOP_SIZE_GAPS.items() for size in sizes},
}


@pytest.mark.parametrize("market", ["poshmark", "mercari", "depop"])
def test_every_general_size_is_an_editor_option_or_empty(market):
    found = {}
    for family in FAMILIES:
        if not family["native"][market]:
            continue
        for size in family_sizes(family):
            landed, labels = EDITOR_SIZES[market](_listing(family, market, size))
            key = f"{short(family['general'])} | {size}"
            if landed is not None and landed.startswith(("invalid", "free text")):
                found[key] = landed
            elif sum(same(label, size) for label in labels) == 1 and not same(landed, size):
                # The list spells this size exactly, so no other option (or none) will do.
                found[key] = f"{landed}, though the list has {size}"
    compare_gaps(found, KNOWN_SIZE_GAPS[market], f"{market} size")


@pytest.mark.parametrize("size", GENERAL["usSize"])
def test_ebay_size_is_its_own_spelling_or_the_tag(size, monkeypatch):
    """eBay's Size takes free text; a spelling it lists is used when it is the same size."""
    monkeypatch.setattr(listing_generate, "ebay_size_choices", lambda listing, block=None: DROPDOWNS["ebay"]["size"])
    listing = {"size": size, "ebay_specifics": {}}
    listing_generate.propagate_general_size(listing)
    sent = listing["ebay_specifics"]["size"]
    assert sent in DROPDOWNS["ebay"]["size"] or sent == size


def test_size_regressions(monkeypatch):
    by_name = {short(family["general"]): family for family in FAMILIES}
    jeans, womens_jeans = by_name["Men's Clothing > Jeans"], by_name["Women's Clothing > Jeans"]
    # #981: Depop lists a man's 36 waist as 36".
    assert _depop_size(_listing(jeans, "depop", "36"))[0] == '36"'
    # #624: a women's 28 stays 28 where Depop offers both 28 and 28".
    assert _depop_size(_listing(womens_jeans, "depop", "28"))[0] == "28"
    # #622: a bare number is a size label, never read as an option id.
    for size in ("3", "15", "17"):
        landed = _depop_size(_listing(womens_jeans, "depop", size))[0]
        assert landed in {size, f"free text {size}"}
    # #689: a tag's "Large (12/14)" is the general L.
    assert _poshmark_size(_listing(by_name["Women's Clothing > Tops"], "poshmark", "Large (12/14)"))[0] == "L"
    # #623: a bare waist is the waist size.
    assert _poshmark_size(_listing(jeans, "poshmark", "32"))[0] == "Waist 32"
    # #1183: the Poshmark size group is its id, never the group's label.
    form = poshmark_form.form(_listing(jeans, "poshmark", "32"))
    item = next(row for row in form["fields"] if row["path"] == "poshmark_specifics.inventoryRows")["value"][0]
    assert re.fullmatch(r"[0-9a-f]{24}", item["sizeSetId"])
    # #767: XXL and 2XL are one size, spelled eBay's way.
    monkeypatch.setattr(listing_generate, "ebay_size_choices", lambda listing, block=None: DROPDOWNS["ebay"]["size"])
    listing = {"size": "XXL", "ebay_specifics": {}}
    listing_generate.propagate_general_size(listing)
    assert listing["ebay_specifics"]["size"] == "2XL"


# --- Brands -------------------------------------------------------------------

def test_brand_fallback_regressions():
    # #415: no brand on Mercari is its own No Brand choice, not free text.
    form = mercari_form.form({"brand": "Unbranded", "price": 20})
    row = next(row for row in form["fields"] if row["path"] == "mercari_specifics.brand")
    assert row["value"] == mercari_form.NO_BRAND and row["value"] in row["option_values"].values()
    # #416: no brand on Depop is its Other choice.
    form = depop_form.form({"brand": "Unbranded", "price": 20})
    row = next(row for row in form["fields"] if row["path"] == "depop_specifics.brand")
    assert row["value"] == "Other" and "Other" in row["options"]
