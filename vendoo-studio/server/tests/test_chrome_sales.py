from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import UTC, datetime
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import chrome_sales, user_settings


def _history(marketplace, listing_ids, sold_at=None):
    valid = [listing_id for listing_id in listing_ids if chrome_sales._LISTING_ID[marketplace].fullmatch(listing_id)]
    return {"ok": True, "orders": [{
        "order_id": f"receipt-{index}", "status": "paid", "currency": "USD",
        "sold_at": sold_at or datetime.now(UTC).isoformat(), "totals": {},
        "lines": [{"line_id": "receipt-line", "listing_id": listing_id}],
    } for index, listing_id in enumerate(valid)]}


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


    def test_shops_take_turns_on_an_uneven_gap(self):
        chrome_sales._next.clear()
        chrome_sales._errors.clear()
        calls = []

        async def request(marketplace, **kwargs):
            calls.append(marketplace)
            return {"ok": True}

        try:
            with mock.patch("vendoo_studio.services.order_history.sync", request), \
                mock.patch.object(chrome_sales, "sale_detection_enabled", return_value=True):
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

        async def request(marketplace, **kwargs):
            calls.append(marketplace)
            if marketplace == "poshmark":
                return {"ok": False, "error": "Couldn't check sales."}
            return {"ok": True}

        try:
            with mock.patch("vendoo_studio.services.order_history.sync", request), \
                mock.patch.object(chrome_sales, "sale_detection_enabled", side_effect=lambda market: market != "depop"):
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
    def test_history_marketplace_reads_orders_instead_of_listing_status(self):
        with mock.patch.object(chrome_sales, "_read_history", new_callable=mock.AsyncMock, return_value=True) as history:
            self.assertTrue(asyncio.run(chrome_sales.check_marketplace("depop")))
        history.assert_awaited_once_with("depop")

    def tearDown(self):
        user_settings.set_sale_detection("depop", True)
        user_settings.set_autodelist(False)

    def test_sale_detection_can_be_turned_off(self):
        client = TestClient(app)
        body = client.get("/api/settings/sale-detection").json()
        self.assertTrue(body["enabled"]["depop"])
        self.assertTrue(body["enabled"]["mercari"])
        self.assertTrue(body["enabled"]["ebay"])
        self.assertFalse(body["autodelist"])
        saved = client.put("/api/settings/sale-detection", json={"marketplace": "depop", "enabled": False}).json()
        self.assertFalse(saved["enabled"]["depop"])
        self.assertFalse(saved["autodelist"])
        self.assertEqual(client.put("/api/settings/sale-detection", json={"marketplace": "grailed", "enabled": True}).status_code, 400)

    def test_autodelist_stays_off_until_the_seller_turns_it_on(self):
        client = TestClient(app)
        turned_on = client.put("/api/settings/sale-detection", json={"autodelist": True}).json()
        self.assertTrue(turned_on["autodelist"])
        self.assertTrue(turned_on["enabled"]["ebay"])
        turned_off = client.put("/api/settings/sale-detection", json={"autodelist": False}).json()
        self.assertFalse(turned_off["autodelist"])
        self.assertEqual(client.put("/api/settings/sale-detection", json={}).status_code, 400)


class ItemSaleCheckTest(unittest.TestCase):
    """Opening an item reads its own live listings, so a sale shows while the seller looks at it."""

    def setUp(self):
        chrome_sales._item_read.clear()

    def tearDown(self):
        chrome_sales._item_read.clear()

    def check(self, Session, conv_id, request):
        manager = mock.Mock(connected=True, capabilities={"marketplace.sales_history"})
        announced = mock.AsyncMock()
        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch("vendoo_studio.services.extension_connection.extension_manager", manager), \
                mock.patch("vendoo_studio.services.marketplace_accounts.announce_sales", announced):
            result = asyncio.run(chrome_sales.check_item(conv_id, session_factory=Session))
        return result, announced

    def test_a_sale_reported_by_the_listing_is_recorded_at_once(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        calls = []

        async def request(_job, _message_type, payload=None, timeout=40):
            calls.append(dict(payload))
            return _history("depop", ["shop-tee-136d"])

        result, announced = self.check(Session, conv_id, request)
        self.assertEqual(result["checked"], ["depop"])
        self.assertEqual(len(result["recorded"]), 1)
        self.assertEqual(calls, [{"marketplace": "depop", "settled": []}])
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual((sale.conversation_id, sale.marketplace, sale.listing_id), (conv_id, "depop", "shop-tee-136d"))
            # The sale is the item's own word from here on: the inventory reads it sold.
            from vendoo_studio.services.item_status import status_for
            from vendoo_studio.services.vendoo_import import parse_notes

            conv = ConversationRepo(db).get(conv_id)
            self.assertEqual(status_for(db, conv_id, parse_notes(conv.notes)).status, "sold")
        announced.assert_awaited_once_with(result["recorded"], session_factory=Session)

    def test_a_shop_is_read_once_a_minute_however_many_items_are_opened(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        calls = []

        async def request(_job, _message_type, payload=None, timeout=40):
            calls.append(payload["marketplace"])
            return _history("depop", [])

        first, _ = self.check(Session, conv_id, request)
        second, _ = self.check(Session, conv_id, request)
        self.assertEqual(first["checked"], ["depop"])
        self.assertEqual(second["checked"], [])
        self.assertEqual(second["skipped"], "Just checked.")
        self.assertEqual(calls, ["depop"])

    def test_a_turned_off_shop_is_not_read(self):
        user_settings.set_sale_detection("depop", False)
        try:
            Session = sessionmaker(bind=_engine())
            conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})

            async def request(_job, _message_type, payload=None, timeout=40):
                raise AssertionError("A shop with detection off must not be read")

            result, _ = self.check(Session, conv_id, request)
            self.assertEqual((result["ok"], result["checked"], result["skipped"]), (True, [], "No live listing to check."))
        finally:
            user_settings.set_sale_detection("depop", True)

    def test_nothing_is_read_without_chrome_or_a_live_listing(self):
        Session = sessionmaker(bind=_engine())
        draft = _listing(Session, {})
        listed = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        # A Facebook link left from before has nothing to read.
        facebook = _listing(Session, {"vendooListingUrls": {"facebook": "https://www.facebook.com/marketplace/item/1234567890123456/"}})

        nothing = asyncio.run(chrome_sales.check_item(draft, session_factory=Session))
        social = asyncio.run(chrome_sales.check_item(facebook, session_factory=Session))
        self.assertEqual(nothing["skipped"], "No live listing to check.")
        self.assertEqual(social["skipped"], "No live listing to check.")
        with mock.patch("vendoo_studio.services.extension_connection.extension_manager", mock.Mock(connected=False)):
            offline = asyncio.run(chrome_sales.check_item(listed, session_factory=Session))
        self.assertEqual((offline["ok"], offline["skipped"]), (False, "Connect Chrome first."))


class ItemSaleCheckRouteTest(unittest.TestCase):
    def setUp(self):
        chrome_sales._item_read.clear()

    def tearDown(self):
        from vendoo_studio.database import get_db

        app.dependency_overrides.pop(get_db, None)
        chrome_sales._item_read.clear()

    def test_the_route_reads_the_item_and_returns_its_listings(self):
        from vendoo_studio.database import get_db

        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})

        def session():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = session

        async def request(_job, _message_type, payload=None, timeout=40):
            self.assertEqual(payload, {"marketplace": "depop", "settled": []})
            return _history("depop", ["shop-tee-136d"])

        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch("vendoo_studio.services.extension_connection.extension_manager", mock.Mock(connected=True, capabilities={"marketplace.sales_history"})), \
                mock.patch("vendoo_studio.database.SessionLocal", Session), \
                mock.patch("vendoo_studio.services.marketplace_accounts.announce_sales", mock.AsyncMock()):
            body = TestClient(app).post(f"/api/conversations/{conv_id}/marketplace-listings/check-sales").json()
        self.assertEqual((body["ok"], body["checked"], len(body["recorded"])), (True, ["depop"], 1))
        self.assertEqual(body["listings"]["depop"]["listing_id"], "shop-tee-136d")
        with Session() as db:
            self.assertEqual(db.query(MarketplaceSale).one().order_id, "receipt-0")


class NotesClaimedSaleCheckTest(unittest.TestCase):
    """A sale only the imported notes claim is confirmed on the marketplace, never believed."""

    def setUp(self):
        chrome_sales._item_read.clear()

    def tearDown(self):
        chrome_sales._item_read.clear()

    def test_a_claimed_sale_is_recorded_once_the_marketplace_confirms_it(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {
            "vendooStatus": "active",
            "vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"},
            "vendooSale": {"marketplace": "depop", "soldAt": "2026-10-27T12:00:00Z"},
            "vendooDates": {"soldByMarketplace": {"depop": "2026-10-27T12:00:00Z"}},
        })

        async def request(_job, _message_type, payload=None, timeout=40):
            return _history("depop", ["shop-tee-136d"])

        announced = mock.AsyncMock()
        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch("vendoo_studio.services.extension_connection.extension_manager", mock.Mock(connected=True, capabilities={"marketplace.sales_history"})), \
                mock.patch("vendoo_studio.services.marketplace_accounts.announce_sales", announced):
            result = asyncio.run(chrome_sales.check_item(conv_id, session_factory=Session))

        self.assertEqual((result["checked"], len(result["recorded"])), (["depop"], 1))
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual((sale.conversation_id, sale.marketplace, sale.source), (conv_id, "depop", "marketplace"))
            from vendoo_studio.services.item_status import status_for
            from vendoo_studio.services.vendoo_import import parse_notes

            conv = ConversationRepo(db).get(conv_id)
            self.assertEqual(status_for(db, conv_id, parse_notes(conv.notes)).status, "sold")

    def test_a_marketplace_anteroom_already_recorded_is_not_read_again(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        with Session() as db:
            db.add(MarketplaceSale(marketplace="depop", order_id="shop-tee-136d", line_id="shop-tee-136d",
                                   listing_id="shop-tee-136d", conversation_id=conv_id))
            db.commit()

        async def request(_job, _message_type, payload=None, timeout=40):
            raise AssertionError("A recorded sale needs no check")

        result = asyncio.run(chrome_sales.check_item(conv_id, session_factory=Session))
        self.assertEqual((result["checked"], result["skipped"]), ([], "No live listing to check."))


class DetectedSaleProtectionTest(unittest.TestCase):
    """A sale Anteroom finds live is attached to its item and blocks anything that would touch it."""

    def setUp(self):
        chrome_sales._item_read.clear()

    def tearDown(self):
        chrome_sales._item_read.clear()

    def check(self, Session, conv_id, reported):
        async def request(_job, _message_type, payload=None, timeout=40):
            return _history(payload["marketplace"], reported)

        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch("vendoo_studio.services.extension_connection.extension_manager", mock.Mock(connected=True, capabilities={"marketplace.sales_history"})), \
                mock.patch("vendoo_studio.services.marketplace_accounts.announce_sales", mock.AsyncMock()):
            return asyncio.run(chrome_sales.check_item(conv_id, session_factory=Session))

    def test_the_listing_that_sold_cannot_be_delisted_or_relisted_by_an_automation(self):
        from vendoo_studio.services import automation_actions, marketplace_delist

        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {
            "depop": "https://www.depop.com/products/shop-tee-136d/",
            "poshmark": "https://poshmark.com/listing/Wool-Coat-65a1b2c3d4e5f6a7b8c9d0e1",
        }})
        self.check(Session, conv_id, ["shop-tee-136d"])
        with Session() as db:
            entry = marketplace_delist.listings(db, conv_id)["depop"]
            self.assertEqual((entry["sold_here"], entry["state"]), (True, "live"))
            with self.assertRaises(marketplace_delist.DelistError) as refused, \
                    mock.patch("vendoo_studio.database.SessionLocal", Session):
                asyncio.run(marketplace_delist.delist(conv_id, ["depop"], True))
            self.assertIn("sold on depop", str(refused.exception).lower())
            # Automation and bulk actions leave it out, and still see the other listing.
            live, fresh = automation_actions._partition(db, conv_id, ["depop", "poshmark"])
            self.assertNotIn("depop", live + fresh)
            self.assertIn("poshmark", live + fresh)

    def test_a_sale_with_ambiguous_ownership_stays_unmatched(self):
        from vendoo_studio.services import marketplace_delist

        Session = sessionmaker(bind=_engine())
        notes = {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}}
        first, second = _listing(Session, notes), _listing(Session, notes)
        self.check(Session, first, ["shop-tee-136d"])
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertIsNone(sale.conversation_id)
            self.assertFalse(marketplace_delist.listings(db, first)["depop"]["sold_here"])
            self.assertFalse(marketplace_delist.listings(db, second)["depop"]["sold_here"])

    def test_a_sale_that_arrived_before_its_item_was_known_is_attached_now(self):
        from vendoo_studio.services import marketplace_delist

        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        with Session() as db:
            db.add(MarketplaceSale(marketplace="depop", order_id="receipt-0", line_id="receipt-line",
                                   listing_id="shop-tee-136d", conversation_id=None))
            db.commit()
        result = self.check(Session, conv_id, ["shop-tee-136d"])
        self.assertEqual(len(result["recorded"]), 1)
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual(sale.conversation_id, conv_id)
            self.assertTrue(marketplace_delist.listings(db, conv_id)["depop"]["sold_here"])

    def test_relisting_after_a_sale_leaves_the_new_listing_alone_again(self):
        from vendoo_studio.services import automation_actions, marketplace_delist

        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        self.check(Session, conv_id, ["shop-tee-136d"])
        with Session() as db:
            self.assertTrue(marketplace_delist.listings(db, conv_id)["depop"]["sold_here"])
            marketplace_delist.mark(db, conv_id, "depop", "listed", "https://www.depop.com/products/shop-tee-v2-136d/")
            entry = marketplace_delist.listings(db, conv_id)["depop"]
            # The sale belongs to the listing that sold; the newer one is live and can be delisted.
            self.assertEqual((entry["listing_id"], entry["sold_here"]), ("shop-tee-v2-136d", False))
            live, fresh = automation_actions._partition(db, conv_id, ["depop"])
            self.assertIn("depop", live + fresh)


class MarkedSoldElsewhereTest(unittest.TestCase):
    """Clearing the other listings after a sale is not another sale."""

    def setUp(self):
        chrome_sales._item_read.clear()

    def tearDown(self):
        chrome_sales._item_read.clear()

    def check(self, Session, conv_id, reported, sold_at=None):
        async def request(_job, _message_type, payload=None, timeout=40):
            return _history(payload["marketplace"], reported, sold_at)

        with mock.patch("vendoo_studio.services.browser_bridge.request", request), \
                mock.patch("vendoo_studio.services.extension_connection.extension_manager", mock.Mock(connected=True, capabilities={"marketplace.sales_history"})), \
                mock.patch("vendoo_studio.services.marketplace_accounts.announce_sales", mock.AsyncMock()):
            return asyncio.run(chrome_sales.check_item(conv_id, session_factory=Session))

    def test_the_listing_marked_sold_elsewhere_adds_no_second_sale(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"}})
        with Session() as db:
            db.add(MarketplaceSale(marketplace="ebay", order_id="o1", line_id="l1", listing_id="123456789012",
                                   conversation_id=conv_id))
            db.commit()
        # The listing reads sold, but this shop has no receipt for it.
        result = self.check(Session, conv_id, [])
        self.assertEqual(result["recorded"], [])
        with Session() as db:
            self.assertEqual([line.marketplace for line in db.query(MarketplaceSale).all()], ["ebay"])

    def test_a_verified_claim_keeps_the_date_the_sale_was_made(self):
        from datetime import datetime

        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {
            "vendooListingUrls": {"depop": "https://www.depop.com/products/shop-tee-136d/"},
            "vendooDates": {"soldByMarketplace": {"depop": "2026-09-02T15:00:00Z"}},
        })
        result = self.check(Session, conv_id, ["shop-tee-136d"], "2026-09-02T15:00:00Z")
        self.assertEqual(len(result["recorded"]), 1)
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual(sale.sold_at, datetime(2026, 9, 2, 15, 0))

    def test_a_second_marketplace_clearing_the_same_sale_adds_nothing(self):
        Session = sessionmaker(bind=_engine())
        conv_id = _listing(Session, {"vendooListingUrls": {
            "depop": "https://www.depop.com/products/shop-tee-136d/",
            "poshmark": "https://poshmark.com/listing/Wool-Coat-65a1b2c3d4e5f6a7b8c9d0e1",
        }})
        first = self.check(Session, conv_id, ["shop-tee-136d"])
        self.assertEqual(len(first["recorded"]), 1)
        chrome_sales._item_read.clear()
        # Clearing Poshmark's listing creates no Poshmark receipt.
        second = self.check(Session, conv_id, [])
        self.assertEqual(second["recorded"], [])
        with Session() as db:
            self.assertEqual([line.marketplace for line in db.query(MarketplaceSale).all()], ["depop"])
