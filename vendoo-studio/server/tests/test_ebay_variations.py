"""eBay listings with variations: one inventory item group, one item and offer per variation."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from vendoo_studio.services import ebay_listings, ebay_variations, marketplace_schemas, native_editor
from vendoo_studio.services.ebay_client import EbayFailure
from vendoo_studio.services.listing_result import ListingProblem
from tests import test_ebay_listings as single

FormatClient = single.FormatClient
LISTING = single.LISTING

VARIED = {
    **LISTING,
    "quantity": 99,
    "ebay_specifics": {
        **LISTING["ebay_specifics"],
        "variations": {
            "aspects": ["Size", "Color"], "photosBy": "Color",
            "rows": [
                {"values": {"Size": "m", "Color": "Red"}, "quantity": 2, "sku": "", "price": ""},
                {"values": {"Size": "L", "Color": "Blue"}, "quantity": "3", "sku": "TEE-L-BLUE", "price": "26.5"},
            ],
            "photos": {"red": ["p2"]},
        },
    },
}


def varied(**changes) -> dict:
    listing = copy.deepcopy(VARIED)
    listing["ebay_specifics"]["variations"].update(changes)
    return listing


class GroupClient(FormatClient):
    """eBay with variation rules, inventory item groups, and publishing or ending a whole group."""

    def __init__(self, supported=True, **kwargs):
        super().__init__(aspects=[
            {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}},
            {"localizedAspectName": "Department", "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
             "aspectValues": [{"localizedValue": "Men"}, {"localizedValue": "Women"}]},
            {"localizedAspectName": "Size", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT",
                                                                 "aspectEnabledForVariations": True},
             "aspectValues": [{"localizedValue": "S"}, {"localizedValue": "M"}, {"localizedValue": "L"}]},
            {"localizedAspectName": "Color", "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "aspectEnabledForVariations": True},
             "aspectValues": [{"localizedValue": "Red"}, {"localizedValue": "Blue"}]},
        ], **kwargs)
        self.supported = supported
        self.groups = {}
        self.published = 0
        self.withdrawn = 0
        self.deleted = []

    async def request(self, method, path, **kwargs):
        if path.endswith("get_listing_structure_policies"):
            self.calls.append((method, path, kwargs))
            category = kwargs["params"]["filter"].removeprefix("categoryIds:{").removesuffix("}")
            return {"listingStructurePolicies": [{"categoryId": category, "variationsSupported": self.supported}]}
        if path.startswith("/sell/inventory/v1/inventory_item_group/"):
            self.calls.append((method, path, kwargs))
            key = path.rsplit("/", 1)[-1]
            if method == "PUT":
                self.groups[key] = kwargs["body"]
                return {}
            if key not in self.groups:
                raise EbayFailure("eBay returned HTTP 404.", 404)
            return self.groups[key]
        if path == "/sell/inventory/v1/offer/publish_by_inventory_item_group":
            self.calls.append((method, path, kwargs))
            if self.publish_failure:
                raise self.publish_failure
            self.published += 1
            skus = set(self.groups[kwargs["body"]["inventoryItemGroupKey"]]["variantSKUs"])
            for offer in self.offers.values():
                if offer["sku"] in skus:
                    offer.update(status="PUBLISHED", listing={"listingId": "223456789012"})
            return {"listingId": "223456789012"}
        if path == "/sell/inventory/v1/offer/withdraw_by_inventory_item_group":
            self.calls.append((method, path, kwargs))
            self.withdrawn += 1
            for offer in self.offers.values():
                offer.update(status="UNPUBLISHED")
                offer.pop("listing", None)
            return {}
        match = path.removeprefix("/sell/inventory/v1/offer/")
        if method == "DELETE" and match in self.offers:
            self.calls.append((method, path, kwargs))
            self.deleted.append(match)
            del self.offers[match]
            return {}
        if path.endswith("/publish"):
            raise AssertionError("A listing with variations is published as a group, never one offer at a time.")
        return await super().request(method, path, **kwargs)

    def writes(self):
        return [call for call in self.calls if call[0] in {"PUT", "POST", "DELETE"} and "create_image_from_file" not in call[1]]


class EbayVariationsTest(unittest.IsolatedAsyncioTestCase):
    setUp = single.EbayListingsTest.setUp
    active = single.EbayListingsTest.active

    def record(self, values):
        # The record is a table: only its columns can be written.
        from vendoo_studio.models.marketplace_listing import FIELDS

        self.assertLessEqual(set(values), set(FIELDS))
        self.binding.update(values)

    async def run_action(self, action, listing, client, confirm=None):
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            return await ebay_listings.execute(action, listing, [self.photo, self.back], self.binding, self.record, self.active,
                                               confirm=action in {"list", "update"} if confirm is None else confirm,
                                               conversation_id="abc123")

    async def test_save_draft_writes_the_group_items_and_unpublished_offers_and_never_publishes(self):
        client = GroupClient()
        result = await self.run_action("save_draft", VARIED, client)
        self.assertEqual(result, {"ok": True, "state": "draft"})
        self.assertEqual(client.published, 0)
        self.assertFalse(any("publish" in call[1] for call in client.calls))
        group = client.groups["ar-abc123"]
        self.assertEqual(group["variantSKUs"], ["ar-abc123-red-m", "TEE-L-BLUE"])
        self.assertEqual(group["variesBy"]["specifications"], [{"name": "Size", "values": ["M", "L"]},
                                                             {"name": "Color", "values": ["Red", "Blue"]}])
        self.assertEqual(group["variesBy"]["aspectsImageVariesBy"], ["Color"])
        self.assertEqual(group["title"], "Abercrombie tee")
        self.assertEqual(len(group["imageUrls"]), 2)
        # Shared specifics stay on the group; the varied ones only on each variation.
        self.assertEqual(group["aspects"]["Brand"], ["Abercrombie"])
        self.assertNotIn("Size", group["aspects"])
        red = client.items["ar-abc123-red-m"]
        self.assertEqual(red["product"]["aspects"]["Size"], ["M"])
        self.assertEqual(red["product"]["aspects"]["Color"], ["Red"])
        self.assertEqual(red["product"]["aspects"]["Brand"], ["Abercrombie"])
        self.assertNotIn("title", red["product"])
        self.assertEqual(red["product"]["imageUrls"], [group["imageUrls"][1]])
        self.assertEqual(red["availability"]["shipToLocationAvailability"]["quantity"], 2)
        self.assertNotIn("imageUrls", client.items["TEE-L-BLUE"]["product"])
        offers = {offer["sku"]: offer for offer in client.offers.values()}
        self.assertEqual(set(offers), {"ar-abc123-red-m", "TEE-L-BLUE"})
        for offer in offers.values():
            self.assertEqual(offer["status"], "UNPUBLISHED")
            self.assertEqual((offer["format"], offer["listingDuration"]), ("FIXED_PRICE", "GTC"))
            self.assertNotIn("bestOfferTerms", offer["listingPolicies"])
            self.assertNotIn("listingDescription", offer)
        self.assertEqual(offers["ar-abc123-red-m"]["pricingSummary"]["price"]["value"], "24.00")
        self.assertEqual(offers["TEE-L-BLUE"]["pricingSummary"]["price"]["value"], "26.50")
        self.assertEqual(offers["TEE-L-BLUE"]["availableQuantity"], 3)
        self.assertEqual(self.binding["ebay_group_key"], "ar-abc123")
        self.assertEqual(set(self.binding["ebay_variant_offers"]), {"ar-abc123-red-m", "TEE-L-BLUE"})

    async def test_saving_again_reuses_every_offer(self):
        client = GroupClient()
        await self.run_action("save_draft", VARIED, client)
        before = dict(client.offers)
        self.assertTrue((await self.run_action("save_draft", VARIED, client))["ok"])
        self.assertEqual(set(client.offers), set(before))
        self.assertEqual(sum(1 for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer")), 2)

    async def test_list_publishes_the_saved_group_once_as_one_listing(self):
        client = GroupClient()
        await self.run_action("save_draft", VARIED, client)
        result = await self.run_action("list", VARIED, client)
        self.assertEqual(result["listing_url"], "https://www.ebay.com/itm/223456789012")
        self.assertEqual(client.published, 1)
        publish = next(call for call in client.calls if call[1].endswith("publish_by_inventory_item_group"))
        self.assertEqual(publish[2]["body"], {"inventoryItemGroupKey": "ar-abc123", "marketplaceId": "EBAY_US"})
        self.assertEqual(self.binding["ebay_listing_id"], "223456789012")
        # Listing again finds the live listing and writes nothing.
        writes = len(client.writes())
        again = await self.run_action("list", VARIED, client)
        self.assertEqual(again["state"], "published")
        self.assertEqual(client.published, 1)
        self.assertEqual(len(client.writes()), writes)

    async def test_list_needs_the_sellers_confirmation(self):
        client = GroupClient()
        result = await self.run_action("list", VARIED, client, confirm=False)
        self.assertFalse(result["ok"])
        self.assertEqual(client.writes(), [])

    async def test_a_lost_publish_reply_is_uncertain_and_check_finds_the_listing(self):
        client = GroupClient()
        original = client.request

        async def lost(method, path, **kwargs):
            answer = await original(method, path, **kwargs)
            if path.endswith("publish_by_inventory_item_group"):
                raise ebay_listings.httpx.ReadTimeout("lost")
            return answer

        client.request = lost
        result = await self.run_action("list", VARIED, client)
        self.assertEqual(result["error_code"], "uncertain")
        self.assertFalse(result["safe_to_retry"])
        self.binding["action"] = "list"
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            checked = await ebay_listings.check(self.binding, VARIED, self.record)
        self.assertEqual(checked["listing_url"], "https://www.ebay.com/itm/223456789012")

    async def test_check_reads_back_a_saved_draft_whose_reply_was_lost(self):
        client = GroupClient()
        await self.run_action("save_draft", VARIED, client)
        self.binding["action"] = "save_draft"
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client):
            self.assertEqual(await ebay_listings.check(self.binding, VARIED, self.record), {"ok": True, "state": "draft"})
            client.offers[next(iter(client.offers))]["availableQuantity"] = 7
            result = await ebay_listings.check(self.binding, VARIED, self.record)
        self.assertFalse(result["ok"])
        self.assertIn("quantity", result["error"])

    async def test_removing_a_row_drops_its_unpublished_offer_from_the_draft(self):
        client = GroupClient()
        three = varied(rows=[*VARIED["ebay_specifics"]["variations"]["rows"],
                             {"values": {"Size": "S", "Color": "Red"}, "quantity": 1}])
        await self.run_action("save_draft", three, client)
        self.assertEqual(len(client.offers), 3)
        self.assertTrue((await self.run_action("save_draft", VARIED, client))["ok"])
        self.assertEqual(len(client.deleted), 1)
        self.assertEqual(client.groups["ar-abc123"]["variantSKUs"], ["ar-abc123-red-m", "TEE-L-BLUE"])
        self.assertEqual(set(self.binding["ebay_variant_offers"]), {"ar-abc123-red-m", "TEE-L-BLUE"})

    async def test_update_rewrites_the_live_group_without_publishing_or_adding_offers(self):
        client = GroupClient()
        await self.run_action("list", VARIED, client)
        self.binding["listing_id"] = "223456789012"
        changed = varied(rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": 5, "price": "22"},
                               {"values": {"Size": "L", "Color": "Blue"}, "quantity": 0, "sku": "TEE-L-BLUE"}])
        result = await self.run_action("update", changed, client)
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.published, 1)
        self.assertEqual(sum(1 for call in client.calls if call[:2] == ("POST", "/sell/inventory/v1/offer")), 2)
        offers = {offer["sku"]: offer for offer in client.offers.values()}
        self.assertEqual(offers["ar-abc123-red-m"]["availableQuantity"], 5)
        self.assertEqual(offers["ar-abc123-red-m"]["pricingSummary"]["price"]["value"], "22.00")
        self.assertEqual(offers["TEE-L-BLUE"]["availableQuantity"], 0)

    async def test_update_refuses_a_new_variation_on_a_live_listing(self):
        client = GroupClient()
        await self.run_action("list", VARIED, client)
        self.binding["listing_id"] = "223456789012"
        writes = len(client.writes())
        added = varied(rows=[*VARIED["ebay_specifics"]["variations"]["rows"], {"values": {"Size": "S", "Color": "Red"}, "quantity": 1}])
        result = await self.run_action("update", added, client)
        self.assertFalse(result["ok"])
        self.assertIn("Adding a variation", result["error"])
        self.assertEqual(len(client.writes()), writes)

    async def test_ebays_rules_are_checked_before_anything_is_written(self):
        cases = {
            "auction": {**VARIED, "ebay_specifics": {**VARIED["ebay_specifics"], "listingFormat": "AUCTION", "auctionStartPrice": "5"}},
            "offers": {**VARIED, "ebay_specifics": {**VARIED["ebay_specifics"], "bestOffer": {"enabled": True}}},
            "duplicate sku": varied(rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": 1, "sku": "A"},
                                          {"values": {"Size": "L", "Color": "Red"}, "quantity": 1, "sku": "a"}]),
            "same values": varied(rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": 1},
                                        {"values": {"Size": "m", "Color": "red"}, "quantity": 1}]),
            "one value": varied(aspects=["Size", "Color"], rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": 1},
                                                                  {"values": {"Size": "L", "Color": "Red"}, "quantity": 1}]),
            "not a variation aspect": varied(aspects=["Department"], photosBy="", photos={},
                                             rows=[{"values": {"Department": "Men"}, "quantity": 1},
                                                   {"values": {"Department": "Women"}, "quantity": 1}]),
            "not ebays value": varied(rows=[{"values": {"Size": "M", "Color": "Green"}, "quantity": 1},
                                            {"values": {"Size": "L", "Color": "Blue"}, "quantity": 1}]),
            "no stock": varied(rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": 0},
                                     {"values": {"Size": "L", "Color": "Blue"}, "quantity": 0}]),
            "unknown photo": varied(photos={"Red": ["gone"]}),
        }
        for name, listing in cases.items():
            with self.subTest(name):
                self.binding = {}
                client = GroupClient()
                result = await self.run_action("save_draft", listing, client)
                self.assertFalse(result["ok"], name)
                self.assertTrue(result["safe_to_retry"], name)
                self.assertEqual(client.writes(), [], name)

    async def test_a_category_without_variations_is_refused(self):
        client = GroupClient(supported=False)
        result = await self.run_action("save_draft", VARIED, client)
        self.assertFalse(result["ok"])
        self.assertIn("does not take listings with variations", result["error"])
        self.assertEqual(client.writes(), [])

    async def test_a_sellers_sku_already_on_ebay_is_never_overwritten(self):
        client = GroupClient()
        client.items["TEE-L-BLUE"] = {"product": {"title": "Another item"}}
        result = await self.run_action("save_draft", VARIED, client)
        self.assertFalse(result["ok"])
        self.assertIn("TEE-L-BLUE", result["error"])
        self.assertEqual(client.writes(), [])

    async def test_delist_ends_the_whole_group_and_is_safe_to_repeat(self):
        client = GroupClient()
        await self.run_action("list", VARIED, client)
        offers = list(self.binding["ebay_variant_offers"].values())
        self.assertEqual(await ebay_variations.end_group(client, "ar-abc123", offers), {"ok": True, "state": "deleted"})
        self.assertEqual(client.withdrawn, 1)
        self.assertTrue((await ebay_variations.end_group(client, "ar-abc123", offers))["already"])
        self.assertEqual(client.withdrawn, 1)

    async def test_delist_uses_the_group_only_for_the_listing_it_made(self):
        from vendoo_studio.services import marketplace_delist

        client = GroupClient()
        trading = AsyncMock()
        client.trading = trading
        record = {"ebay_group_key": "ar-abc123", "ebay_variant_offers": {"a": "offer-1"}, "ebay_listing_id": "223456789012"}
        with patch.object(ebay_listings.EbayClient, "connect", return_value=client), \
                patch.object(ebay_variations, "end_group", new=AsyncMock(return_value={"ok": True, "state": "deleted"})) as ended:
            await marketplace_delist._delete_ebay("223456789012", record)
            ended.assert_awaited_once_with(client, "ar-abc123", ["offer-1"])
            await marketplace_delist._delete_ebay("999999999999", record)
        trading.assert_awaited_once()

    def test_generated_skus_are_stable_unique_and_within_ebays_limit(self):
        sku = ebay_variations.variant_sku("ar-abc123", {"Size": "M", "Color": "Red"})
        self.assertEqual(sku, "ar-abc123-red-m")
        self.assertEqual(ebay_variations.variant_sku("ar-abc123", {"Color": "red", "Size": "m"}), sku)
        long = ebay_variations.variant_sku("A" * 40, {"Size": "Extra Large Tall", "Color": "Heather Grey"})
        self.assertLessEqual(len(long), 50)
        self.assertNotEqual(long, ebay_variations.variant_sku("A" * 40, {"Size": "Extra Large Tall", "Color": "Heather Gray"}))

    def test_quantity_is_the_total_of_the_variations(self):
        self.assertEqual(ebay_variations.total_quantity(VARIED), 5)
        self.assertIsNone(ebay_variations.total_quantity(LISTING))
        self.assertEqual(ebay_listings._listing_fields(VARIED, VARIED["form"])["quantity"], 5)

    def test_preflight_reports_variation_problems_on_their_rows(self):
        listing = varied(rows=[{"values": {"Size": "M", "Color": "Red"}, "quantity": "x"},
                               {"values": {"Size": "", "Color": "Blue"}, "quantity": 1}])
        with self.assertRaises(ListingProblem) as raised:
            ebay_listings._listing_fields(listing, listing["form"])
        fields = {item["field"] for item in raised.exception.fields}
        self.assertIn("ebay_specifics.variations.rows.0.quantity", fields)
        self.assertIn("ebay_specifics.variations.rows.1.values.Size", fields)


    def test_cached_aspect_check_takes_a_varied_aspect_from_the_rows(self):
        rows = [{"id": "Size", "name": "Size", "required": True, "multi": False, "mode": "free_text", "variations": True, "values": []},
                {"id": "Brand", "name": "Brand", "required": True, "multi": False, "mode": "free_text", "values": []}]
        with patch("vendoo_studio.services.category_fields.load_rows", return_value=rows):
            self.assertEqual(ebay_listings._cached_aspect_gaps(VARIED, VARIED["form"]), [])
            gaps = ebay_listings._cached_aspect_gaps(LISTING, LISTING["form"])
        self.assertEqual([item["field"] for item in gaps], ["ebay_specifics.Size"])


class VariationStockTest(unittest.TestCase):
    def test_stock_is_read_per_variation_and_summed(self):
        from vendoo_studio.services.marketplace_inventory import _ebay_variations

        info = {"variation_rows": [{"sku": "A", "quantity": "3", "soldQuantity": "1", "price": "20.00", "values": {"Size": "M"}},
                                   {"sku": "B", "quantity": "2", "soldQuantity": "2", "price": "20.00", "values": {"Size": "L"}}]}
        result = _ebay_variations(info, {"state": "active", "price": None, "quantity": None, "warning": None})
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["price"], 20.0)
        self.assertEqual([(row["sku"], row["quantity"]) for row in result["variations"]], [("A", 2), ("B", 0)])
        broken = _ebay_variations({"variation_rows": [{"sku": "A", "quantity": "1", "soldQuantity": "2"}]},
                                  {"state": "active", "price": None, "quantity": None, "warning": None})
        self.assertIsNone(broken["quantity"])
        self.assertTrue(broken["warning"])

    def test_getitem_variations_are_parsed_with_sku_stock_and_values(self):
        import asyncio
        import xml.etree.ElementTree as ET

        from vendoo_studio.services import ebay_workspace

        xml = ET.fromstring("""<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Item><ItemID>223456789012</ItemID>
          <SellingStatus><ListingStatus>Active</ListingStatus><QuantitySold>1</QuantitySold></SellingStatus>
          <Variations><Variation><SKU>A</SKU><StartPrice currencyID="USD">20.0</StartPrice><Quantity>3</Quantity>
          <VariationSpecifics><NameValueList><Name>Size</Name><Value>M</Value></NameValueList></VariationSpecifics>
          <SellingStatus><QuantitySold>1</QuantitySold></SellingStatus></Variation></Variations></Item></GetItemResponse>""")
        client = type("C", (), {"trading": AsyncMock(return_value={"xml": xml})})()
        info = asyncio.run(ebay_workspace.listing(client, "223456789012"))
        self.assertTrue(info["variations"])
        self.assertEqual(info["variation_rows"], [{"sku": "A", "quantity": "3", "soldQuantity": "1", "price": "20.0", "values": {"Size": "M"}}])


class VariationFormTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_form_offers_the_aspects_ebay_lets_vary_and_hides_offers(self):
        from vendoo_studio.services.ebay_client import EbayClient

        rows = [{"id": "Size", "name": "Size", "required": True, "multi": False, "mode": "free_text", "variations": True,
                 "values": [{"id": "M", "label": "M"}]},
                {"id": "Department", "name": "Department", "required": False, "multi": False, "mode": "selection", "variations": False,
                 "values": [{"id": "Men", "label": "Men"}]}]
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)), \
                patch.object(EbayClient, "connect", new=AsyncMock()), \
                patch.object(marketplace_schemas, "ebay_listing_types", new=AsyncMock(return_value={"AUCTION": ["DAYS_7"]})), \
                patch.object(marketplace_schemas, "ebay_variations_supported", new=AsyncMock(return_value=True)):
            form = await native_editor.ebay_form({**VARIED, "direct_category_ids": {"ebay": "15687"}})
            plain = await native_editor.ebay_form({**LISTING, "direct_category_ids": {"ebay": "15687"}})
        fields = {row["path"]: row for row in form["fields"]}
        variations = fields["ebay_specifics.variations"]
        self.assertEqual(variations["input_type"], "variations")
        self.assertEqual(variations["variation_aspects"], [{"name": "Size", "selection_only": False, "options": ["M"]}])
        self.assertEqual(variations["value"]["aspects"], ["Size", "Color"])
        self.assertNotIn("ebay_specifics.bestOffer.enabled", fields)
        self.assertTrue(fields["quantity"]["locked"])
        self.assertEqual(fields["quantity"]["value"], 5)
        self.assertEqual(fields["ebay_specifics.listingFormat"]["options"], ["Fixed price"])
        plain_fields = {row["path"]: row for row in plain["fields"]}
        self.assertEqual(plain_fields["ebay_specifics.variations"]["value"], {"aspects": [], "rows": [], "photos": {}})
        self.assertIn("ebay_specifics.bestOffer.enabled", plain_fields)

    async def test_variation_support_is_read_from_ebays_listing_structure_policy(self):
        client = AsyncMock()
        client.request.return_value = {"listingStructurePolicies": [{"categoryId": "15687", "variationsSupported": False}]}
        with patch.object(marketplace_schemas, "load_rows", return_value=None), patch.object(marketplace_schemas, "save_rows") as saved:
            self.assertFalse(await marketplace_schemas.ebay_variations_supported(client, "15687"))
        saved.assert_called_once()
        self.assertTrue(marketplace_schemas._ebay_field({"localizedAspectName": "Size",
                                                         "aspectConstraint": {"aspectEnabledForVariations": True}})["variations"])
