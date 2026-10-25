"""Settings → Connections saves one eBay policy without the others."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from vendoo_studio.main import app

PROFILES = {
    "fulfillment": [{"id": "ship-1", "title": "USPS"}, {"id": "ship-2", "title": "UPS"}],
    "payment": [{"id": "pay-1", "title": "Managed payments"}],
    "return": [{"id": "ret-1", "title": "30 days"}],
    "locations": [{"id": "wh", "title": "Home"}],
}


class EbayListingDefaultsRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._data = os.environ.get("VENDOO_STUDIO_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VENDOO_STUDIO_DATA_DIR"] = self.tmp.name
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._data is None:
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
        else:
            os.environ["VENDOO_STUDIO_DATA_DIR"] = self._data

    def test_one_shipping_policy_saves_when_the_other_choices_are_blank(self):
        """The account has several shipping policies and one of everything else.
        The screen sends the chosen shipping id and blanks for the rest."""
        with patch("vendoo_studio.routes.ebay.ebay_listings.listing_profiles", new=AsyncMock(return_value=PROFILES)):
            response = self.client.put("/api/ebay/listing-defaults", json={
                "fulfillment_policy_id": "ship-1",
                "payment_policy_id": "",
                "return_policy_id": "",
                "merchant_location_key": "",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"fulfillment_policy_id": "ship-1"})
        self.assertNotIn("pattern", response.text)

    def test_a_policy_from_another_account_is_refused(self):
        with patch("vendoo_studio.routes.ebay.ebay_listings.listing_profiles", new=AsyncMock(return_value=PROFILES)):
            response = self.client.put("/api/ebay/listing-defaults", json={"fulfillment_policy_id": "someone-else"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("connected eBay account", response.text)
        self.assertNotIn("pattern", response.text)
