"""Sales received before linking an item can be reconciled without Vendoo."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services.relay_sales import reconcile_unmatched

EBAY = "https://www.ebay.com/itm/123456789012"
POSHMARK = "https://poshmark.com/listing/Coat-abcdef012345678901234567"


class LocalSalesReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.item = ConversationRepo(self.db).create(title="Coat")
        self.bridge = self.enterContext(patch("vendoo_studio.services.browser_bridge.request", new_callable=AsyncMock))
        self.autodelist = self.enterContext(patch("vendoo_studio.services.marketplace_accounts.autodelist_for_sales", new_callable=AsyncMock))

    def tearDown(self):
        self.bridge.assert_not_called()
        self.autodelist.assert_not_called()
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def sale(self, *, listing_id="123456789012", state="new", conversation_id=None, line="one", marketplace="ebay"):
        sale = MarketplaceSale(marketplace=marketplace, order_id="order", line_id=line, listing_id=listing_id,
                               state=state, quantity=2, conversation_id=conversation_id)
        self.db.add(sale)
        self.db.commit()
        return sale

    def link_native(self, item_id=None, marketplace="ebay", url=EBAY):
        MarketplaceListingRepo(self.db).save(item_id or self.item.id, marketplace, {"state": "published", "listing_url": url})

    def test_linking_an_item_matches_its_previously_received_sale_locally(self):
        sale = self.sale()
        received_at = sale.received_at
        response = self.client.post(f"/api/conversations/{self.item.id}/marketplace-listings/ebay/mark",
                                    json={"state": "listed", "url": EBAY})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(sale)
        self.assertEqual((sale.conversation_id, sale.quantity, sale.received_at, sale.state), (self.item.id, 2, received_at, "new"))

    def test_reconcile_endpoint_is_idempotent_and_preserves_existing_assignments_and_dismissals(self):
        assigned_to = ConversationRepo(self.db).create(title="Already assigned")
        pending = self.sale()
        dismissed = self.sale(state="dismissed", line="dismissed")
        assigned = self.sale(conversation_id=assigned_to.id, line="assigned")
        missing = self.sale(listing_id="987654321098", line="missing")
        no_id = self.sale(listing_id=None, line="no-id")
        self.link_native()
        first = self.client.post("/api/marketplace-sales/reconcile")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"matched": 1, "unmatched": 2})
        self.assertEqual(self.client.post("/api/marketplace-sales/reconcile").json(), {"matched": 0, "unmatched": 2})
        for sale in (pending, dismissed, assigned, missing, no_id):
            self.db.refresh(sale)
        self.assertEqual(pending.conversation_id, self.item.id)
        self.assertIsNone(dismissed.conversation_id)
        self.assertEqual(dismissed.state, "dismissed")
        self.assertEqual(assigned.conversation_id, assigned_to.id)

    def test_ambiguous_and_forged_links_remain_unmatched(self):
        pending = self.sale()
        self.link_native()
        other = ConversationRepo(self.db).create(title="Duplicate")
        other.notes = json.dumps({"vendooListingUrls": {"ebay": EBAY}})
        self.db.commit()
        self.assertEqual(reconcile_unmatched(self.db), 0)
        self.db.refresh(pending)
        self.assertIsNone(pending.conversation_id)
        MarketplaceListingRepo(self.db).delete_for_conversation(self.item.id)
        other.notes = json.dumps({"vendooListingUrls": {"ebay": "https://evil.example/ebay.com/itm/123456789012"}})
        self.db.commit()
        self.assertEqual(reconcile_unmatched(self.db), 0)

    def test_item_sync_only_assigns_that_items_sales(self):
        other = ConversationRepo(self.db).create(title="Other coat")
        self.link_native()
        self.link_native(other.id, url="https://www.ebay.com/itm/987654321098")
        own = self.sale()
        other_sale = self.sale(listing_id="987654321098", line="other")
        self.assertEqual(reconcile_unmatched(self.db, conversation_id=self.item.id), 1)
        self.db.refresh(own)
        self.db.refresh(other_sale)
        self.assertEqual(own.conversation_id, self.item.id)
        self.assertIsNone(other_sale.conversation_id)

    def test_large_batches_match_each_order_line_once(self):
        self.link_native()
        for number in range(125):
            self.db.add(MarketplaceSale(marketplace="ebay", order_id="bulk", line_id=str(number), listing_id="123456789012"))
        self.db.commit()
        self.assertEqual(reconcile_unmatched(self.db), 125)
        self.assertEqual(reconcile_unmatched(self.db), 0)
        self.assertEqual(self.db.query(MarketplaceSale).filter_by(conversation_id=self.item.id).count(), 125)

    def test_marketplace_refresh_only_matches_and_counts_that_marketplace(self):
        self.link_native()
        self.link_native(marketplace="etsy", url="https://www.etsy.com/listing/12345/coat")
        ebay = self.sale()
        etsy = self.sale(marketplace="etsy", listing_id="12345")
        self.sale(marketplace="etsy", listing_id="98765", line="missing-etsy")
        response = self.client.post("/api/marketplace-sales/reconcile?marketplace=etsy")
        self.assertEqual(response.json(), {"matched": 1, "unmatched": 1})
        self.db.refresh(ebay)
        self.db.refresh(etsy)
        self.assertIsNone(ebay.conversation_id)
        self.assertEqual(etsy.conversation_id, self.item.id)
        self.assertEqual(self.client.post("/api/marketplace-sales/reconcile?marketplace=unknown").status_code, 422)

    def test_sale_alert_includes_direct_listings_and_excludes_sold_or_removed_ones(self):
        self.sale(conversation_id=self.item.id)
        self.link_native(marketplace="poshmark", url=POSHMARK)
        self.link_native(marketplace="mercari", url="https://www.mercari.com/us/item/m12345678/")
        self.link_native(marketplace="depop", url="https://www.depop.com/products/seller-coat/")
        self.link_native(marketplace="etsy", url="https://www.etsy.com/listing/12345/coat")
        sold = self.sale(marketplace="depop", listing_id="seller-coat", conversation_id=self.item.id, line="depop")
        sold.quantity = 1
        self.item.notes = json.dumps({
            "marketplaceMarks": {"mercari": {"state": "not_listed", "listing_id": "m12345678"}},
            "marketplaceDelists": {"etsy": {"state": "deleted", "listing_id": "12345"}},
        })
        self.db.commit()
        sales = self.client.get("/api/marketplace-sales").json()["sales"]
        ebay_sale = next(sale for sale in sales if sale["marketplace"] == "ebay")
        self.assertEqual(ebay_sale["listedElsewhere"], ["poshmark"])
        self.assertIsNone(ebay_sale["vendooUrl"])

    def test_multiunit_sale_keeps_other_marketplace_stock_available_for_review(self):
        self.sale(conversation_id=self.item.id)
        self.link_native(marketplace="depop", url="https://www.depop.com/products/seller-coat/")
        self.sale(marketplace="depop", listing_id="seller-coat", conversation_id=self.item.id, line="depop")
        sales = self.client.get("/api/marketplace-sales").json()["sales"]
        ebay_sale = next(sale for sale in sales if sale["marketplace"] == "ebay")
        self.assertEqual(ebay_sale["listedElsewhere"], ["depop"])
        self.assertEqual(ebay_sale["stockStatus"], "unknown")
