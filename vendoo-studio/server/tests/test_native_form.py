from __future__ import annotations

import unittest

from vendoo_studio.models.ebay_fields import EBAY_CONDITION_DESCRIPTION
from vendoo_studio.services.native_form import vendoo_form

LISTING = {
    "title": "Tee",
    "description": "Soft cotton",
    "price": 18,
    "brand": "Hanes",
    "condition": "Pre-Owned - Good",
}


class NativeFormTest(unittest.TestCase):
    def test_direct_form_carries_the_defaults_vendoo_would_write(self):
        mercari = vendoo_form(LISTING, "mercari")
        self.assertIn("Ground Advantage", mercari["specifics"]["shippingLabel"])
        self.assertEqual(mercari["specifics"]["shipping"]["payerId"], 1)
        self.assertEqual(mercari["overrides"]["brand"], "Hanes")

        ebay = vendoo_form(LISTING, "ebay")
        self.assertEqual(ebay["specifics"]["conditionDescription"], EBAY_CONDITION_DESCRIPTION)
        self.assertEqual(ebay["specifics"]["pricingFormat"], "FixedPriceItem")
        self.assertTrue(ebay["specifics"]["pricingFormatDetails"]["fixedPrice"]["allowBestOffer"])

        poshmark = vendoo_form({**LISTING, "price": 20}, "poshmark")
        self.assertTrue(poshmark["specifics"]["smartSell"]["enabled"])
        self.assertTrue(poshmark["specifics"]["smartSell"]["minPrice"])
