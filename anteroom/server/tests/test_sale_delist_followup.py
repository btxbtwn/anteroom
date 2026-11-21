"""The follow-up after a sale: alert on, and retry, listings a sale left live elsewhere."""
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.services.marketplace_delist import DelistError, _open_delist
from anteroom.services import sale_delist_followup as followup
from anteroom.services.vendoo_import import merge_notes, parse_notes
import test_marketplace_delist as delist_tests

_H = delist_tests.MarketplaceDelistTest


class SaleDelistFollowupTest(unittest.TestCase):
    setUp = _H.setUp
    tearDown = _H.tearDown
    _answer_by_marketplace = _H._answer_by_marketplace
    _sales = _H._sales
    _states = _H._states
    _status = _H._status
    _row = _H._row

    def _sold(self, *, marketplace="depop", listing_id="shop-classic-tee-136d", status="paid", source="marketplace",
              days_ago=0) -> str:
        self.conv.notes = merge_notes(self.conv.notes, {"vendooStatus": "active", "vendooMarketplaces": list(self._urls())})
        when = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)
        sale = MarketplaceSale(marketplace=marketplace, order_id="o1", line_id="o1-line", listing_id=listing_id, quantity=1,
                               conversation_id=self.conv.id, order_status=status, source=source, sold_at=when,
                               received_at=when)
        self.db.add(sale)
        self.db.commit()
        return sale.id

    @staticmethod
    def _urls():
        return delist_tests.URLS

    def _still(self):
        self.db.expire_all()
        return followup.still_listed(self.db)

    def _run(self, *, autodelist=True):
        followup._next.clear()
        followup._running.clear()
        with patch("anteroom.services.user_settings.autodelist_enabled", return_value=autodelist), \
                patch("anteroom.services.mac_workspace.account_shown", return_value=False):
            asyncio.run(followup.run(session_factory=sessionmaker(bind=self.db.get_bind())))

    def _fail(self, names, error="Sign in to eBay again."):
        """Record a failed delist of these marketplaces, as the delist after the sale leaves it."""
        self._answer_by_marketplace({name: {"ok": False, "error": error, "error_code": "rejected"} for name in names})
        self.ebay.side_effect = RuntimeError(error)
        self.client.post(f"{self.base}/delist", json={"marketplaces": names, "confirm": True})
        self.ebay.side_effect = None

    def _age_records(self, minutes):
        """Move every delist record back in time, as if the last try was this long ago."""
        self.db.expire_all()
        notes = parse_notes(self.conv.notes)
        stamp = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        delists = {name: {**entry, "at": stamp} for name, entry in notes["marketplaceDelists"].items()}
        self.conv.notes = merge_notes(self.conv.notes, {"marketplaceDelists": delists})
        self.db.commit()

    def test_a_sold_item_still_listed_elsewhere_is_reported_without_the_marketplace_that_sold(self):
        sale_id = self._sold()
        self._fail(["ebay", "mercari"])
        [item] = self._still()
        self.assertEqual((item["conversationId"], item["saleId"], item["soldOn"]), (self.conv.id, sale_id, "depop"))
        left = {entry["marketplace"]: entry["state"] for entry in item["stillListed"]}
        self.assertEqual(left, {"ebay": "failed", "mercari": "failed", "etsy": "live", "poshmark": "live"})
        body = self.client.get("/api/marketplace-sales/still-listed").json()
        self.assertEqual([row["conversationId"] for row in body["items"]], [self.conv.id])

    def test_nothing_is_reported_once_every_other_listing_is_confirmed_gone(self):
        self._sold()
        self.ebay.return_value = self.etsy.return_value = {"ok": True, "state": "deleted"}
        self._answer_by_marketplace({"poshmark": {"ok": True, "state": "deleted", "already": True},
                                     "mercari": _H.MERCARI_GONE})
        self.client.post(f"{self.base}/delist", json={"marketplaces": ["ebay", "etsy", "poshmark", "mercari"], "confirm": True})
        self.assertEqual(self._still(), [])

    def test_cancelled_old_or_multi_unit_sales_are_not_followed(self):
        self._sold(status="cancelled")
        self.assertEqual(self._still(), [])
        self.db.query(MarketplaceSale).delete()
        self.db.commit()
        self._sold(days_ago=30)
        self.assertEqual(self._still(), [])
        with patch("anteroom.services.sale_inventory.stock_after_sale", return_value={"status": "remaining", "remaining": 2}):
            self.db.query(MarketplaceSale).delete()
            self.db.commit()
            self._sold()
            self.assertEqual(self._still(), [])

    def test_the_follow_up_delists_what_is_due_and_never_the_marketplace_that_sold(self):
        self._sold()
        sales = self._sales()
        self._run()
        touched = sorted(call.args[2]["marketplace"] for call in self.bridge.call_args_list)
        self.assertEqual(touched, ["mercari", "poshmark"])
        self.ebay.assert_awaited_once()
        self.etsy.assert_awaited_once()
        self.assertEqual(self._states(), {"ebay": "deleted", "etsy": "deleted", "poshmark": "deleted",
                                          "mercari": "deleted", "depop": "live"})
        self.assertEqual(self._still(), [])
        # The sale is unchanged: the follow-up only delists.
        self.assertEqual(self._sales(), sales)
        self.assertEqual(self._status(), "sold")

    def test_a_failed_delist_waits_its_turn_then_is_tried_again(self):
        self._sold()
        self._fail(["ebay", "etsy", "poshmark", "mercari"])
        self.ebay.reset_mock()
        self.bridge.reset_mock()
        self.ebay.return_value = {"ok": True, "state": "deleted"}
        self.bridge.side_effect = None
        self.bridge.return_value = {"ok": True, "state": "deleted"}
        # Just failed: not due yet.
        self._run()
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)
        # Past the first retry gap: tried again, and confirmed.
        self._age_records(6)
        self._run()
        self.ebay.assert_awaited_once()
        self.assertEqual(self._still(), [])

    def test_the_gap_between_tries_widens_to_an_hour(self):
        self.assertEqual(followup.retry_after(1), timedelta(minutes=5))
        self.assertEqual(followup.retry_after(2), timedelta(minutes=10))
        self.assertEqual(followup.retry_after(3), timedelta(minutes=20))
        self.assertEqual(followup.retry_after(4), timedelta(minutes=40))
        for attempts in (5, 40, 10**6):
            self.assertEqual(followup.retry_after(attempts), timedelta(hours=1))

    def test_sale_is_rechecked_when_the_job_is_claimed(self):
        sale_id = self._sold()
        [(conv_id, _, names)] = followup._due(self.db)
        sessions = sessionmaker(bind=self.db.get_bind())
        with sessions() as worker_db:
            with patch("anteroom.services.sale_inventory.stock_after_sale", return_value={"status": "remaining"}):
                with self.assertRaisesRegex(DelistError, "no longer qualifies"):
                    _open_delist(worker_db, conv_id, names, True, sale_id)
        self.db.get(MarketplaceSale, sale_id).order_status = "refunded"
        self.db.commit()
        with sessions() as worker_db, self.assertRaisesRegex(DelistError, "no longer qualifies"):
            _open_delist(worker_db, conv_id, names, True, sale_id)

    def test_nothing_is_delisted_unless_the_seller_turned_it_on(self):
        self._sold()
        self._run(autodelist=False)
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)
        # The alert still shows it.
        self.assertEqual(len(self._still()), 1)

    def test_a_sale_the_seller_marked_is_reported_but_never_delisted_automatically(self):
        self._sold(source="seller", marketplace="other", listing_id=None)
        [item] = self.client.get("/api/marketplace-sales/still-listed").json()["items"]
        self.assertEqual(item["soldOn"], "other")
        self.assertIn("ebay", {entry["marketplace"] for entry in item["stillListed"]})
        self.assertFalse(item["detected"])
        self._run()
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)

    def test_a_failure_that_keeps_failing_alerts_the_operator_on_the_first_try(self):
        self._sold()
        self.ebay.side_effect = RuntimeError("Sign in to eBay again.")
        with self.assertLogs("anteroom", level="ERROR") as logs:
            self._run()
        self.assertTrue(any("still listed on ebay" in line for line in logs.output), logs.output)
        self.assertFalse(any("anteroom.jobs" in line for line in logs.output), logs.output)
        self._age_records(6)
        with self.assertNoLogs("anteroom", level="WARNING"):
            self._run()
