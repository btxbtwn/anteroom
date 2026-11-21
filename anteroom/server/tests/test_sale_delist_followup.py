"""The follow-up after a sale: alert on, and retry, listings a sale left live elsewhere."""
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from anteroom.models.marketplace_sale import MarketplaceSale
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
        self.assertEqual(followup.retry_after(10), timedelta(hours=1))
        # Hundreds of failed tries stay at the cap instead of overflowing.
        self.assertEqual(followup.retry_after(400), timedelta(hours=1))

    def test_nothing_is_delisted_unless_the_seller_turned_it_on(self):
        self._sold()
        self._run(autodelist=False)
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)
        # The alert still shows it.
        self.assertEqual(len(self._still()), 1)

    def test_a_sale_the_seller_marked_is_reported_but_never_delisted_automatically(self):
        self._sold(source="seller")
        [item] = self._still()
        self.assertFalse(item["detected"])
        self._run()
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)

    def _relist_on_mercari(self, new_id="m99999999999", minutes_after=5):
        """Anteroom lists the item on Mercari again, after the sale, under a new listing ID."""
        from anteroom.repositories.marketplace_listings import MarketplaceListingRepo

        when = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=minutes_after)
        MarketplaceListingRepo(self.db).save(self.conv.id, "mercari", {
            "state": "published", "listing_url": f"https://www.mercari.com/us/item/{new_id}", "listing_id": new_id,
            "listed_at": when})
        self.db.commit()

    def test_a_listing_put_up_after_the_sale_is_shown_but_never_delisted(self):
        self._sold()
        self._relist_on_mercari()
        [item] = self._still()
        mercari = next(entry for entry in item["stillListed"] if entry["marketplace"] == "mercari")
        self.assertEqual((mercari["listingId"], mercari["followed"]), ("m99999999999", False))
        self._run()
        touched = sorted(call.args[2]["marketplace"] for call in self.bridge.call_args_list)
        self.assertEqual(touched, ["poshmark"])
        self.assertEqual(self._states()["mercari"], "live")

    def test_retries_stay_on_the_listing_the_sale_left_even_after_a_relist(self):
        self._sold()
        self._fail(["ebay", "etsy", "poshmark", "mercari"])
        # The first sweep pins what the sale left up; nothing is due yet.
        self._run()
        self.db.expire_all()
        pins = parse_notes(self.conv.notes)["saleDelistTargets"]
        [pin] = pins.values()
        self.assertEqual(pin["targets"]["mercari"], "m70565754838")
        # Mercari is relisted under a new ID before the retry is due.
        self._relist_on_mercari(minutes_after=-1)
        self._age_records(6)
        self.bridge.reset_mock()
        self.bridge.side_effect = None
        self.bridge.return_value = {"ok": True, "state": "deleted"}
        self.ebay.return_value = self.etsy.return_value = {"ok": True, "state": "deleted"}
        self._run()
        touched = sorted(call.args[2]["marketplace"] for call in self.bridge.call_args_list)
        self.assertEqual(touched, ["poshmark"])
        [item] = self._still()
        self.assertEqual([(e["marketplace"], e["followed"]) for e in item["stillListed"]], [("mercari", False)])

    def test_turning_the_setting_off_stops_the_next_delist_in_the_same_sweep(self):
        self._sold()
        followup._next.clear()
        followup._running.clear()
        with patch.object(followup, "_enabled", side_effect=[True, False]):
            asyncio.run(followup.run(session_factory=sessionmaker(bind=self.db.get_bind())))
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)

    def test_pins_are_kept_while_the_setting_is_off_so_turning_it_on_later_skips_relists(self):
        self._sold()
        self._run(autodelist=False)
        self._relist_on_mercari(minutes_after=-1)
        self._run()
        touched = sorted(call.args[2]["marketplace"] for call in self.bridge.call_args_list)
        self.assertEqual(touched, ["poshmark"])
        self.assertEqual(self._states()["mercari"], "live")

    def test_an_in_person_sale_is_alerted_but_never_delisted_automatically(self):
        self._sold(marketplace="other", listing_id=None, source="seller")
        [item] = self._still()
        self.assertEqual((item["soldOn"], item["detected"]), ("other", False))
        self.assertEqual({entry["marketplace"] for entry in item["stillListed"]}, set(delist_tests.URLS))
        self._run()
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)

    def test_a_sale_refunded_before_its_delist_starts_deletes_nothing(self):
        sale_id = self._sold()
        self.db.expire_all()
        due = followup._sweep(self.db, act=True)
        self.assertEqual([(conv, sale) for conv, sale, _targets in due], [(self.conv.id, sale_id)])
        # The order is refunded after the sweep chose it, before its delist starts.
        self.db.get(MarketplaceSale, sale_id).order_status = "refunded"
        self.db.commit()
        [(conv_id, _sale, targets)] = due
        with self.assertLogs("anteroom.sale_delist_followup", level="INFO") as logs:
            asyncio.run(followup._follow(conv_id, sale_id, targets))
        self.assertTrue(any("sale changed" in line for line in logs.output), logs.output)
        self.ebay.assert_not_awaited()
        self.assertEqual(self.bridge.await_count, 0)

    def test_a_failure_that_keeps_failing_alerts_the_operator_on_the_first_try(self):
        self._sold()
        self.ebay.side_effect = RuntimeError("Sign in to eBay again.")
        with self.assertLogs("anteroom", level="ERROR") as logs:
            self._run()
        self.assertTrue(any("still listed on ebay" in line for line in logs.output), logs.output)
        # The failed job itself is not a second operator alert: the follow-up decides when to alert.
        self.assertFalse(any(line.startswith("ERROR:anteroom.jobs") for line in logs.output), logs.output)
        # The second try is not due an alert.
        self._age_records(6)
        with self.assertNoLogs("anteroom", level="WARNING"):
            self._run()
