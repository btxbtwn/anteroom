from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import chrome_sales, user_settings


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def _listing(Session, notes: dict) -> str:
    with Session() as db:
        conv = ConversationRepo(db).create(title="coat")
        conv.notes = json.dumps(notes)
        db.commit()
        return conv.id


class ChromeSaleParsingTest(unittest.TestCase):
    def test_only_known_listing_ids_are_kept(self):
        found = chrome_sales.normalize_sales("depop", {"ok": True, "sales": [
            {"listing_id": "shop-classic-tee-136d", "buyer": "Pat", "price": "20.00"},
            {"listing_id": "Not A Slug"},
            {"listing_id": "shop-classic-tee-136d"},
        ]})
        self.assertEqual(found, ["shop-classic-tee-136d"])
        self.assertEqual(chrome_sales.normalize_sales("poshmark", {"ok": False, "sales": []}), [])

    def test_only_live_listings_are_checked(self):
        Session = sessionmaker(bind=_engine())
        url = "https://www.depop.com/products/shop-{}-136d/"
        _listing(Session, {"vendooListingUrls": {"depop": url.format("live")}})
        _listing(Session, {"vendooListingUrls": {"depop": url.format("vendoo-sold")},
                           "vendooDates": {"soldByMarketplace": {"depop": "2026-09-01T00:00:00Z"}}})
        _listing(Session, {"vendooListingUrls": {"depop": url.format("taken-down")},
                           "marketplaceMarks": {"depop": {"state": "not_listed", "listing_id": "shop-taken-down-136d"}}})
        _listing(Session, {"nativeMarketplaceDrafts": {"depop": {"listing_url": "https://www.depop.com/sellinghub/drafts/edit/123456/"}}})
        reported = _listing(Session, {"vendooListingUrls": {"depop": url.format("reported")}})
        with Session() as db:
            db.add(MarketplaceSale(marketplace="depop", order_id="x", line_id="x", conversation_id=reported))
            db.commit()
        self.assertEqual(chrome_sales.live_listing_ids("depop", session_factory=Session), ["shop-live-136d"])

    def test_direct_listings_are_checked_and_their_sales_matched(self):
        Session = sessionmaker(bind=_engine())
        slug = "shop-direct-136d"
        conv_id = _listing(Session, {})
        with Session() as db:
            MarketplaceListingRepo(db).save(conv_id, "depop", {"state": "published",
                                                               "listing_url": f"https://www.depop.com/products/{slug}/"})
        self.assertEqual(chrome_sales.live_listing_ids("depop", session_factory=Session), [slug])
        self.assertEqual(len(chrome_sales.record_reported_sales("depop", [slug], session_factory=Session)), 1)
        with Session() as db:
            self.assertEqual(db.query(MarketplaceSale).one().conversation_id, conv_id)
        self.assertEqual(chrome_sales.live_listing_ids("depop", session_factory=Session), [])

    def test_a_reported_sale_matches_the_listing_once_and_stops_its_checks(self):
        Session = sessionmaker(bind=_engine())
        slug = "shop-classic-tee-136d"
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": f"https://www.depop.com/products/{slug}/"}})
        self.assertEqual(len(chrome_sales.record_reported_sales("depop", [slug], session_factory=Session)), 1)
        self.assertEqual(chrome_sales.record_reported_sales("depop", [slug], session_factory=Session), [])
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual((sale.conversation_id, sale.marketplace, sale.listing_id), (conv_id, "depop", slug))
        self.assertEqual(chrome_sales.live_listing_ids("depop", session_factory=Session), [])

    def test_only_sold_items_still_live_in_anteroom_are_recorded(self):
        recorded = []

        async def request(_job, _message_type, payload=None, timeout=40):
            return {"ok": True, "sales": [{"listing_id": "shop-live-136d"}, {"listing_id": "shop-sold-long-ago-136d"}]}

        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch.object(chrome_sales, "live_listing_ids", lambda market: ["shop-live-136d", "shop-other-136d"]), \
                mock.patch.object(chrome_sales, "record_reported_sales", lambda market, ids: recorded.append(ids) or []):
            asyncio.run(chrome_sales.check_marketplace("depop"))
        self.assertEqual(recorded, [["shop-live-136d"]])

    def test_every_live_listing_is_sent_in_one_check(self):
        ids = [f"shop-item-{index}-136d" for index in range(20)]
        seen = {}

        async def request(_job, _message_type, payload=None, timeout=40):
            seen["ids"] = list(payload["listing_ids"])
            seen["timeout"] = timeout
            return {"ok": True, "sales": []}

        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch.object(chrome_sales, "live_listing_ids", lambda market: ids), \
                mock.patch.object(chrome_sales, "record_reported_sales", lambda market, found: []):
            asyncio.run(chrome_sales.check_marketplace("depop"))
        self.assertEqual(seen["ids"], ids)
        self.assertGreaterEqual(seen["timeout"], 90)

    def test_shops_take_turns_on_an_uneven_gap(self):
        chrome_sales._next.clear()
        chrome_sales._errors.clear()
        calls = []

        async def request(_job, _message_type, payload=None, timeout=40):
            calls.append(payload["marketplace"])
            return {"ok": True, "sales": []}

        try:
            with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                    mock.patch.object(chrome_sales, "live_listing_ids", lambda market: ["x"]):
                asyncio.run(chrome_sales.check_due())
                self.assertEqual(calls, ["poshmark"])
                gap = chrome_sales._next["poshmark"] - time.time()
                self.assertGreaterEqual(gap, chrome_sales.MIN_GAP_S - 1)
                self.assertLessEqual(gap, chrome_sales.MAX_GAP_S + 1)
                asyncio.run(chrome_sales.check_due())
                self.assertEqual(calls, ["poshmark", "mercari"])
                self.assertGreater(chrome_sales._next["poshmark"], time.time())
        finally:
            chrome_sales._next.clear()
            chrome_sales._errors.clear()

    def test_a_turned_off_marketplace_is_not_checked(self):
        user_settings.set_sale_detection("depop", False)
        chrome_sales._next.clear()
        chrome_sales._errors.clear()
        calls = []

        async def request(_job, _message_type, payload=None, timeout=40):
            calls.append(payload["marketplace"])
            if payload["marketplace"] == "poshmark":
                return {"ok": False, "error": "Could not check sales."}
            return {"ok": True, "sales": []}

        try:
            with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                    mock.patch.object(chrome_sales, "live_listing_ids", lambda market: [] if market == "mercari" else ["x"]):
                asyncio.run(chrome_sales.check_due())
            self.assertNotIn("depop", calls)
            self.assertIn("poshmark", calls)
            self.assertNotIn("mercari", calls)
            self.assertTrue(chrome_sales.check_errors()["poshmark"].startswith("Couldn't check"))
        finally:
            user_settings.set_sale_detection("depop", True)
            chrome_sales._next.clear()
            chrome_sales._errors.clear()


class SaleDetectionRouteTest(unittest.TestCase):
    def tearDown(self):
        user_settings.set_sale_detection("sellwild", True)
        user_settings.set_autodelist(False)

    def test_sale_detection_can_be_turned_off(self):
        client = TestClient(app)
        body = client.get("/api/settings/sale-detection").json()
        self.assertTrue(body["enabled"]["sellwild"])
        self.assertTrue(body["enabled"]["vinted"])
        self.assertTrue(body["enabled"]["ebay"])
        self.assertFalse(body["autodelist"])
        saved = client.put("/api/settings/sale-detection", json={"marketplace": "sellwild", "enabled": False}).json()
        self.assertFalse(saved["enabled"]["sellwild"])
        self.assertFalse(saved["autodelist"])
        self.assertEqual(client.put("/api/settings/sale-detection", json={"marketplace": "facebook", "enabled": True}).status_code, 400)

    def test_autodelist_stays_off_until_the_seller_turns_it_on(self):
        client = TestClient(app)
        turned_on = client.put("/api/settings/sale-detection", json={"autodelist": True}).json()
        self.assertTrue(turned_on["autodelist"])
        self.assertTrue(turned_on["enabled"]["ebay"])
        turned_off = client.put("/api/settings/sale-detection", json={"autodelist": False}).json()
        self.assertFalse(turned_off["autodelist"])
        self.assertEqual(client.put("/api/settings/sale-detection", json={}).status_code, 400)
