"""A general category lands on each direct form's own dropdown option, or on none."""

from __future__ import annotations

import unittest

from vendoo_studio.services import depop_form, mercari_form, poshmark_form
from vendoo_studio.services.native_category_match import match_row, native_rows
from vendoo_studio.services.sellwild_listing import sellwild_catalog

C = "Clothing, Shoes & Accessories"
# General category -> the option each form should show. None means no option
# names that item, so the seller is asked instead.
EXPECTED = {
    f"{C} > Men > Men's Clothing > Shirts > T-Shirts": {
        "poshmark": "Men > Shirts > None", "depop": "Men > Tops > T-shirts",
        "mercari": "Men > Tops > T-shirts", "sellwild": "Fashion > Tops & Shirts",
    },
    f"{C} > Women > Women's Clothing > Dresses": {
        "poshmark": "Women > Dresses > None", "depop": "women > dresses > Dresses",
        "mercari": "Women > Dresses > Other", "sellwild": "Fashion > Dresses",
    },
    f"{C} > Women > Women's Clothing > Tops": {
        "poshmark": "Women > Tops > None", "depop": "Women > Tops > Other",
        "mercari": "Women > Tops & blouses > Other", "sellwild": "Fashion > Tops & Shirts",
    },
    f"{C} > Men > Men's Clothing > Jeans": {
        "poshmark": "Men > Jeans > None", "depop": "men > bottoms > Jeans",
        "mercari": "Men > Jeans > Other", "sellwild": "Fashion > Pants & Jeans",
    },
    f"{C} > Men > Men's Shoes > Athletic Shoes": {
        "poshmark": "Men > Shoes > Athletic Shoes", "depop": "men > footwear > Sneakers",
        "mercari": "Men > Shoes > Fashion sneakers", "sellwild": "Fashion > Sneakers",
    },
    f"{C} > Women > Women's Shoes > Heels": {
        "poshmark": "Women > Shoes > Heels", "depop": None,
        "mercari": "Women > Shoes > Heels", "sellwild": "Fashion > Heels",
    },
    f"{C} > Women > Women's Bags & Handbags": {
        "poshmark": "Women > Bags > None", "depop": "women > accessories > Bags",
        "mercari": "Women > Women's handbags > Other", "sellwild": "Fashion > Handbags & Purses",
    },
    f"{C} > Men > Men's Clothing > Coats, Jackets & Vests": {
        "poshmark": "Men > Jackets & Coats > None", "depop": "men > coats and jackets > Other",
        "mercari": "Men > Coats & jackets > Other", "sellwild": "Fashion > Coats & Jackets",
    },
    f"{C} > Women > Women's Clothing > Sweaters": {
        "poshmark": "Women > Sweaters > None", "depop": "Women > Tops > Sweaters",
        "mercari": "Women > Sweaters > Other", "sellwild": "Fashion > Sweaters & Hoodies",
    },
    f"{C} > Women > Women's Clothing > Skirts": {
        "poshmark": "Women > Skirts > None", "depop": "women > bottoms > Skirts",
        "mercari": "Women > Skirts > Other", "sellwild": "Fashion > Skirts",
    },
    f"{C} > Women > Women's Clothing > Jumpsuits & Rompers": {
        "poshmark": "Women > Pants & Jumpsuits > Jumpsuits & Rompers", "depop": "women > jumpsuits and rompers > Other",
        "mercari": "Women > Dresses > Jumpsuits & Rompers", "sellwild": None,
    },
    f"{C} > Men > Men's Clothing > Shirts > Casual Button-Down Shirts": {
        "poshmark": "Men > Shirts > Casual Button Down Shirts", "depop": "Men > Tops > Shirts",
        "mercari": "Men > Tops > Button-front", "sellwild": "Fashion > Tops & Shirts",
    },
    f"{C} > Men > Men's Accessories > Hats": {
        "poshmark": "Men > Accessories > Hats", "depop": "men > accessories > Hats and caps",
        "mercari": "Men > Men's accessories > Hats", "sellwild": "Fashion > Hats",
    },
    f"{C} > Kids > Girls > Girls' Clothing (Sizes 4 & Up) > Dresses": {
        "poshmark": "Kids > Dresses > None", "depop": "kids > dresses > Dresses",
        "mercari": "Kids > Girls dresses > Girls (4+)", "sellwild": "Fashion > Dresses",
    },
    f"{C} > Kids > Boys > Boys' Clothing (Sizes 4 & Up) > Tops, Shirts & T-Shirts": {
        "poshmark": "Kids > Shirts & Tops > None", "depop": None,
        "mercari": "Kids > Boys tops & t-shirts > Boys (4+)", "sellwild": "Fashion > Tops & Shirts",
    },
    "Video Games & Consoles > Video Games": {
        "poshmark": "Electronics > Video Games & Consoles > Video Games", "depop": None,
        "mercari": "Electronics > Video games & consoles > Games", "sellwild": "Electronics > Video Games",
    },
    "Cameras & Photo > Film Photography > Film Cameras": {
        "poshmark": "Electronics > Cameras, Photo & Video > None",
        "mercari": "Electronics > Cameras & photography > Film & Polaroid Cameras",
    },
    f"{C} > Specialty > Vintage > Men's Vintage Clothing > T-Shirts": {
        "poshmark": None, "depop": "Men > Tops > T-shirts", "mercari": "Men > Tops > T-shirts", "sellwild": None,
    },
}


def dropdown_options() -> dict[str, set[str]]:
    """The ids each form's category dropdown offers, read from the form builders' own data."""
    poshmark = next(row for row in poshmark_form.catalog()["fields"] if row["path"] == "direct_category_ids.poshmark")
    return {
        "poshmark": set(poshmark["option_values"].values()),
        "depop": {row["id"] for row in depop_form.catalog()["categories"]},
        "mercari": {row["id"] for row in mercari_form.categories()},
        "sellwild": {str(row["id"]) for row in sellwild_catalog()["categories"]},
    }


class NativeCategoryMatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = native_rows()
        cls.options = dropdown_options()

    def test_each_general_category_lands_on_the_expected_option(self):
        for general, expected in EXPECTED.items():
            for market, path in expected.items():
                with self.subTest(general=general, market=market):
                    hit = match_row(self.rows[market], general)
                    self.assertEqual(hit and hit["path"], path)

    def test_every_match_is_an_option_in_that_forms_dropdown(self):
        for general in EXPECTED:
            for market, rows in self.rows.items():
                hit = match_row(rows, general)
                if hit:
                    with self.subTest(general=general, market=market):
                        self.assertIn(hit["id"], self.options[market])

    def test_the_listings_words_pick_between_sleeve_lengths(self):
        general = f"{C} > Men > Men's Clothing > Shirts > T-Shirts"
        self.assertEqual(match_row(self.rows["poshmark"], general, "Short Sleeve")["path"], "Men > Shirts > Tees - Short Sleeve")
        self.assertEqual(match_row(self.rows["poshmark"], general, "Long Sleeve")["path"], "Men > Shirts > Tees - Long Sleeve")

    def test_a_marketplace_path_matches_its_own_option_exactly(self):
        self.assertEqual(match_row(self.rows["depop"], "Men > Tops > T-shirts")["id"], "menswear_tshirts")

    def test_a_mercari_parent_is_not_offered(self):
        self.assertFalse(any(row["path"] == "Women > Tops & blouses" for row in self.rows["mercari"]))
