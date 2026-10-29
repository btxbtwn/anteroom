"""The captured category forms must work on a fresh listing, without Chrome."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from vendoo_studio.services import depop_form


class DepopFormTest(unittest.TestCase):
    def test_previously_missing_tshirts_have_department_sizes_without_chrome(self):
        for department, size, count in (("menswear", "3XS", 14), ("womenswear", "00", 39),
                                        ("kidswear", "0-3 months", 23)):
            with self.subTest(department=department), patch(
                "vendoo_studio.services.browser_bridge.request", side_effect=AssertionError("Chrome used")
            ):
                result = depop_form.form({"direct_category_ids": {"depop": department + "_tshirts"}})
                fields = {row["path"]: row for row in result["fields"]}
                self.assertTrue(result["known"])
                self.assertEqual(len(fields["depop_specifics.size"]["options"]), count)
                self.assertIn(size, fields["depop_specifics.size"]["options"])
                self.assertIn("Cotton", fields["depop_specifics.attributes.material"]["options"])
                self.assertEqual(fields["depop_specifics.attributes.occasion"]["max_values"], 3)

    def test_fixed_choices_keep_native_ids_that_differ_from_the_labels(self):
        fields = {row["path"]: row for row in depop_form.form({})["fields"]}
        for name, label, native_id in (("style", "Utility", "techwear"), ("style", "Y2K", "y2_k"),
                                       ("age", "00s", "y2k"), ("source", "Reworked / Upcycled", "reworked")):
            row = fields["depop_specifics." + name]
            self.assertIn(label, row["options"])
            self.assertEqual(row["option_values"][label], native_id)

    def test_worldwide_fee_is_required_only_when_worldwide_shipping_is_enabled(self):
        for enabled in (False, True):
            form = depop_form.form({"depop_specifics": {"worldwideShipping": {"enabled": enabled}}})
            row = next(row for row in form["fields"] if row["path"] == "depop_specifics.worldwideShipping.price")
            self.assertEqual(row["required"], enabled)

    def test_every_captured_category_preserves_fields_choices_limits_and_order(self):
        categories = depop_form.catalog()["categories"]
        self.assertEqual(len(categories), 281)
        with patch("vendoo_studio.services.browser_bridge.request", side_effect=AssertionError("Chrome used")):
            for category in categories:
                with self.subTest(category=category["id"]):
                    result = depop_form.form({"direct_category_ids": {"depop": category["id"]}})
                    self.assertEqual(result["category_id"], category["id"])
                    fields = result["fields"]
                    attributes = [row for row in fields if row["path"].startswith("depop_specifics.attributes.")]
                    captured = [row for row in category["fields"] if row["id"].startswith("attributes.")]
                    self.assertEqual([row["label"] for row in attributes], [row["label"] for row in captured])
                    for actual, source in zip(attributes, captured, strict=True):
                        self.assertEqual(actual["options"], source["options"])
                        self.assertEqual(actual["max_values"], source["max_values"])
                    sizes = [row for row in fields if row["path"] == "depop_specifics.size"]
                    source_sizes = [row for row in category["fields"] if row["id"] == "variants"]
                    self.assertEqual(bool(sizes), bool(source_sizes))
                    if sizes:
                        self.assertEqual(sizes[0]["options"], source_sizes[0]["options"])
                    self.assertNotIn("Title", [row["label"] for row in fields])

    def test_unselected_and_retired_categories_offer_only_active_category_choices(self):
        for category in ("", "menswear_necklace", "invented_category"):
            result = depop_form.form({"direct_category_ids": {"depop": category}})
            fields = {row["path"]: row for row in result["fields"]}
            choices = fields["direct_category_ids.depop"]["option_values"]
            self.assertEqual(len(choices), 281)
            self.assertNotIn("menswear_necklace", choices.values())
            self.assertEqual(result["category_id"], "")
            self.assertEqual(result["known"], not category)
            self.assertIn("depop_specifics.condition", fields)
            self.assertNotIn("depop_specifics.size", fields)

    def test_seller_values_survive_without_mutating_the_catalog_or_listing(self):
        listing = {"description": "General", "direct_category_ids": {"depop": "menswear_jeans"},
                   "depop_specifics": {"description": "", "size": '32"', "condition": "used_good",
                                       "attributes": {"material": ["Cotton"]}, "style": []}}
        original = copy.deepcopy(listing)
        fields = {row["path"]: row for row in depop_form.form(listing)["fields"]}
        self.assertEqual(fields["depop_specifics.description"]["value"], "")
        self.assertEqual(fields["depop_specifics.size"]["value"], '32"')
        self.assertEqual(fields["depop_specifics.style"]["value"], [])
        self.assertIn("used_good", fields["depop_specifics.condition"]["option_values"].values())
        fields["depop_specifics.attributes.material"]["options"].clear()
        again = {row["path"]: row for row in depop_form.form(listing)["fields"]}
        self.assertIn("Cotton", again["depop_specifics.attributes.material"]["options"])
        self.assertEqual(listing, original)
