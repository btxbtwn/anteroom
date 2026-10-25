from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vendoo_studio.services import ebay_listings
from vendoo_studio.services.ebay_client import EbayFailure

LISTING = {
    "title": "Abercrombie tee",
    "description": "Soft cotton tee.",
    "price": "24",
    "quantity": 1,
    "condition": "Pre-Owned - Good",
    "brand": "Abercrombie",
    "marketplace_category_ids": {"ebay": "15687"},
    "ebay_specifics": {"department": "Men", "category_specifics": {"Sleeve Length": "Does Not Apply"}},
    "form": {"overrides": {"brand": "Abercrombie"}, "specifics": {"conditionDescription": "Pre-owned, washed, no stains."}},
}


class Client:
    def __init__(self, policies=1):
        self.policies = policies
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("get_item_aspects_for_category"):
            return {"aspects": [
                {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}},
                {"localizedAspectName": "Sleeve Length", "aspectConstraint": {"aspectRequired": True, "aspectMode": "SELECTION_ONLY"},
                 "aspectValues": [{"localizedValue": "Does Not Apply"}, {"localizedValue": "Short Sleeve"}]},
            ]}
        if path.endswith("/fulfillment_policy"):
            rows = [{"fulfillmentPolicyId": f"f{i}", "name": f"Ship {i}", "marketplaceId": "EBAY_US"} for i in range(self.policies)]
            return {"fulfillmentPolicies": rows}
        if path.endswith("/payment_policy"):
            return {"paymentPolicies": [{"paymentPolicyId": "p1", "name": "Pay", "marketplaceId": "EBAY_US"}]}
        if path.endswith("/return_policy"):
            return {"returnPolicies": [{"returnPolicyId": "r1", "name": "Return", "marketplaceId": "EBAY_US"}]}
        if path == "/sell/inventory/v1/location":
            return {"locations": [{"merchantLocationKey": "home", "name": "Home", "merchantLocationStatus": "ENABLED"}]}
        if path.endswith("create_image_from_file"):
            return {"imageUrl": "https://i.ebayimg.com/images/g/test/s-l1600.jpg"}
        if path.startswith("/sell/inventory/v1/inventory_item/"):
            return {}
        if path == "/sell/inventory/v1/offer" and method == "POST":
            return {"offerId": "offer-1"}
        if path.endswith("/publish"):
            return {"listingId": "123456789012"}
        if path == "/sell/inventory/v1/offer/offer-1":
            return {"status": "PUBLISHED", "listing": {"listingId": "123456789012"}}
        raise AssertionError(path)


class EbayListingsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(ebay_listings, "PHOTOS_DIR", self.temp))
        Path(self.temp, "tee.jpg").write_bytes(b"image")
        self.photo = SimpleNamespace(stored_filename="tee.jpg", mime_type="image/jpeg")
        self.binding = {}

    def record(self, values):
        self.binding.update(values)

    def active(self):
        return True

    async def test_list_publishes_the_same_specifics_vendoo_would_send(self):
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        inventory = next(call for call in client.calls if call[0] == "PUT")
        product = inventory[2]["body"]["product"]
        self.assertEqual(product["aspects"]["Brand"], ["Abercrombie"])
        self.assertEqual(product["aspects"]["Department"], ["Men"])
        self.assertEqual(product["aspects"]["Sleeve Length"], ["Does Not Apply"])
        self.assertEqual(inventory[2]["body"]["condition"], "USED_GOOD")
        self.assertEqual(inventory[2]["body"]["conditionDescription"], "Pre-owned, washed, no stains.")
        self.assertEqual(product["imageUrls"], ["https://i.ebayimg.com/images/g/test/s-l1600.jpg"])
        offer = next(call for call in client.calls if call[1] == "/sell/inventory/v1/offer")
        self.assertTrue(offer[2]["body"]["listingPolicies"]["bestOfferTerms"]["bestOfferEnabled"])
        self.assertEqual(client.calls[-1][1], "/sell/inventory/v1/offer/offer-1/publish")

    async def test_more_than_one_shipping_policy_lists_nothing(self):
        client = Client(policies=2)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("shipping", result["error"])
        self.assertTrue(result["safe_to_retry"])
        self.assertFalse(any(call[0] == "PUT" for call in client.calls))

    async def test_a_lost_publish_is_confirmed_by_reading_the_offer(self):
        self.binding["ebay_offer_id"] = "offer-1"
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.check(self.binding)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        self.assertEqual(client.calls[0][0], "GET")

    async def test_a_rejected_inventory_write_does_not_become_safe_after_the_item_exists(self):
        class Failing(Client):
            async def request(self, method, path, **kwargs):
                if path.startswith("/sell/inventory/v1/inventory_item/"):
                    self.calls.append((method, path, kwargs))
                    raise EbayFailure("eBay returned HTTP 500.", 500)
                return await super().request(method, path, **kwargs)

        client = Failing()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["safe_to_retry"])
        self.assertNotIn("offer-1", self.binding)
