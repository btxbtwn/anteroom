from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.services import listing_persistence, sale_quantity_sync, user_settings


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SaleQuantitySyncAndTrackerTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        app.dependency_overrides[get_db] = lambda: self.db
        self.enterContext(patch("anteroom.database.SessionLocal", self.Session))
        tmp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("anteroom.services.user_settings.user_data_root", return_value=Path(tmp_dir)))
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _create_multi_qty_item(self, *, title: str = "3-Pack Socks", qty: int = 3, sku: str = "SKU-SOCK-3") -> Conversation:
        conv = Conversation(id=new_id(), title=title, status="listed")
        self.db.add(conv)
        self.db.flush()
        listing_persistence.save_revision(
            self.db,
            conv.id,
            {"title": title, "description": "Multi-quantity item", "price": 24.0, "quantity": qty, "sku": sku},
            source="manual",
            commit=True,
        )
        return conv

    def _add_live(self, conv_id: str, marketplace: str, listing_id: str, url: str) -> MarketplaceListing:
        ml = MarketplaceListing(
            id=new_id(),
            conversation_id=conv_id,
            marketplace=marketplace,
            state="published",
            listing_id=listing_id,
            listing_url=url,
            listed_at=_now() - timedelta(days=5),
        )
        self.db.add(ml)
        self.db.commit()
        return ml

    def _add_sale(
        self,
        conv_id: str | None,
        marketplace: str,
        *,
        order_id: str,
        listing_id: str = "orig-1",
        quantity: int = 1,
        order_status: str = "paid",
        sold_at: datetime | None = None,
    ) -> MarketplaceSale:
        ts = sold_at or _now()
        sale = MarketplaceSale(
            id=new_id(),
            marketplace=marketplace,
            order_id=order_id,
            line_id=f"line-{order_id}",
            listing_id=listing_id,
            conversation_id=conv_id,
            quantity=quantity,
            order_status=order_status,
            state="new",
            source="poll",
            sold_at=ts,
            received_at=ts,
        )
        self.db.add(sale)
        self.db.commit()
        return sale

    def test_mercari_adapter_uses_completed_job_listing_id(self):
        from anteroom.services import automation_actions, native_marketplaces

        conv = self._create_multi_qty_item()
        record = self._add_live(conv.id, "mercari", "m1234567", "https://www.mercari.com/us/item/m1234567/")
        job_id = "relist-job"

        async def settle(expected_job_id):
            self.assertEqual(expected_job_id, job_id)
            record.job_id = job_id
            record.listing_id = "m7654321"
            record.listing_url = "https://www.mercari.com/us/item/m7654321/"
            self.db.commit()
            return True

        with patch.object(native_marketplaces, "list_now", return_value={"job_id": job_id, "state": "saving"}), \
             patch.object(automation_actions, "_wait_for_job", side_effect=settle), \
             patch.object(automation_actions, "_job_outcome", return_value=("completed", None)):
            actual = asyncio.run(sale_quantity_sync._default_mercari_relister(conv.id, "reviewed"))
        self.assertTrue(actual["ok"])
        self.assertEqual(actual["listing_id"], "m7654321")

    def test_auto_adjust_quantity_defaults_off_and_toggles_via_settings(self):
        self.assertFalse(user_settings.auto_adjust_quantity_enabled())
        res = self.client.get("/api/settings/sale-detection")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["auto_adjust_quantity"])

        updated = self.client.put("/api/settings/sale-detection", json={"auto_adjust_quantity": True})
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json()["auto_adjust_quantity"])
        self.assertTrue(user_settings.auto_adjust_quantity_enabled())

    def test_multi_qty_ebay_sale_decrements_other_markets_and_skips_live_mercari(self):
        conv = self._create_multi_qty_item(qty=3, sku="SKU-MULTI-1")
        posh_id = "5f0000000000000000000100"
        self._add_live(conv.id, "ebay", "123456789100", "https://www.ebay.com/itm/123456789100")
        self._add_live(conv.id, "poshmark", posh_id, f"https://poshmark.com/listing/{posh_id}")
        self._add_live(conv.id, "depop", "depop-100", "https://www.depop.com/products/depop-100/")
        self._add_live(conv.id, "mercari", "m100000100", "https://www.mercari.com/us/item/m100000100/")
        sale = self._add_sale(conv.id, "ebay", order_id="ord-1", listing_id="123456789100", quantity=1)

        update_calls: list[tuple[str, str, int]] = []

        async def fake_updater(conv_id: str, market: str, _rev_id: str, new_qty: int) -> dict:
            update_calls.append((conv_id, market, new_qty))
            return {"ok": True}

        result = asyncio.run(
            sale_quantity_sync.adjust_quantities_for_sale(
                sale.id, 2, session_factory=self.Session, updater=fake_updater
            )
        )
        self.assertEqual(result["remaining"], 2)
        updated_markets = sorted(m for _c, m, _q in update_calls)
        self.assertEqual(updated_markets, ["depop", "poshmark"])
        self.assertTrue(all(q == 2 for _c, _m, q in update_calls))

        # Local revision quantity updated to 2
        self.db.expire_all()
        listing = self.db.query(Listing).filter_by(conversation_id=conv.id).one()
        rev = self.db.get(ListingRevision, listing.current_revision_id)
        assert rev is not None
        self.assertEqual(rev.listing_json["quantity"], 2)

        # Mercari recorded as skipped (already 1-unit max)
        events = {e["marketplace"]: e for e in result["events"]}
        self.assertEqual(events["poshmark"]["status"], "succeeded")
        self.assertEqual(events["depop"]["status"], "succeeded")
        self.assertEqual(events["mercari"]["status"], "skipped")

    def test_mercari_sale_with_remaining_stock_relists_and_swaps_listing_id_atomically(self):
        conv = self._create_multi_qty_item(qty=4, sku="SKU-MERC-4")
        posh_id = "5f0000000000000000000009"
        self._add_live(conv.id, "mercari", "m100000009", "https://www.mercari.com/us/item/m100000009/")
        self._add_live(conv.id, "poshmark", posh_id, f"https://poshmark.com/listing/{posh_id}")
        sale = self._add_sale(conv.id, "mercari", order_id="ord-merc-1", listing_id="m100000009", quantity=1)

        async def fake_updater(_cid: str, _m: str, _rid: str, _qty: int) -> dict:
            return {"ok": True}

        async def fake_relister(_cid: str, _rid: str) -> dict:
            return {
                "ok": True,
                "listing_id": "m100000010",
                "listing_url": "https://www.mercari.com/us/item/m100000010/",
            }

        res = asyncio.run(
            sale_quantity_sync.adjust_quantities_for_sale(
                sale.id,
                3,
                session_factory=self.Session,
                updater=fake_updater,
                relister=fake_relister,
            )
        )
        by_market = {e["marketplace"]: e for e in res["events"]}
        self.assertEqual(by_market["mercari"]["action"], "mercari_relist")
        self.assertEqual(by_market["mercari"]["status"], "succeeded")
        self.assertEqual(by_market["mercari"]["newListingId"], "m100000010")
        self.assertEqual(by_market["poshmark"]["action"], "adjust_quantity")
        self.assertEqual(by_market["poshmark"]["status"], "succeeded")

        # Tracked Mercari listing ID atomically swapped so future auto-delisting stays pinned
        self.db.expire_all()
        ml = self.db.query(MarketplaceListing).filter_by(conversation_id=conv.id, marketplace="mercari").one()
        self.assertEqual(ml.listing_id, "m100000010")

    def test_cancelled_orders_never_adjusted_or_retried(self):
        conv = self._create_multi_qty_item(qty=3, sku="SKU-CANCEL")
        posh_c = "5f000000000000000000000c"
        self._add_live(conv.id, "poshmark", posh_c, f"https://poshmark.com/listing/{posh_c}")
        sale = self._add_sale(conv.id, "ebay", order_id="ord-cancel", order_status="cancelled")

        updater_called = False

        async def fake_updater(*_args) -> dict:
            nonlocal updater_called
            updater_called = True
            return {"ok": True}

        res = asyncio.run(
            sale_quantity_sync.adjust_quantities_for_sale(sale.id, 2, session_factory=self.Session, updater=fake_updater)
        )
        self.assertFalse(updater_called)
        self.assertEqual(res["events"], [])

        # Even if a sync event existed, retrying on a cancelled order is refused (409)
        ev = sale_quantity_sync.record_sync_event(
            self.db,
            sale_id=sale.id,
            conversation_id=conv.id,
            sku="SKU-CANCEL",
            sold_marketplace="ebay",
            target_marketplace="poshmark",
            action="delist",
            status="failed",
            error="timeout",
        )
        retry_res = self.client.post(f"/api/marketplace-sales/tracker/{ev.id}/retry")
        self.assertEqual(retry_res.status_code, 409)

    def test_60_day_tracker_window_and_one_click_retry(self):
        conv_recent = self._create_multi_qty_item(title="Recent Jacket", qty=1, sku="SKU-REC-1")
        self._add_live(conv_recent.id, "poshmark", "posh-rec", "https://poshmark.com/listing/posh-rec")
        sale_recent = self._add_sale(conv_recent.id, "ebay", order_id="ord-rec", quantity=1, sold_at=_now() - timedelta(days=3))

        # Old sale outside 60-day window -> must NOT appear in tracker
        conv_old = self._create_multi_qty_item(title="Old Boots", qty=1, sku="SKU-OLD-99")
        self._add_sale(conv_old.id, "ebay", order_id="ord-old", quantity=1, sold_at=_now() - timedelta(days=75))

        # Record a failed delist outcome for the recent sale
        sale_quantity_sync.record_delist_outcomes(
            self.db,
            sale_ids=[sale_recent.id],
            conv_id=conv_recent.id,
            live_targets={"poshmark": "posh-rec"},
            result={"poshmark": {"state": "failed", "error": "Extension offline", "attempts": 1}},
        )

        tracker_res = self.client.get("/api/marketplace-sales/tracker")
        self.assertEqual(tracker_res.status_code, 200)
        tracker = tracker_res.json()
        self.assertEqual(tracker["windowDays"], 60)
        self.assertEqual(tracker["summary"]["totalSales"], 1)
        self.assertEqual(tracker["summary"]["failed"], 1)
        entry = tracker["entries"][0]
        self.assertEqual(entry["sku"], "SKU-REC-1")
        self.assertEqual(entry["soldOn"], "ebay")
        self.assertEqual(len(entry["outcomes"]), 1)
        outcome = entry["outcomes"][0]
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], "Extension offline")
        self.assertTrue(outcome["retryable"])

        # One-click retry succeeds and flips status to succeeded
        event_id = outcome["eventId"]
        assert event_id is not None

        async def fake_delister(_cid: str, _market: str) -> dict:
            return {"ok": True}

        retried = asyncio.run(
            sale_quantity_sync.retry_sync_event(event_id, session_factory=self.Session, delister=fake_delister)
        )
        self.assertEqual(retried["status"], "succeeded")
        self.assertEqual(retried["attempts"], 2)
        self.assertIsNone(retried["error"])

        # Delist retry is blocked when stock still remains
        conv_multi = self._create_multi_qty_item(title="Multi Pack", qty=5, sku="SKU-5")
        sale_multi = self._add_sale(conv_multi.id, "ebay", order_id="ord-m5", quantity=1)
        bad_delist_ev = sale_quantity_sync.record_sync_event(
            self.db,
            sale_id=sale_multi.id,
            conversation_id=conv_multi.id,
            sku="SKU-5",
            sold_marketplace="ebay",
            target_marketplace="poshmark",
            action="delist",
            status="failed",
            error="stale",
        )
        blocked = self.client.post(f"/api/marketplace-sales/tracker/{bad_delist_ev.id}/retry")
        self.assertEqual(blocked.status_code, 409)


if __name__ == "__main__":
    unittest.main()
