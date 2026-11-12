from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, update
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy.sql.dml import Update
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
        # Promotion sends and reviews open their own short sessions on worker threads.
        self.enterContext(patch("vendoo_studio.database.SessionLocal", sessionmaker(bind=engine)))
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
        self.enterContext(patch("vendoo_studio.services.extension_connection.extension_manager", SimpleNamespace(connected=True)))
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

    def like_feed(self, events):
        async def answer(job, message_type, payload, timeout=0):
            if message_type == "marketplace.like_events":
                return {"ok": True, "events": events}
            return await self.answer(job, message_type, payload, timeout)
        self.bridge.side_effect = answer

    def check_likes(self):
        return asyncio.run(promotion_automations.check_new_likes(self.db, sleep=AsyncMock()))

    def test_recent_like_sends_before_round_due_and_repeated_feed_never_resends(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        due = row.next_run_at
        at = _now() - timedelta(seconds=10)
        row.like_events_since = at - timedelta(seconds=10)
        self.db.commit()
        self.like_feed([{"listing_id": self.items[0][1], "at": at.isoformat() + "Z"}])
        self.assertEqual(self.check_likes(), 1)
        self.assertEqual(len(self.promoted()), 1)
        self.assertEqual(row.next_run_at, due)
        # Durable journal also prevents replay after a new service/session load.
        self.db.expire_all()
        self.assertEqual(self.check_likes(), 0)
        self.assertEqual(len(self.promoted()), 1)

    def test_draft_deleted_while_reading_like_feed_is_skipped(self):
        kept, gone = self.items[0], self.items[1]
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [kept[0].id, gone[0].id]})
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        at = _now() - timedelta(seconds=10)
        row.like_events_since = at - timedelta(seconds=10)
        self.db.commit()
        feed = [{"listing_id": post, "at": at.isoformat() + "Z"} for _conv, post in (kept, gone)]

        async def answer(job, message_type, payload, timeout=0):
            if message_type == "marketplace.like_events":
                # The seller deletes a draft in another request while Chrome reads the feed.
                self.db.connection().execute(text("DELETE FROM conversations WHERE id = :id"), {"id": gone[0].id})
                return {"ok": True, "events": feed}
            return await self.answer(job, message_type, payload, timeout)
        self.bridge.side_effect = answer
        self.assertEqual(self.check_likes(), 1)
        self.assertEqual([sent["listing_id"] for sent in self.promoted()], [kept[1]])

    def test_verified_new_like_can_follow_previous_offer_with_same_total_likes(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        self.due(row.id)
        self.tick()
        self.assertEqual(len(self.promoted()), 3)
        first = self.db.query(ListingPromotion).filter_by(listing_id=self.items[0][1], state="done").one()
        first.sent_at = _now() - timedelta(minutes=2)
        row.like_events_since = _now() - timedelta(minutes=3)
        self.db.commit()
        self.like_feed([{"listing_id": self.items[0][1], "at": (_now() - timedelta(minutes=1)).isoformat() + "Z"}])
        self.assertEqual(self.check_likes(), 1)
        self.assertEqual(len(self.promoted()), 4)
        self.assertEqual(self.check_likes(), 0)
        self.assertIsNotNone(listing_promotions.blocking(self.db, "poshmark", self.items[0][1], "offer"))

    def test_recent_like_respects_approval_exclusions_and_minimum(self):
        saved = self.create({**OFFER, "excluded_ids": [self.items[0][0].id], "minimum_price": "45"})
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.like_events_since = _now() - timedelta(minutes=2)
        self.db.commit()
        self.like_feed([{"listing_id": post, "at": (_now() - timedelta(minutes=1)).isoformat() + "Z"}
                        for _conv, post in self.items])
        self.check_likes()
        self.assertEqual(self.promoted(), [])
        checked = [call.args[2]["listing_id"] for call in self.bridge.call_args_list if call.args[1] == "marketplace.promotion_check"]
        self.assertNotIn(self.items[0][1], checked)
        promotion_automations.pause(self.db, row.id)
        self.bridge.reset_mock()
        self.check_likes()
        self.bridge.assert_not_awaited()

    def test_old_future_malformed_and_unknown_likes_cannot_trigger(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        self.like_feed([
            {"listing_id": self.items[0][1], "at": (_now() - timedelta(days=1)).isoformat() + "Z"},
            {"listing_id": self.items[1][1], "at": (_now() + timedelta(days=1)).isoformat() + "Z"},
            {"listing_id": "not-ours", "at": _now().isoformat() + "Z"},
        ])
        self.assertEqual(self.check_likes(), 0)
        self.assertEqual(self.promoted(), [])
        self.like_feed([{"listing_id": self.items[0][1], "at": "bad"}])
        self.assertEqual(self.check_likes(), 0)
        self.assertFalse(row.enabled)

    def test_pause_during_like_check_prevents_send(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.like_events_since = _now() - timedelta(minutes=2)
        self.db.commit()
        self.like_feed([{"listing_id": self.items[0][1], "at": (_now() - timedelta(minutes=1)).isoformat() + "Z"}])
        self.on_check = lambda: promotion_automations.pause(self.db, row.id)
        self.check_likes()
        self.assertEqual(self.promoted(), [])

    def test_depop_recent_like_uses_the_same_approved_offer_path(self):
        conv, _post = self.items[0]
        slug = "seller-classic-blue-coat"
        conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"depop": f"https://www.depop.com/products/{slug}/"}})
        self.db.commit()
        self.likes[slug] = 3
        saved = self.create({**OFFER, "marketplace": "depop", "shipping_label": None})
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.like_events_since = _now() - timedelta(minutes=2)
        self.db.commit()
        self.like_feed([{"listing_id": slug, "at": (_now() - timedelta(minutes=1)).isoformat() + "Z"}])
        self.assertEqual(self.check_likes(), 1)
        self.assertEqual(self.promoted()[0]["marketplace"], "depop")
        self.assertEqual(self.check_likes(), 0)

    def test_new_like_never_bypasses_an_unconfirmed_offer(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.like_events_since = _now() - timedelta(minutes=3)
        conv, post = self.items[0]
        send = listing_promotions.new_row(conv, "poshmark", "offer", "earlier", 20, row.id)
        send.listing_id, send.state, send.sent_at = post, "unknown", _now() - timedelta(minutes=2)
        self.db.add(send)
        self.db.commit()
        self.like_feed([{"listing_id": post, "at": (_now() - timedelta(minutes=1)).isoformat() + "Z"}])
        self.assertEqual(self.check_likes(), 0)
        self.assertEqual(self.promoted(), [])

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

    def test_depop_rules_save_off_and_turn_on_only_when_asked(self):
        saved = self.create({**OFFER, "marketplace": "depop", "shipping_label": None})
        self.assertTrue(saved["available"])
        self.assertFalse(saved["enabled"])
        self.assertEqual(self.tick(), 0)
        self.assertTrue(self.enable(saved["id"])["enabled"])
        self.bridge.assert_not_called()

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

    def test_a_due_round_another_machine_already_started_does_not_run_again(self):
        saved = self.create(SHARE)
        self.enable(saved["id"])
        self.due(saved["id"])
        # Machine B read the same due rule, in its own session, before A claimed it.
        machine_b = sessionmaker(bind=self.db.get_bind())()
        self.addCleanup(machine_b.close)
        stale = machine_b.get(PromotionAutomation, saved["id"])
        self.assertEqual(self.tick(), 1)
        sent = len(self.promoted())
        self.assertGreater(sent, 0)
        due_at = self.db.get(PromotionAutomation, saved["id"]).next_run_at
        self.assertNotEqual(stale.next_run_at, due_at)
        self.assertIsNone(asyncio.run(promotion_automations.run_round(machine_b, stale, sleep=AsyncMock())))
        self.db.expire_all()
        self.assertEqual(len(self.promoted()), sent)
        self.assertEqual(self.db.query(PromotionRun).count(), 1)
        self.assertEqual(self.db.get(PromotionAutomation, saved["id"]).next_run_at, due_at)

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

    def test_coverage_reads_the_same_number_of_queries_however_many_items_there_are(self):
        saved = self.create(OFFER)

        def queries() -> tuple[int, dict]:
            count = [0]

            def counted(*_args, **_kwargs):
                count[0] += 1
            engine = self.db.get_bind()
            event.listen(engine, "before_cursor_execute", counted)
            try:
                self.db.expire_all()
                body = self.client.get(f"/api/promotion-automations/{saved['id']}/coverage").json()
            finally:
                event.remove(engine, "before_cursor_execute", counted)
            return count[0], body

        few, _body = queries()
        for index in range(200):
            conv = ConversationRepo(self.db).create(title=f"More {index}")
            conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"poshmark": f"https://poshmark.com/listing/More-{index + 100:024x}"}})
        self.db.commit()
        many, body = queries()
        self.assertEqual(len(body["items"]), 203)
        self.assertEqual(many, few)

    def test_offers_go_only_above_the_floor_to_new_likers_with_the_chosen_shipping(self):
        (first, first_post), (second, second_post), (third, _post) = self.items
        for conv, post, cents in ((first, first_post, 3000), (second, second_post, 4500)):
            self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=cents))
        self.db.commit()
        saved = self.create({**OFFER, "excluded_ids": [third.id]})
        coverage = self.client.get(f"/api/promotion-automations/{saved['id']}/coverage").json()
        self.assertEqual((coverage["included"], len(coverage["items"])), (2, 3))
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
        # A new like reaches the rule through the recent-like feed.
        self.likes[first_post] = 5
        row = self.db.get(PromotionAutomation, saved["id"])
        at = _now() - timedelta(seconds=10)
        row.like_events_since = at - timedelta(seconds=10)
        self.db.commit()
        self.like_feed([{"listing_id": first_post, "at": at.isoformat() + "Z"}])
        self.check_likes()
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

    def test_bounded_offer_scans_rotate_skipped_listings_across_sessions(self):
        saved = self.create(OFFER)
        # Skipped for a price floor, which a new like does not change, so every round checks again.
        for conv, post in self.items:
            self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=4900))
        self.db.commit()
        self.enable(saved["id"])
        checked = []
        # An unlucky random order used to revisit the same two listings forever.
        with patch.object(promotion_automations, "OFFER_READS", 2), patch.object(
                promotion_automations.random, "sample", side_effect=lambda targets, _count: targets):
            for _ in range(3):
                self.due(saved["id"])
                self.bridge.reset_mock()
                self.tick()
                checked.append([call.args[2]["listing_id"] for call in self.bridge.call_args_list
                                if call.args[1] == "marketplace.promotion_check"])
                engine = self.db.get_bind()
                self.db.close()
                self.db = sessionmaker(bind=engine)()
        posts = sorted(post for _conv, post in self.items)
        self.assertEqual(checked, [posts[:2], [posts[2], posts[0]], posts[1:]])
        self.assertEqual(self.db.query(ListingPromotion).filter_by(state="skipped").count(), 6)
        self.assertEqual(self.db.query(ListingPromotion).filter(ListingPromotion.sent_at.is_not(None)).count(), 0)

    def checked(self):
        return [call.args[2]["listing_id"] for call in self.bridge.call_args_list if call.args[1] == "marketplace.promotion_check"]

    def test_scan_waits_for_a_new_like_on_listings_with_nobody_to_offer_to(self):
        (_c0, none), (_c1, few), (_c2, liked) = self.items
        self.likes.update({none: 0, few: 1})
        saved = self.create(OFFER)
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(sorted(self.checked()), sorted([none, few, liked]))
        self.assertEqual([sent["listing_id"] for sent in self.promoted()], [liked])
        # Nothing new was liked: the next scans ask the marketplace nothing and journal nothing.
        journaled = self.db.query(ListingPromotion).count()
        for _ in range(2):
            self.bridge.reset_mock()
            self.due(saved["id"])
            self.tick()
            self.assertEqual(self.checked(), [])
        self.assertEqual(self.db.query(ListingPromotion).count(), journaled)
        self.assertIn("no new likes since the last check (2)", self.db.query(PromotionRun).order_by(PromotionRun.started_at.desc()).first().note)
        # A like from the feed is checked and offered at once.
        self.likes[none] = 3
        row = self.db.get(PromotionAutomation, saved["id"])
        at = _now() - timedelta(seconds=10)
        row.like_events_since = at - timedelta(seconds=10)
        self.db.commit()
        self.like_feed([{"listing_id": none, "at": at.isoformat() + "Z"}])
        self.bridge.reset_mock()
        self.assertEqual(self.check_likes(), 1)
        self.assertEqual(self.checked(), [none])
        self.assertEqual([sent["listing_id"] for sent in self.promoted()], [none])
        # A like the feed missed is still found by the weekly re-check.
        self.db.query(ListingPromotion).filter_by(listing_id=few).update(
            {"created_at": _now() - promotion_automations.LIKE_RECHECK["poshmark"] - timedelta(minutes=1)})
        self.db.commit()
        self.bridge.reset_mock()
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.checked(), [few])

    def test_mercari_waits_a_day_before_re_checking_listings_nobody_liked(self):
        items = self.mercari_items(3)
        (_c0, none), (_c1, few), (_c2, liked) = items
        self.likes.update({none: 0, few: 1})
        saved = self.create({**OFFER, "marketplace": "mercari", "shipping_label": None, "scope": "items",
                             "conversation_ids": [conv.id for conv, _item in items]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(sorted(self.checked()), sorted([none, few, liked]))
        self.assertEqual([sent["listing_id"] for sent in self.promoted()], [liked])
        # Within the day, the 15-minute scans ask Mercari nothing about them.
        self.bridge.reset_mock()
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.checked(), [])
        # A day later Mercari has no like feed to report new likes, so they are read again.
        self.likes[none] = 3
        self.db.query(ListingPromotion).filter(ListingPromotion.listing_id.in_([none, few])).update(
            {"created_at": _now() - promotion_automations.LIKE_RECHECK["mercari"] - timedelta(minutes=1)},
            synchronize_session=False)
        self.db.commit()
        self.bridge.reset_mock()
        self.due(saved["id"])
        self.tick()
        self.assertEqual(sorted(self.checked()), sorted([none, few]))
        self.assertEqual([sent["listing_id"] for sent in self.promoted()], [none])

    def test_nobody_to_offer_to_refusal_waits_for_a_new_like(self):
        conv, post = self.items[0]

        async def refuse(job, message_type, payload, timeout=0):
            if message_type == "marketplace.promotion_check":
                return {"ok": False, "error": "Depop has nobody to send an offer to on this listing.", "error_code": "rejected",
                        "like_count": 0}
            return await self.answer(job, message_type, payload, timeout)
        self.bridge.side_effect = refuse
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.checked(), [post])
        self.assertEqual(self.db.query(ListingPromotion).one().like_count, 0)
        self.bridge.reset_mock()
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.checked(), [])

    def test_offer_scan_uses_latest_check_for_this_rule_and_marketplace_only(self):
        saved = self.create(OFFER)
        other = self.create(OFFER)
        now = _now()
        first, second, unseen = [post for _conv, post in self.items]
        for post, days, rule, marketplace, action in (
            (first, 4, saved["id"], "poshmark", "offer"),
            (first, 1, saved["id"], "poshmark", "offer"),
            (second, 2, saved["id"], "poshmark", "offer"),
            (unseen, 0, other["id"], "poshmark", "offer"),
            (unseen, 0, saved["id"], "depop", "offer"),
            (unseen, 0, saved["id"], "poshmark", "share"),
        ):
            self.db.add(ListingPromotion(batch_id="test", listing_id=post, marketplace=marketplace,
                                         action=action, automation_id=rule, state="skipped",
                                         created_at=now - timedelta(days=days), expires_at=now))
        self.db.commit()
        row = self.db.get(PromotionAutomation, saved["id"])
        ordered = promotion_automations._order(self.db, row, promotion_automations._covered(self.db, row))
        self.assertEqual([target[1] for target in ordered], [unseen, second, first])

    def test_a_skip_names_the_marketplaces_own_reason(self):
        conv, post = self.items[0]

        async def refuse(job, message_type, payload, timeout=0):
            if message_type == "marketplace.promotion_check":
                return {"ok": False, "error": "Poshmark refused the request.", "error_code": "rejected",
                        "marketplace_message": "This item is reserved for a buyer."}
            return await self.answer(job, message_type, payload, timeout)
        self.bridge.side_effect = refuse
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.promoted(), [])
        self.assertIn("Poshmark refused the request: This item is reserved for a buyer. (1)", self.db.query(PromotionRun).one().note)

    def test_overdue_enabled_round_is_logged_without_sending(self):
        saved = self.create(OFFER)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.next_run_at = _now() - timedelta(hours=1)
        self.db.commit()
        with self.assertLogs("vendoo_studio.promotion_automations", level="ERROR") as errors:
            promotion_automations.report_overdue(self.db)

        self.assertIn(row.id, " ".join(errors.output))
        self.assertIn("overdue", " ".join(errors.output))
        self.assertTrue(row.enabled)
        self.assertEqual(self.promoted(), [])
        row.enabled = False
        self.db.commit()
        with self.assertNoLogs("vendoo_studio.promotion_automations", level="ERROR"):
            promotion_automations.report_overdue(self.db)

    def test_failed_job_reports_once_even_when_error_was_handled(self):
        job = JobRepo(self.db).create(self.items[0][0].id, "", {})
        with self.assertLogs("vendoo_studio.jobs", level="ERROR") as errors:
            JobRepo(self.db).update_status(job.id, "failed", error="Chrome refused the action")
        self.assertIn("Chrome refused the action", " ".join(errors.output))
        with self.assertNoLogs("vendoo_studio.jobs", level="ERROR"):
            JobRepo(self.db).update_status(job.id, "failed", error="Chrome refused the action")

    def test_review_refusal_keeps_listing_and_sanitized_reason_in_activity(self):
        conv, post = self.items[0]
        detail = "HTTP 400, code 1003: " + "No recipient was returned. " * 8
        self.bridge.side_effect = AsyncMock(return_value={
            "ok": False, "error": "Depop refused the preview. token=private-value",
            "error_code": "rejected", "marketplace_message": detail + " Bearer private-token",
        })
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        with self.assertLogs("vendoo_studio.listing_promotions", level="ERROR") as errors:
            self.tick()
        self.assertIn("1003", " ".join(errors.output))
        self.assertNotIn("private-token", " ".join(errors.output))
        activity = self.client.get(f"/api/promotion-automations/{saved['id']}/activity").json()
        self.assertEqual(len(activity["sends"]), 1)
        skipped = activity["sends"][0]
        self.assertEqual((skipped["state"], skipped["listing_id"], skipped["title"]), ("skipped", post, conv.title))
        self.assertIn(detail.strip(), skipped["note"])
        self.assertIn("HTTP 400, code 1003", skipped["marketplace_message"])
        self.assertNotIn("private-value", skipped["note"])
        self.assertNotIn("private-token", str(activity))
        self.assertIsNotNone(skipped["finished_at"])
        self.assertIsNone(listing_promotions.blocking(self.db, "poshmark", post, "offer"))
        self.assertEqual(self.promoted(), [])
        self.assertEqual(activity["runs"][0]["skipped"], 1)

    def test_review_stop_is_journaled_with_known_listing_even_before_remote_check(self):
        conv, post = self.items[0]
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        with patch.object(listing_promotions, "review", new_callable=AsyncMock) as review:
            review.side_effect = listing_promotions.PromotionError(409, "Reconnect Depop.", "signed_out")
            self.tick()
        activity = self.client.get(f"/api/promotion-automations/{saved['id']}/activity").json()
        self.assertEqual(len(activity["sends"]), 1)
        skipped = activity["sends"][0]
        self.assertEqual((skipped["state"], skipped["listing_id"], skipped["title"]), ("skipped", post, conv.title))
        self.assertEqual(skipped["note"], "Reconnect Depop.")
        self.assertIsNone(skipped["marketplace_message"])
        self.assertEqual(activity["runs"][0]["skipped"], 1)
        self.assertFalse(self.db.get(PromotionAutomation, saved["id"]).enabled)
        self.assertEqual(self.promoted(), [])

    def test_failed_commit_during_round_still_closes_the_round_without_pending_rollback(self):
        # Production 4605eb294eb3: saving a review skip failed (pool timeout during
        # flush), then the round's closing commit hit PendingRollbackError.
        conv, post = self.items[0]
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        engine = self.db.get_bind()
        failures = []

        def fail_skip_insert(_conn, _cursor, statement, _params, _context, _many):
            if not failures and statement.lstrip().upper().startswith("INSERT INTO LISTING_PROMOTIONS"):
                failures.append(statement)
                raise sqlalchemy_exc.TimeoutError("QueuePool limit of size 8 overflow 4 reached")

        event.listen(engine, "before_cursor_execute", fail_skip_insert)
        self.addCleanup(event.remove, engine, "before_cursor_execute", fail_skip_insert)
        with patch.object(listing_promotions, "review", new_callable=AsyncMock) as review:
            review.side_effect = listing_promotions.PromotionError(409, "Nobody has liked this listing yet.")
            with self.assertRaises(sqlalchemy_exc.TimeoutError):
                self.tick()
        self.assertEqual(len(failures), 1)
        self.db.expire_all()
        run = self.db.query(PromotionRun).one()
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(self.db.query(ListingPromotion).count(), 0)
        self.assertEqual(self.promoted(), [])
        self.assertIsNone(listing_promotions.blocking(self.db, "poshmark", post, "offer"))
        self.assertTrue(self.db.get(PromotionAutomation, saved["id"]).enabled)

    def test_deleted_draft_during_review_keeps_skip_without_a_dangling_reference(self):
        conv, post = self.items[0]
        conv_id, title = conv.id, conv.title
        saved = self.create({**OFFER, "scope": "items", "conversation_ids": [conv_id]})
        self.enable(saved["id"])
        self.due(saved["id"])

        async def refuse_after_delete(_job, message_type, payload, timeout=0):
            self.assertEqual(message_type, "marketplace.promotion_check")
            self.db.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": conv_id})
            self.db.commit()
            return {"ok": False, "error": "Depop refused the preview.", "error_code": "rejected"}

        self.bridge.side_effect = refuse_after_delete
        self.tick()
        skipped = self.client.get(f"/api/promotion-automations/{saved['id']}/activity").json()["sends"][0]
        self.assertIsNone(skipped["conversation_id"])
        self.assertEqual((skipped["listing_id"], skipped["title"], skipped["state"]), (post, title, "skipped"))
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

    def test_optional_minimum_includes_current_and_future_listings_on_every_marketplace(self):
        for marketplace in ("poshmark", "mercari", "depop"):
            with self.subTest(marketplace=marketplace):
                for index, (conv, _post) in enumerate(self.items):
                    item = f"m{index + 1:011d}" if marketplace == "mercari" else f"{index:024x}" if marketplace == "poshmark" else f"item-{index}"
                    url = (f"https://www.mercari.com/us/item/{item}/" if marketplace == "mercari" else
                           f"https://poshmark.com/listing/Item-{item}" if marketplace == "poshmark" else
                           f"https://www.depop.com/products/{item}/")
                    conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {marketplace: url}})
                    self.likes[item] = 3
                self.db.commit()
                before = len(self.promoted())
                saved = self.create({**OFFER, "marketplace": marketplace, "shipping_label": None})
                self.assertIsNone(saved["minimum_price"])
                self.enable(saved["id"])
                self.due(saved["id"])
                self.tick()
                self.assertEqual(len(self.promoted()) - before, 3)
                self.assertEqual({send["floor_cents"] for send in self.promoted()[before:]}, {1})
                future = ConversationRepo(self.db).create(title="Future item")
                item = "m99999999999" if marketplace == "mercari" else "f" * 24 if marketplace == "poshmark" else "future-item"
                url = (f"https://www.mercari.com/us/item/{item}/" if marketplace == "mercari" else
                       f"https://poshmark.com/listing/Item-{item}" if marketplace == "poshmark" else
                       f"https://www.depop.com/products/{item}/")
                future.notes = merge_notes(future.notes, {"vendooListingUrls": {marketplace: url}})
                self.likes[item] = 3
                self.db.commit()
                self.due(saved["id"])
                self.tick()
                self.assertEqual(len(self.promoted()) - before, 4)
                self.assertEqual(self.db.query(MarketplaceOfferFloor).count(), 0)

    def test_shared_minimum_and_saved_individual_minimum_are_both_enforced(self):
        conv, post = self.items[0]
        self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=4500))
        self.db.commit()
        saved = self.create({**OFFER, "minimum_price": "35.00"})
        self.assertEqual(saved["minimum_price"], "35.00")
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(len(self.promoted()), 2)
        self.assertEqual({send["floor_cents"] for send in self.promoted()}, {3500})
        self.assertNotIn(post, {send["listing_id"] for send in self.promoted()})

    def test_optional_shared_minimum_validation_and_edits_stay_off(self):
        for minimum in ("0", "-1", "NaN", "Infinity", "1.001", "10000000"):
            self.assertEqual(self.client.post("/api/promotion-automations", json={**OFFER, "minimum_price": minimum}).status_code, 400)
        saved = self.create({**OFFER, "minimum_price": "45"})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.promoted(), [])
        edited = self.client.put(f"/api/promotion-automations/{saved['id']}", json={**OFFER, "minimum_price": None}).json()
        self.assertFalse(edited["enabled"])
        self.assertIsNone(edited["minimum_price"])

    def test_shared_minimum_is_rechecked_after_reading_marketplace(self):
        saved = self.create({**OFFER, "minimum_price": "30"})
        def raise_minimum():
            automation = self.db.get(PromotionAutomation, saved["id"])
            automation.minimum_price_cents = 4500
            self.db.commit()
        self.on_check = raise_minimum
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual(self.promoted(), [])

    def test_changed_minimum_blocks_the_final_send_reservation(self):
        saved = self.create({**OFFER, "minimum_price": "30"})
        self.enable(saved["id"])
        self.due(saved["id"])
        execute = self.db.execute
        changed = False

        def intercept(statement, *args, **kwargs):
            nonlocal changed
            if isinstance(statement, Update) and statement.table.name == "promotion_automations" and not changed:
                changed = True
                execute(update(PromotionAutomation).where(PromotionAutomation.id == saved["id"]).values(minimum_price_cents=4500))
                self.db.commit()
            return execute(statement, *args, **kwargs)

        with patch.object(self.db, "execute", side_effect=intercept):
            self.tick()
        self.assertTrue(changed)
        self.assertEqual(self.promoted(), [])

    def test_clearing_an_individual_minimum_uses_the_rule_minimum(self):
        conv, post = self.items[0]
        self.db.add(MarketplaceOfferFloor(marketplace="poshmark", listing_id=post, conversation_id=conv.id, floor_cents=4500))
        self.db.commit()
        response = self.client.put("/api/promotions/floors", json={"marketplace": "poshmark", "floors": {conv.id: ""}})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()[conv.id]["floor"])
        saved = self.create({**OFFER, "minimum_price": "35", "scope": "items", "conversation_ids": [conv.id]})
        self.enable(saved["id"])
        self.due(saved["id"])
        self.tick()
        self.assertEqual([send["floor_cents"] for send in self.promoted()], [3500])

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

    def test_a_round_waits_quietly_while_chrome_reconnects(self):
        # After a restart Chrome takes a moment to reconnect: no empty round, and the slot is kept.
        saved = self.create(SHARE)
        self.enable(saved["id"])
        self.due(saved["id"])
        due_at = self.db.get(PromotionAutomation, saved["id"]).next_run_at
        with patch("vendoo_studio.services.extension_connection.extension_manager", SimpleNamespace(connected=False)):
            self.tick()
        self.assertEqual(self.db.query(PromotionRun).count(), 0)
        self.assertEqual(self.db.get(PromotionAutomation, saved["id"]).next_run_at, due_at)
        self.bridge.assert_not_called()
        self.tick()
        self.assertEqual(self.db.query(PromotionRun).count(), 1)

    def test_chrome_disconnected_for_long_records_the_round_and_sends_nothing(self):
        saved = self.create(SHARE)
        self.enable(saved["id"])
        row = self.db.get(PromotionAutomation, saved["id"])
        row.next_run_at = _now() - promotion_automations.CHROME_GRACE - timedelta(minutes=1)
        self.db.commit()
        with patch("vendoo_studio.services.extension_connection.extension_manager", SimpleNamespace(connected=False)):
            self.tick()
        self.assertIn("Connect Chrome", self.db.query(PromotionRun).one().note)
        self.bridge.assert_not_called()
        self.assertTrue(self.db.get(PromotionAutomation, saved["id"]).enabled)

    def test_offers_wait_fifteen_minutes_then_schedule_the_next_check(self):
        moment = _now()
        with patch.object(promotion_automations, "_now", return_value=moment):
            saved = self.create({**OFFER, "scope": "items", "conversation_ids": [self.items[0][0].id]})
            enabled = self.enable(saved["id"])
        self.assertEqual(datetime.fromisoformat(enabled["next_run_at"].rstrip("Z")), moment + timedelta(minutes=15))
        self.assertEqual(asyncio.run(promotion_automations.tick(self.db, now=moment + timedelta(minutes=14), sleep=AsyncMock())), 0)
        self.assertEqual(self.promoted(), [])
        with patch.object(promotion_automations, "_now", return_value=moment + timedelta(minutes=15)):
            self.assertEqual(asyncio.run(promotion_automations.tick(self.db, now=moment + timedelta(minutes=15), sleep=AsyncMock())), 1)
        self.assertEqual(len(self.promoted()), 1)
        self.assertEqual(self.db.get(PromotionAutomation, saved["id"]).next_run_at, moment + timedelta(minutes=30))

    def test_restart_shortens_existing_offer_schedules_without_changing_shares_or_pauses(self):
        moment = _now()
        old_due = moment + timedelta(hours=1)
        offers = []
        for marketplace in ("poshmark", "mercari", "depop"):
            saved = self.create({**OFFER, "marketplace": marketplace, "shipping_label": None})
            self.enable(saved["id"])
            row = self.db.get(PromotionAutomation, saved["id"])
            row.next_run_at = old_due
            offers.append(row)
        paused = self.create(OFFER)
        share = self.create(SHARE)
        self.enable(share["id"])
        share_row = self.db.get(PromotionAutomation, share["id"])
        share_row.next_run_at = old_due
        self.db.commit()
        with patch.object(promotion_automations, "_now", return_value=moment):
            promotion_automations.recover(self.db)
        self.assertEqual({row.next_run_at for row in offers}, {moment + timedelta(minutes=15)})
        self.assertEqual(share_row.next_run_at, old_due)
        self.assertFalse(self.db.get(PromotionAutomation, paused["id"]).enabled)
        self.bridge.assert_not_called()
