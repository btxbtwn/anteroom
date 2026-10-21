from __future__ import annotations

import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from vendoo_studio.database import SessionLocal
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import boxes
from vendoo_studio.services.inventory_analytics import load_rows
from vendoo_studio.services.job_snapshot import prepare_listing_snapshot


def _sold(price: float, *, fees: float = 0, shipping_cost: float = 0, days: int = 10) -> str:
    return json.dumps({
        "vendooStatus": "sold",
        "vendooSale": {"price": price, "fees": fees, "shippingCost": shipping_cost, "marketplace": "ebay"},
        "vendooDates": {
            "listed": "2026-09-01T00:00:00+00:00",
            "sold": f"2026-09-{1 + days:02d}T00:00:00+00:00",
        },
    })


class BoxResultsTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        # Each test gets its own store name, since the database is shared.
        self.store = f"Raghouse {uuid.uuid4().hex[:6]}"
        self.box = boxes.create_box(
            self.db, store=self.store, title="Carhartt mix 25 pcs", price=180, shipping=34, pieces=20,
        )
        repo = ConversationRepo(self.db)
        self.sold_a = repo.create(title="Jacket", notes=_sold(80, fees=10, shipping_cost=8, days=6), box_id=self.box.id)
        self.sold_b = repo.create(title="Vest", notes=_sold(50, fees=6, days=20), box_id=self.box.id)
        self.active = repo.create(title="Pants", notes=json.dumps({"vendooStatus": "active"}), box_id=self.box.id)
        self.loose = repo.create(title="Not from a box", notes=_sold(30))

    def tearDown(self):
        self.db.close()

    def _row(self, payload, box_id):
        return next(row for row in payload["boxes"] if row["id"] == box_id)

    def test_a_box_reports_money_in_against_what_its_sales_brought_back(self):
        row = self._row(boxes.results(self.db), self.box.id)
        self.assertEqual(row["spent"], 214)
        self.assertEqual(row["cost_per_piece"], 10.7)
        self.assertEqual(row["listings"], 3)
        self.assertEqual(row["listed"], 3)
        self.assertEqual(row["sold"], 2)
        # (80 - 10 - 8) + (50 - 6)
        self.assertEqual(row["returned"], 106)
        self.assertEqual(row["profit"], -108)
        self.assertEqual(row["sell_through"], 0.1)
        self.assertEqual(row["median_days"], 13)

    def test_stores_add_up_their_boxes(self):
        boxes.create_box(self.db, store=self.store, title="Second box", price=100, shipping=0, pieces=10)
        store = next(row for row in boxes.results(self.db)["stores"] if row["store"] == self.store)
        self.assertEqual(store["boxes"], 2)
        self.assertEqual(store["spent"], 314)
        self.assertEqual(store["sold"], 2)
        self.assertEqual(store["sell_through"], round(2 / 30, 3))

    def test_an_item_with_no_cost_carries_its_share_of_the_box(self):
        rows = {row.conversation_id: row for row in load_rows(self.db)}
        self.assertEqual(rows[self.sold_a.id].cost, 10.7)
        self.assertIsNone(rows[self.loose.id].cost)

    def test_the_vendoo_form_gets_the_box_share_unless_cog_was_typed(self):
        snapshot = prepare_listing_snapshot(self.db, self.active, {"title": "Pants", "price": 40})
        self.assertEqual(snapshot["cost"], 10.7)
        self.active.notes = json.dumps({"cog": "4"})
        self.db.commit()
        snapshot = prepare_listing_snapshot(self.db, self.active, {"title": "Pants", "price": 40})
        self.assertEqual(snapshot["cost"], 4)

    def test_unsellable_pieces_raise_the_cost_of_the_rest(self):
        boxes.update_box(self.db, self.box, {"unsellable": 4})
        row = self._row(boxes.results(self.db), self.box.id)
        self.assertEqual(row["sellable"], 16)
        # 214 over 16 sellable pieces, not 20
        self.assertEqual(row["cost_per_piece"], 13.38)
        self.assertEqual(row["unsellable"], 4)
        self.assertEqual(row["unsellable_rate"], 0.2)
        self.assertEqual(row["sell_through"], 0.125)
        rows = {item.conversation_id: item for item in load_rows(self.db)}
        self.assertEqual(rows[self.active.id].cost, 13.38)

    def test_zero_unsellable_counts_toward_the_rate(self):
        boxes.update_box(self.db, self.box, {"unsellable": 0})
        row = self._row(boxes.results(self.db), self.box.id)
        self.assertEqual((row["unsellable"], row["unsellable_rate"]), (0, 0))

    def test_more_unsellable_than_pieces_is_refused(self):
        with self.assertRaises(boxes.BoxCountError):
            boxes.update_box(self.db, self.box, {"unsellable": 21})
        self.db.refresh(self.box)
        self.assertIsNone(self.box.unsellable)

    def test_a_box_reports_its_average_sale_and_premium_sales(self):
        row = self._row(boxes.results(self.db), self.box.id)
        self.assertEqual(row["asp"], 65)
        self.assertEqual(row["sold_premium"], 2)
        self.assertIsNone(row["unsellable_rate"])

    def test_sell_through_by_age_waits_until_the_box_is_that_old(self):
        bought = datetime(2026, 8, 25, tzinfo=UTC)
        boxes.update_box(self.db, self.box, {"bought_at": bought})
        # The jacket sold 2026-09-07 (13 days in), the vest 2026-09-21 (27 days in).
        row = self._row(boxes.results(self.db, now=bought + timedelta(days=45)), self.box.id)
        self.assertEqual(row["sell_through_by_age"], [{"days": 30, "sold": 2, "of": 20, "rate": 0.1}])
        late = self._row(boxes.results(self.db, now=bought + timedelta(days=95)), self.box.id)
        self.assertEqual([entry["days"] for entry in late["sell_through_by_age"]], [30, 60, 90])
        early = self._row(boxes.results(self.db, now=bought + timedelta(days=10)), self.box.id)
        self.assertEqual(early["sell_through_by_age"], [])

    def test_sales_after_the_window_do_not_count_toward_it(self):
        # The jacket sold 2026-09-07, inside 30 days; the vest 2026-09-21, after it.
        boxes.update_box(self.db, self.box, {"bought_at": datetime(2026, 8, 15, tzinfo=UTC)})
        row = self._row(boxes.results(self.db, now=datetime(2026, 10, 20, tzinfo=UTC)), self.box.id)
        thirty = row["sell_through_by_age"][0]
        self.assertEqual((thirty["days"], thirty["sold"]), (30, 1))

    def test_deleting_a_box_keeps_its_listings(self):
        boxes.delete_box(self.db, self.box)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Conversation, self.sold_a.id).box_id)


class ResaleCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.store = f"calibrated-{uuid.uuid4().hex[:6]}"
        self.box = boxes.create_box(
            self.db, store=self.store, title="Tees", price=100, pieces=40, estimate_per_piece=20,
        )
        self.repo = ConversationRepo(self.db)

    def tearDown(self):
        self.db.close()

    def _sell(self, *prices):
        for price in prices:
            self.repo.create(title="Tee", notes=_sold(price), box_id=self.box.id)

    def test_waits_for_enough_sales_before_adjusting(self):
        self._sell(10, 12)
        self.assertEqual(
            boxes.resale_calibration(self.db)[self.store],
            {"factor": None, "sales": 2, "needed": boxes.CALIBRATION_MIN_SALES},
        )

    def test_the_median_sale_against_the_estimate_becomes_the_factor(self):
        self._sell(10, 12, 16, 18, 400)
        self.assertEqual(boxes.resale_calibration(self.db)[self.store]["factor"], 0.8)

    def test_the_factor_is_kept_within_range(self):
        self._sell(1, 1, 1, 1, 1)
        self.assertEqual(boxes.resale_calibration(self.db)[self.store]["factor"], 0.3)


class ShippingCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.store = f"tvf-{uuid.uuid4().hex[:6]}"

    def tearDown(self):
        self.db.close()

    def _order(self, shipping: float, list_shipping: float | None):
        boxes.create_box(self.db, store=self.store, title="Flannels", price=100, shipping=shipping, list_shipping=list_shipping)

    def test_one_order_is_not_enough(self):
        self._order(20, 40)
        self._order(0, 40)  # free shipping over the threshold says nothing about the rate
        self._order(25, None)  # typed in by hand, no estimate to compare with
        self.assertEqual(boxes.shipping_calibration(self.db)[self.store], {"factor": None, "orders": 1, "needed": 2})

    def test_the_median_charge_against_the_list_rate_becomes_the_factor(self):
        self._order(20, 40)
        self._order(33, 50)
        self._order(90, 60)
        self.assertEqual(boxes.shipping_calibration(self.db)[self.store]["factor"], 0.66)

    def test_the_factor_is_kept_within_range(self):
        self._order(1, 100)
        self._order(1, 100)
        self.assertEqual(boxes.shipping_calibration(self.db)[self.store]["factor"], 0.2)


class BoxRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_create_assign_and_clear_a_box(self):
        store = f"Thrift Vintage Fashion {uuid.uuid4().hex[:6]}"
        created = self.client.post("/api/boxes", json={
            "store": store, "title": "Y2K 50 lb", "price": 250, "shipping": 0, "pieces": 100,
        })
        self.assertEqual(created.status_code, 200, created.text)
        box = next(row for row in created.json()["boxes"] if row["store"] == store)

        conv = self.client.post("/api/conversations", json={"title": "Tee", "box_id": box["id"]}).json()
        self.assertEqual(conv["box_id"], box["id"])

        renamed = self.client.patch(f"/api/conversations/{conv['id']}", json={"title": "Band tee"}).json()
        self.assertEqual(renamed["box_id"], box["id"])

        cleared = self.client.patch(f"/api/conversations/{conv['id']}", json={"box_id": None}).json()
        self.assertIsNone(cleared["box_id"])

        missing = self.client.patch(f"/api/conversations/{conv['id']}", json={"box_id": "nope"})
        self.assertEqual(missing.status_code, 404)

        edited = self.client.patch(f"/api/boxes/{box['id']}", json={"shipping": 40}).json()
        self.assertEqual(next(row for row in edited["boxes"] if row["id"] == box["id"])["spent"], 290)

        gone = self.client.delete(f"/api/boxes/{box['id']}").json()
        self.assertFalse(any(row["id"] == box["id"] for row in gone["boxes"]))

    def test_unsellable_count_is_saved_and_checked(self):
        store = f"PaperCrane {uuid.uuid4().hex[:6]}"
        created = self.client.post("/api/boxes", json={
            "store": store, "title": "Denim 20", "price": 100, "pieces": 20, "unsellable": 2,
        }).json()
        box = next(row for row in created["boxes"] if row["store"] == store)
        self.assertEqual((box["sellable"], box["cost_per_piece"]), (18, 5.56))
        refused = self.client.patch(f"/api/boxes/{box['id']}", json={"unsellable": 30})
        self.assertEqual(refused.status_code, 422)
        too_many = self.client.post("/api/boxes", json={
            "store": store, "title": "Denim 5", "price": 10, "pieces": 5, "unsellable": 6,
        })
        self.assertEqual(too_many.status_code, 422)

    def test_unknown_box_on_create_is_refused(self):
        response = self.client.post("/api/conversations", json={"title": "Tee", "box_id": "nope"})
        self.assertEqual(response.status_code, 404)
