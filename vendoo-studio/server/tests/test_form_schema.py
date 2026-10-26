"""Each marketplace form is that category's own fields for this item."""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services.form_schema import department_bucket, field_applies, form_schema_prompt
from vendoo_studio.services.vendoo_specifics import FieldSpec

SCALES = {
    "us-men": {"display": "US Men's", "options": {"32": "32", "34": "34"}},
    "us-women": {"display": "US Women's", "options": {"0": "0", "8": "8"}},
}

LISTING = {
    "title": "Levi's 32 Jeans",
    "department": "Men",
    "condition": "Pre-Owned - Good",
    "size": "32",
    "marketplace_category_ids": {
        "ebay": "11483", "poshmark": "jeans-men", "mercari": "1931", "depop": "jeans",
        "etsy": "12", "sellwild": "246", "vinted": "9",
    },
    "ebay_specifics": {"inseam": "30", "Fabric Weight": "Lightweight"},
    "poshmark_specifics": {"size": "Waist 32"},
}


def _specs(marketplace: str) -> dict[str, FieldSpec]:
    size = FieldSpec("size", display="Size", required=True, selection_only=True, scales=SCALES)
    nwt = FieldSpec("nwt", display="New with tags", options={"yes": "Yes"})
    womens = FieldSpec("womensSize", display="Women's Size", selection_only=True, options={"0": "0", "8": "8"})
    specs = {"size": size, "nwt": nwt, "womensSize": womens}
    if marketplace == "ebay":
        specs["inseam"] = FieldSpec(
            "inseam", display="Inseam", selection_only=True, options={"30": "30", "32": "32"},
        )
        specs["sleeve"] = FieldSpec(
            "sleeve", display="Sleeve Length", required=True, selection_only=True,
            options={"short": "Short Sleeve"},
        )
    elif marketplace == "poshmark":
        specs["color"] = FieldSpec("color", display="Color", selection_only=True, options={"red": "Red", "blue": "Blue"})
    elif marketplace == "mercari":
        specs["brand"] = FieldSpec("brand", display="Brand", options={"levis": "Levi's"})
    elif marketplace == "depop":
        specs["source"] = FieldSpec("source", display="Source", selection_only=True, options={"preloved": "Preloved"})
    elif marketplace == "etsy":
        specs["occasion"] = FieldSpec("occasion", display="Occasion", selection_only=True, options={"casual": "Casual"})
    elif marketplace == "sellwild":
        specs["material"] = FieldSpec("material", display="Material", selection_only=True, options={"denim": "Denim"})
    elif marketplace == "vinted":
        specs["color"] = FieldSpec("color", display="Color", selection_only=True, options={"blue": "Blue"})
    return specs


LIVE_ASPECTS = [
    {"name": "Sleeve Length", "required": True, "usage": "RECOMMENDED", "mode": "selection",
     "values": [{"label": "Short Sleeve"}, {"label": "Long Sleeve"}]},
    {"name": "Theme", "usage": "OPTIONAL", "values": [{"label": "Western"}]},
    {"name": "New with tags", "usage": "RECOMMENDED", "values": [{"label": "Yes"}]},
    {"id": "condition", "name": "Condition", "usage": "", "values": [{"label": "Pre-owned"}]},
]


class FormSchemaTest(unittest.TestCase):
    def test_department_and_condition_decide_which_fields_apply(self):
        self.assertFalse(field_applies("Women's Size", LISTING))
        self.assertFalse(field_applies("New with tags", LISTING))
        self.assertTrue(field_applies("Sleeve Length", LISTING))
        self.assertTrue(field_applies("Size", LISTING))
        fresh = {"condition": "New with tags"}
        self.assertFalse(field_applies("Pre-owned condition", fresh))
        self.assertTrue(field_applies("Sleeve Length", fresh))
        # "men" inside "women" is still women's.
        self.assertEqual(department_bucket({"department": "Women"}), "women")
        self.assertEqual(department_bucket({"department": "Men"}), "men")

    def test_a_sample_item_form_matches_each_marketplace_schema(self):
        from vendoo_studio.services import category_fields as cf

        def load_fields(marketplace, _category_id):
            return _specs(marketplace)

        stored = {
            **LISTING,
            # A numeric eBay id would ask eBay for live aspects. This test is the category schema.
            "marketplace_category_ids": {key: "leaf" for key in LISTING["marketplace_category_ids"]},
        }
        with mock.patch.object(cf, "listing_category_ids", return_value=dict(LISTING["marketplace_category_ids"])), \
                mock.patch.object(cf, "load_fields", side_effect=load_fields), \
                mock.patch("vendoo_studio.repositories.queries.ConversationRepo.get", return_value=object()), \
                mock.patch("vendoo_studio.repositories.queries.ListingRepo.get_revisions",
                           return_value=[type("R", (), {"listing_json": stored})()]):
            body = TestClient(app).get("/api/conversations/c1/vendoo-api/fields").json()

        forms = {form["marketplace"]: form for form in body["forms"]}
        self.assertEqual(set(forms), set(LISTING["marketplace_category_ids"]))
        for marketplace, specs in ((name, _specs(name)) for name in forms):
            form = forms[marketplace]
            self.assertTrue(form["known"], marketplace)
            shown = sorted(row["label"] for row in form["fields"] if row["on_form"])
            expected = sorted(spec.display for spec in specs.values() if field_applies(spec.display, LISTING))
            self.assertEqual(shown, expected, marketplace)
            size = next(row for row in form["fields"] if row["label"] == "Size")
            self.assertEqual(size["options"], ["32", "34"], marketplace)
            self.assertNotIn("0", size["options"], marketplace)
            self.assertNotIn("8", size["options"], marketplace)
            self.assertEqual(size["options_source"], "vendoo")
            self.assertTrue(size["selection_only"])
            womens = next(row for row in form["fields"] if row["label"] == "Women's Size")
            self.assertFalse(womens["on_form"])
            self.assertEqual(womens["options"], ["0", "8"])
            nwt = next(row for row in form["fields"] if row["label"] == "New with tags")
            self.assertFalse(nwt["on_form"])
        ebay = {row["label"]: row for row in forms["ebay"]["fields"]}
        self.assertEqual(ebay["Inseam"]["options"], ["30", "32"])
        self.assertEqual(ebay["Sleeve Length"]["options"], ["Short Sleeve"])
        self.assertTrue(ebay["Sleeve Length"]["on_form"])
        self.assertEqual(ebay["Fabric Weight"]["value"], "Lightweight")
        self.assertFalse(ebay["Fabric Weight"]["on_form"])
        self.assertIn("Material", ebay)
        self.assertFalse(ebay["Material"]["on_form"])
        self.assertTrue(ebay["Material"]["options"])

    def test_optional_ebay_aspects_stay_off_the_form_and_keep_their_choices(self):
        from vendoo_studio.services.direct_categories import merge_live_fields

        rows = {row["label"]: row for row in merge_live_fields([], LIVE_ASPECTS, LISTING, "ebay")}
        self.assertNotIn("Condition", rows)
        sleeve = rows["Sleeve Length"]
        self.assertTrue(sleeve["on_form"])
        self.assertEqual(sleeve["options"], ["Short Sleeve", "Long Sleeve"])
        self.assertEqual(sleeve["options_source"], "marketplace")
        self.assertTrue(sleeve["selection_only"])
        theme = rows["Theme"]
        self.assertFalse(theme["on_form"])
        self.assertEqual(theme["options"], ["Western"])
        self.assertFalse(rows["New with tags"]["on_form"])

    def test_chat_lists_the_same_fields_the_form_shows(self):
        def load_rows(marketplace, _category_id):
            if marketplace == "ebay_aspects":
                return LIVE_ASPECTS
            return None

        def load_fields(marketplace, _category_id):
            return _specs(marketplace)

        with mock.patch("vendoo_studio.services.category_fields.load_rows", side_effect=load_rows), \
                mock.patch("vendoo_studio.services.category_fields.load_fields", side_effect=load_fields):
            text = form_schema_prompt(LISTING)

        self.assertIn("Sleeve Length (required) — Short Sleeve, Long Sleeve", text)
        self.assertNotIn("Theme", text)
        self.assertNotIn("New with tags", text)
        self.assertIn("- Size (required) — 32, 34", text)
        self.assertNotIn("Condition — Pre-owned", text)
        self.assertNotIn("Women's Size", text)
        self.assertIn("poshmark", text)
        self.assertIn("- Color — Blue, Red", text)
        for marketplace in ("mercari", "depop", "etsy", "sellwild", "vinted"):
            self.assertIn(marketplace, text)
        self.assertIn("- Material — Denim", text)
        self.assertIn("- Source — Preloved", text)
        self.assertIn("- Occasion — Casual", text)
