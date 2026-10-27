from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from vendoo_studio.models.promotion_automation import PromotionAutomation, PromotionRun
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services import browser_bridge, listing_promotions, promotion_automations
from vendoo_studio.services.vendoo_import import merge_notes

DISCOUNT = "c" * 24
SHARE = {"marketplace": "poshmark", "action": "share", "share_order": "newest", "speed": "slow", "daily_cap": 2,
         "times": ["09:00", "20:00"], "timezone": "America/Chicago"}
OFFER = {"marketplace": "poshmark", "action": "offer", "discount_percent": 20, "min_likes": 2, "shipping_label": "-$1.50"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PromotionAutomationsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.items = []
        for index, days in enumerate((30, 5, 90)):
            conv = ConversationRepo(self.db).create(title=f"Item {index}")
            post = f"{index:024x}"
            conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"poshmark": f"https://poshmark.com/listing/Item-{post}"},
                                                  "vendooDates": {"listed": (_now() - timedelta(days=days)).isoformat()}})
            self.items.append((conv, post))
        self.db.commit()
        self.bridge = self.enterContext(patch("vendoo_studio.services.listing_promotions.browser_bridge.request", new_callable=AsyncMock))
        self.bridge.side_effect = self.answer
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=True)))
        self.enterContext(patch("vendoo_studio.routes.extension.schedule_advance_job_queue", lambda: None))
        self.likes = {post: 3 for _conv, post in self.items}
        self.sent = {}
        self.on_check = None

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    async def answer(self, _job, message_type, payload, timeout=0):
        if message_type == "marketplace.promotion_check":
            if self.on_check:
                self.on_check()
            facts = {"ok": True, "price_cents": 5000, "like_count": self.likes[payload["listing_id"]]}
            if payload["action"] == "offer":
                facts.update(minimum_discount_percent=10, maximum_discount_percent=79,
                             offer_price_cents=5000 * (100 - payload["discount_percent"]) // 100,
                             shipping_discounts=[{"id": DISCOUNT, "label": "-$1.50", "min_offer_cents": None}])
            return facts
        if message_type == "marketplace.promote":
            answer = self.sent.get(payload["listing_id"], {"ok": True, "state": "done"})
            if isinstance(answer, Exception):
                raise answer
            return answer
        return {"finished": False, "known": False}

    def promoted(self):
        return [call.args[2] for call in self.bridge.call_args_list if call.args[1] == "marketplace.promote"]

    def create(self, body):
        response = self.client.post("/api/promotion-automations", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def enable(self, automation_id):
        response = self.client.post(f"/api/promotion-automations/{automation_id}/enable", json={"confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def due(self, automation_id):
        row = self.db.get(PromotionAutomation, automation_id)
        row.next_run_at = _now() - timedelta(minutes=1)
        self.db.commit()

    def tick(self):
        return asyncio.run(promotion_automations.tick(self.db, sleep=AsyncMock()))

    def test_saved_off_and_enabling_or_editing_sends_nothing(self):
        saved = self.create(SHARE)
        self.assertFalse(saved["enabled"])
        self.assertEqual((saved["times"], saved["daily_cap"]), (["09:00", "20:00"], 2))
        self.assertEqual(self.client.post(f"/api/promotion-automations/{saved['id']}/enable", json={}).status_code, 422)
        self.assertEqual(self.tick(), 0)
        on = self.enable(saved["id"])
        self.assertTrue(on["enabled"])
        self.assertGreater(datetime.fromisoformat(on["next_run_at"].rstrip("Z")), _now())
        self.assertEqual(self.tick(), 0)
        edited = self.client.put(f"/api/promotion-automations/{saved['id']}", json={**SHARE, "daily_cap": 5}).json()
        self.assertEqual((edited["enabled"], edited["daily_cap"]), (False, 5))
        self.bridge.assert_not_called()

    def test_invalid_settings_are_refused(self):
        for body in ({**SHARE, "times": []}, {**SHARE, "times": ["25:00"]}, {**SHARE, "daily_cap": 6001},
                     {**SHARE, "timezone": "Mars/Base"}, {**OFFER, "discount_percent": None},
                     {**OFFER, "marketplace": "mercari", "shipping_label": "-$1"}, {"marketplace": "depop", "action": "share"},
                     {**SHARE, "scope": "items", "conversation_ids": []}):
            self.assertIn(self.client.post("/api/promotion-automations", json=body).status_code, (400, 422), body)

    def test_depop_rules_save_but_cannot_be_turned_on_until_captured(self):
        saved = self.create({**OFFER, "marketplace": "depop", "shipping_label": None})
        self.assertFalse(saved["available"])
        response = self.client.post(f"/api/promotion-automations/{saved['id']}/enable", json={"confirm": True})
        self.assertEqual(response.status_code, 409)

    def mercari_items(self, count):
        items = []
        for index in range(count):
            conv = ConversationRepo(self.db).create(title=f"Mercari {index}")
            item = f"m{index + 1:011d}"
            conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"mercari": f"https://www.mercari.com/us/item/{item}/"}})
            self.db.add(MarketplaceOfferFloor(marketplace="mercari", listing_id=item, conversation_id=conv.id, floor_cents=3000))
            self.likes[item] = 3
            items.append((conv, item))
        self.db.commit()
        return items

    def test_a_mercari_offer_rule_starts_off_and_pauses_on_an_unknown_result(self):
        (first, first_item), (second, second_item) = self.mercari_items(2)
        saved = self.create({**OFFER, "marketplace": "mercari", "shipping_label": None, "scope": "items",
                             "conversation_ids": [first.id, second.id]})
        self.assertEqual((saved["enabled"], saved["available"]), (False, True))
        self.due(saved["id"])
        self.assertEqual(self.tick(), 0)
        self.bridge.assert_not_called()
        self.enable(saved["id"])
        self.due(saved["id"])
        for item in (first_item, second_item):
            self.sent[item] = browser_bridge.BrowserBridgeError("Chrome did not answer in time.")
        self.tick()
        sent = self.promoted()
        self.assertEqual(len(sent), 1)
        unknown = sent[0]["listing_id"]
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertFalse(automation.enabled)
        self.assertIn("not confirmed", automation.paused_reason)
        # Back on, the unconfirmed listing stays blocked; the other one is offered once.
        self.sent.clear()
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        sent = self.promoted()
        self.assertEqual([payload["listing_id"] for payload in sent], [unknown, ({first_item, second_item} - {unknown}).pop()])
        self.assertEqual((sent[1]["marketplace"], sent[1]["offer_price_cents"], sent[1]["shipping_discount_id"]), ("mercari", 4000, None))

    def test_a_mercari_sign_out_stops_the_round_and_pauses(self):
        items = self.mercari_items(2)
        saved = self.create({**OFFER, "marketplace": "mercari", "shipping_label": None, "scope": "items",
                             "conversation_ids": [conv.id for conv, _item in items]})
        self.enable(saved["id"])
        self.due(saved["id"])
        for _conv, item in items:
            self.sent[item] = {"ok": False, "safe_to_retry": True, "error_code": "signed_out", "error": "Mercari asked to sign in again."}
        self.tick()
        self.assertEqual(len(self.promoted()), 1)
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertFalse(automation.enabled)
        self.assertIn("sign in", automation.paused_reason)

    def test_a_share_round_follows_order_exclusions_cap_and_speed(self):
        newest, _middle, oldest = self.items[1], self.items[0], self.items[2]
        saved = self.create({**SHARE, "excluded_ids": [newest[0].id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.assertEqual(self.tick(), 1)
        sent = self.promoted()
        self.assertEqual([payload["listing_id"] for payload in sent], [self.items[0][1], oldest[1]])
        self.assertTrue(all(payload["gap_ms"] == 15000 and payload["action"] == "share" for payload in sent))
        rows = self.db.query(ListingPromotion).all()
        self.assertTrue(all(row.automation_id == saved["id"] and row.state == "done" for row in rows))
        run = self.db.query(PromotionRun).one()
        self.assertEqual(run.sent, 2)
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertTrue(automation.enabled)
        self.assertGreater(automation.next_run_at, _now())
        # The daily cap counts the last 24 hours, so the next round shares nothing.
        self.client.put(f"/api/promotion-automations/{saved['id']}", json=SHARE)
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(len(self.promoted()), 2)
        self.assertIn("daily cap", self.db.query(PromotionRun).order_by(PromotionRun.started_at.desc()).first().note)

    def test_first_rate_limit_stops_and_pauses(self):
        saved = self.create({**SHARE, "daily_cap": 10, "share_order": "oldest"})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.sent[self.items[2][1]] = {"ok": False, "safe_to_retry": True, "error_code": "rate_limited",
                                       "error": "Poshmark is limiting shares."}
        self.tick()
        self.assertEqual(len(self.promoted()), 1)
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertFalse(automation.enabled)
        self.assertIn("limiting", automation.paused_reason)

    def test_unconfirmed_send_pauses_and_blocks_that_listing(self):
        saved = self.create({**SHARE, "daily_cap": 10, "share_order": "oldest"})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.sent[self.items[2][1]] = browser_bridge.BrowserBridgeError("Chrome did not answer in time.")
        self.tick()
        self.assertEqual(len(self.promoted()), 1)
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertFalse(automation.enabled)
        self.assertIn("not confirmed", automation.paused_reason)
        self.enable(saved["id"])
        self.due(saved["id"])
        self.sent.clear()
        self.tick()
        shared = [payload["listing_id"] for payload in self.promoted()]
        self.assertEqual(shared.count(self.items[2][1]), 1)
        self.assertEqual(len(shared), 3)

    def test_pausing_mid_round_stops_before_the_next_send(self):
        saved = self.create({**SHARE, "daily_cap": 10})
        self.enable(saved["id"])
        self.due(saved["id"])
        calls = []

        def pause_after_first():
            calls.append(1)
            if len(calls) == 2:
                self.db.query(PromotionAutomation).update({"enabled": False})
                self.db.commit()
        self.on_check = pause_after_first
        self.tick()
        self.assertEqual(len(self.promoted()), 1)
        run = self.db.query(PromotionRun).one()
        self.assertIn("turned off", run.note.lower())

    def test_busy_slot_waits_for_the_next_tick(self):
        saved = self.create(SHARE)
        self.enable(saved["id"])
        self.due(saved["id"])
        JobRepo(self.db).create(self.items[0][0].id, "", {}, status="dispatched", current_step="native_delist")
        self.assertEqual(self.tick(), 0)
        self.bridge.assert_not_called()

    def test_offers_go_only_above_the_floor_to_new_likers_with_the_chosen_shipping(self):
        (first, first_post), (second, second_post), (third, _post) = self.items
        for conv, post, cents in ((first, first_post, 3000), (second, second_post, 4500)):
            self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=cents))
        self.db.commit()
        saved = self.create({**OFFER, "excluded_ids": [third.id]})
        coverage = self.client.get(f"/api/promotion-automations/{saved['id']}/coverage").json()
        self.assertEqual((coverage["missing_floor"], coverage["included"], len(coverage["items"])), (0, 2, 3))
        self.assertEqual({item["conversation_id"]: item["included"] for item in coverage["items"]}[third.id], False)
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        sent = self.promoted()
        # 20% off $50 is $40: above the $30 floor, below the $45 one.
        self.assertEqual([(payload["listing_id"], payload["offer_price_cents"], payload["shipping_discount_id"]) for payload in sent],
                         [(first_post, 4000, DISCOUNT)])
        run = self.db.query(PromotionRun).one()
        self.assertEqual((run.sent, run.skipped), (1, 1))
        self.assertIn("below your floor", run.note)
        # No new likers since the last offer, and the 24-hour cooldown: nothing more.
        self.db.query(ListingPromotion).update({"sent_at": _now() - timedelta(days=2)})
        self.db.commit()
        self.due(saved["id"])
        self.tick()
        self.assertEqual(len(self.promoted()), 1)
        self.likes[first_post] = 5
        self.due(saved["id"])
        self.tick()
        self.assertEqual(len(self.promoted()), 2)

    def test_offers_need_enough_likes_and_the_named_shipping_discount(self):
        conv, post = self.items[0]
        self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=1000))
        self.db.commit()
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id], "shipping_label": "Free"})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.promoted(), [])
        self.assertIn("Free", self.db.query(PromotionRun).one().note)
        self.likes[post] = 1
        self.client.put(f"/api/promotion-automations/{saved['id']}", json={**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.promoted(), [])

    def test_minimum_days_listed_and_activity_log(self):
        saved = self.create({**SHARE, "min_days_listed": 20, "daily_cap": 10})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        shared = {payload["listing_id"] for payload in self.promoted()}
        self.assertEqual(shared, {self.items[0][1], self.items[2][1]})
        log = self.client.get(f"/api/promotion-automations/{saved['id']}/activity").json()
        self.assertEqual(len(log["runs"]), 1)
        self.assertEqual({send["state"] for send in log["sends"]}, {"done"})

    def test_pause_all_and_interrupted_sends_pause_their_automation(self):
        saved = self.create(SHARE)
        self.enable(saved["id"])
        self.assertEqual(self.client.post("/api/promotion-automations/pause").json(), {"paused": 1})
        self.enable(saved["id"])
        conv, post = self.items[0]
        self.db.add(ListingPromotion(batch_id="b", conversation_id=conv.id, marketplace="poshmark", action="share", listing_id=post,
                                     state="sending", job_id="job-1", automation_id=saved["id"], expires_at=_now()))
        self.db.add(PromotionRun(automation_id=saved["id"]))
        self.db.commit()
        listing_promotions.mark_interrupted(self.db, "job-1")
        promotion_automations.recover(self.db)
        automation = self.db.get(PromotionAutomation, saved["id"])
        self.assertFalse(automation.enabled)
        self.assertIn("not confirmed", automation.paused_reason)
        self.assertEqual(self.db.query(PromotionRun).one().note, promotion_automations.INTERRUPTED)

    def test_chrome_not_connected_records_the_round_and_sends_nothing(self):
        saved = self.create(SHARE)
        self.enable(saved["id"])
        self.due(saved["id"])
        with patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)):
            self.tick()
        self.assertIn("Connect Chrome", self.db.query(PromotionRun).one().note)
        self.bridge.assert_not_called()
        self.assertTrue(self.db.get(PromotionAutomation, saved["id"]).enabled)
