"""Direct List checks that leave Send to Vendoo on its own categories and fields."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from vendoo_studio.services.chrome_listing import (
    depop_requirements,
    mercari_requirements,
    poshmark_color_name,
    poshmark_requirements,
)
from vendoo_studio.services.direct_preflight import Context
from vendoo_studio.services.ebay_listings import requirements as ebay_requirements
from vendoo_studio.services.etsy_listings import requirements as etsy_requirements
from vendoo_studio.services.sellwild_listing import suggest_sellwild


class SellwildConditionTest(unittest.TestCase):
    def test_excellent_is_like_new_and_good_is_good(self):
        self.assertEqual(suggest_sellwild({"condition": "Pre-Owned - Excellent"})["condition_id"], 4)
        self.assertEqual(suggest_sellwild({"condition": "Pre-Owned - Good"})["condition_id"], 3)
        self.assertEqual(suggest_sellwild({"condition": "Pre-Owned"})["condition_id"], 2)
        self.assertEqual(suggest_sellwild({
            "condition": "Pre-Owned - Excellent", "sellwild_specifics": {"nativeConditionId": "2"},
        })["condition_id"], 2)


class ChromePreflightTest(unittest.TestCase):
    def test_poshmark_says_what_it_will_change_and_still_allows_list(self):
        gaps = poshmark_requirements({
            "title": "Tee", "description": "Soft cotton", "price": "18.40", "quantity": 2,
            "primaryColor": "Grey", "secondaryColor": "Lilac",
            "marketplace_category_ids": {"poshmark": "abc"},
        }, Context())
        by_message = {gap["message"]: gap["blocking"] for gap in gaps}
        self.assertIs(by_message["Poshmark lists this at $18."], False)
        self.assertIs(by_message["Poshmark lists a quantity of 1."], False)
        dropped = next(message for message in by_message if "left off" in message)
        self.assertIn("Lilac", dropped)
        self.assertNotIn("Grey", dropped)
        self.assertIs(by_message[dropped], False)
        self.assertEqual(poshmark_color_name("Beige"), "Tan")

    def test_a_missing_parcel_or_a_parent_category_holds_list_off(self):
        depop = depop_requirements({"marketplace_category_ids": {"depop": "menswear_tshirts"}}, Context())
        self.assertTrue(any(gap["field"] == "depop_specifics.parcelSize" and gap["blocking"] for gap in depop))
        mercari = mercari_requirements({
            "description": "Soft cotton",
            "marketplace_category_ids": {"mercari": "1885"},
            "form": {"overrides": {"categoryV2": {"id": "1885", "hasChildren": True}}},
        }, Context())
        self.assertTrue(any(gap["blocking"] and "subcategories" in gap["message"] for gap in mercari))

    def test_a_cached_required_ebay_aspect_holds_list_off(self):
        rows = [{"name": "Sleeve Length", "required": True, "mode": "selection", "multi": False, "max_length": None,
                 "values": [{"label": "Short"}]}]
        listing = {"title": "Tee", "description": "Soft cotton tee", "price": "12", "quantity": 1,
                   "weight_oz": 8, "marketplace_category_ids": {"ebay": "15687"}}
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            gaps = ebay_requirements(listing, Context())
        self.assertTrue(any(gap["field"].endswith("Sleeve Length") and gap["blocking"] for gap in gaps))

    def test_a_cached_required_etsy_property_holds_list_off(self):
        rows = [{"id": 1, "name": "Sleeve length", "required": True, "multi": False, "max_values": 1,
                 "values": [{"id": 2, "label": "Short"}]}]
        listing = {"title": "Tee", "description": "Soft cotton tee", "price": "12", "quantity": 1,
                   "etsy_specifics": {"who_made": "I did", "what_is": "A finished product", "when_made": "1990s (Vintage)"},
                   "direct_category_ids": {"etsy": "482"}, "marketplace_category_ids": {"etsy": "111"}}
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            gaps = etsy_requirements(listing, Context())
        self.assertTrue(any("Sleeve length" in gap["message"] and gap["blocking"] for gap in gaps))
        self.assertEqual(listing["marketplace_category_ids"]["etsy"], "111")
