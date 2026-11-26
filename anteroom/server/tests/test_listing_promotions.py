from __future__ import annotations

import unittest
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.models.job import Job
from anteroom.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from anteroom.repositories.queries import ConversationRepo, JobRepo
from anteroom.services import browser_bridge, listing_promotions
from anteroom.services.vendoo_import import merge_notes

POST = "6aae234280cb08338a8cb591"
DISCOUNT = "c" * 24
URL = f"https://poshmark.com/listing/Vintage-Tee-{POST}"


class OfferConfirmationTest(unittest.TestCase):
    def settle(self, marketplace, **result):
        row = ListingPromotion(marketplace=marketplace, action="offer", offer_price_cents=4000, like_count=99)
        listing_promotions._settle(row, {"ok": True, "state": "done", **result})
        return row

    def test_marketplace_evidence_is_distinct_from_preview_likes(self):
        cases = [
            ("poshmark", "request_accepted", None, "Recipient delivery was not verified."),
            ("depop", "job_completed", None, "The recipient count was not provided."),
            ("mercari", "recipients_created", 2, "2 recipient offers created"),
        ]
        for marketplace, confirmation, count, note in cases:
            with self.subTest(marketplace=marketplace):
                row = self.settle(marketplace, confirmation=confirmation, recipient_count=2)
                self.assertEqual((row.state, row.confirmation, row.recipient_count), ("done", confirmation, count))
                self.assertIn(note, row.note)

    def test_legacy_or_wrong_marketplace_evidence_never_claims_delivery(self):
        for evidence in ({}, {"recipient_count": 99}, {"confirmation": "recipients_created", "recipient_count": 99}):
            row = self.settle("poshmark", **evidence)
            self.assertEqual((row.state, row.confirmation, row.recipient_count), ("done", "legacy_unverified", None))
            self.assertIn("not verified", row.note)

    def test_invalid_mercari_receipt_stays_unknown(self):
        for count in (None, 0, -1, True, "2", 1.5):
            row = self.settle("mercari", confirmation="recipients_created", recipient_count=count)
            self.assertEqual((row.state, row.recipient_count), ("unknown", None))


class ListingPromotionsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        # Promotion sends and reviews open their own short sessions on worker threads.
        self.enterContext(patch("anteroom.database.SessionLocal", sessionmaker(bind=engine)))
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title="Vintage tee")
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": {"poshmark": URL}})
        self.db.commit()
        self.bridge = self.enterContext(patch("anteroom.services.listing_promotions.browser_bridge.request", new_callable=AsyncMock))
        self.bridge.side_effect = self.answer
        self.enterContext(patch("anteroom.services.extension_connection.extension_manager", SimpleNamespace(connected=True)))
        self.check = {"ok": True, "price_cents": 5000, "like_count": 3, "minimum_discount_percent": 10,
                      "maximum_discount_percent": 79, "offer_price_cents": 4000,
                      "shipping_discounts": [{"id": DISCOUNT, "label": "-$1.50", "min_offer_cents": 500}]}
        self.sent = {"ok": True, "state": "done"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    async def answer(self, _job, message_type, payload, timeout=0):
        if message_type == "marketplace.promotion_check":
            return self.check
        if message_type == "marketplace.promote":
            if isinstance(self.sent, Exception):
                raise self.sent
            return self.sent
        return {"finished": False, "known": False}

    def calls(self, kind):
        return [call for call in self.bridge.call_args_list if call.args[1] == kind]

    def preview(self, **body):
        response = self.client.post("/api/promotions/preview", json={"conversation_ids": [self.conv.id], "marketplace": "poshmark", **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["rows"][0]

    def test_depop_multisize_preview_stays_skipped_and_rechecks_after_size_changes(self):
        message = "Depop does not send offers on listings with several sizes."
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": {"depop": "https://www.depop.com/products/fixture-tee/"}})
        self.db.commit()
        self.check = {"ok": False, "error": message, "error_code": "rejected", "safe_to_retry": True}
        first = self.preview(marketplace="depop", action="offer", discount_percent=10,
                             floors={self.conv.id: "10"})
        self.assertEqual((first["state"], first["note"]), ("skipped", message))
        self.assertIsNone(first["like_count"])
        self.assertEqual(self.client.post(f"/api/promotions/{first['id']}/perform", json={"confirm": True}).status_code, 409)
        # A fresh marketplace read can observe sizes changed by the seller.
        self.check = {"ok": True, "price_cents": 5000, "like_count": 3, "minimum_discount_percent": 5,
                      "maximum_discount_percent": 80, "offer_price_cents": 4500}
        second = self.preview(marketplace="depop", action="offer", discount_percent=10)
        self.assertEqual(second["state"], "preview")
        self.assertEqual(len(self.calls("marketplace.promotion_check")), 2)
        self.assertEqual(self.calls("marketplace.promote"), [])

    def test_share_is_reviewed_then_sent_once(self):
        row = self.preview(action="share")
        self.assertEqual((row["state"], row["listing_id"], row["price"]), ("preview", POST, "50.00"))
        self.assertEqual(self.calls("marketplace.promote"), [])
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={}).status_code, 422)
        done = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["state"], "done")
        payload = self.calls("marketplace.promote")[0].args[2]
        self.assertEqual((payload["action_id"], payload["action"], payload["listing_id"], payload["confirm"]), (row["id"], "share", POST, True))
        # The same reviewed row is never sent twice.
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).status_code, 409)
        self.assertEqual(len(self.calls("marketplace.promote")), 1)
        job = self.db.query(Job).one()
        self.assertEqual((job.current_step, job.status), ("native_promote", "completed"))

    def test_only_named_supported_marketplaces_and_actions(self):
        for body in ({"marketplace": "facebook", "action": "share"}, {"marketplace": "poshmark", "action": "refresh"}, {"action": "share"}):
            response = self.client.post("/api/promotions/preview", json={"conversation_ids": [self.conv.id], **body})
            self.assertEqual(response.status_code, 422, body)
        self.assertEqual(self.client.post("/api/promotions/preview", json={"conversation_ids": [self.conv.id], "marketplace": "mercari",
                                                                           "action": "share"}).status_code, 400)
        self.bridge.assert_not_called()

    def test_depop_offer_finishes_without_claiming_the_preview_count_was_delivered(self):
        self.enterContext(patch("anteroom.services.cloud_sessions.has_active_session", return_value=True))
        conv = ConversationRepo(self.db).create(title="Depop tee")
        conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"depop": "https://www.depop.com/products/seller-vintage-tee/"}})
        self.db.commit()
        self.check = {"ok": True, "price_cents": 5000, "like_count": 4, "minimum_discount_percent": 5,
                      "maximum_discount_percent": 90, "offer_price_cents": 4250, "shipping_discounts": []}
        self.sent = {"ok": True, "state": "done"}
        response = self.client.post("/api/promotions/preview", json={"conversation_ids": [conv.id], "marketplace": "depop",
                                                                     "action": "offer", "discount_percent": 15, "floors": {conv.id: "40"}})
        row = response.json()["rows"][0]
        self.assertEqual((row["state"], row["listing_id"], row["offer_price"], row["like_count"]), ("preview", "seller-vintage-tee", "42.50", 4))
        done = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).json()
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["note"], "Chrome reported completion of the $42.50 offer. Recipient delivery was not verified.")
        self.assertEqual(done["confirmation"], "legacy_unverified")
        self.assertIsNone(done["recipient_count"])
        payload = self.calls("marketplace.promote")[0].args[2]
        self.assertEqual((payload["marketplace"], payload["offer_price_cents"], payload["floor_cents"]), ("depop", 4250, 4000))
        self.assertEqual(len(self.calls("marketplace.promote")), 1)

    def test_mercari_cooldown_survives_session_reload_and_only_expires_to_preview(self):
        item = "m82647727742"
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": {"mercari": f"https://www.mercari.com/us/item/{item}/"}})
        self.db.commit()
        body = {"conversation_ids": [self.conv.id], "marketplace": "mercari", "action": "offer",
                "discount_percent": 12, "floors": {self.conv.id: "1"}}
        self.check = {"ok": False, "error": "Mercari refused the request.", "error_code": "rejected", "safe_to_retry": True,
                      "marketplace_message": "You can make an offer again in 47 hours. (once every 3 days)"}
        with self.assertLogs(listing_promotions.log, level="INFO") as logs:
            first = self.client.post("/api/promotions/preview", json=body).json()["rows"][0]
        self.assertTrue(all(entry.levelname == "INFO" for entry in logs.records))
        self.assertEqual(first["marketplace_message"], self.check["marketplace_message"])
        self.assertEqual(first["state"], "skipped")
        refused_send = self.client.post(f"/api/promotions/{first['id']}/perform", json={"confirm": True})
        self.assertEqual(refused_send.status_code, 409)
        saved = self.db.get(ListingPromotion, first["id"])
        self.assertIsNone(saved.sent_at)
        self.assertIsNone(saved.offer_price_cents)
        self.assertIsNone(saved.confirmation)
        created = saved.finished_at
        self.db.expunge_all()  # Force the next request to use durable journal evidence.
        self.check = {"ok": True, "price_cents": 1000, "like_count": 2, "offer_price_cents": 800}
        with patch.object(listing_promotions, "_now", return_value=created + timedelta(hours=47, minutes=59)):
            again = self.client.post("/api/promotions/preview", json=body).json()["rows"][0]
        self.assertEqual(again["state"], "skipped")
        self.assertEqual(len(self.calls("marketplace.promotion_check")), 1)
        with patch.object(listing_promotions, "_now", return_value=created + timedelta(hours=48)):
            expired = self.client.post("/api/promotions/preview", json=body).json()["rows"][0]
        self.assertEqual(expired["state"], "preview")
        self.assertEqual(len(self.calls("marketplace.promotion_check")), 2)
        self.assertEqual(self.calls("marketplace.promote"), [])

    def test_cooldown_recognition_is_narrow_and_other_refusals_stay_errors(self):
        valid = "You can make an offer again in 47 hours. (once every 3 days)"
        base = {"ok": False, "error": "Mercari refused the request.", "error_code": "rejected",
                "safe_to_retry": True, "marketplace_message": valid}
        variants = [
            {"marketplace_message": message} for message in (
                "You can make an offer again", valid + " extra", valid.replace("47", "0"),
                valid.replace("47", "73"), valid.replace("47", "-1"), valid.replace("47", "1.5"),
                valid.replace("47", "999999999"), valid.replace("3 days", "2 days"),
                "You must lower your offer price.", None,
            )
        ] + [{"error_code": code} for code in ("signed_out", "challenge", "rate_limited", "uncertain", "network")] + [
            {"safe_to_retry": False}, {"safe_to_retry": None}, {"error": "Other refusal"}]
        for variant in variants:
            with self.subTest(variant=variant):
                row = listing_promotions.new_row(self.conv, "mercari", "offer", "test", 12)
                self.check = {**base, **variant}
                with self.assertLogs(listing_promotions.log, level="ERROR"):
                    with self.assertRaises(listing_promotions.PromotionError) as error:
                        asyncio.run(listing_promotions._review_remote(row, "m12345678901"))
                self.assertNotEqual(error.exception.code, "eligibility_cooldown")
        for marketplace, action in (("poshmark", "offer"), ("depop", "offer"), ("mercari", "share")):
            row = listing_promotions.new_row(self.conv, marketplace, action, "test", 12)
            self.check = base
            with self.assertLogs(listing_promotions.log, level="ERROR"):
                with self.assertRaises(listing_promotions.PromotionError):
                    asyncio.run(listing_promotions._review_remote(row, "m12345678901"))

    def test_cooldown_timestamps_cannot_create_an_indefinite_wait(self):
        now = listing_promotions._now()
        row = listing_promotions.new_row(self.conv, "mercari", "offer", "test", 12)
        row.listing_id, row.state = "m12345678901", "skipped"
        row.note = listing_promotions.MERCARI_COOLDOWN_NOTE
        row.marketplace_message = "You can make an offer again in 47 hours. (once every 3 days)"
        # Exercise validation independently of the ORM's DateTime decoder: an
        # invalid legacy value must never become an indefinite eligibility gate.
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [row]
        for timestamp in (None, "tomorrow", float("nan"), now.replace(tzinfo=UTC),
                          now + timedelta(seconds=1), datetime.max, now - timedelta(hours=48)):
            with self.subTest(timestamp=timestamp):
                row.finished_at = timestamp
                self.assertIsNone(listing_promotions.blocking(db, "mercari", row.listing_id, "offer"))
        row.finished_at = now
        self.assertIsNotNone(listing_promotions.blocking(db, "mercari", row.listing_id, "offer"))

    def test_cooldown_scope_and_unconfirmed_send_precedence(self):
        now = listing_promotions._now()
        row = listing_promotions.new_row(self.conv, "mercari", "offer", "test", 12)
        row.listing_id, row.state = "m12345678901", "skipped"
        row.note = listing_promotions.MERCARI_COOLDOWN_NOTE
        row.marketplace_message = "You can make an offer again in 47 hours. (once every 3 days)"
        row.finished_at = now
        self.db.add(row)
        self.db.commit()
        self.assertIsNotNone(listing_promotions.blocking(self.db, "mercari", row.listing_id, "offer", new_like_at=now))
        for marketplace, item, action in (("mercari", "m99999999999", "offer"), ("poshmark", row.listing_id, "offer"),
                                          ("depop", row.listing_id, "offer"), ("mercari", row.listing_id, "share")):
            self.assertIsNone(listing_promotions.blocking(self.db, marketplace, item, action))
        for state in ("sending", "unknown"):
            uncertain = listing_promotions.new_row(self.conv, "mercari", "offer", state, 12)
            uncertain.listing_id, uncertain.state = row.listing_id, state
            self.db.add(uncertain)
            self.db.commit()
            self.assertIn("unconfirmed", listing_promotions.blocking(self.db, "mercari", row.listing_id, "offer"))
            self.db.delete(uncertain)
            self.db.commit()

    def test_mercari_offer_has_no_shipping_choice(self):
        item = "m12345678901"
        conv = ConversationRepo(self.db).create(title="Mercari jacket")
        conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"mercari": f"https://www.mercari.com/us/item/{item}/"}})
        self.db.commit()
        self.check = {"ok": True, "price_cents": 5000, "like_count": 4, "minimum_discount_percent": 20,
                      "maximum_discount_percent": 48, "offer_price_cents": 4000, "shipping_discounts": []}
        response = self.client.post("/api/promotions/preview", json={"conversation_ids": [conv.id], "marketplace": "mercari",
                                                                     "action": "offer", "discount_percent": 20, "floors": {conv.id: "30"}})
        row = response.json()["rows"][0]
        self.assertEqual((row["state"], row["listing_id"], row["offer_price"]), ("preview", item, "40.00"))
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True, "shipping_discount_id": DISCOUNT}).status_code, 400)
        done = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).json()
        self.assertEqual(done["state"], "done")
        payload = self.calls("marketplace.promote")[0].args[2]
        self.assertEqual((payload["marketplace"], payload["offer_price_cents"], payload["shipping_discount_id"]), ("mercari", 4000, None))

    def test_an_unconfirmed_share_blocks_sharing_that_listing(self):
        row = self.preview(action="share")
        self.sent = browser_bridge.BrowserBridgeError("Chrome did not answer in time.")
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).json()["state"], "unknown")
        again = self.preview(action="share")
        self.assertEqual(again["state"], "skipped")
        self.assertIn("unconfirmed", again["note"])
        self.assertEqual(len(self.calls("marketplace.promote")), 1)

    def test_offer_needs_a_floor_and_never_goes_below_it(self):
        row = self.preview(action="offer", discount_percent=20)
        self.assertEqual(row["state"], "skipped")
        self.assertIn("lowest price you accept", row["note"])
        self.assertEqual(self.calls("marketplace.promotion_check"), [])
        low = self.preview(action="offer", discount_percent=20, floors={self.conv.id: "45"})
        self.assertEqual(low["state"], "skipped")
        self.assertIn("below your floor", low["note"])
        ok = self.preview(action="offer", discount_percent=20, floors={self.conv.id: "35.00"})
        self.assertEqual((ok["state"], ok["offer_price"], ok["floor"]), ("preview", "40.00", "35.00"))
        # A later, higher floor blocks the reviewed offer.
        self.db.query(MarketplaceOfferFloor).one().floor_cents = 4500
        self.db.commit()
        refused = self.client.post(f"/api/promotions/{ok['id']}/perform", json={"confirm": True, "shipping_discount_id": DISCOUNT})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.calls("marketplace.promote"), [])

    def test_offer_needs_an_explicit_reviewed_shipping_choice(self):
        row = self.preview(action="offer", discount_percent=20, floors={self.conv.id: "30"})
        for choice in (None, "d" * 24):
            body = {"confirm": True, **({"shipping_discount_id": choice} if choice else {})}
            self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json=body).status_code, 400)
        self.assertEqual(self.calls("marketplace.promote"), [])
        done = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True, "shipping_discount_id": DISCOUNT}).json()
        self.assertEqual((done["state"], done["shipping_discount_label"]), ("done", "-$1.50"))
        payload = self.calls("marketplace.promote")[0].args[2]
        self.assertEqual((payload["offer_price_cents"], payload["floor_cents"], payload["shipping_discount_id"]), (4000, 3000, DISCOUNT))
        # One offer per listing per day.
        again = self.preview(action="offer", discount_percent=20)
        self.assertEqual(again["state"], "skipped")
        self.assertIn("last 24 hours", again["note"])

    def test_no_likers_or_a_discount_poshmark_refuses_is_skipped(self):
        self.check = {**self.check, "like_count": 0}
        self.assertIn("Nobody has liked", self.preview(action="offer", discount_percent=20, floors={self.conv.id: "1"})["note"])
        self.check = {**self.check, "like_count": 3, "offer_price_cents": None}
        self.assertIn("between 10% and 79%", self.preview(action="offer", discount_percent=5)["note"])
        # A marketplace whose limits leave no valid discount says so instead of "None%".
        self.check = {**self.check, "minimum_discount_percent": None, "maximum_discount_percent": None}
        note = self.preview(action="offer", discount_percent=5)["note"]
        self.assertIn("leave no discount", note)
        self.assertNotIn("None", note)

    def test_lost_reply_is_unknown_blocks_offers_and_check_only_reads(self):
        row = self.preview(action="offer", discount_percent=20, floors={self.conv.id: "30"})
        self.sent = browser_bridge.BrowserBridgeError("Chrome did not answer in time.")
        result = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True, "shipping_discount_id": "none"}).json()
        self.assertEqual(result["state"], "unknown")
        self.assertIn("unconfirmed", self.preview(action="offer", discount_percent=20)["note"])
        checked = self.client.post(f"/api/promotions/{row['id']}/check").json()
        self.assertEqual(checked["state"], "unknown")
        self.assertEqual(len(self.calls("marketplace.promote")), 1)
        self.assertEqual(self.calls("marketplace.promotion_result")[0].args[2], {"action_id": row["id"]})
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/checked", json={"confirm": True}).json()["state"], "checked")

    def test_check_settles_from_the_extension_journal(self):
        row = self.preview(action="share")
        self.sent = {"ok": False, "safe_to_retry": False, "error_code": "uncertain"}
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).json()["state"], "unknown")
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "done"}}
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/check").json()["state"], "done")
        stale = {"finished": True, "result": {"ok": False, "safe_to_retry": False, "error_code": "uncertain"}}
        self.assertEqual(listing_promotions._record_check(self.db, row["id"], stale)["state"], "done")

    def test_account_check_recovers_cloud_receipt_without_a_listing_job(self):
        row = ListingPromotion(batch_id="batch", marketplace="poshmark", listing_id="__account__",
                               action="follow", state="unknown", title="Follow", expires_at=datetime.now(UTC))
        self.db.add(row)
        self.db.commit()
        receipt = {"finished": True, "result": {"ok": True, "state": "done", "followed_count": 3}}
        with patch("anteroom.services.cloud_sessions.load_journals", return_value={f"promotion_{row.id}": receipt}):
            checked = self.client.post(f"/api/promotions/{row.id}/check").json()
        self.assertEqual((checked["state"], checked["recipient_count"]), ("done", 3))
        self.bridge.assert_not_awaited()

    def test_late_check_reply_does_not_overwrite_manual_acknowledgment(self):
        row = self.preview(action="share")
        self.sent = {"ok": False, "safe_to_retry": False, "error_code": "uncertain"}
        self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True})
        self.client.post(f"/api/promotions/{row['id']}/checked", json={"confirm": True})
        stale = {"finished": True, "result": {"ok": False, "safe_to_retry": False}}
        self.assertEqual(listing_promotions._record_check(self.db, row["id"], stale)["state"], "checked")

    def test_a_refusal_is_failed_and_a_rate_limit_stops_the_batch(self):
        row = self.preview(action="share")
        self.sent = {"ok": False, "safe_to_retry": True, "error_code": "rate_limited", "error": "Poshmark is limiting requests"}
        result = self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).json()
        self.assertEqual((result["state"], result["stop"]), ("failed", True))

    def _mercari_batch(self, count):
        conversations = []
        for index in range(count):
            conv = ConversationRepo(self.db).create(title=f"Jacket {index}")
            conv.notes = merge_notes(conv.notes, {"vendooListingUrls": {"mercari": f"https://www.mercari.com/us/item/m8264772774{index}/"}})
            conversations.append(conv.id)
        self.db.commit()
        return conversations

    def test_a_blocked_marketplace_stops_a_several_item_review(self):
        for code in sorted(listing_promotions.STOP_CODES):
            with self.subTest(code=code):
                self.bridge.reset_mock()
                ids = self._mercari_batch(3)
                self.check = {"ok": False, "safe_to_retry": True, "error_code": code,
                              "error": "Mercari blocked Anteroom's cloud request for listing details. The offer check stopped."}
                response = self.client.post("/api/promotions/preview", json={
                    "conversation_ids": ids, "marketplace": "mercari", "action": "offer", "discount_percent": 12,
                    "floors": {conv_id: "1" for conv_id in ids}})
                self.assertEqual(response.status_code, 200, response.text)
                rows = response.json()["rows"]
                # Mercari is asked about the first item only.
                self.assertEqual(len(self.calls("marketplace.promotion_check")), 1)
                self.assertEqual([row["conversation_id"] for row in rows], ids)
                self.assertEqual([row["state"] for row in rows], ["skipped"] * 3)
                self.assertEqual(rows[0]["note"], self.check["error"])
                for row in rows[1:]:
                    self.assertIn("Not checked", row["note"])
                    self.assertIn(self.check["error"], row["note"])
                # No row of the stopped review can be sent.
                for row in rows:
                    self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).status_code, 409)
                self.assertEqual(self.calls("marketplace.promote"), [])

    def test_one_refused_item_does_not_stop_a_several_item_review(self):
        ids = self._mercari_batch(2)
        answers = iter([{"ok": False, "safe_to_retry": True, "error_code": "rejected", "error": "Item is no longer available"},
                        {**self.check, "offer_price_cents": 4400, "minimum_discount_percent": 1}])

        async def answer(_job, message_type, payload, timeout=0):
            return next(answers) if message_type == "marketplace.promotion_check" else self.sent

        self.bridge.side_effect = answer
        rows = self.client.post("/api/promotions/preview", json={
            "conversation_ids": ids, "marketplace": "mercari", "action": "offer", "discount_percent": 12,
            "floors": {conv_id: "1" for conv_id in ids}}).json()["rows"]
        self.assertEqual([row["state"] for row in rows], ["skipped", "preview"])
        self.assertEqual(len(self.calls("marketplace.promotion_check")), 2)

    def test_sold_items_and_busy_slot_send_nothing(self):
        row = self.preview(action="share")
        JobRepo(self.db).create(self.conv.id, "", {}, status="dispatched", current_step="native_delist")
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).status_code, 409)
        self.conv.status = "sold"
        self.db.commit()
        self.assertIn("sold", self.preview(action="share")["note"])
        self.assertEqual(self.calls("marketplace.promote"), [])

    def test_expired_review_is_refused(self):
        row = self.preview(action="share")
        self.db.get(ListingPromotion, row["id"]).expires_at -= timedelta(hours=1)
        self.db.commit()
        self.assertEqual(self.client.post(f"/api/promotions/{row['id']}/perform", json={"confirm": True}).status_code, 409)
        self.assertEqual(self.calls("marketplace.promote"), [])

    def test_interrupted_sends_become_unknown(self):
        row = self.preview(action="share")
        stored = self.db.get(ListingPromotion, row["id"])
        stored.state, stored.job_id = "sending", "job-1"
        self.db.commit()
        self.assertEqual(listing_promotions.mark_interrupted(self.db, "other-job"), 0)
        self.assertEqual(listing_promotions.mark_interrupted(self.db, "job-1"), 1)
        self.assertEqual(self.db.get(ListingPromotion, row["id"]).state, "unknown")
        history = self.client.get("/api/promotions").json()
        self.assertEqual(
            history["supported"],
            {"poshmark": ["bundle_offer", "follow", "offer", "share"], "mercari": ["offer"], "depop": ["offer"]},
        )
        self.assertEqual(history["awaiting_capture"], {})
        self.assertEqual(history["history"][0]["state"], "unknown")
