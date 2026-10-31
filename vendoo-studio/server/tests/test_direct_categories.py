"""The eBay and Etsy tabs keep their own categories, apart from the ids Send to Vendoo uses."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vendoo_studio.database import Base
from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode

from vendoo_studio.services.direct_categories import (
    attach_direct_categories,
    live_fields_prompt,
    merge_live_fields,
    overlay_live_fields,
    pick_ebay_suggestion,
    pick_etsy_leaf,
    search_text,
)
from vendoo_studio.services.ebay_listings import ebay_category_id


class DirectCategoryChoiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def test_generation_uses_the_exact_synced_ebay_leaf_without_a_suggestion_request(self):
        path = "Clothing, Shoes & Accessories > Men > Men's Clothing > Jeans"
        self.db.add(CategoryTree(marketplace="ebay", status="complete"))
        for category_id, category_path in [("11483", path), ("11554", path.replace("Men", "Women"))]:
            self.db.add(CategoryTreeNode(marketplace="ebay", category_id=category_id, parent_id="1",
                                        label="Jeans", path=category_path, is_leaf=True, has_children=False))
        self.db.commit()
        listing = {"category_path": path}
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", AsyncMock(side_effect=AssertionError("network suggestion used"))) as search, \
                patch("vendoo_studio.services.direct_categories._etsy_leaf", AsyncMock(return_value=None)):
            await attach_direct_categories(self.db, listing)
        search.assert_not_awaited()
        self.assertEqual(listing["direct_category_ids"]["ebay"], "11483")
        self.assertEqual(listing["direct_category_ids"]["ebay_path"], path)

    def test_category_lookup_keeps_department_before_a_title_exists(self):
        listing = {"title": "Draft listing", "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Jeans"}
        query = search_text(listing)
        self.assertIn("Women", query)
        self.assertIn("Jeans", query)
        self.assertNotIn("Draft listing", query)

    def test_ebay_suggestion_is_the_first_leaf_and_its_path(self):
        hit = pick_ebay_suggestion({"categorySuggestions": [{
            "category": {"categoryId": "15687", "categoryName": "T-Shirts"},
            "categoryTreeNodeAncestors": [
                {"categoryName": "Men's Clothing"},
                {"categoryName": "Clothing, Shoes & Accessories"},
            ],
        }, {"category": {"categoryId": "99", "categoryName": "Other"}}]})
        self.assertEqual(hit, {
            "id": "15687",
            "path": "Clothing, Shoes & Accessories > Men's Clothing > T-Shirts",
        })

    def test_etsy_leaf_prefers_the_closer_longer_path(self):
        leaves = [
            {"id": "1", "path": "Clothing"},
            {"id": "2", "path": "Clothing > Men > Shirts > T-shirts"},
            {"id": "3", "path": "Home > Shirts"},
        ]
        self.assertEqual(pick_etsy_leaf(leaves, "men cotton t-shirt")["id"], "2")

    def test_etsy_tshirts_do_not_match_childrens_button_downs(self):
        leaves = [
            {"id": "11173", "path": "Clothing > Gender-Neutral Kids' Clothing > Tops & Tees > Dress Shirts & Button Downs"},
            {"id": "20", "path": "Clothing > Women's Clothing > Tops & Tees > T-shirts"},
            {"id": "30", "path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts"},
        ]
        self.assertEqual(pick_etsy_leaf(leaves, "Clothing > Men's Clothing > Shirts & Tees > T-shirts")["id"], "30")

    def test_etsy_leaf_name_outweighs_repeated_ancestor_words(self):
        leaves = [
            {"id": "1", "path": "Clothing > Shirts > Dress Shirts & Button Downs"},
            {"id": "2", "path": "Clothing > Tops > T-shirts"},
        ]
        self.assertEqual(pick_etsy_leaf(leaves, "Draft listing T-Shirts")["id"], "2")

    def test_etsy_product_type_outweighs_shoes_in_the_source_root(self):
        leaves = [
            {"id": "1789", "path": "Weddings > Shoes > Men's Wedding Shoes"},
            {"id": "30", "path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts"},
        ]
        query = "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"
        self.assertEqual(pick_etsy_leaf(leaves, query)["id"], "30")
        self.assertIsNone(pick_etsy_leaf(leaves[:1], query))

    def test_a_live_ebay_category_does_not_replace_vendoo(self):
        listing = {"marketplace_category_ids": {"ebay": "111"}, "direct_category_ids": {"ebay": "15687"}}
        self.assertEqual(ebay_category_id(listing), "15687")
        self.assertEqual(listing["marketplace_category_ids"]["ebay"], "111")

    def test_vendoos_ebay_category_is_not_the_ebay_tabs(self):
        from vendoo_studio.services.listing_result import ListingProblem

        with self.assertRaises(ListingProblem):
            ebay_category_id({"marketplace_category_ids": {"ebay": "111"}})
        self.assertEqual(ebay_category_id({"direct_category_ids": {"ebay": "111"}}), "111")

    def test_matching_live_field_keeps_the_vendoo_key_and_takes_live_choices(self):
        fields = merge_live_fields(
            [{"key": "sleeveLength", "label": "Sleeve Length", "required": False, "options": [], "selection_only": False}],
            [{"id": "Sleeve Length", "name": "Sleeve Length", "required": True, "mode": "selection",
              "values": [{"label": "Short"}, {"label": "Long"}]},
             {"id": "condition", "name": "Condition", "required": True, "mode": "selection", "values": [{"label": "New"}]},
             {"id": "Fit", "name": "Fit", "required": False, "mode": "free_text", "values": []}],
            {},
            "ebay",
        )
        sleeve = next(row for row in fields if row["label"] == "Sleeve Length")
        self.assertEqual(sleeve["key"], "sleeveLength")
        self.assertEqual(sleeve["options"], ["Short", "Long"])
        self.assertTrue(sleeve["required"])
        self.assertFalse(any(row["label"] == "Condition" for row in fields))
        self.assertEqual(next(row for row in fields if row["label"] == "Fit")["key"], "Fit")

    def test_live_constraints_replace_stale_cached_control_types(self):
        fields = merge_live_fields(
            [{"key": "season", "label": "Season", "required": True, "multi": False,
              "selection_only": True, "options": ["Obsolete"]}],
            [{"name": "Season", "required": False, "multi": True, "mode": "free_text",
              "max_values": 4, "max_length": 65, "values": []}], {}, "ebay",
        )
        self.assertEqual(fields[0], {
            "key": "season", "label": "Season", "required": False, "multi": True,
            "selection_only": False, "options": [], "max_values": 4, "max_length": 65,
            "options_source": "marketplace", "on_form": True,
        })

    def test_the_generation_prompt_names_live_fields_and_keeps_vendoo_names(self):
        listing = {"direct_category_ids": {"ebay": "15687", "ebay_path": "Clothing > T-Shirts"}}
        rows = [{"name": "Sleeve Length", "required": True, "values": [{"label": "Short"}]}]
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            text = live_fields_prompt(listing)
        self.assertIn("Sleeve Length (required)", text)
        self.assertIn("Send to Vendoo reads those", text)
        self.assertIn("Fill these under ebay_specifics using", text)

    async def test_only_the_tabs_own_ebay_category_reads_its_live_fields(self):
        vendoo = {"marketplace_category_ids": {"ebay": "260968", "etsy": "392"}}
        rows = [{"name": "Outer Shell Material", "required": True, "values": [{"label": "Polyester"}]}]
        live = AsyncMock(return_value=[{"name": "Outer Shell Material", "required": True, "mode": "free_text",
                                        "values": [{"label": "Polyester"}]}])
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows), \
                patch("vendoo_studio.services.marketplace_schemas.fields", live):
            self.assertEqual(live_fields_prompt(vendoo), "")
            self.assertEqual(await overlay_live_fields(vendoo, "ebay", []), [])
            live.assert_not_awaited()
            listing = {**vendoo, "direct_category_ids": {"ebay": "57988"}}
            self.assertIn("(id 57988)", live_fields_prompt(listing))
            fields = await overlay_live_fields(listing, "ebay", [])
        live.assert_awaited_once_with("ebay", "57988")
        self.assertEqual([(row["key"], row["options"]) for row in fields], [("Outer Shell Material", ["Polyester"])])

    async def test_choosing_a_live_category_leaves_the_vendoo_id_alone(self):
        listing = {
            "title": "Nike tee",
            "marketplace_categories": {"ebay": "Clothing > Shirts"},
            "marketplace_category_ids": {"ebay": "111", "etsy": "222"},
        }

        async def etsy_taxonomy():
            return ({"id": "482", "path": "Clothing > Men > T-shirts"},)

        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value={"id": "15687", "path": "Clothing > T-Shirts"})), \
             patch("vendoo_studio.services.direct_categories._etsy_leaf", new=AsyncMock(return_value={"id": "482", "path": "Clothing > Men > T-shirts"})), \
             patch("vendoo_studio.services.marketplace_schemas.etsy_taxonomy", etsy_taxonomy), \
             patch("vendoo_studio.services.chrome_catalog.live_preview", new=AsyncMock(return_value={})):
            await attach_direct_categories(self.db, listing, "cotton tee")
        self.assertEqual(listing["marketplace_category_ids"], {"ebay": "111", "etsy": "222"})
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["etsy"], "482")

    async def test_poshmark_and_depop_are_left_to_local_native_form_selection(self):
        listing = {
            "title": "Nike tee",
            "marketplace_categories": {"poshmark": "Men > Shirts > Tees", "depop": "Men > Tops > T-Shirts"},
            "marketplace_category_ids": {"ebay": "111", "poshmark": "stale", "depop": "old_leaf"},
        }
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value=None)), \
             patch("vendoo_studio.services.direct_categories._etsy_leaf", new=AsyncMock(return_value=None)), \
             patch("vendoo_studio.services.chrome_catalog.live_preview", new=AsyncMock(
                 side_effect=AssertionError("Chrome catalog used"))) as chrome_read:
            await attach_direct_categories(self.db, listing, "")
        self.assertEqual(listing["marketplace_category_ids"], {"ebay": "111", "poshmark": "stale", "depop": "old_leaf"})
        chrome_read.assert_not_awaited()
        self.assertNotIn("poshmark", listing.get("direct_category_ids", {}))
        self.assertNotIn("depop", listing.get("direct_category_ids", {}))


if __name__ == "__main__":
    unittest.main()
