from __future__ import annotations

import copy
import unittest

from vendoo_studio.services import native_contract, poshmark_form


class PoshmarkFormTest(unittest.TestCase):
    def test_hidden_optional_controls_do_not_block_a_listing(self):
        rows = {row["path"]: row for row in poshmark_form.form({})["fields"]}
        self.assertFalse(rows["poshmark_specifics.smartSell.minimumPrice"]["required"])
        self.assertFalse(rows["poshmark_specifics.makeAvailableAt"]["required"])
        rows = {row["path"]: row for row in poshmark_form.form({"poshmark_specifics": {
            "smartSell": {"enabled": True}, "availability": "coming_soon"}})["fields"]}
        self.assertTrue(rows["poshmark_specifics.smartSell.minimumPrice"]["required"])
        self.assertTrue(rows["poshmark_specifics.makeAvailableAt"]["required"])

    def test_general_size_is_matched_only_to_a_verified_size_in_the_selected_group(self):
        catalog = poshmark_form.catalog()
        category = next(iter(catalog["inventory_groups"]))
        standard = next(group for group in catalog["inventory_groups"][category] if group["label"] == "Standard")
        size = standard["systems"]["us"][0]
        listing = {"direct_category_ids": {"poshmark": category}, "size": size["label"]}
        inventory = next(row for row in poshmark_form.form(listing)["fields"] if row.get("input_type") == "inventory")
        self.assertEqual(inventory["value"][0]["sizeSetId"], standard["id"])
        self.assertEqual(inventory["value"][0]["sizeId"], size["id"])
        self.assertIsNone(native_contract.value_error(inventory, inventory["value"]))
        listing["size"] = "not a marketplace size"
        inventory = next(row for row in poshmark_form.form(listing)["fields"] if row.get("input_type") == "inventory")
        self.assertIsNotNone(native_contract.value_error(inventory, inventory["value"]))

    def test_existing_inventory_keeps_multi_item_mode_and_string_quantity_is_accepted(self):
        inventory = [{"sizeSetId": "one", "sizeId": "M", "quantity": 1},
                     {"sizeSetId": "two", "sizeId": "L", "quantity": 1}]
        listing = {"quantity": 1, "poshmark_specifics": {"inventoryRows": inventory}}
        rows = {row["path"]: row for row in poshmark_form.form(listing)["fields"]}
        self.assertEqual(rows["poshmark_specifics.multiItem"]["value"], "true")
        self.assertEqual(rows["poshmark_specifics.inventoryRows"]["value"], inventory)
        rows = {row["path"]: row for row in poshmark_form.form({"quantity": "2"})["fields"]}
        self.assertEqual(rows["poshmark_specifics.multiItem"]["value"], "true")

    def test_all_native_categories_and_size_groups_are_available_locally(self):
        catalog = poshmark_form.catalog()
        self.assertEqual(len(catalog["inventory_groups"]), 114)
        categories = next(row for row in catalog["fields"] if row["path"] == "direct_category_ids.poshmark")
        self.assertEqual(len(categories["options"]), 747)
        for category, groups in catalog["inventory_groups"].items():
            with self.subTest(category=category):
                result = poshmark_form.form({"direct_category_ids": {"poshmark": category}})
                inventory = next(row for row in result["fields"] if row.get("input_type") == "inventory")
                self.assertEqual(inventory["inventory_groups"], groups)

    def test_seller_values_and_earnings_are_current(self):
        listing = {"title": "General", "description": "Details", "brand": "Nike", "price": 20,
                   "poshmark_specifics": {"title": "Own title", "description": "", "styleTags": [], "smartSell": {"enabled": False}}}
        before = copy.deepcopy(listing)
        rows = {row["path"]: row for row in poshmark_form.form(listing)["fields"]}
        self.assertEqual(rows["poshmark_specifics.title"]["value"], "Own title")
        self.assertEqual(rows["poshmark_specifics.description"]["value"], "")
        self.assertEqual(rows["poshmark_specifics.brand"]["value"], "Nike")
        self.assertEqual(rows["poshmark_specifics.styleTags"]["value"], [])
        self.assertEqual(rows["poshmark_specifics.smartSell.enabled"]["value"], False)
        self.assertEqual(rows["poshmark_specifics.earnings"]["value"], "$16.00")
        self.assertEqual(listing, before)

    def test_account_discounts_are_not_invented(self):
        rows = {row["path"]: row for row in poshmark_form.form({})["fields"]}
        self.assertEqual(rows["poshmark_specifics.shippingDiscountId"]["options"], [])
        self.assertIn("account-specific", rows["poshmark_specifics.shippingDiscountId"]["help"])
        listing = {"price": 20, "poshmark_specifics": {"shippingDiscountId": "seller-option"},
                   "_native_forms": {"poshmark": {"fields": [{"path": "poshmark_specifics.shippingDiscountId",
                       "options": ["Seller discount"], "option_values": {"Seller discount": "seller-option"},
                       "option_amounts": {"seller-option": 2}}]}}}
        rows = {row["path"]: row for row in poshmark_form.form(listing)["fields"]}
        self.assertEqual(rows["poshmark_specifics.shippingDiscountId"]["options"], ["Seller discount"])
        self.assertEqual(rows["poshmark_specifics.earnings"]["value"], "$14.00")

    def test_inventory_is_checked_locally_before_publication(self):
        row = {"input_type": "inventory", "required": True, "inventory_system": "us", "inventory_groups": [
            {"id": "standard", "sizes": [{"id": "M"}]}, {"id": "custom", "sizes": []}]}
        valid = [{"sizeSetId": "standard", "sizeId": "M", "quantity": 1}]
        self.assertIsNone(native_contract.value_error(row, valid))
        for invalid in [[], valid + valid,
                        [{"sizeSetId": "wrong", "sizeId": "M", "quantity": 1}],
                        [{"sizeSetId": "standard", "sizeId": "XS", "quantity": 1}],
                        [{"sizeSetId": "standard", "sizeId": "M", "quantity": 0}]]:
            self.assertIsNotNone(native_contract.value_error(row, invalid))
