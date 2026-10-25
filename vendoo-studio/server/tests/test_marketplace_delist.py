from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services.marketplace_delist import listing_id
from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

URLS = {
    "ebay": "https://www.ebay.com/itm/377507697747",
    "etsy": "https://www.etsy.com/listing/4578284433/vintage-tee",
    "poshmark": "https://poshmark.com/listing/Vintage-Tee-6aae234280cb08338a8cb591",
    "mercari": "https://www.mercari.com/us/item/m70565754838",
    "depop": "https://www.depop.com/products/shop-classic-tee-136d/",
    "sellwild": "https://sellwild.com/product/105190104/vintage-tee",
}


class MarketplaceDelistTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title="Vintage tee")
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": URLS})
        self.db.commit()
        self.base = f"/api/conversations/{self.conv.id}/marketplace-listings"
        self.bridge = self.enterContext(patch("vendoo_studio.services.marketplace_delist.browser_bridge.request", new_callable=AsyncMock))
        self.bridge.return_value = {"ok": True, "state": "deleted"}
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=True)))
        self.ebay = self.enterContext(patch("vendoo_studio.services.marketplace_delist._delete_ebay", new_callable=AsyncMock))
        self.ebay.return_value = {"ok": True, "state": "deleted"}
        self.etsy = self.enterContext(patch("vendoo_studio.services.marketplace_delist._delete_etsy", new_callable=AsyncMock))
        self.etsy.return_value = {"ok": True, "state": "deleted"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_listing_ids_come_from_each_marketplace_link(self):
        self.assertEqual({m: listing_id(m, u) for m, u in URLS.items()}, {
            "ebay": "377507697747", "etsy": "4578284433", "poshmark": "6aae234280cb08338a8cb591",
            "mercari": "m70565754838", "depop": "shop-classic-tee-136d", "sellwild": "105190104"})
        self.assertIsNone(listing_id("ebay", "https://evil.example/itm/377507697747"))
        self.assertIsNone(listing_id("mercari", "https://www.mercari.com/us/item/m1/../x"))

    def test_lists_every_live_marketplace(self):
        result = self.client.get(self.base).json()
        self.assertEqual(set(result), set(URLS))
        self.assertTrue(all(entry["state"] == "live" and not entry["sold_here"] for entry in result.values()))

    def test_delist_needs_confirmation_and_named_marketplaces(self):
        self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["poshmark"]}).status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": [], "confirm": True}).status_code, 422)
        self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["vinted"], "confirm": True}).status_code, 400)
        self.bridge.assert_not_called()
        self.ebay.assert_not_called()

    def test_delist_deletes_only_the_named_marketplaces(self):
        result = self.client.post(f"{self.base}/delist", json={"marketplaces": ["poshmark", "ebay"], "confirm": True})
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual((body["poshmark"]["state"], body["ebay"]["state"], body["mercari"]["state"]), ("deleted", "deleted", "live"))
        self.assertEqual(self.bridge.call_count, 1)
        args = self.bridge.call_args.args
        self.assertEqual(args[1], "marketplace.delete")
        self.assertEqual(args[2], {"marketplace": "poshmark", "listing_id": "6aae234280cb08338a8cb591", "confirm": True})
        self.ebay.assert_awaited_once_with("377507697747")
        self.etsy.assert_not_called()

    def test_the_marketplace_it_sold_on_is_never_deleted(self):
        self.db.add(MarketplaceSale(marketplace="ebay", order_id="o1", line_id="l1", conversation_id=self.conv.id))
        self.db.commit()
        self.assertTrue(self.client.get(self.base).json()["ebay"]["sold_here"])
        self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["ebay"], "confirm": True}).status_code, 409)
        self.conv.notes = merge_notes(self.conv.notes, {"vendooSale": {"marketplace": "depop"}})
        self.db.commit()
        self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["mercari", "depop"], "confirm": True}).status_code, 409)
        self.ebay.assert_not_called()
        self.bridge.assert_not_called()

    def test_a_failure_is_recorded_and_others_still_run(self):
        self.bridge.side_effect = [{"ok": False, "error": "Poshmark rejected the delete (HTTP 500)."}, {"ok": True, "state": "deleted"}]
        body = self.client.post(f"{self.base}/delist", json={"marketplaces": ["poshmark", "mercari"], "confirm": True}).json()
        self.assertEqual(body["poshmark"]["state"], "failed")
        self.assertIn("HTTP 500", body["poshmark"]["error"])
        self.assertEqual(body["mercari"]["state"], "deleted")

    def test_a_newer_listing_after_a_delete_is_live_again(self):
        self.client.post(f"{self.base}/delist", json={"marketplaces": ["mercari"], "confirm": True})
        self.assertEqual(self.client.get(self.base).json()["mercari"]["state"], "deleted")
        notes = parse_notes(self.conv.notes)
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": {**notes["vendooListingUrls"],
                                                                              "mercari": "https://www.mercari.com/us/item/m99999999999"}})
        self.db.commit()
        self.assertEqual(self.client.get(self.base).json()["mercari"]["state"], "live")

    def test_browser_marketplaces_need_chrome(self):
        with patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)):
            self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["depop"], "confirm": True}).status_code, 409)
            self.assertEqual(self.client.post(f"{self.base}/delist", json={"marketplaces": ["etsy"], "confirm": True}).status_code, 200)


class MarketplaceStepsTest(unittest.IsolatedAsyncioTestCase):
    async def test_etsy_checks_the_shop_then_deletes(self):
        from vendoo_studio.services import marketplace_delist as d

        calls = []

        async def api(method, path, **_):
            calls.append((method, path))
            return {"shop_id": 42, "state": "active"} if method == "GET" else {}

        with patch("vendoo_studio.services.etsy_listings.connected_shop_id", return_value="42"), \
                patch.object(d.marketplace_accounts, "etsy_api", side_effect=api):
            self.assertEqual((await d._delete_etsy("4578284433"))["state"], "deleted")
        self.assertEqual(calls, [("GET", "listings/4578284433"), ("DELETE", "listings/4578284433")])

    async def test_etsy_refuses_another_shop(self):
        from vendoo_studio.services import marketplace_delist as d

        api = AsyncMock(return_value={"shop_id": 7, "state": "active"})
        with patch("vendoo_studio.services.etsy_listings.connected_shop_id", return_value="42"), \
                patch.object(d.marketplace_accounts, "etsy_api", api):
            with self.assertRaises(ValueError):
                await d._delete_etsy("4578284433")
        self.assertEqual(api.await_count, 1)

    async def test_ebay_ends_the_item_and_treats_already_ended_as_done(self):
        from vendoo_studio.services import marketplace_delist as d
        from vendoo_studio.services.ebay_client import EbayFailure

        client = SimpleNamespace(trading=AsyncMock(return_value={}))
        with patch("vendoo_studio.services.ebay_client.EbayClient.connect", AsyncMock(return_value=client)):
            self.assertEqual((await d._delete_ebay("377507697747"))["state"], "deleted")
            client.trading.assert_awaited_once_with("EndFixedPriceItem", {"ItemID": "377507697747", "EndingReason": "NotAvailable"})
            client.trading.side_effect = EbayFailure("eBay rejected the listing request. Error code: 1047.")
            self.assertTrue((await d._delete_ebay("377507697747"))["already"])
            client.trading.side_effect = EbayFailure("eBay rejected the listing request. Error code: 17.")
            with self.assertRaises(EbayFailure):
                await d._delete_ebay("377507697747")
