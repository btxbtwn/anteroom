"""Anteroom has no Facebook Marketplace support. Facebook rows a seller already has stay readable."""
from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import chrome_sales, hosted_browser_relay, marketplace_delist, sales_ledger, user_settings
from vendoo_studio.services.extension_connection import _SESSION_MARKETS
from vendoo_studio.services.marketplace_identity import listing_id

FACEBOOK = "https://www.facebook.com/marketplace/item/1234567890123456"
POSHMARK = "https://poshmark.com/listing/Vintage-Tee-6aae234280cb08338a8cb591"


class FacebookRemovedTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title="Vintage tee")
        self.conv.notes = json.dumps({"vendooListingUrls": {"facebook": FACEBOOK, "poshmark": POSHMARK}})
        self.db.add(MarketplaceSale(id="sale-fb", marketplace="facebook", order_id="o1", line_id="1",
                                    listing_id="1234567890123456", conversation_id=self.conv.id, order_status="paid",
                                    state="dismissed", sold_at=datetime.now(UTC).replace(tzinfo=None)))
        self.db.commit()
        self.base = f"/api/conversations/{self.conv.id}/marketplace-listings"

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_facebook_is_not_a_marketplace_anywhere(self):
        for markets in (marketplace_delist.MARKETPLACES, sales_ledger.SALE_MARKETPLACES, chrome_sales.CHROME_MARKETS,
                        user_settings.SALE_DETECTION_MARKETS, _SESSION_MARKETS):
            self.assertNotIn("facebook", markets)
        self.assertIsNone(listing_id("facebook", FACEBOOK))

    def test_the_bridge_is_never_asked_to_read_a_facebook_listing(self):
        self.assertNotIn("marketplace.listing_state", hosted_browser_relay.COMMANDS)
        self.assertNotIn("facebook.listing", hosted_browser_relay.NEEDS)

    def test_there_is_no_facebook_check(self):
        self.assertEqual(self.client.post(f"{self.base}/facebook/check").status_code, 404)

    def test_an_old_facebook_link_no_longer_shows_but_the_others_do(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), {"poshmark"})

    def test_a_new_sale_cannot_be_recorded_on_facebook(self):
        response = self.client.post(f"{self.base}/sold", json={"marketplace": "facebook", "sold_on": "2026-10-01",
                                                              "currency": "USD", "request_id": uuid4().hex})
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_past_facebook_sale_stays_in_the_ledger_and_analytics(self):
        today = datetime.now(UTC).date().isoformat()
        ledger = self.client.get("/api/sales-ledger", params={"start": "2020-01-01", "end": today})
        self.assertEqual(ledger.status_code, 200, ledger.text)
        self.assertIn("facebook", json.dumps(ledger.json()))
        analytics = self.client.get("/api/analytics", params={"range": "12m"})
        self.assertEqual(analytics.status_code, 200, analytics.text)
