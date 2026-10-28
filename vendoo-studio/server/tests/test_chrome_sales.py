from __future__ import annotations

import asyncio
import json
import unittest
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


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


class ChromeSaleParsingTest(unittest.TestCase):
    def test_only_listing_and_order_ids_are_kept(self):
        pairs = chrome_sales.normalize_sales("depop", {"ok": True, "sales": [
            {"listing_id": "12345678", "order_id": "900", "buyer": "Pat", "price": "20.00"},
            {"listing_id": "nope", "order_id": "1"},
            {"listing_id": "12345678", "order_id": "900"},
        ]})
        self.assertEqual(pairs, [("900", "12345678")])
        self.assertEqual(chrome_sales.normalize_sales("poshmark", {"ok": False, "sales": []}), [])

    def test_a_reported_sale_matches_the_native_listing_once(self):
        Session = sessionmaker(bind=_engine())
        listing_id = "66f0aabbccddeeff00112233"
        with Session() as db:
            conv = ConversationRepo(db).create(title="coat")
            conv.notes = json.dumps({"nativeMarketplaceDrafts": {
                "poshmark": {"listing_url": f"https://poshmark.com/listing/Vintage-Tee-{listing_id}"},
            }})
            db.commit()
            conv_id = conv.id
        created = chrome_sales.record_reported_sales(
            "poshmark", [("bbbbbbbbbbbbbbbbbbbbbbbb", listing_id)], session_factory=Session,
        )
        self.assertEqual(len(created), 1)
        again = chrome_sales.record_reported_sales(
            "poshmark", [("bbbbbbbbbbbbbbbbbbbbbbbb", listing_id)], session_factory=Session,
        )
        self.assertEqual(again, [])
        with Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual(sale.conversation_id, conv_id)
            self.assertEqual(sale.marketplace, "poshmark")

    def test_a_depop_sale_matches_a_listing_whose_link_ends_in_a_slug(self):
        # Depop product links carry a slug, not the numeric ID the receipts API
        # reports, so a link like this used to orphan every sale it recorded.
        Session = sessionmaker(bind=_engine())
        listing_id = "762263520"
        with Session() as db:
            conv = ConversationRepo(db).create(title="floral blouse")
            conv.notes = json.dumps({"nativeMarketplaceDrafts": {
                "depop": {"listing_url": f"https://www.depop.com/products/floral-blouse-{listing_id}/"},
            }})
            db.commit()
            conv_id = conv.id
        created = chrome_sales.record_reported_sales(
            "depop", [("468061004475", listing_id)], session_factory=Session,
        )
        self.assertEqual(len(created), 1)
        with Session() as db:
            self.assertEqual(db.query(MarketplaceSale).one().conversation_id, conv_id)

    def test_the_check_cursor_survives_a_restart(self):
        # The cursor used to live in module memory, so every restart re-checked
        # the same first batch and starved the listings after it.
        saved = chrome_sales._cursor.copy()
        try:
            chrome_sales.save_cursor("mercari", 7)
            chrome_sales._cursor.clear()
            self.assertEqual(chrome_sales.load_cursor("mercari"), 7)
            chrome_sales.save_cursor("mercari", 0)
        finally:
            chrome_sales._cursor.clear()
            chrome_sales._cursor.update(saved)

    def test_a_turned_off_marketplace_is_not_checked(self):
        user_settings.set_sale_detection("depop", False)
        chrome_sales._last.clear()
        chrome_sales._errors.clear()
        calls = []

        async def request(_job, _message_type, payload=None, timeout=40):
            calls.append(payload["marketplace"])
            if payload["marketplace"] == "poshmark":
                return {"ok": False, "error": "Poshmark did not return sales."}
            return {"ok": True, "sales": []}

        try:
            with mock.patch("vendoo_studio.services.browser_bridge.request", request):
                asyncio.run(chrome_sales.check_due())
            self.assertNotIn("depop", calls)
            self.assertIn("poshmark", calls)
            self.assertNotIn("mercari", calls)
            self.assertTrue(chrome_sales.check_errors()["poshmark"].startswith("Couldn't check"))
        finally:
            user_settings.set_sale_detection("depop", True)
            chrome_sales._last.clear()
            chrome_sales._errors.clear()


class SaleDetectionRouteTest(unittest.TestCase):
    def tearDown(self):
        user_settings.set_sale_detection("sellwild", True)

    def test_sale_detection_can_be_turned_off(self):
        client = TestClient(app)
        body = client.get("/api/settings/sale-detection").json()
        self.assertTrue(body["enabled"]["sellwild"])
        self.assertTrue(body["enabled"]["ebay"])
        saved = client.put("/api/settings/sale-detection", json={"marketplace": "sellwild", "enabled": False}).json()
        self.assertFalse(saved["enabled"]["sellwild"])
        self.assertEqual(client.put("/api/settings/sale-detection", json={"marketplace": "facebook", "enabled": True}).status_code, 400)

class CursorRestartTest(unittest.TestCase):
    def test_listing_ids_to_check_advances_past_a_restart(self):
        Session = sessionmaker(bind=_engine())
        ids = [f"m70322377{400 + i}" for i in range(30)]
        saved = chrome_sales._cursor.copy()
        try:
            with Session() as db:
                for n, mid in enumerate(ids):
                    conv = ConversationRepo(db).create(title=f"item {n}")
                    conv.notes = json.dumps({"nativeMarketplaceDrafts": {
                        "mercari": {"listing_url": f"https://www.mercari.com/us/item/{mid}/"},
                    }})
                db.commit()
            import vendoo_studio.database as database
            orig = database.SessionLocal
            database.SessionLocal = Session
            try:
                first = chrome_sales.listing_ids_to_check("mercari")
                chrome_sales._cursor.clear()
                second = chrome_sales.listing_ids_to_check("mercari")
            finally:
                database.SessionLocal = orig
            self.assertNotEqual(first, second, "a restart re-checked the same batch")
        finally:
            chrome_sales._cursor.clear()
            chrome_sales._cursor.update(saved)
