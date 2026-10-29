"""eBay's condition on a direct listing: shown, saved and explained the same way.

The seller writes the condition in Studio's words ("Pre-Owned - Good"). The
editor draws it from the grade eBay would use, and the saved form has to carry
that same grade — with plain wording the leaf's exact-match encoder found
nothing, so the listing went out with its condition empty while the row showed
a value, and the pre-flight reported it as an empty required field.
"""
from __future__ import annotations

import unittest

from vendoo_studio.services.direct_preflight import _unresolved
from vendoo_studio.services.ebay_listings import CONDITION_IDS
from vendoo_studio.services.vendoo_item_build import _listing_section
from vendoo_studio.services.vendoo_specifics import normalize_specifics

LEAF = "53159"

# eBay's apparel condition aspect, as Vendoo's schema for the leaf describes it.
EBAY_SPECS = normalize_specifics({
    "condition": {
        "id": "condition", "display": "Condition",
        "options": {code: {"id": code, "display": label} for code, label in (
            ("1000", "New"),
            ("3000", "Pre-owned"),
            ("5000", "Used"),
        )},
        "rules": {"fieldOptions": {"minValues": 1, "maxValues": 1, "selectionMode": "SelectionOnly"}},
    },
})


def section(listing: dict) -> dict:
    """The eBay section for a listing whose leaf is resolved, as a direct List builds it.

    ``unresolved`` is the list the builder appends to, so it rides along on the
    answer the way ``vendoo_form`` carries it.
    """
    listing = {
        **listing,
        "marketplace_category_ids": {"ebay": LEAF},
        "marketplace_category_objects": {"ebay": {"id": LEAF, "path": "Tops", "displayPath": "Tops"}},
    }
    unresolved: list[dict] = []
    out = _listing_section("ebay", listing, {}, None, unresolved, EBAY_SPECS)
    return {**out, "unresolved": unresolved}


class EbayConditionEncodingTest(unittest.TestCase):
    def test_plain_wording_reaches_the_form_as_ebay_s_own_grade(self):
        # A leaf that offers the grade the editor shows sends it, never empty.
        for wording, grade in (("Pre-Owned - Good", "3000"), ("New with tags", "1000")):
            with self.subTest(wording=wording):
                out = section({"condition": wording, "ebay_specifics": {}})
                self.assertEqual(out["overrides"]["condition"], grade)
                self.assertEqual(out["categorySpecifics"][f"{LEAF}_condition"], grade)

    def test_new_with_tags_is_new(self):
        out = section({"condition": "New with tags", "ebay_specifics": {}})
        self.assertEqual(out["categorySpecifics"][f"{LEAF}_condition"], CONDITION_IDS["NEW"])

    def test_the_editor_and_the_saved_form_agree(self):
        # This is the invariant the screenshot broke: what the row shows is what is sent.
        from vendoo_studio.services.ebay_listings import ebay_condition

        listing = {"condition": "Pre-Owned - Good", "ebay_specifics": {}}
        self.assertEqual(CONDITION_IDS[ebay_condition(listing)], section(listing)["overrides"]["condition"])

    def test_an_unknown_condition_falls_back_to_the_grade_the_editor_shows(self):
        out = section({"condition": "Mystery grade", "ebay_specifics": {}})
        self.assertEqual(out["overrides"].get("condition"), "3000")  # eBay's documented fallback
        # The fallback is the same one the editor shows, so nothing is left unresolved.
        self.assertEqual([row for row in out.get("unresolved") or [] if row.get("field") == "condition:ebay"], [])

    def test_a_grade_this_leaf_does_not_offer_is_reported_rather_than_invented(self):
        # eBay grades Excellent 2990, which this leaf's list does not carry. The
        # editor shows 2990, so the listing must not quietly become 3000.
        out = section({"condition": "Pre-Owned - Excellent", "ebay_specifics": {}})
        self.assertNotIn("condition", out["overrides"])
        self.assertEqual([row for row in out.get("unresolved") or [] if row.get("field") == "condition:ebay"],
                         [{"field": "condition:ebay", "value": "Pre-Owned - Excellent"}])


class EbayConditionGapWordingTest(unittest.TestCase):
    def test_a_condition_that_cannot_be_matched_says_so(self):
        snapshot = {"form": {"unresolved": [{"field": "ebay:condition", "value": "Mystery grade"}]}}
        found = _unresolved(snapshot, "ebay")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["blocking"])
        self.assertIn("“Mystery grade”", found[0]["message"])
        self.assertNotIn("and it is empty", found[0]["message"])

    def test_the_answer_keeps_the_name_of_the_row_that_carries_it(self):
        snapshot = {"form": {"unresolved": [{"field": "ebay:condition", "value": ""}]}}
        self.assertEqual(_unresolved(snapshot, "ebay")[0]["field"], "ebay_specifics.category_specifics.condition")

    def test_other_aspects_keep_their_own_wording(self):
        snapshot = {"form": {"unresolved": [{"field": "ebay:Color", "value": "Chartreuse"}]}}
        found = _unresolved(snapshot, "ebay")
        self.assertIn("is not one of eBay's choices", found[0]["message"])


if __name__ == "__main__":
    unittest.main()
