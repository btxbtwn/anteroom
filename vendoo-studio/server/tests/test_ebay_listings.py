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
    "weight_oz": 8,
    "condition": "Pre-Owned - Good",
    "brand": "Abercrombie",
    "marketplace_category_ids": {"ebay": "15687"},
    "ebay_specifics": {"department": "Men", "category_specifics": {"Sleeve Length": "Does Not Apply"}},
    "form": {"overrides": {"brand": "Abercrombie"}, "specifics": {"conditionDescription": "Pre-owned, washed, no stains."}},
}


class Client:
    def __init__(self, policies=1, aspects=None, conditions=None, locations=None):
        self.policies = policies
        self.conditions = conditions
        self.calls = []
        self.aspects = aspects
        self.offers = {}
        self.items = {}
        self.uploads = 0
        self.publish_failure = None
        self.locations = locations
        self.location_conflict = False
        # "saved": the create is stored, then eBay answers 503. "down": it never lands.
        self.location_fault = None

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("get_item_aspects_for_category") and self.aspects is not None:
            return {"aspects": self.aspects}
        if path.endswith("get_item_aspects_for_category"):
            return {"aspects": [
                {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}},
                {"localizedAspectName": "Department", "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
                 "aspectValues": [{"localizedValue": "Men"}, {"localizedValue": "Women"}]},
                {"localizedAspectName": "Sleeve Length", "aspectConstraint": {"aspectRequired": True, "aspectMode": "SELECTION_ONLY"},
                 "aspectValues": [{"localizedValue": "Does Not Apply"}, {"localizedValue": "Short Sleeve"}]},
            ]}
        if path.endswith("get_item_condition_policies"):
            if self.conditions is None:
                raise EbayFailure("eBay returned HTTP 403.", 403)
            category = kwargs["params"]["filter"].removeprefix("categoryIds:{").removesuffix("}")
            return {"itemConditionPolicies": [{"categoryId": category, "itemConditions": [
                {"conditionId": cid, "conditionDescription": name} for cid, name in self.conditions]}]}
        if path.endswith("/fulfillment_policy"):
            rows = [{"fulfillmentPolicyId": f"f{i}", "name": f"Ship {i}", "marketplaceId": "EBAY_US"} for i in range(self.policies)]
            return {"fulfillmentPolicies": rows}
        if path.endswith("/payment_policy"):
            return {"paymentPolicies": [{"paymentPolicyId": "p1", "name": "Pay", "marketplaceId": "EBAY_US"}]}
        if path.endswith("/return_policy"):
            return {"returnPolicies": [{"returnPolicyId": "r1", "name": "Return", "marketplaceId": "EBAY_US"}]}
        if path == "/sell/inventory/v1/location" and method == "GET":
            rows = self.locations
            if rows is None:
                rows = [{"merchantLocationKey": "home", "name": "Home", "merchantLocationStatus": "ENABLED"}]
            return {"locations": rows}
        if method == "POST" and path.startswith("/sell/inventory/v1/location/"):
            if self.location_conflict:
                raise EbayFailure("eBay returned HTTP 409.", 409)
            key = path.rsplit("/", 1)[-1]
            if self.location_fault:
                if self.location_fault == "saved":
                    self.locations = [{"merchantLocationKey": key, "name": key, "merchantLocationStatus": "ENABLED"}]
                    self.location_fault = None
                raise EbayFailure("eBay returned HTTP 503.", 503, marketplace_message="HTTP 503 Service Unavailable")
            self.locations = [{"merchantLocationKey": key, "name": key, "merchantLocationStatus": "ENABLED"}]
            return {}
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
    def test_offer_controls_and_category_metadata_are_not_product_aspects(self):
        listing = {"ebay_specifics": {
            "autoAccept": 14, "minimumOffer": 12, "category_path": ["Men", "T-Shirts"],
            "primaryStoreCategory": "Other", "secondaryStoreCategory": "Other",
            "category_specifics": {"Brand": "Tennessee River", "Size": "XL", "storeCategoryName": "Other",
                                   "condition": "Pre-owned - Good", "upc": "Does Not Apply"},
        }}
        self.assertEqual(ebay_listings.ebay_aspects(listing, {}), {"Brand": ["Tennessee River"], "Size": ["XL"]})

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(ebay_listings, "PHOTOS_DIR", self.temp))
        Path(self.temp, "tee.jpg").write_bytes(b"image")
        Path(self.temp, "back.jpg").write_bytes(b"back")
        self.photo = SimpleNamespace(id="p1", stored_filename="tee.jpg", mime_type="image/jpeg")
        self.back = SimpleNamespace(id="p2", stored_filename="back.jpg", mime_type="image/jpeg")
        self.binding = {}
        # Each test answers eBay's schema itself; nothing is kept between tests.
        self.enterContext(patch("vendoo_studio.services.marketplace_schemas.load_rows", return_value=None))
        self.enterContext(patch("vendoo_studio.services.marketplace_schemas.save_rows"))
        self.defaults = {}
        self.enterContext(patch.object(ebay_listings, "get_ebay_listing_defaults", lambda: dict(self.defaults)))

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
        self.assertEqual(inventory[2]["body"]["condition"], "USED_EXCELLENT")
        self.assertEqual(inventory[2]["body"]["conditionDescription"], "Pre-owned, washed, no stains.")
        self.assertEqual(product["imageUrls"], ["https://i.ebayimg.com/images/g/test1/s-l1600.jpg"])
        self.assertEqual(inventory[2]["body"]["packageWeightAndSize"]["weight"], {"value": 8, "unit": "OUNCE"})
        offer = next(call for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer"))
        self.assertTrue(offer[2]["body"]["listingPolicies"]["bestOfferTerms"]["bestOfferEnabled"])
        self.assertEqual(client.calls[-1][1], "/sell/inventory/v1/offer/offer-1/publish")

    def test_studio_conditions_map_to_ebays_apparel_grades(self):
        expected = {
            "New With Tags/Box": "NEW", "New Without Tags/Box": "NEW_OTHER", "New With Imperfections": "NEW_WITH_DEFECTS",
            "Pre-Owned - Excellent": "PRE_OWNED_EXCELLENT", "Pre-Owned - Good": "USED_EXCELLENT",
            "Pre-Owned - Fair": "PRE_OWNED_FAIR", "Poor (Major flaws)": "PRE_OWNED_FAIR",
            "New with defects": "NEW_WITH_DEFECTS", "Pre-owned - Good": "USED_EXCELLENT", "USED_GOOD": "USED_GOOD",
        }
        for condition, code in expected.items():
            self.assertEqual(ebay_listings.ebay_condition({"condition": condition}), code, condition)
        self.assertEqual(ebay_listings.ebay_condition({"condition": "Pre-Owned - Good",
                                                       "ebay_specifics": {"condition": "New with tags"}}), "NEW")

    async def test_a_condition_the_category_does_not_take_lists_nothing(self):
        client = Client(conditions=[("1000", "New"), ("3000", "Used"), ("7000", "For parts or not working")])
        listing = {**LISTING, "condition": "Pre-Owned - Excellent"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", listing, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "missing_fields")
        self.assertEqual([field["field"] for field in result["fields"]], ["condition"])
        self.assertIn("New, Used, For parts or not working", result["error"])
        self.assertFalse(any(call[0] in ("PUT", "POST") for call in client.calls))

    async def test_a_condition_the_category_takes_is_listed(self):
        client = Client(conditions=[("1000", "New with tags"), ("3000", "Pre-owned - Good")])
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"])
        self.assertEqual(client.items["ar-abc123"]["condition"], "USED_EXCELLENT")

    async def test_several_shipping_policies_and_no_choice_ask_for_one(self):
        client = Client(policies=2)
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "missing_fields")
        self.assertEqual(result["fields"][0]["field"], "settings.ebay.fulfillment_policy_id")
        self.assertIn("2 shipping policies", result["error"])
        self.assertTrue(result["safe_to_retry"])
        self.assertFalse(any(call[0] in ("PUT", "POST") for call in client.calls))

    def test_a_saved_zip_is_the_postal_code_ebay_stores(self):
        with patch("vendoo_studio.services.user_settings.get_ebay_inventory_location", return_value="70125"):
            self.assertEqual(ebay_listings._ship_from_postal_code(), "70125")
        with patch("vendoo_studio.services.user_settings.get_ebay_inventory_location", return_value="70125-1234"):
            self.assertEqual(ebay_listings._ship_from_postal_code(), "70125")
        with patch("vendoo_studio.services.user_settings.get_ebay_inventory_location", return_value="New Orleans"):
            self.assertIsNone(ebay_listings._ship_from_postal_code())

    async def test_no_inventory_location_is_created_from_the_saved_zip(self):
        client = Client(locations=[])
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value="70125"), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.offers["offer-1"]["merchantLocationKey"], "zip-70125")
        created = [call for call in client.calls if call[0] == "POST" and call[1].startswith("/sell/inventory/v1/location/")]
        self.assertEqual(created[0][1], "/sell/inventory/v1/location/zip-70125")
        self.assertEqual(created[0][2]["body"]["location"]["address"], {"postalCode": "70125", "country": "US"})
        self.assertEqual(created[0][2]["body"]["merchantLocationStatus"], "ENABLED")

    async def test_a_place_name_does_not_create_an_inventory_location(self):
        client = Client(locations=[])
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value=None), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "missing_fields")
        self.assertEqual(result["fields"][0]["field"], "settings.ebay.merchant_location_key")
        self.assertIn("5-digit ZIP", result["error"])
        self.assertFalse(any(call[0] == "POST" and "/location/" in call[1] for call in client.calls))

    async def test_a_location_without_a_status_is_already_enabled(self):
        client = Client(locations=[{"merchantLocationKey": "warehouse", "name": "Warehouse"}])
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.offers["offer-1"]["merchantLocationKey"], "warehouse")
        self.assertFalse(any(call[0] == "POST" and "/location/" in call[1] for call in client.calls))

    async def test_a_503_after_the_location_is_stored_still_lists(self):
        client = Client(locations=[])
        client.location_fault = "saved"
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value="70125"), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.offers["offer-1"]["merchantLocationKey"], "zip-70125")
        created = [call for call in client.calls if call[0] == "POST" and call[1].startswith("/sell/inventory/v1/location/")]
        self.assertEqual(len(created), 1)

    async def test_a_503_when_the_location_never_lands_can_be_tried_again(self):
        client = Client(locations=[])
        client.location_fault = "down"
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value="70125"), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "network")
        self.assertTrue(result["safe_to_retry"])
        self.assertEqual(result["error"], "eBay is temporarily unavailable. Try again.")
        self.assertEqual(result["marketplace_message"], "HTTP 503 Service Unavailable")
        created = [call for call in client.calls if call[0] == "POST" and call[1].startswith("/sell/inventory/v1/location/")]
        self.assertEqual(len(created), 2)
        self.assertEqual(client.uploads, 0)

    async def test_creating_the_same_zip_again_uses_that_location(self):
        client = Client(locations=[])
        client.location_conflict = True
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value="70125"), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.offers["offer-1"]["merchantLocationKey"], "zip-70125")

    async def test_a_disabled_location_is_replaced_by_the_saved_zip(self):
        client = Client(locations=[{"merchantLocationKey": "old", "name": "Old", "merchantLocationStatus": "DISABLED"}])
        with patch.object(ebay_listings, "_ship_from_postal_code", return_value="70125"), \
             patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.offers["offer-1"]["merchantLocationKey"], "zip-70125")

    async def test_several_shipping_policies_use_the_stored_default(self):
        client = Client(policies=3)
        self.defaults = {"fulfillment_policy_id": "f2"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        offer = client.offers["offer-1"]
        self.assertEqual(offer["listingPolicies"]["fulfillmentPolicyId"], "f2")
        self.assertEqual((offer["merchantLocationKey"], offer["listingDuration"]), ("home", "GTC"))
        # A stored default that is no longer on the account is not used.
        self.defaults = {"fulfillment_policy_id": "gone"}
        self.assertEqual((await ebay_listings.listing_choices(Client(policies=1)))["fulfillmentPolicyId"], "f0")

    async def test_best_offer_uses_the_vendoo_paths_floors(self):
        from vendoo_studio.services.native_form import vendoo_form

        listing = {**LISTING, "price": "24"}
        listing["form"] = vendoo_form(listing, "ebay")
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", listing, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"], result)
        terms = client.offers["offer-1"]["listingPolicies"]["bestOfferTerms"]
        self.assertEqual(terms, {"bestOfferEnabled": True, "autoAcceptPrice": {"value": "22.00", "currency": "USD"},
                                 "autoDeclinePrice": {"value": "20.00", "currency": "USD"}})

    async def test_aspects_use_ebays_exact_names_values_and_cardinality(self):
        catalog = [
            {"localizedAspectName": "Country/Region of Manufacture", "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
             "aspectValues": [{"localizedValue": "Bangladesh"}, {"localizedValue": "China"}]},
            {"localizedAspectName": "Theme", "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "itemToAspectCardinality": "MULTI"},
             "aspectValues": [{"localizedValue": "Floral"}, {"localizedValue": "Spring"}, {"localizedValue": "Animals"}]},
            {"localizedAspectName": "Sleeve Length", "aspectConstraint": {"aspectRequired": True, "aspectMode": "SELECTION_ONLY"},
             "aspectValues": [{"localizedValue": "Short Sleeve"}, {"localizedValue": "Long Sleeve"}]},
            {"localizedAspectName": "Performance/Activity", "aspectConstraint": {"aspectMode": "FREE_TEXT"}},
            {"localizedAspectName": "MPN", "aspectConstraint": {"aspectMode": "FREE_TEXT", "aspectMaxLength": 65}},
            {"localizedAspectName": "Style", "aspectConstraint": {"aspectMode": "FREE_TEXT", "itemToAspectCardinality": "SINGLE"}},
        ]
        listing = {**LISTING, "ebay_specifics": {"countryOfOrigin": "bangladesh", "theme": "Floral, Spring", "sleeveLength": "short sleeves",
                                                 "performanceActivity": "Running", "mpn": "CW-123", "upc": "Does Not Apply",
                                                 "style": ["Basic", "Classic"], "madeUpThing": "x"}}
        aspects = await ebay_listings._complete_aspects(Client(aspects=catalog), "15687", ebay_listings.ebay_aspects(listing, {}))
        self.assertEqual(aspects, {"Country/Region of Manufacture": ["Bangladesh"], "Theme": ["Floral", "Spring"],
                                   "Sleeve Length": ["Short Sleeve"], "Performance/Activity": ["Running"], "MPN": ["CW-123"],
                                   "Style": ["Basic"]})
        self.assertEqual(ebay_listings.product_ids(listing), {"upc": ["Does not apply"]})

    async def test_a_category_ebay_does_not_take_is_a_missing_field(self):
        class Refusing(Client):
            async def request(self, method, path, **kwargs):
                if path.endswith("get_item_aspects_for_category"):
                    raise EbayFailure("eBay returned HTTP 400.", 400, code="rejected")
                return await super().request(method, path, **kwargs)

        with patch.object(ebay_listings.EbayClient, "connect", return_value=Refusing()):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True)
        self.assertEqual((result["error_code"], result["fields"][0]["field"]), ("missing_fields", "marketplace_category_ids.ebay"))

    async def test_no_weight_is_asked_for_instead_of_guessed(self):
        client = Client()
        listing = {**LISTING, "weight_oz": 0}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", listing, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertEqual((result["error_code"], result["fields"][0]["field"]), ("missing_fields", "weight_oz"))
        self.assertFalse(any(call[0] in ("PUT", "POST") for call in client.calls if "image" not in call[1]))

    def test_long_titles_are_cut_at_a_word_and_the_preflight_says_so(self):
        from vendoo_studio.services.direct_preflight import Context

        title = "Carter's Girls 4T Pink Floral Cotton Short Sleeve Dress Toddler Spring Easter Party Outfit Gift"
        listing = {**LISTING, "title": title}
        self.assertEqual(ebay_listings.ebay_title(listing), "Carter's Girls 4T Pink Floral Cotton Short Sleeve Dress Toddler Spring Easter")
        gaps = ebay_listings.requirements(listing, Context())
        self.assertEqual([(gap["field"], gap["blocking"]) for gap in gaps], [("title", False)])
        long_text = "word " * 1000
        fields = ebay_listings._listing_fields({**LISTING, "description": long_text}, {})
        body = ebay_listings._inventory_body(fields, {}, [], LISTING, {})
        self.assertLessEqual(len(body["product"]["description"]), 4000)
        self.assertEqual(ebay_listings._offer_body("s", fields, "home", {})["listingDescription"], f"<p>{long_text.strip()}</p>")

    async def test_up_to_24_photos_are_listed(self):
        photos = [self.photo] * 24
        client = Client()
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, photos, self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
            self.assertTrue(result["ok"], result)
            too_many = await ebay_listings.execute("list", LISTING, [self.photo] * 25, {}, self.record, self.active, confirm=True)
        self.assertEqual(too_many["fields"][0]["field"], "photos")

    def test_trading_cards_carry_condition_descriptors(self):
        card = {**LISTING, "marketplace_category_ids": {"ebay": "183454"}}
        graded = {**card, "ebay_specifics": {"category_specifics": {"Professional Grader": "PSA", "Grade": "9.5",
                                                                     "Certification Number": "12345678"}}}
        self.assertEqual(ebay_listings.card_condition(graded, "183454"), ("LIKE_NEW", [
            {"name": "27501", "values": ["275010"]}, {"name": "27502", "values": ["275021"]},
            {"name": "27503", "additionalInfo": "12345678"}]))
        ungraded = {**card, "ebay_specifics": {"cardCondition": "Near Mint or Better"}}
        self.assertEqual(ebay_listings.card_condition(ungraded, "183454"), ("USED_VERY_GOOD", [{"name": "40001", "values": ["400010"]}]))
        fields = ebay_listings._listing_fields(ungraded, {})
        self.assertEqual(ebay_listings._inventory_body(fields, {}, [], ungraded, {})["conditionDescriptors"],
                         [{"name": "40001", "values": ["400010"]}])
        with self.assertRaises(ebay_listings.ListingProblem) as raised:
            ebay_listings._listing_fields(card, {})
        self.assertEqual(raised.exception.fields[0]["field"], "ebay_specifics.Card Condition")
        self.assertIsNone(ebay_listings.card_condition(LISTING, "15687"))

    async def test_the_shipping_policy_chosen_in_settings_is_used_when_there_are_several(self):
        client = Client(policies=3)
        self.defaults = {"fulfillment_policy_id": "f2"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            result = await ebay_listings.execute("list", LISTING, [self.photo], self.binding, self.record, self.active, confirm=True,
                                                 conversation_id="abc123")
        self.assertTrue(result["ok"])
        offer = next(call for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer"))
        self.assertEqual(offer[2]["body"]["listingPolicies"]["fulfillmentPolicyId"], "f2")
        self.assertEqual(offer[2]["body"]["listingPolicies"]["paymentPolicyId"], "p1")

    async def test_a_saved_policy_no_longer_on_the_account_lists_nothing(self):
        client = Client(policies=2)
        self.defaults = {"fulfillment_policy_id": "gone"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
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
                         ["ebay_specifics.Color", "ebay_specifics.Outer Shell Material"])

    def test_the_description_keeps_its_paragraphs_and_line_breaks(self):
        text = "Columbia windbreaker.\n\nMeasurements:\nPit to pit: 20\"\nLength <27>"
        fields = ebay_listings._listing_fields({**LISTING, "description": text}, {})
        self.assertEqual(ebay_listings._offer_body("s", fields, "home", {})["listingDescription"],
                         "<p>Columbia windbreaker.</p><p>Measurements:<br>Pit to pit: 20&quot;<br>Length &lt;27&gt;</p>")
        # The product's copy stays plain text.
        self.assertEqual(ebay_listings._inventory_body(fields, {}, [], LISTING, {})["product"]["description"], text)

    def test_one_copy_per_aspect_and_no_does_not_apply_on_optionals(self):
        listing = {**LISTING, "ebay_specifics": {
            "size": "XL", "season": "Spring", "character": "Does Not Apply", "mpn": "Does Not Apply",
            "bestOffer": {"autoAcceptPrice": 15}, "primaryStoreCategory": "Other", "shippingPolicy": "BUYER PAYS SHIPPING",
            "category_specifics": {"size": "XL (18/20)", "season": ["Fall", "Spring", "Summer"], "silhouette": "Does Not Apply",
                                   "outerShellMaterial": "Polyester"},
        }}
        aspects = ebay_listings.ebay_aspects(listing, {})
        self.assertEqual(aspects["Size"], ["XL"])
        self.assertEqual(aspects["Season"], ["Spring", "Fall", "Summer"])
        self.assertEqual(aspects["Outer Shell Material"], ["Polyester"])
        self.assertEqual(aspects["MPN"], ["Does Not Apply"])
        for name in ("Character", "Silhouette", "Best Offer", "Primary Store Category", "Shipping Policy"):
            self.assertNotIn(name, aspects)

    def test_free_text_values_take_ebays_spelling_when_one_matches(self):
        schema = [
            {"name": "Size", "required": True, "mode": "free_text", "multi": False,
             "values": [{"label": label} for label in ("XL", "18", "20", "18-20")]},
            {"name": "Features", "required": False, "mode": "free_text", "multi": True,
             "values": [{"label": label} for label in ("Full Zip", "Hooded", "Side Pockets")]},
            {"name": "Brand", "required": True, "mode": "free_text", "multi": False,
             "values": [{"label": "Columbia Sportswear"}]},
        ]
        aspects = ebay_listings._match_aspects(schema, {"Size": ["XL (18/20)"], "Features": ["full-zip", "hood", "Drawcord"],
                                                        "Brand": ["Columbia"]})
        self.assertEqual(aspects, {"Size": ["18-20"], "Features": ["Full Zip", "Hooded", "Drawcord"], "Brand": ["Columbia"]})

    def test_store_categories_are_sent_only_when_the_seller_chose_one(self):
        fields = ebay_listings._listing_fields(LISTING, {})
        self.assertNotIn("storeCategoryNames", ebay_listings._offer_body("s", fields, "home", {}))
        chosen = {**LISTING, "ebay_specifics": {"primaryStoreCategory": "Kids Jackets", "secondaryStoreCategory": "Other"}}
        fields = ebay_listings._listing_fields(chosen, {})
        self.assertEqual(ebay_listings._offer_body("s", fields, "home", {})["storeCategoryNames"], ["/Kids Jackets"])

    def test_the_sellers_best_offer_floors_win(self):
        from vendoo_studio.services.native_form import vendoo_form

        listing = {**LISTING, "price": "17", "ebay_specifics": {"bestOffer": {"autoAcceptPrice": 15, "minimumOfferPrice": 12}}}
        terms = ebay_listings.best_offer_terms(vendoo_form(listing, "ebay"), "17.00")
        self.assertEqual(terms, {"bestOfferEnabled": True, "autoAcceptPrice": {"value": "15.00", "currency": "USD"},
                                 "autoDeclinePrice": {"value": "12.00", "currency": "USD"}})

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

    async def test_photo_upload_goes_to_ebays_media_host(self):
        import base64
        import httpx

        from vendoo_studio.services import ebay_client

        seen = []

        async def handler(request):
            seen.append(str(request.url))
            return httpx.Response(201, json={"imageUrl": "https://i.ebayimg.com/images/g/test/s-l1600.jpg"})

        real = httpx.AsyncClient
        with patch.object(ebay_client.httpx, "AsyncClient", side_effect=lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)), \
             patch.object(ebay_client, "assert_identity"):
            client = ebay_client.EbayClient("token", ("seller", "1"))
            await client.request("POST", "/commerce/media/v1_beta/image/create_image_from_file",
                                 upload={"field": "image", "data": base64.b64encode(b"jpeg").decode(), "mime": "image/jpeg"})
            await client.request("GET", "/sell/inventory/v1/location")
            await client.request("GET", "/sell/finances/v1/transaction")
        self.assertEqual(seen, [
            "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file",
            "https://api.ebay.com/sell/inventory/v1/location",
            "https://apiz.ebay.com/sell/finances/v1/transaction",
        ])

    async def test_a_media_query_is_left_off_the_inventory_photo(self):
        import hashlib

        checksum = hashlib.sha256(b"image").hexdigest()
        raw = "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/abc/$_1.JPG?set_id=8800005007"
        self.binding["ebay_images"] = [{"photo_id": "p1", "checksum": checksum, "url": raw}]
        client = Client()
        urls = await ebay_listings._image_urls(client, [self.photo], self.binding, self.record, self.active)
        self.assertEqual(urls, ["https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/abc/$_1.JPG"])
        self.assertEqual(client.uploads, 0)
        body = ebay_listings._inventory_body(
            {"title": "Columbia jacket", "description": "Blue windbreaker.", "condition": "USED_EXCELLENT",
             "condition_description": "Washed.", "quantity": 1},
            {"Brand": ["Columbia"]}, urls, LISTING, {},
        )
        self.assertEqual(body["product"]["imageUrls"], urls)
        self.assertIsInstance(body["packageWeightAndSize"]["weight"]["value"], int)
