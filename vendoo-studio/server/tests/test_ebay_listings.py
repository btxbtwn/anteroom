from __future__ import annotations

import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
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
    def __init__(self, policies=1, aspects=None):
        self.policies = policies
        self.calls = []
        self.aspects = aspects
        self.offers = {}
        self.items = {}
        self.uploads = 0
        self.publish_failure = None

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("get_item_aspects_for_category") and self.aspects is not None:
            return {"aspects": self.aspects}
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
            self.uploads += 1
            return {"imageUrl": f"https://i.ebayimg.com/images/g/test{self.uploads}/s-l1600.jpg"}
        if path.startswith("/sell/inventory/v1/inventory_item/"):
            sku = path.rsplit("/", 1)[-1]
            if method == "PUT":
                self.items[sku] = kwargs["body"]
                return {}
            if sku not in self.items:
                raise EbayFailure("eBay returned HTTP 404.", 404)
            return self.items[sku]
        if path == "/sell/inventory/v1/offer" and method == "GET":
            found = [offer for offer in self.offers.values() if offer["sku"] == kwargs["params"]["sku"]]
            if not found:
                raise EbayFailure("eBay returned HTTP 404.", 404)
            return {"offers": found}
        if path == "/sell/inventory/v1/offer" and method == "POST":
            self.offers["offer-1"] = {**kwargs["body"], "offerId": "offer-1", "status": "UNPUBLISHED"}
            return {"offerId": "offer-1"}
        if path.endswith("/publish"):
            if self.publish_failure:
                raise self.publish_failure
            self.offers["offer-1"].update(status="PUBLISHED", listing={"listingId": "123456789012"})
            return {"listingId": "123456789012"}
        if path == "/sell/inventory/v1/offer/offer-1":
            # An offer the test did not make is the live one an Update writes onto.
            offer = self.offers.setdefault("offer-1", {"offerId": "offer-1", "sku": "sku", "status": "PUBLISHED",
                                                       "listing": {"listingId": "123456789012"}})
            if method == "PUT":
                offer.update(kwargs["body"])
                return {}
            return offer
        raise AssertionError(path)


class EbayListingsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(ebay_listings, "PHOTOS_DIR", self.temp))
        Path(self.temp, "tee.jpg").write_bytes(b"image")
        Path(self.temp, "back.jpg").write_bytes(b"back")
        self.photo = SimpleNamespace(id="p1", stored_filename="tee.jpg", mime_type="image/jpeg")
        self.back = SimpleNamespace(id="p2", stored_filename="back.jpg", mime_type="image/jpeg")
        self.binding = {}

    def record(self, values):
        self.binding.update(values)

    def active(self):
        return True

    async def test_list_publishes_the_same_specifics_vendoo_would_send(self):
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        self.assertEqual(self.binding["ebay_sku"], "ar-abc123")
        inventory = next(call for call in client.calls if call[0] == "PUT")
        product = inventory[2]["body"]["product"]
        self.assertEqual(product["aspects"]["Brand"], ["Abercrombie"])
        self.assertEqual(product["aspects"]["Department"], ["Men"])
        self.assertEqual(product["aspects"]["Sleeve Length"], ["Does Not Apply"])
        self.assertEqual(inventory[2]["body"]["condition"], "USED_GOOD")
        self.assertEqual(inventory[2]["body"]["conditionDescription"], "Pre-owned, washed, no stains.")
        self.assertEqual(product["imageUrls"], ["https://i.ebayimg.com/images/g/test1/s-l1600.jpg"])
        offer = next(call for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer"))
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

    async def test_the_shipping_policy_chosen_in_settings_is_used_when_there_are_several(self):
        client = Client(policies=3)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client), \
                patch.object(ebay_listings.user_settings, "get_ebay_listing_defaults", return_value={"fulfillment_policy_id": "f2"}):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"])
        offer = next(call for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer"))
        self.assertEqual(offer[2]["body"]["listingPolicies"]["fulfillmentPolicyId"], "f2")
        self.assertEqual(offer[2]["body"]["listingPolicies"]["paymentPolicyId"], "p1")

    async def test_a_saved_policy_no_longer_on_the_account_lists_nothing(self):
        client = Client(policies=2)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client), \
                patch.object(ebay_listings.user_settings, "get_ebay_listing_defaults", return_value={"fulfillment_policy_id": "gone"}):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("Settings", result["error"])
        self.assertFalse(any(call[0] == "PUT" for call in client.calls))

    async def test_update_rewrites_the_live_offer_and_does_not_publish_another(self):
        self.binding.update(ebay_offer_id="offer-1", ebay_listing_id="123456789012", listing_id="123456789012",
                            ebay_images=[{"photo_id": "p1", "checksum": ebay_listings._photo_checksum(self.photo),
                                          "url": "https://i.ebayimg.com/images/g/test/s-l1600.jpg"}])
        client = Client()
        changed = {**LISTING, "title": "Updated tee", "price": "30"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("update", changed, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        self.assertFalse(any(call[1].endswith("/publish") or call[1] == "/sell/inventory/v1/offer" for call in client.calls))
        inventory = next(call for call in client.calls if call[0] == "PUT" and "/inventory_item/" in call[1])
        self.assertEqual(inventory[2]["body"]["product"]["title"], "Updated tee")
        offer = next(call for call in client.calls if call[0] == "PUT" and call[1].endswith("/offer/offer-1"))
        self.assertEqual(offer[2]["body"]["pricingSummary"]["price"]["value"], "30.00")
        self.assertEqual(offer[2]["body"]["sku"], inventory[1].rsplit("/", 1)[-1])

    async def test_update_without_an_offer_revises_the_same_item(self):
        self.binding["listing_id"] = "123456789012"
        client = Client()
        item = (
            '<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack><Item>'
            "<ItemID>123456789012</ItemID><Title>Abercrombie tee</Title><Description>Soft cotton tee.</Description>"
            '<ListingType>FixedPriceItem</ListingType><Site>US</Site><SKU></SKU>'
            "<Seller><UserID>ada</UserID></Seller><Quantity>1</Quantity>"
            '<SellingStatus><ListingStatus>Active</ListingStatus><QuantitySold>0</QuantitySold>'
            '<CurrentPrice currencyID="USD">24.00</CurrentPrice></SellingStatus>'
            "<PrimaryCategory><CategoryID>15687</CategoryID></PrimaryCategory></Item></GetItemResponse>"
        )
        revised = item.replace("Abercrombie tee", "Updated tee").replace(">24.00<", ">30.00<")
        seen = {"revise": False}

        async def request(method, path, **kwargs):
            client.calls.append((method, path, kwargs))
            if kwargs.get("call") == "ReviseFixedPriceItem":
                seen["revise"] = True
                return {"xml": ET.Element("ok")}
            if kwargs.get("call") == "GetItem":
                xml = revised if seen["revise"] else item
                return {"xml": ET.fromstring(xml)}
            raise AssertionError(path)

        client.request = request
        client.trading = types.MethodType(ebay_listings.EbayClient.trading, client)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client), \
             patch("vendoo_studio.services.keychain.get_marketplace_accounts", return_value={"ebay": {"label": "ada"}}):
            result = await ebay_listings.execute("update", {**LISTING, "title": "Updated tee", "price": "30"}, [self.photo],
                                                 self.binding, self.record, self.active, confirm=True)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        self.assertTrue(seen["revise"])
        self.assertTrue(all(call[1] != "/sell/inventory/v1/offer" for call in client.calls))

    async def test_a_lost_publish_is_confirmed_by_reading_the_offer(self):
        self.binding.update(ebay_offer_id="offer-1", ebay_sku="ar-abc")
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.check(self.binding)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        self.assertTrue(all(call[0] == "GET" for call in client.calls))

    async def test_a_lost_offer_reply_is_found_by_sku_and_is_resumable(self):
        client = Client()
        client.offers["offer-1"] = {"offerId": "offer-1", "sku": "ar-abc", "status": "UNPUBLISHED"}
        self.binding.update(ebay_sku="ar-abc", ebay_inventory_written=True)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.check(self.binding, None, self.record)
        self.assertEqual((result["ok"], result["safe_to_retry"]), (False, True))
        self.assertIn("List again to publish it", result["error"])
        self.assertEqual(self.binding["ebay_offer_id"], "offer-1")
        self.assertTrue(all(call[0] == "GET" for call in client.calls))

    async def test_publish_refused_after_the_offer_exists_is_safe_and_the_retry_reuses_the_ids(self):
        client = Client()
        client.publish_failure = EbayFailure("eBay returned HTTP 400. Error code: 25002.", 400, code="rejected",
                                             marketplace_message="25002: A user error has occurred. (0=Item specific Color is missing.)")
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            failed = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc")
            self.assertEqual((failed["safe_to_retry"], failed["error_code"]), (True, "rejected"))
            self.assertIn("Item specific Color is missing", failed["marketplace_message"])
            self.assertEqual(self.binding["ebay_offer_id"], "offer-1")
            client.publish_failure = None
            client.calls.clear()
            result = await ebay_listings.execute("list", {**LISTING, "price": "26"}, [self.photo], self.binding, self.record,
                                                 self.active, confirm=True, conversation_id="abc")
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/123456789012")
        writes = [(method, path) for method, path, _ in client.calls if method != "GET"]
        # Same SKU, same offer brought up to date, published once; no second offer and no second upload.
        self.assertEqual(writes, [("PUT", "/sell/inventory/v1/inventory_item/ar-abc"), ("PUT", "/sell/inventory/v1/offer/offer-1"),
                                  ("POST", "/sell/inventory/v1/offer/offer-1/publish")])
        self.assertEqual(client.offers["offer-1"]["pricingSummary"]["price"]["value"], "26.00")

    async def test_an_already_published_offer_is_reported_without_another_write(self):
        client = Client()
        client.offers["offer-1"] = {"offerId": "offer-1", "sku": "ar-abc", "status": "PUBLISHED", "listing": {"listingId": "123456789012"}}
        # An earlier List wrote the item and lost the offer's reply.
        self.binding.update(ebay_sku="ar-abc", ebay_inventory_written=True)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc")
        self.assertEqual(result["state"], "published")
        self.assertFalse([call for call in client.calls if call[0] != "GET"])

    async def test_a_seller_sku_used_by_another_item_is_never_overwritten(self):
        client = Client()
        client.items["TEE-001"] = {"product": {"title": "Someone else's jacket"}}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", {**LISTING, "sku": "TEE-001"}, [self.photo], self.binding, self.record,
                                                 self.active, confirm=True, conversation_id="abc")
        self.assertEqual((result["error_code"], result["safe_to_retry"]), ("missing_fields", True))
        self.assertEqual(result["fields"][0]["field"], "sku")
        self.assertFalse([call for call in client.calls if call[0] != "GET"])
        self.assertEqual(client.items["TEE-001"], {"product": {"title": "Someone else's jacket"}})
        # The same SKU this item already wrote is its own.
        self.binding.update(ebay_sku="TEE-001", ebay_inventory_written=True)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            mine = await ebay_listings.execute("list", {**LISTING, "sku": "TEE-001"}, [self.photo], self.binding, self.record,
                                               self.active, confirm=True, conversation_id="abc")
        self.assertEqual(mine["state"], "published")

    async def test_photos_are_reused_only_when_the_same_file_is_in_the_same_place(self):
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            urls = await ebay_listings._image_urls(client, [self.photo, self.back], self.binding, self.record, self.active)
            self.assertEqual(client.uploads, 2)
            # Same count, different photo: the swapped one is uploaded, the kept one reused.
            Path(self.temp, "other.jpg").write_bytes(b"other")
            other = SimpleNamespace(id="p3", stored_filename="other.jpg", mime_type="image/jpeg")
            swapped = await ebay_listings._image_urls(client, [self.photo, other], self.binding, self.record, self.active)
            self.assertEqual(client.uploads, 3)
            self.assertEqual(swapped[0], urls[0])
            self.assertNotEqual(swapped[1], urls[1])
            # Same photo id, edited file: uploaded again.
            Path(self.temp, "tee.jpg").write_bytes(b"cropped")
            edited = await ebay_listings._image_urls(client, [self.photo, other], self.binding, self.record, self.active)
            self.assertEqual(client.uploads, 4)
            self.assertNotEqual(edited[0], urls[0])
            self.assertEqual(edited[1], swapped[1])

    async def test_a_server_error_on_the_idempotent_inventory_write_is_safe_to_list_again(self):
        class Failing(Client):
            async def request(self, method, path, **kwargs):
                if path.startswith("/sell/inventory/v1/inventory_item/") and method == "PUT":
                    self.calls.append((method, path, kwargs))
                    raise EbayFailure("eBay returned HTTP 500.", 500)
                return await super().request(method, path, **kwargs)

        client = Failing()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc")
        # The PUT replaces the same SKU's item, so a retry cannot duplicate anything.
        self.assertTrue(result["safe_to_retry"])
        self.assertTrue(self.binding["ebay_inventory_written"])
        self.assertNotIn("ebay_offer_id", self.binding)

    async def test_a_lost_publish_reply_is_uncertain(self):
        import httpx

        client = Client()
        client.publish_failure = httpx.ReadTimeout("lost")
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc")
        self.assertEqual((result["safe_to_retry"], result["error_code"]), (False, "uncertain"))

    async def test_general_fields_fill_empty_aspects_and_missing_ones_are_named(self):
        catalog = [
            {"localizedAspectName": "Color", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}},
            {"localizedAspectName": "Size", "aspectConstraint": {"aspectRequired": True, "aspectMode": "SELECTION_ONLY"},
             "aspectValues": [{"localizedValue": "M"}, {"localizedValue": "L"}]},
            {"localizedAspectName": "Department", "aspectConstraint": {"aspectRequired": True, "aspectMode": "SELECTION_ONLY"},
             "aspectValues": [{"localizedValue": "Boys"}, {"localizedValue": "Girls"}]},
            {"localizedAspectName": "Outer Shell Material", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}},
            {"localizedAspectName": "Style", "aspectConstraint": {"aspectMode": "SELECTION_ONLY"}, "aspectValues": [{"localizedValue": "Windbreaker"}]},
        ]
        jacket = {"title": "Columbia kids windbreaker", "description": "Blue windbreaker.", "price": "20", "brand": "Columbia",
                  "primaryColor": "Blue", "size": "m", "department": "boys", "style": "Rain Jacket",
                  "marketplace_category_ids": {"ebay": "57988"}, "ebay_specifics": {}}
        aspects = await ebay_listings._complete_aspects(Client(aspects=[a for a in catalog if a["localizedAspectName"] != "Outer Shell Material"]),
                                                        "57988", ebay_listings.ebay_aspects(jacket, {}))
        self.assertEqual((aspects["Color"], aspects["Size"], aspects["Department"]), (["Blue"], ["M"], ["Boys"]))
        self.assertNotIn("Style", aspects)
        with self.assertRaises(ebay_listings.ListingProblem) as raised:
            await ebay_listings._complete_aspects(Client(aspects=catalog), "57988", ebay_listings.ebay_aspects({**jacket, "primaryColor": ""}, {}))
        self.assertEqual(raised.exception.code, "missing_fields")
        self.assertEqual([item["field"] for item in raised.exception.fields],
                         ["ebay_specifics.category_specifics.Color", "ebay_specifics.category_specifics.Outer Shell Material"])

    async def test_a_401_refreshes_the_token_once_and_a_second_401_is_signed_out(self):
        import httpx

        from vendoo_studio.services import ebay_client

        statuses = iter([401, 200, 401, 401])
        tokens = []

        async def handler(request):
            tokens.append(request.headers["authorization"])
            return httpx.Response(next(statuses), json={"errors": [{"errorId": 1001, "message": "Invalid access token"}]})

        real = httpx.AsyncClient
        refreshed = []

        async def access_token(marketplace, *, rejected=None):
            refreshed.append(rejected)
            return "fresh"

        with patch.object(ebay_client.httpx, "AsyncClient", side_effect=lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)), \
             patch.object(ebay_client, "access_token", access_token), patch.object(ebay_client, "assert_identity"):
            client = ebay_client.EbayClient("stale", ("seller", "1"))
            await client.request("GET", "/sell/inventory/v1/offer/1")
            self.assertEqual(tokens, ["Bearer stale", "Bearer fresh"])
            self.assertEqual(refreshed, ["stale"])
            with self.assertRaises(EbayFailure) as raised:
                await client.request("GET", "/sell/inventory/v1/offer/1")
        self.assertEqual((raised.exception.status, raised.exception.code), (401, "signed_out"))
        self.assertIn("Invalid access token", raised.exception.marketplace_message)
