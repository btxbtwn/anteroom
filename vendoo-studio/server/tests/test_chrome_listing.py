"""Direct List checks that leave Send to Vendoo on its own categories and fields."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from vendoo_studio.services.chrome_catalog import (
    apply_direct_ids,
    apply_preview,
    keep_direct_categories,
)
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
    def test_missing_categories_block_every_direct_marketplace(self):
        from vendoo_studio.services.direct_preflight import blocking, gaps

        listing = {"title": "Vintage dress pants", "description": "Vintage dress pants", "price": 20,
                   "etsy_specifics": {"who_made": "Someone else", "what_is": "A finished product",
                                      "when_made": "Before 2007 (Vintage)"}}
        for market in ("ebay", "etsy", "poshmark", "mercari", "depop", "vinted", "sellwild"):
            with self.subTest(market=market):
                found = blocking(gaps(listing, market, Context()))
                self.assertTrue(any("categor" in item["field"].lower() for item in found), found)

    def test_a_poshmark_breadcrumb_without_a_selected_category_is_incomplete(self):
        listing = {"marketplace_category_objects": {"poshmark": {"path": "Women > Pants"}}}
        self.assertTrue(any(item["field"] == "direct_category_ids.poshmark" and item["blocking"]
                            for item in poshmark_requirements(listing, Context())))

    def test_depop_worldwide_shipping_requires_a_native_fee_and_accepts_free_shipping(self):
        for price, valid in [(0, True), ("15.25", True), ("", False), (-1, False), (100, False), ("1.111", False)]:
            listing = {"direct_category_ids": {"depop": "menswear_tshirts"}, "depop_specifics": {
                "parcelSize": "Medium", "worldwideShipping": {"enabled": True, "price": price}}}
            fields = {row["field"] for row in depop_requirements(listing, Context())}
            self.assertEqual("depop_specifics.worldwideShipping.price" not in fields, valid)

    def test_poshmark_says_what_it_will_change_and_still_allows_list(self):
        gaps = poshmark_requirements({
            "title": "Tee", "description": "Soft cotton", "price": "18.40", "quantity": 2,
            "primaryColor": "Grey", "secondaryColor": "Lilac",
            "marketplace_category_ids": {"poshmark": "abc"},
        }, Context())
        by_message = {gap["message"]: gap["blocking"] for gap in gaps}
        self.assertIs(by_message["Poshmark lists this at $18."], False)
        self.assertNotIn("Poshmark lists a quantity of 1.", by_message)
        dropped = next(message for message in by_message if "left off" in message)
        self.assertIn("Lilac", dropped)
        self.assertNotIn("Grey", dropped)
        self.assertIs(by_message[dropped], False)
        self.assertEqual(poshmark_color_name("Beige"), "Tan")

    def test_a_missing_parcel_or_a_parent_category_holds_list_off(self):
        depop = depop_requirements({"marketplace_category_ids": {"depop": "menswear_tshirts"}}, Context())
        self.assertTrue(any(gap["field"] == "depop_specifics.parcelSize" and gap["blocking"] for gap in depop))
        titled = depop_requirements({
            "depop_specifics": {"parcelSize": "Under 1lb"},
            "marketplace_category_ids": {"depop": "menswear_tshirts"},
        }, Context())
        self.assertTrue(any("does not offer" in gap["message"] and gap["blocking"] for gap in titled))
        known = depop_requirements({
            "depop_specifics": {"parcelSize": "Medium"},
            "marketplace_category_ids": {"depop": "menswear_tshirts"},
        }, Context())
        self.assertFalse(any("does not offer" in gap["message"] for gap in known))
        mercari = mercari_requirements({
            "description": "Soft cotton",
            "marketplace_category_ids": {"mercari": "1885"},
            "form": {"overrides": {"categoryV2": {"id": "1885", "hasChildren": True}}},
        }, Context())
        self.assertTrue(any(gap["blocking"] and "subcategories" in gap["message"] for gap in mercari))

    def test_mercari_needs_its_shipping_answers_before_list_everywhere_tries(self):
        listing = {"description": "Light wash jeans", "direct_category_ids": {"mercari": "1931"}, "form": {"overrides": {}}}
        blocking = {gap["field"] for gap in mercari_requirements(listing, Context()) if gap["blocking"]}
        self.assertIn("mercari_specifics.shipsFrom", blocking)
        self.assertIn("mercari_specifics.package.fitsShoebox", blocking)
        self.assertIn("mercari_specifics.package.pounds", blocking)
        self.assertIn("mercari_specifics.shippingClassId", blocking)

        complete = {**listing, "weight_oz": 8, "mercari_specifics": {
            "shipsFrom": "70125", "shippingClassId": "2550", "package": {"fitsShoebox": "no"}}}
        self.assertFalse([gap for gap in mercari_requirements(complete, Context()) if gap["blocking"]])

        # A saved carrier default is a label too; the extension matches it to this package's tier.
        saved_label = {**listing, "weight_oz": 8, "mercari_specifics": {
            "shipsFrom": "70125", "package": {"fitsShoebox": "yes"}},
            "form": {"overrides": {}, "mercari_shipping_label": {"id": "2550", "label": "USPS Ground Advantage"}}}
        self.assertFalse([gap for gap in mercari_requirements(saved_label, Context()) if gap["blocking"]])

        # Ship on your own uses the seller's own label and needs none of the prepaid answers.
        own_label = {"description": "Light wash jeans", "direct_category_ids": {"mercari": "1931"},
                     "form": {"overrides": {}},
                     "mercari_specifics": {"shipsFrom": "70125", "shippingMethod": "SOYO"}}
        self.assertFalse([gap for gap in mercari_requirements(own_label, Context()) if gap["blocking"]])

    def test_mercari_ignores_a_mirrored_size_table_and_checks_native_sizes_in_chrome(self):
        listing = {"description": "Graphic tee", "marketplace_category_ids": {"mercari": "1934"},
                   "form": {"sizes": [{"id": 4, "name": "M"}]}}
        self.assertFalse(any(gap["field"] == "size" for gap in mercari_requirements(listing, Context())))
        listing["form"]["aspects"] = {"1934_Size": 4}
        self.assertFalse(any(gap["field"] == "size" for gap in mercari_requirements(listing, Context())))
        listing["form"] = {"sizes": []}
        self.assertFalse(any(gap["field"] == "size" for gap in mercari_requirements(listing, Context())))

    def test_modern_resale_is_not_ready_for_etsy(self):
        listing = {"title": "Graphic tee", "etsy_specifics": {
            "who_made": "Another company or person", "what_is": "A finished product", "when_made": "2020 - 2026 (Recently)",
        }}
        gaps = etsy_requirements(listing, Context())
        self.assertTrue(any(gap["field"] == "etsy_specifics.when_made" and gap["blocking"] for gap in gaps))

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


DEPT = "01008c10d97b4e1245005764"
SHIRTS = "07008c10d97b4e1245005764"
TEES = "a" * 24


class ChromeCatalogTest(unittest.TestCase):
    def test_chrome_ids_are_remembered_beside_vendoos(self):
        listing = {"marketplace_category_ids": {"poshmark": "stale", "depop": "old_leaf"}}
        self.assertTrue(apply_direct_ids(listing, {
            "poshmark": f"{DEPT}/{SHIRTS}/{TEES}", "depop": "menswear_tshirts",
        }))
        self.assertEqual(listing["marketplace_category_ids"], {"poshmark": "stale", "depop": "old_leaf"})
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")

    def test_a_poshmark_size_chrome_refuses_holds_list_off(self):
        found = apply_preview([], {
            "ok": True,
            "gaps": {"poshmark": [{"field": "size", "message": "Poshmark needs a size Poshmark offers in this category.", "blocking": True}]},
        }, "poshmark")
        self.assertTrue(found[0]["blocking"])
        self.assertIn("size", found[0]["message"])

    def test_a_live_parcel_match_keeps_a_title_the_saved_list_does_not_know(self):
        found = depop_requirements({
            "depop_specifics": {"parcelSize": "Oversize"},
            "direct_category_ids": {"depop": "menswear_tshirts"},
        }, Context())
        kept = apply_preview(found, {"parcels_checked": True, "gaps": {"depop": []}}, "depop")
        self.assertFalse(any("does not offer" in gap["message"] for gap in kept))
        refused = apply_preview(found, {"parcels_checked": True, "gaps": {"depop": [{
            "field": "depop_specifics.parcelSize", "message": 'Depop does not offer "Medium" as a parcel size.', "blocking": True,
        }]}}, "depop")
        self.assertTrue(any("Medium" in gap["message"] and gap["blocking"] for gap in refused))

    def test_an_editor_save_keeps_a_resolved_category(self):
        previous = {"direct_category_ids": {"ebay": "111", "poshmark": f"{DEPT}/{SHIRTS}/{TEES}"}, "title": "Tee"}
        incoming = {"direct_category_ids": {"ebay": "111"}, "title": "Tee"}
        keep_direct_categories(previous, incoming)
        self.assertEqual(incoming["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(incoming["direct_category_ids"]["ebay"], "111")


class MercariParentCategoryTest(unittest.TestCase):
    """Mercari lists a parent category with no size, so List holds it off, stored id or not."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from vendoo_studio.database import Base
        from vendoo_studio.models.catalog import CategoryTreeNode

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        for cid, parent, path, children in (
            ("1881", "3", "Kids > Boys coats & jackets", True),
            ("1915", "1881", "Kids > Boys coats & jackets > Boys 0-24 mos", False),
            ("1923", "1881", "Kids > Boys coats & jackets > Boys 2T-5T", False),
            ("1931", "1881", "Kids > Boys coats & jackets > Boys (4+)", False),
        ):
            self.db.add(CategoryTreeNode(marketplace="mercari", category_id=cid, parent_id=parent, label=path.split(" > ")[-1],
                                         path=path, is_leaf=True, has_children=children, children_loaded=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _gaps(self, **listing):
        listing.setdefault("mercari_specifics", {
            "shipsFrom": "70125", "shippingClassId": "2550", "package": {"fitsShoebox": "no", "pounds": 1},
        })
        return mercari_requirements({"description": "Windbreaker", **listing}, Context(db=self.db))

    def test_a_stored_parent_id_is_refused_and_its_subcategories_named(self):
        for listing in ({"direct_category_ids": {"mercari": "1881"}},
                        {"marketplace_category_ids": {"mercari": "1881"},
                         "form": {"overrides": {"categoryV2": {"id": "1881", "isLeaf": True}}}},
                        {"marketplace_categories": {"mercari": "Kids > Boys coats & jackets"}}):
            gaps = self._gaps(**listing)
            self.assertTrue(any(gap["blocking"] and "Boys 0-24 mos, Boys 2T-5T, Boys (4+)" in gap["message"]
                                for gap in gaps), listing)

    def test_a_sized_leaf_no_longer_needs_vendoos_size_list(self):
        leaf = {"size": "XL (18/20)", "direct_category_ids": {"mercari": "1931"}}
        unread = self._gaps(**leaf, form={"overrides": {}})
        self.assertFalse(any(gap["blocking"] and gap["field"] == "mercari_specifics.size" for gap in unread))
        self.assertEqual(self._gaps(**leaf, form={"overrides": {}, "sizes": [{"id": 168}]}), [])
        self.assertEqual(self._gaps(direct_category_ids={"mercari": "1931"}, form={"overrides": {}}), [])
