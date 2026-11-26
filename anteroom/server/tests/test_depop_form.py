"""The captured category forms must work on a fresh listing, without Chrome."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from anteroom.services import depop_form


class DepopFormTest(unittest.TestCase):
    def test_jeans_show_the_offered_parcels_and_flat_measurements(self):
        listing = {"direct_category_ids": {"depop": "womenswear_jeans"}, "depop_specifics": {
            "measurements": {"values": [{"id": "waist", "value": "14", "unit": "in"}]},
            "measurementInputs": {"outseam": "90 cm"},
        }}
        rows = depop_form.form(listing)["fields"]
        self.assertEqual([(row["label"], row["value"]) for row in rows if ".measurementInputs." in row["path"]],
                         [("Waist", "14 in"), ("Inseam", ""), ("Outseam", "90 cm")])
        parcel = next(row for row in rows if row["label"] == "Package size")
        self.assertEqual(parcel["options"], ["Medium", "Large", "Extra large"])
        self.assertIn("suggests Large", parcel["help"])

        from anteroom.services import native_contract

        seed = {**listing, native_contract.KEY: native_contract.capture(listing, {"depop": depop_form.form(listing)}, ["depop"])}
        generated = {**listing, "depop_specifics": {"parcelSize": "Extra small"}}
        native_contract.constrain_generated(generated, seed)
        self.assertEqual(generated["depop_specifics"]["parcelSize"], "Large")

    def test_seller_package_choice_survives_weight_defaults(self):
        from anteroom.services.depop_fields import ensure_depop_category_optionals

        listing = {"weight_oz": 3, "seller_fields": ["depop_specifics.parcelSize"],
                   "depop_specifics": {"parcelSize": "Large"}}
        ensure_depop_category_optionals(listing)
        self.assertEqual(listing["depop_specifics"]["parcelSize"], "Large")

    def test_unbranded_uses_depop_other_without_changing_general_brand(self):
        for brand in ("Unbranded", "No brand"):
            listing = {"brand": brand}
            field = next(row for row in depop_form.form(listing)["fields"] if row["path"] == "depop_specifics.brand")
            self.assertEqual(field["value"], "Other")
            self.assertIn(field["value"], field["options"])
            self.assertEqual(listing["brand"], brand)

    def test_brand_picker_uses_local_native_choices_and_preserves_the_seller_brand(self):
        for listing in ({"brand": "Nike"}, {"brand": "Nike", "depop_specifics": {"brand": ""}}):
            fields = {row["path"]: row for row in depop_form.form(listing)["fields"]}
            brand = fields["depop_specifics.brand"]
            self.assertTrue(brand["selection_only"])
            self.assertIn("Nike", brand["options"])
            self.assertIn("Other", brand["options"])
            self.assertEqual(len(brand["options"]), 21697)
            self.assertEqual(brand["value"], listing.get("depop_specifics", {}).get("brand", "Nike"))

    def test_previously_missing_tshirts_have_department_sizes_without_chrome(self):
        for department, size, count in (("menswear", "3XS", 14), ("womenswear", "00", 39),
                                        ("kidswear", "0-3 months", 23)):
            with self.subTest(department=department), patch(
                "anteroom.services.browser_bridge.request", side_effect=AssertionError("Chrome used")
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
        with patch("anteroom.services.browser_bridge.request", side_effect=AssertionError("Chrome used")):
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

    def test_the_size_row_shows_the_general_size_in_the_categorys_spelling(self):
        def size_row(listing):
            return {row["path"]: row for row in depop_form.form(listing)["fields"]}["depop_specifics.size"]

        general = size_row({"direct_category_ids": {"depop": "menswear_jeans"}, "size": "36"})
        self.assertEqual(general["value"], '36"')
        self.assertIn(general["value"], general["options"])
        own = size_row({"direct_category_ids": {"depop": "menswear_jeans"}, "size": "36",
                        "depop_specifics": {"size": "38"}})
        self.assertEqual(own["value"], '38"')
        cleared = size_row({"direct_category_ids": {"depop": "menswear_jeans"}, "size": "36",
                            "depop_specifics": {"size": ""}})
        self.assertEqual(cleared["value"], "")

    def test_only_the_tabs_own_category_shows_its_size_list(self):
        form = depop_form.form({"direct_category_ids": {"depop": "menswear_jeans"}, "size": "36"})
        fields = {row["path"]: row for row in form["fields"]}
        self.assertEqual(form["category_id"], "menswear_jeans")
        self.assertEqual(fields["depop_specifics.size"]["value"], '36"')
        vendoo = depop_form.form({"marketplace_category_ids": {"depop": "menswear_jeans"}, "size": "36"})
        self.assertEqual(vendoo["category_id"], "")

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

    def test_condition_and_colour_follow_general_without_overwriting_seller_clears(self):
        listing = {
            "direct_category_ids": {"depop": "menswear_tshirts"},
            "condition": "Good",
            "primaryColor": "White",
            "secondaryColor": "Gray",
            "size": "S",
        }
        fields = {row["path"]: row for row in depop_form.form(listing)["fields"]}
        self.assertEqual(fields["depop_specifics.condition"]["value"], "used_good")
        self.assertEqual(fields["depop_specifics.colour"]["value"], ["white", "grey"])
        self.assertEqual(fields["depop_specifics.size"]["value"], "S")

        # An explicit seller clear on Depop stays blank instead of re-inheriting General.
        cleared = {
            **listing,
            "depop_specifics": {"condition": "", "colour": [], "size": ""},
        }
        cleared_fields = {row["path"]: row for row in depop_form.form(cleared)["fields"]}
        self.assertEqual(cleared_fields["depop_specifics.condition"]["value"], "")
        self.assertEqual(cleared_fields["depop_specifics.colour"]["value"], [])
        self.assertEqual(cleared_fields["depop_specifics.size"]["value"], "")

    def test_depop_followed_size_and_condition_have_no_empty_gaps(self):
        from anteroom.services import native_contract

        listing = {
            "description": "Vintage sun valley long sleeve t-shirt.",
            "brand": "Hind Performance",
            "condition": "Good",
            "primaryColor": "White",
            "size": "S",
            "price": 18,
            "direct_category_ids": {"depop": "menswear_tshirts"},
            "depop_specifics": {"parcelSize": "Small"},
        }
        listing[native_contract.KEY] = native_contract.capture(
            listing, {"depop": depop_form.form(listing)}, ["depop"],
        )
        blocking = [gap["field"] for gap in native_contract.gaps(listing, "depop") if gap.get("blocking")]
        self.assertEqual(blocking, [])

        # Explicit seller clears on required fields still block.
        cleared = copy.deepcopy(listing)
        cleared["depop_specifics"] = {"size": "", "condition": "", "parcelSize": ""}
        cleared[native_contract.KEY] = native_contract.capture(
            cleared, {"depop": depop_form.form(cleared)}, ["depop"],
        )
        cleared_blocking = {gap["field"] for gap in native_contract.gaps(cleared, "depop") if gap.get("blocking")}
        self.assertEqual(
            cleared_blocking,
            {"depop_specifics.size", "depop_specifics.condition", "depop_specifics.parcelSize"},
        )
