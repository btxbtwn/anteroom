"""General fills every marketplace form. An edit on one form stays there."""

from __future__ import annotations

import copy
import unittest

from vendoo_studio.services.direct_preflight import Context
from vendoo_studio.services.chrome_listing import poshmark_requirements
from vendoo_studio.services.ebay_listings import ebay_aspects, ebay_title
from vendoo_studio.services.etsy_listings import etsy_title
from vendoo_studio.services.listing_generate import apply_send_readiness_fixes, propagate_general_size
from vendoo_studio.services.registry import _apply_department
from vendoo_studio.services.seller_overrides import (
    apply_marketplace_overrides,
    field_locked,
    marketplace_text,
    prepare_user_listing,
)
from vendoo_studio.services.vendoo_item_build import _listing_section
from vendoo_studio.services.vendoo_item_update import apply_update_all


class IndependentFormsTest(unittest.TestCase):
    def test_a_general_edit_fills_forms_that_still_match_and_leaves_a_locked_edit(self):
        previous = {
            "title": "Old",
            "description": "Same",
            "ebay_specifics": {"title": "Old", "brand": "Nike"},
            "depop_specifics": {"title": "Depop only"},
            "poshmark_specifics": {"title": "Posh only"},
            "seller_fields": ["poshmark_specifics.title"],
        }
        submitted = {
            "title": "New",
            "description": "Same",
            "ebay_specifics": {"title": "Old", "brand": "Nike"},
            "depop_specifics": {"title": "Depop only"},
            "poshmark_specifics": {"title": "Posh only"},
            "seller_fields": ["poshmark_specifics.title"],
        }
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["title"], "New")
        self.assertNotIn("title", submitted["ebay_specifics"])
        self.assertEqual(submitted["ebay_specifics"]["brand"], "Nike")
        self.assertNotIn("title", submitted["depop_specifics"])
        self.assertEqual(submitted["poshmark_specifics"]["title"], "Posh only")
        locks = set(submitted["seller_fields"])
        self.assertNotIn("ebay_specifics.title", locks)
        self.assertNotIn("depop_specifics.title", locks)
        self.assertIn("poshmark_specifics.title", locks)

    def test_a_marketplace_edit_in_the_same_save_as_general_stays_on_that_form(self):
        previous = {"title": "Old", "ebay_specifics": {"title": "Old"}}
        submitted = {"title": "New", "ebay_specifics": {"title": "eBay now"}}
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["title"], "New")
        self.assertEqual(submitted["ebay_specifics"]["title"], "eBay now")
        self.assertIn("ebay_specifics.title", submitted["seller_fields"])

    def test_editing_one_marketplace_does_not_rewrite_general_or_the_other_forms(self):
        previous = {"title": "Shared", "description": "Same", "brand": "Nike"}
        submitted = {
            "title": "Shared",
            "description": "Same",
            "brand": "Nike",
            "ebay_specifics": {"title": "eBay only", "description": ""},
        }
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["title"], "Shared")
        self.assertEqual(submitted["description"], "Same")
        self.assertEqual(submitted["ebay_specifics"]["title"], "eBay only")
        self.assertEqual(submitted["ebay_specifics"]["description"], "")
        self.assertNotIn("depop_specifics", submitted)
        self.assertIn("ebay_specifics.title", submitted["seller_fields"])
        self.assertIn("ebay_specifics.description", submitted["seller_fields"])

    def test_a_cleared_title_stays_cleared_instead_of_falling_back_to_general(self):
        listing = {"title": "General", "ebay_specifics": {"title": ""}}
        apply_marketplace_overrides(listing, "ebay")
        self.assertEqual(listing["title"], "")
        untouched = {"title": "General", "depop_specifics": {"brand": "Nike"}}
        apply_marketplace_overrides(untouched, "depop")
        self.assertEqual(untouched["title"], "General")

    def test_ebay_aspects_ignore_a_form_title_and_keep_an_edited_size(self):
        listing = {
            "title": "General",
            "size": "M",
            "brand": "Nike",
            "ebay_specifics": {"title": "eBay only", "size": "L"},
        }
        aspects = ebay_aspects(listing, {})
        self.assertNotIn("Title", aspects)
        self.assertEqual(aspects["Size"], ["L"])
        self.assertEqual(aspects["Brand"], ["Nike"])

    def test_vendoo_keeps_a_marketplace_title_and_still_fills_the_others_from_general(self):
        general = {"title": "General", "description": "About it", "quantity": "1"}
        ebay = _listing_section("ebay", {"title": "General", "description": "About it", "ebay_specifics": {"title": "eBay only"}}, general)
        depop = _listing_section("depop", {"title": "General", "description": "About it"}, general)
        self.assertEqual(ebay["overrides"]["title"], "eBay only")
        self.assertNotIn("title", depop["overrides"])
        current = {"generalDetails": general, "listings": {"ebay": {"overrides": {}}, "depop": {"overrides": {"quantity": "1"}}}}
        desired = {"generalDetails": general, "listings": {"ebay": ebay, "depop": depop}}
        apply_update_all(current, desired)
        self.assertEqual(desired["listings"]["ebay"]["overrides"]["title"], "eBay only")
        self.assertEqual(desired["listings"]["depop"]["overrides"]["title"], "General")
        self.assertEqual(desired["listings"]["depop"]["overrides"]["description"], "About it")

    def test_setting_an_override_back_to_general_makes_that_field_follow_again(self):
        previous = {
            "title": "Shared",
            "description": "About it",
            "brand": "Nike",
            "department": "Women",
            "ebay_specifics": {"title": "eBay only", "description": "eBay copy", "brand": "Adidas", "department": "Girls"},
            "seller_fields": [
                "ebay_specifics.title", "ebay_specifics.description", "ebay_specifics.brand", "ebay_specifics.department",
            ],
        }
        submitted = copy.deepcopy(previous)
        submitted["ebay_specifics"].update({
            "title": "Shared", "description": "About it", "brand": "Nike", "department": "Women",
        })
        prepare_user_listing(previous, submitted)
        for field in ("title", "description", "brand", "department"):
            self.assertNotIn(field, submitted["ebay_specifics"])
            self.assertFalse(field_locked(submitted, "ebay", field))
        later = copy.deepcopy(submitted)
        later.update({"title": "Newer", "description": "Cooler", "brand": "Adidas", "department": "Men"})
        prepare_user_listing(submitted, later)
        for field in ("title", "description", "brand", "department"):
            self.assertNotIn(field, later["ebay_specifics"])
        self.assertEqual(marketplace_text(later, "ebay", "title"), "Newer")
        self.assertEqual(marketplace_text(later, "mercari", "brand"), "Adidas")
        self.assertEqual(marketplace_text(later, "vinted", "department"), "Men")

    def test_a_locked_override_stays_on_that_marketplace_when_general_changes(self):
        previous = {"title": "Coat", "description": "Warm", "brand": "Nike", "department": "Women"}
        submitted = {
            **previous,
            "ebay_specifics": {"title": "eBay coat", "description": "", "brand": "eBay Nike", "department": "Girls"},
            "mercari_specifics": {"description": "Warm"},
        }
        prepare_user_listing(previous, submitted)
        saved = copy.deepcopy(submitted)
        saved.update({"title": "Jacket", "description": "Cooler", "brand": "Adidas", "department": "Men"})
        prepare_user_listing(submitted, saved)
        self.assertEqual(saved["title"], "Jacket")
        self.assertEqual(saved["ebay_specifics"]["title"], "eBay coat")
        self.assertEqual(saved["ebay_specifics"]["description"], "")
        self.assertEqual(saved["ebay_specifics"]["brand"], "eBay Nike")
        self.assertEqual(saved["ebay_specifics"]["department"], "Girls")
        self.assertNotIn("description", saved["mercari_specifics"])
        self.assertEqual(marketplace_text(saved, "poshmark", "title"), "Jacket")
        self.assertEqual(marketplace_text(saved, "depop", "brand"), "Adidas")
        self.assertTrue(field_locked(saved, "ebay", "title"))
        self.assertFalse(field_locked(saved, "poshmark", "title"))

    def test_a_locked_ebay_size_survives_a_general_size_change_and_send_readiness(self):
        previous = {"size": "M", "title": "Nike M Tee", "ebay_specifics": {}, "depop_specifics": {}}
        submitted = {"size": "M", "title": "Nike M Tee", "ebay_specifics": {"size": "10"}, "depop_specifics": {"size": "10"}}
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["ebay_specifics"]["size"], "10")
        self.assertTrue(field_locked(submitted, "ebay", "size"))
        changed = copy.deepcopy(submitted)
        changed["size"] = "L"
        prepare_user_listing(submitted, changed)
        self.assertEqual(changed["ebay_specifics"]["size"], "10")
        self.assertEqual(changed["depop_specifics"]["size"], "10")
        apply_send_readiness_fixes(changed)
        self.assertEqual(changed["ebay_specifics"]["size"], "10")
        self.assertEqual(changed["size"], "L")

        following = {"size": "L", "ebay_specifics": {"size": "10"}}
        propagate_general_size(following)
        self.assertEqual(following["ebay_specifics"]["size"], "L")

    def test_an_ebay_size_that_matches_propagation_follows_including_petite(self):
        previous = {"size": "M", "sizeType": "Petites", "ebay_specifics": {"size": "PM", "sizeType": "Petites"}}
        submitted = {"size": "L", "sizeType": "Petites", "ebay_specifics": {"size": "PM", "sizeType": "Petites"}}
        prepare_user_listing(previous, submitted)
        self.assertNotIn("size", submitted["ebay_specifics"])
        self.assertFalse(field_locked(submitted, "ebay", "size"))
        propagate_general_size(submitted)
        self.assertEqual(submitted["ebay_specifics"]["size"], "PL")

        locked = {
            "size": "XXL",
            "ebay_specifics": {"size": "10"},
            "seller_fields": ["ebay_specifics.size"],
        }
        returned = copy.deepcopy(locked)
        returned["ebay_specifics"]["size"] = "2XL"
        prepare_user_listing(locked, returned)
        self.assertNotIn("size", returned["ebay_specifics"])
        self.assertFalse(field_locked(returned, "ebay", "size"))
        before = copy.deepcopy(returned)
        returned["size"] = "L"
        prepare_user_listing(before, returned)
        propagate_general_size(returned)
        self.assertEqual(returned["ebay_specifics"]["size"], "L")

    def test_a_locked_ebay_department_is_not_replaced_by_gender_alignment(self):
        locked = {"department": "Women", "ebay_specifics": {"department": "Girls"}, "seller_fields": ["ebay_specifics.department"]}
        _apply_department(locked, "men")
        self.assertEqual(locked["department"], "Men")
        self.assertEqual(locked["ebay_specifics"]["department"], "Girls")
        following = {"department": "Women", "ebay_specifics": {"department": "Girls"}}
        _apply_department(following, "men")
        self.assertEqual(following["ebay_specifics"]["department"], "Men")

    def test_payloads_send_the_override_or_the_general_value(self):
        overridden = {"title": "General", "description": "About it", "ebay_specifics": {"title": "eBay only"}, "etsy_specifics": {"title": ""}}
        self.assertEqual(ebay_title(overridden), "eBay only")
        self.assertEqual(etsy_title(overridden), "")
        self.assertEqual(ebay_title({"title": "General"}), "General")
        self.assertEqual(marketplace_text({"title": "General", "description": "About it", "poshmark_specifics": {"description": ""}}, "poshmark", "description"), "")
        gaps = poshmark_requirements({"title": "General", "description": "About it", "price": 20, "poshmark_specifics": {"title": ""}}, Context())
        self.assertIn("title", {gap["field"] for gap in gaps})


if __name__ == "__main__":
    unittest.main()
