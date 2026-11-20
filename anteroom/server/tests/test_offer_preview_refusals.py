from __future__ import annotations

import unittest

from anteroom.services import offer_preview_refusals as refusals

CHECK = "marketplace.promotion_check"
DEPOP = {"ok": False, "error": "Depop refused the request (HTTP 404).", "error_code": "rejected", "safe_to_retry": True,
         "marketplace_message": "Product not found", "preview_step": "depop-product"}
MERCARI = {"ok": False, "error": "Mercari refused the request.", "error_code": "rejected", "safe_to_retry": True,
           "marketplace_message": "Oops! Your offer can't be less than $1.00.", "preview_step": "mercari-eligibility"}


class OfferPreviewRefusalsTest(unittest.TestCase):
    def test_the_observed_replies_are_recognized(self):
        self.assertEqual(refusals.recognized("depop", CHECK, "offer", DEPOP), refusals.DEPOP_NOT_FOUND)
        self.assertEqual(refusals.recognized("mercari", CHECK, "offer", MERCARI), refusals.MERCARI_REFUSED)

    def test_anything_else_is_not(self):
        cases = [
            ("depop", CHECK, "offer", {k: v for k, v in DEPOP.items() if k != "preview_step"}),
            ("depop", CHECK, "offer", {**DEPOP, "preview_step": "depop-offer-preview"}),
            ("depop", CHECK, "offer", {**DEPOP, "preview_step": "depop-account"}),
            ("depop", CHECK, "offer", {**DEPOP, "error": "Depop had a problem answering (HTTP 503)."}),
            ("depop", CHECK, "offer", {**DEPOP, "marketplace_message": "Not found"}),
            ("depop", CHECK, "offer", {**DEPOP, "error_code": "uncertain"}),
            ("depop", CHECK, "offer", {**DEPOP, "safe_to_retry": False}),
            ("depop", CHECK, "offer", {**DEPOP, "statusCode": 404}),
            ("depop", CHECK, "offer", {**DEPOP, "state": "unknown"}),
            ("depop", CHECK, "offer", {**DEPOP, "marketplace": "mercari"}),
            ("depop", CHECK, "share", DEPOP),
            ("depop", "marketplace.promote", "offer", DEPOP),
            ("mercari", CHECK, "offer", DEPOP),
            ("depop", CHECK, "offer", MERCARI),
            ("mercari", CHECK, "offer", {**MERCARI, "preview_step": "mercari-bounds"}),
            ("mercari", CHECK, "offer", {**MERCARI, "ok": True}),
            ("mercari", CHECK, "offer", {**MERCARI, "marketplace_message": "You can make an offer again in 47 hours. (once every 3 days)"}),
            ("mercari", CHECK, "offer", "Mercari refused the request."),
        ]
        for marketplace, operation, action, result in cases:
            with self.subTest(marketplace=marketplace, operation=operation, action=action, result=result):
                self.assertIsNone(refusals.recognized(marketplace, operation, action, result))

    def test_held_note_matches_only_the_journaled_forms(self):
        self.assertTrue(refusals.held_note(refusals.DEPOP_NOT_FOUND, None))
        self.assertTrue(refusals.held_note("Depop could not find a listing at this item's saved Depop link: Product not found",
                                           "Product not found"))
        self.assertFalse(refusals.held_note("Depop could not find a listing at this item's saved Depop link: Something else",
                                            "Product not found"))
        self.assertFalse(refusals.held_note("Depop refused the request (HTTP 404): Product not found", "Product not found"))
        self.assertFalse(refusals.held_note(None, None))
