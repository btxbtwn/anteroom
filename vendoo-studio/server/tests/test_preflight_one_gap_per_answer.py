"""One missing answer is said once, whichever check found it.

The module requirements and the captured native form both report a field. Read
under its own row, the pair became two sentences about one empty value, and the
form's generic wording repeated what the marketplace-specific check had already
said better.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from vendoo_studio.services import direct_preflight


class OneGapPerAnswerTest(unittest.TestCase):
    def gaps(self, found: list[dict], marketplace: str = "ebay") -> list[dict]:
        """``gaps`` with the checks this test supplies instead of the real modules."""
        with patch.object(direct_preflight, "_REQUIREMENTS", {marketplace: [lambda snapshot, context: found]}), \
             patch.object(direct_preflight, "_unresolved", return_value=[]), \
             patch("vendoo_studio.services.native_contract.gaps", return_value=[]), \
             patch.object(direct_preflight, "RULE_MODULES", ()):
            return direct_preflight.gaps({}, marketplace)

    def test_two_checks_naming_one_field_say_it_once(self):
        found = [direct_preflight.gap("ebay_specifics.Sport", "eBay needs Sport."),
                 direct_preflight.gap("ebay_specifics.Sport", "Sport: Required field is empty.")]
        self.assertEqual([item["message"] for item in self.gaps(found)], ["eBay needs Sport."])

    def test_the_specific_wording_wins_over_the_generic_one(self):
        found = [direct_preflight.gap("price", "Item price: Required field is empty."),
                 direct_preflight.gap("price", "Poshmark needs a price of $3 or more.")]
        self.assertEqual([item["message"] for item in self.gaps(found, "poshmark")],
                         ["Poshmark needs a price of $3 or more."])

    def test_a_blocking_report_wins_over_a_note(self):
        found = [direct_preflight.gap("photos", "The first 8 of 9 are sent.", blocking=False),
                 direct_preflight.gap("photos", "Depop needs a photo.")]
        self.assertEqual(self.gaps(found, "depop"), [direct_preflight.gap("photos", "Depop needs a photo.")])

    def test_general_and_its_marketplace_copy_are_one_answer(self):
        found = [direct_preflight.gap("description", "Mercari needs a description."),
                 direct_preflight.gap("mercari_specifics.description", "Description: Required field is empty.")]
        self.assertEqual([item["message"] for item in self.gaps(found, "mercari")],
                         ["Mercari needs a description."])

    def test_answers_that_only_share_a_word_stay_separate(self):
        # Worldwide shipping has its own price, so it is its own gap.
        found = [direct_preflight.gap("price", "Item price: Required field is empty."),
                 direct_preflight.gap("depop_specifics.worldwideShipping.price",
                                      "Worldwide Shipping: Required field is empty.")]
        self.assertEqual(len(self.gaps(found, "depop")), 2)


if __name__ == "__main__":
    unittest.main()
