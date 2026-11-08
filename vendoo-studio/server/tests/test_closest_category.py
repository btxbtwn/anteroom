"""A General category fills the closest marketplace option, marked as a suggestion.

When no option names the item exactly, the form still gets the nearest sensible
one (a tie broken by the order General names its items, the matching item in
the nearest department, the item's branch, the department's Other leaf) and the
seller sees "Suggested: check it" on that tab. A pick never leaves apparel.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi import HTTPException

from vendoo_studio.services.agent_categories import apply_agent_categories
from vendoo_studio.services.hosted_listing_contract import editable_listing, stored_listing_fields
from vendoo_studio.services.native_category_match import closest_row, department, match_row, native_rows
from vendoo_studio.services.seller_overrides import locked_paths, prepare_user_listing

ROOT = Path(__file__).resolve().parents[3]
C = "Clothing, Shoes & Accessories"


@lru_cache(maxsize=1)
def rows() -> dict[str, list[dict]]:
    return native_rows()


@lru_cache(maxsize=1)
def apparel_leaves() -> tuple[str, ...]:
    seed = json.loads(gzip.open(ROOT / "vendoo-studio/data/category-trees-seed.json.gz").read())
    return tuple(node["path"] for node in seed["category_tree_nodes"]
                 if node["marketplace"] == "general" and node.get("is_leaf") and node["path"].startswith(C))


def pick(market: str, path: str, evidence: str = "") -> tuple[str, bool] | None:
    found = closest_row(rows()[market], path, evidence)
    return (found[0]["path"], found[1]) if found else None


def test_an_exact_match_is_not_a_suggestion():
    assert pick("depop", f"{C} > Men > Men's Clothing > Shirts > T-Shirts") == ("Men > Tops > T-shirts", False)


def test_swimwear_is_poshmarks_swim():
    assert pick("poshmark", f"{C} > Women > Women's Clothing > Swimwear") == ("Women > Swim > None", False)


def test_a_tie_takes_the_first_item_general_names():
    assert pick("depop", f"{C} > Men > Men's Clothing > Activewear > Hoodies & Sweatshirts") == ("Men > Tops > Hoodies", True)


def test_a_girls_item_uses_womens_when_the_marketplace_has_no_kids_option():
    path, suggested = pick("mercari", f"{C} > Kids > Girls > Girls' Accessories > Hats")
    assert suggested and path.startswith("Women >") and "hat" in path.lower()
    assert pick("poshmark", f"{C} > Kids > Girls > Girls' Accessories > Scarves & Wraps") == (
        "Women > Accessories > Scarves & Wraps", True)


def test_a_womens_tie_uses_mens_when_only_men_have_ties():
    path, suggested = pick("mercari", f"{C} > Women > Women's Accessories > Ties")
    assert suggested and path.startswith("Men >") and "tie" in path.lower()


def test_bridesmaid_dresses_are_womens_dresses():
    path, suggested = pick("poshmark", f"{C} > Specialty > Wedding & Formal Occasion > Bridesmaid Dresses")
    assert suggested and path.startswith("Women > Dresses")


def test_the_department_other_leaf_is_the_last_resort():
    path, suggested = pick("poshmark", f"{C} > Men > Men's Accessories > Collar Stays")
    assert suggested and path.startswith("Men >")


def test_a_non_apparel_category_is_never_pushed_into_a_department():
    assert pick("poshmark", "eBay Motors > Parts & Accessories > Car & Truck Parts > Brakes > Brake Pads") is None
    assert pick("depop", "Coins & Paper Money > Coins: US > Cents") is None


@pytest.mark.parametrize("market", ["poshmark", "mercari", "depop"])
def test_kids_sets_and_gloves_stay_in_apparel(market):
    for item in ("Kids > Girls > Girls' Accessories > Gloves & Mittens", "Kids > Boys > Boys' Clothing (Sizes 4 & Up) > Outfits & Sets",
                 "Baby > Baby Accessories > Gloves & Mittens", "Women > Women's Clothing > Outfits & Sets"):
        for found in (match_row(rows()[market], f"{C} > {item}"), (closest_row(rows()[market], f"{C} > {item}") or [None])[0]):
            if found:
                assert found["path"].split(" > ")[0].lower() in {"women", "men", "kids"}, (item, found)


# The same apparel branches as the value matrix (test_marketplace_value_matrix.APPAREL_ROOTS).
APPAREL_ROOTS = {
    "poshmark": {"women", "men", "kids"},
    "depop": {"women", "men", "kids", "everything else"},
    "mercari": {"women", "men", "kids", "vintage & collectibles", "sports & outdoors"},
}


@pytest.mark.parametrize("market", ["poshmark", "mercari", "depop"])
def test_a_suggestion_keeps_the_department_or_its_nearest(market):
    nearest = {"kids": {"kids", "women", "men"}, "women": {"women", "men"}, "men": {"men", "women"}, "": {"women", "men", "kids", ""}}
    for path in apparel_leaves():
        found = closest_row(rows()[market], path)
        if not found or not found[1]:
            continue
        top = found[0]["path"].split(" > ")[0].lower()
        assert top in APPAREL_ROOTS[market], (path, found[0]["path"])
        assert department(found[0]["path"]) in nearest[department(path)], (path, found[0]["path"])


# General apparel leaves (457) each form fills: (exact, exact or suggested).
# Floors that only rise: raise them when a change fills more.
APPAREL_FLOOR = {"poshmark": (228, 377), "mercari": (195, 383), "depop": (209, 317)}


@pytest.mark.parametrize("market", sorted(APPAREL_FLOOR))
def test_apparel_coverage_floor(market):
    found = [closest_row(rows()[market], path) for path in apparel_leaves()]
    exact = sum(1 for hit in found if hit and not hit[1])
    filled = sum(1 for hit in found if hit)
    floor_exact, floor_filled = APPAREL_FLOOR[market]
    assert exact >= floor_exact and filled >= floor_filled, f"{market}: {exact} exact, {filled} filled of {len(found)}"


# --- Storing the suggestion -----------------------------------------------------

POSHMARK = [{"id": "w/scarves", "path": "Women > Accessories > Scarves & Wraps"},
            {"id": "m/tees", "path": "Men > Shirts > None"}]


def test_a_closest_pick_is_stored_with_its_suggested_flag_and_an_exact_one_clears_it():
    listing = {}
    apply_agent_categories(listing, {"poshmark": POSHMARK}, markets={"poshmark"},
                           requested={"category_path": f"{C} > Kids > Girls > Girls' Accessories > Scarves & Wraps"})
    assert listing["direct_category_ids"] == {"poshmark": "w/scarves", "poshmark_suggested": True}
    apply_agent_categories(listing, {"poshmark": POSHMARK}, markets={"poshmark"},
                           requested={"category_path": f"{C} > Men > Men's Clothing > Shirts > T-Shirts"})
    assert listing["direct_category_ids"] == {"poshmark": "m/tees"}


def test_the_seller_choosing_a_category_clears_the_flag_without_locking_it():
    previous = {"direct_category_ids": {"poshmark": "w/scarves", "poshmark_suggested": True, "depop": "d", "depop_suggested": True}}
    submitted = {"direct_category_ids": {"poshmark": "m/tees", "poshmark_suggested": True, "depop": "d", "depop_suggested": True}}
    prepare_user_listing(previous, submitted)
    assert submitted["direct_category_ids"] == {"poshmark": "m/tees", "depop": "d", "depop_suggested": True}
    assert not any(path.endswith("_suggested") for path in locked_paths(submitted))
    # "Looks right" removes the flag; the category is the seller's from then on.
    confirmed = {"direct_category_ids": {"poshmark": "m/tees", "depop": "d"}}
    prepare_user_listing(submitted, confirmed)
    assert "direct_category_ids.depop" in locked_paths(confirmed)
    assert not any(path.endswith("_suggested") for path in locked_paths(confirmed))


def test_the_hosted_contract_round_trips_the_flag():
    listing = {"direct_category_ids": {"poshmark": "w/scarves", "poshmark_suggested": True, "query": "server only"}}
    assert stored_listing_fields(listing)["direct_category_ids"] == {"poshmark": "w/scarves", "poshmark_suggested": True}
    assert editable_listing({"direct_category_ids": {"poshmark_suggested": True}})
    with pytest.raises(HTTPException):
        editable_listing({"direct_category_ids": {"poshmark_suggested": "yes"}})
    with pytest.raises(HTTPException):
        editable_listing({"direct_category_ids": {"shop_suggested": True}})
