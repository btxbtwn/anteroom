"""Live eBay and Etsy categories stay off the ids Send to Vendoo uses."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from vendoo_studio.services.direct_categories import (
    attach_direct_categories,
    live_category_id,
    live_fields_prompt,
    merge_live_fields,
    overlay_live_fields,
    pick_ebay_suggestion,
    pick_etsy_leaf,
)
from vendoo_studio.services.ebay_listings import ebay_category_id


class DirectCategoryChoiceTest(unittest.IsolatedAsyncioTestCase):
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

    def test_a_live_ebay_category_does_not_replace_vendoo(self):
        listing = {"marketplace_category_ids": {"ebay": "111"}, "direct_category_ids": {"ebay": "15687"}}
        self.assertEqual(ebay_category_id(listing), "15687")
        self.assertEqual(listing["marketplace_category_ids"]["ebay"], "111")

    def test_vendoo_category_is_used_when_no_live_leaf_was_chosen(self):
        self.assertEqual(ebay_category_id({"marketplace_category_ids": {"ebay": "111"}}), "111")

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

    def test_the_generation_prompt_names_live_fields_and_keeps_vendoo_names(self):
        listing = {"direct_category_ids": {"ebay": "15687", "ebay_path": "Clothing > T-Shirts"}}
        rows = [{"name": "Sleeve Length", "required": True, "values": [{"label": "Short"}]}]
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            text = live_fields_prompt(listing)
        self.assertIn("Sleeve Length (required)", text)
        self.assertIn("Send to Vendoo reads those", text)
        self.assertIn("Fill these under ebay_specifics using", text)

    async def test_the_vendoo_ebay_id_is_the_live_leaf_when_none_was_chosen(self):
        listing = {"marketplace_category_ids": {"ebay": "260968", "etsy": "392"}}
        self.assertEqual(live_category_id(listing, "ebay"), "260968")
        self.assertEqual(live_category_id(listing, "etsy"), "")
        self.assertEqual(live_category_id({**listing, "direct_category_ids": {"ebay": "57988"}}, "ebay"), "57988")
        rows = [{"name": "Outer Shell Material", "required": True, "values": [{"label": "Polyester"}]}]
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            self.assertIn("(id 260968)", live_fields_prompt(listing))
        live = AsyncMock(return_value=[{"name": "Outer Shell Material", "required": True, "mode": "free_text",
                                        "values": [{"label": "Polyester"}]}])
        with patch("vendoo_studio.services.marketplace_schemas.fields", live):
            fields = await overlay_live_fields(listing, "ebay", [])
        live.assert_awaited_once_with("ebay", "260968")
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
             patch("vendoo_studio.services.chrome_catalog.chrome_category_ids", new=AsyncMock(return_value={})):
            await attach_direct_categories(listing, "cotton tee")
        self.assertEqual(listing["marketplace_category_ids"], {"ebay": "111", "etsy": "222"})
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["etsy"], "482")

    async def test_poshmark_and_depop_categories_are_stored_beside_vendoo(self):
        listing = {
            "title": "Nike tee",
            "marketplace_categories": {"poshmark": "Men > Shirts > Tees", "depop": "Men > Tops > T-Shirts"},
            "marketplace_category_ids": {"ebay": "111", "poshmark": "stale", "depop": "old_leaf"},
        }
        chrome = {"poshmark": "dept/shirts/tees", "depop": "menswear_tshirts"}
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value=None)), \
             patch("vendoo_studio.services.direct_categories._etsy_leaf", new=AsyncMock(return_value=None)), \
             patch("vendoo_studio.services.chrome_catalog.chrome_category_ids", new=AsyncMock(return_value=chrome)):
            await attach_direct_categories(listing, "")
        self.assertEqual(listing["marketplace_category_ids"], {"ebay": "111", "poshmark": "stale", "depop": "old_leaf"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], "dept/shirts/tees")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")


if __name__ == "__main__":
    unittest.main()
