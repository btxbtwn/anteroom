from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing_promotion import ListingPromotion
from anteroom.models.promotion_automation import PromotionAutomation
from anteroom.services import ebay_offers, listing_promotions, promotion_automations


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WaterfallAndPoshmarkBotTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        app.dependency_overrides[get_db] = lambda: self.db
        self.enterContext(patch("anteroom.database.SessionLocal", self.Session))
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_waterfall_validation_and_progression_with_cooldown(self):
        steps = listing_promotions.normalize_waterfall_steps([
            {"discount_percent": 10, "delay_minutes": 15},
            {"discount_percent": 15, "delay_minutes": 1440},
            {"discount_percent": 20, "delay_minutes": 2880},
        ])
        assert steps is not None
        self.assertEqual([s["step"] for s in steps], [1, 2, 3])

        # Non-increasing discount is rejected
        with self.assertRaises(listing_promotions.PromotionError):
            listing_promotions.normalize_waterfall_steps([
                {"discount_percent": 15, "delay_minutes": 15},
                {"discount_percent": 15, "delay_minutes": 1440},
            ])

        # Step 1 respects initial delay after like
        like_at = _now()
        step1, wait_reason = listing_promotions.next_waterfall_step(
            self.db, "poshmark", "posh-1", "offer", steps, now=like_at + timedelta(minutes=5), new_like_at=like_at
        )
        self.assertIsNone(step1)
        self.assertIn("waiting", wait_reason or "")

        step1_due, reason = listing_promotions.next_waterfall_step(
            self.db, "poshmark", "posh-1", "offer", steps, now=like_at + timedelta(minutes=20), new_like_at=like_at
        )
        self.assertEqual(step1_due["step"], 1)
        self.assertEqual(step1_due["discount_percent"], 10)
        self.assertIsNone(reason)

        # Record step 1 sent; step 2 must wait 24h (1440m)
        sent1 = like_at + timedelta(minutes=20)
        self.db.add(ListingPromotion(
            id=new_id(),
            batch_id="batch-1",
            marketplace="poshmark",
            listing_id="posh-1",
            action="offer",
            state="done",
            discount_percent=10,
            waterfall_step=1,
            sent_at=sent1,
            created_at=sent1,
            expires_at=sent1 + timedelta(hours=24),
        ))
        self.db.commit()

        step2_early, reason2 = listing_promotions.next_waterfall_step(
            self.db, "poshmark", "posh-1", "offer", steps, now=sent1 + timedelta(hours=12)
        )
        self.assertIsNone(step2_early)
        self.assertIsNotNone(reason2)

        step2_due, _ = listing_promotions.next_waterfall_step(
            self.db, "poshmark", "posh-1", "offer", steps, now=sent1 + timedelta(hours=25)
        )
        self.assertEqual(step2_due["step"], 2)
        self.assertEqual(step2_due["discount_percent"], 15)

        # Create promotion automation via API with waterfall steps
        res = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "offer",
                "discount_percent": 10,
                "min_likes": 1,
                "minimum_price": "25.00",
                "waterfall_steps": [
                    {"discount_percent": 10, "delay_minutes": 15},
                    {"discount_percent": 15, "delay_minutes": 1440},
                    {"discount_percent": 20, "delay_minutes": 2880},
                ],
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["enabled"])
        self.assertEqual(len(data["waterfall_steps"]), 3)
        self.assertEqual(data["waterfall_steps"][2]["discount_percent"], 20)

    def test_ebay_waterfall_stepping_and_policy_persistence(self):
        conv = Conversation(id=new_id(), title="eBay Watch", status="listed")
        self.db.add(conv)
        rule = EbayOfferRule(
            id=new_id(),
            account_id="ebay-acct-1",
            conversation_id=conv.id,
            listing_id="1234567890",
            enabled=True,
            discount_percent=10,
            minimum_price_cents=4000,
            waterfall_steps=[
                {"step": 1, "discount_percent": 10, "delay_minutes": 15},
                {"step": 2, "discount_percent": 15, "delay_minutes": 1440},
            ],
        )
        self.db.add(rule)
        self.db.commit()

        # First step due immediately when no attempt sent yet
        first = ebay_offers.next_waterfall_step(self.db, rule, rule.waterfall_steps)
        self.assertEqual(first["step"], 1)

        sent_at = _now()
        self.db.add(EbayOfferAttempt(
            id=new_id(),
            rule_id=rule.id,
            current_price_cents=8000,
            offer_price_cents=7200,
            minimum_price_cents=4000,
            discount_percent=10,
            waterfall_step=1,
            state="sent",
            sent_at=sent_at,
            created_at=sent_at,
            expires_at=sent_at + timedelta(minutes=15),
        ))
        self.db.commit()

        # Within 24h cooldown -> None
        self.assertIsNone(ebay_offers.next_waterfall_step(self.db, rule, rule.waterfall_steps, moment=sent_at + timedelta(hours=10)))
        # After 24h -> step 2 (15%)
        second = ebay_offers.next_waterfall_step(self.db, rule, rule.waterfall_steps, moment=sent_at + timedelta(hours=25))
        self.assertEqual(second["step"], 2)
        self.assertEqual(second["discount_percent"], 15)

        # Save all-listings eBay policy with waterfall steps via API
        with patch("anteroom.services.ebay_offers.get_marketplace_accounts", return_value={"ebay": {"accountId": "ebay-acct-1", "accessToken": "tok"}}):
            res = self.client.post(
                "/api/ebay-offers/enable-all",
                json={
                    "confirm": True,
                    "discount_percent": 10,
                    "minimum_price": "30.00",
                    "waterfall_steps": [
                        {"discount_percent": 10, "delay_minutes": 15},
                        {"discount_percent": 18, "delay_minutes": 1440},
                    ],
                },
            )
            self.assertEqual(res.status_code, 200)
            status_res = self.client.get("/api/ebay-offers")
            self.assertEqual(status_res.status_code, 200)
            self.assertEqual(len(status_res.json()["allListings"]["waterfallSteps"]), 2)

    def test_poshmark_private_bundle_offers_config_and_validation(self):
        res = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "bundle_offer",
                "discount_percent": 15,
                "shipping_label": "-$1.50",
                "minimum_price": "20.00",
                "bundle_pause_minutes": 10,
                "bundle_min_items": 2,
                "bundle_comment": "Thanks for liking! Here is a private bundle offer.",
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["action"], "bundle_offer")
        self.assertEqual(body["bundle_pause_minutes"], 10)
        self.assertEqual(body["bundle_min_items"], 2)
        self.assertEqual(body["bundle_comment"], "Thanks for liking! Here is a private bundle offer.")

        # Bundle offers are Poshmark-only
        bad = self.client.post(
            "/api/promotion-automations",
            json={"marketplace": "mercari", "action": "bundle_offer", "discount_percent": 15},
        )
        self.assertEqual(bad.status_code, 400)

    def test_poshmark_sharing_upgrade_8000_cap_party_windows_and_24h_schedule(self):
        self.assertEqual(promotion_automations.MAX_DAILY_SHARES, 8000)

        # Verify Posh Party PT slots: 9am, 12pm, 4pm, 7pm PT (PDT = UTC-7 in July)
        self.assertEqual(promotion_automations.active_posh_party_window(datetime(2026, 7, 15, 16, 30)), "09:00")
        self.assertEqual(promotion_automations.active_posh_party_window(datetime(2026, 7, 15, 19, 15)), "12:00")
        self.assertEqual(promotion_automations.active_posh_party_window(datetime(2026, 7, 15, 23, 30)), "16:00")
        self.assertEqual(promotion_automations.active_posh_party_window(datetime(2026, 7, 16, 2, 15)), "19:00")
        self.assertIsNone(promotion_automations.active_posh_party_window(datetime(2026, 7, 15, 14, 0)))

        # Verify 24h hourly intensity helper
        schedule = dict(promotion_automations.DEFAULT_HOURLY_SCHEDULE)
        self.assertEqual(promotion_automations.hourly_intensity_for(schedule, "UTC", datetime(2026, 7, 15, 3, 0)), "none")
        self.assertEqual(promotion_automations.hourly_intensity_for(schedule, "UTC", datetime(2026, 7, 15, 10, 0)), "high")

        # Save a Poshmark share rule with self + party + community modes, 24h schedule, and 8000 cap
        res = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "share",
                "share_order": "newest",
                "speed": "normal",
                "daily_cap": 8000,
                "timezone": "America/Los_Angeles",
                "share_modes": ["self", "party", "community"],
                "hourly_schedule": schedule,
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["daily_cap"], 8000)
        self.assertEqual(body["share_modes"], ["self", "party", "community"])
        self.assertEqual(body["hourly_schedule"]["10"], "high")

        # Exceeding 8000/day cap is rejected
        too_high = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "share",
                "share_order": "newest",
                "speed": "normal",
                "daily_cap": 8001,
                "times": ["09:00"],
                "timezone": "UTC",
            },
        )
        self.assertIn(too_high.status_code, (400, 422))

    def test_follow_budget_counts_other_rules_and_uncertain_sends_for_24_hours(self):
        now = _now()
        for name, age, budget, state in (
            ("other-rule", 1, {"active": 45, "followed": 970, "unfollowed": 30}, "done"),
            ("uncertain", 2, {"active": 15, "followed": 20, "unfollowed": 5}, "unknown"),
            ("expired", 25, {"active": 100, "followed": 1000, "unfollowed": 100}, "done"),
        ):
            self.db.add(ListingPromotion(
                id=name, batch_id=name, marketplace="poshmark", action="follow",
                listing_id="__account__", sent_at=now - timedelta(hours=age),
                expires_at=now, state=state, follow_budget=budget,
            ))
        self.db.commit()
        payload, reservation = listing_promotions._follow_allowance(self.db, "poshmark", {
            "reciprocal_24h": True, "new_active_sellers_cap": 100, "unfollow_cap": 40,
        })
        self.assertEqual(payload["remaining_follow_cap"], 10)
        self.assertEqual(payload["new_active_sellers_cap"], 10)
        self.assertEqual(payload["unfollow_cap"], 5)
        self.assertEqual(reservation, {"active": 10, "followed": 10, "unfollowed": 5})

    def test_poshmark_automatic_follows_caps_and_live_show_opt_in(self):
        # Valid follow rule with reciprocal 24h, active seller cap <= 1000, unfollow cap <= 1000, live show opt-in OFF
        res = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "follow",
                "timezone": "America/Chicago",
                "follow_config": {
                    "reciprocal_24h": True,
                    "new_active_sellers_cap": 1000,
                    "unfollow_cap": 500,
                    "unsubscribe_live_shows": False,
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["enabled"])
        self.assertTrue(body["follow_config"]["reciprocal_24h"])
        self.assertEqual(body["follow_config"]["new_active_sellers_cap"], 1000)
        self.assertEqual(body["follow_config"]["unfollow_cap"], 500)
        self.assertFalse(body["follow_config"]["unsubscribe_live_shows"])

        # > 1000 follow cap rejected
        over_follow = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "follow",
                "timezone": "UTC",
                "follow_config": {"new_active_sellers_cap": 1001, "unfollow_cap": 0},
            },
        )
        self.assertIn(over_follow.status_code, (400, 422))

        # > 1000 unfollow cap rejected
        over_unfollow = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "follow",
                "timezone": "UTC",
                "follow_config": {"new_active_sellers_cap": 0, "unfollow_cap": 1001},
            },
        )
        self.assertIn(over_unfollow.status_code, (400, 422))

        # Empty follow config (nothing enabled) rejected
        empty_cfg = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "follow",
                "timezone": "UTC",
                "follow_config": {
                    "reciprocal_24h": False,
                    "new_active_sellers_cap": 0,
                    "unfollow_cap": 0,
                    "unsubscribe_live_shows": False,
                },
            },
        )
        self.assertEqual(empty_cfg.status_code, 400)

    def test_follow_rule_preserves_times_and_schedules_by_times(self):
        res = self.client.post(
            "/api/promotion-automations",
            json={
                "marketplace": "poshmark",
                "action": "follow",
                "times": ["09:00", "17:00"],
                "timezone": "UTC",
                "follow_config": {"reciprocal_24h": True, "new_active_sellers_cap": 10, "unfollow_cap": 0},
            },
        )
        self.assertEqual(res.status_code, 200)
        rule_id = res.json()["id"]
        self.assertEqual(res.json()["times"], ["09:00", "17:00"])

        enabled = promotion_automations.enable(self.db, rule_id)
        self.assertTrue(enabled["enabled"])
        row = self.db.get(PromotionAutomation, rule_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.times, ["09:00", "17:00"])
        self.assertIsNotNone(row.next_run_at)
        assert row.next_run_at is not None
        self.assertIn(row.next_run_at.hour, (9, 17))
        self.assertEqual(row.next_run_at.minute, 0)

    def test_shares_today_counts_community_recipient_units_and_bundle_offer_rotates_targets(self):
        now = _now()
        self.db.add_all([
            ListingPromotion(
                id="self-share-1", batch_id="b1", marketplace="poshmark", action="share",
                listing_id="5f0000000000000000000001", sent_at=now - timedelta(minutes=10),
                expires_at=now, state="done", recipient_count=None,
            ),
            ListingPromotion(
                id="comm-share-1", batch_id="b2", marketplace="poshmark", action="share",
                listing_id="__account__", sent_at=now - timedelta(minutes=5),
                expires_at=now, state="done", recipient_count=15,
            ),
        ])
        self.db.commit()
        self.assertEqual(promotion_automations._shares_today(self.db, "poshmark", now), 16)

        rule = PromotionAutomation(
            id="bundle-rot", marketplace="poshmark", action="bundle_offer", name="Bundle",
            discount_percent=15, timezone="UTC",
        )
        self.db.add(rule)
        self.db.add(ListingPromotion(
            id="bundle-checked-a", batch_id="b3", automation_id="bundle-rot",
            marketplace="poshmark", action="bundle_offer", listing_id="aaa",
            created_at=now - timedelta(minutes=1), expires_at=now, state="skipped",
        ))
        self.db.commit()
        ordered = promotion_automations._order(self.db, rule, [("c1", "aaa", now), ("c2", "bbb", now)])
        self.assertEqual([t[1] for t in ordered], ["bbb", "aaa"])

    def test_perform_account_action_cancellation_settles_unknown_and_blocks_overlap(self):
        import asyncio
        from unittest.mock import patch

        rule = PromotionAutomation(
            id="follow-cancel", marketplace="poshmark", action="follow", name="Follow",
            enabled=True, timezone="UTC",
            follow_config={"reciprocal_24h": True, "new_active_sellers_cap": 10, "unfollow_cap": 0, "unsubscribe_live_shows": False},
        )
        self.db.add(rule)
        self.db.commit()

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_cloud(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"ok": True, "state": "done", "followed_count": 1, "active_followed_count": 0, "unfollowed_count": 0}

        async def run_scenario():
            with (
                patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
                patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
                patch("anteroom.services.cloud_marketplace.execute", side_effect=slow_cloud),
            ):
                task = asyncio.create_task(listing_promotions.perform_account_action(
                    "poshmark", "follow", automation_id="follow-cancel", batch_id="batch-c1", extra_payload={},
                ))
                await asyncio.wait_for(started.wait(), timeout=5)

                # Overlapping account action and native/delist guards must see this as busy
                from anteroom.repositories.queries import JobRepo
                from anteroom.services import native_marketplaces

                with self.Session() as probe_db:
                    self.assertIsNotNone(native_marketplaces.blocking_job(JobRepo(probe_db), "poshmark"))
                with self.assertRaises(listing_promotions.PromotionError) as ctx:
                    await listing_promotions.perform_account_action(
                        "poshmark", "follow", automation_id="follow-cancel", batch_id="batch-c2", extra_payload={},
                    )
                self.assertEqual(ctx.exception.code, "busy")

                # Cancelling the in-flight task must settle it to unknown (never stuck in sending)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(run_scenario())
        self.db.expire_all()
        rows = self.db.query(ListingPromotion).filter_by(automation_id="follow-cancel").all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, "unknown")
        self.assertTrue(str(rows[0].job_id or "").startswith("cloud-account:"))
        # Cloud journal recovery still finds Cloud executor even if active session later disconnects
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=False),
            patch("anteroom.services.cloud_sessions.load_journals", return_value={
                f"promotion_{rows[0].id}": {"finished": True, "result": {"ok": True, "state": "done", "followed_count": 2, "unfollowed_count": 0}},
            }),
        ):
            settled, _job_key, cloud_answer = listing_promotions._check_target(self.db, rows[0].id)
            self.assertIsNone(settled)
            self.assertIsNotNone(cloud_answer)
            assert cloud_answer is not None
            self.assertTrue(cloud_answer["finished"])

    def test_poshmark_bundle_floors_combines_saved_floors_and_shared_minimum(self):
        from anteroom.models.conversation import Conversation
        from anteroom.models.listing_promotion import MarketplaceOfferFloor
        from anteroom.models.marketplace_listing import MarketplaceListing

        c1 = Conversation(id="conv-b1", title="Item 1", status="listed")
        c2 = Conversation(id="conv-b2", title="Item 2", status="listed")
        self.db.add_all([c1, c2])
        self.db.flush()
        p1 = "5f0000000000000000000011"
        p2 = "5f0000000000000000000022"
        self.db.add_all([
            MarketplaceListing(
                conversation_id="conv-b1", marketplace="poshmark", state="published",
                listing_id=p1, listing_url=f"https://poshmark.com/listing/item-1-{p1}",
            ),
            MarketplaceListing(
                conversation_id="conv-b2", marketplace="poshmark", state="published",
                listing_id=p2, listing_url=f"https://poshmark.com/listing/item-2-{p2}",
            ),
            MarketplaceOfferFloor(marketplace="poshmark", listing_id=p1, conversation_id="conv-b1", floor_cents=3500),
            PromotionAutomation(
                id="bundle-rule-fl", marketplace="poshmark", action="bundle_offer", name="Bundle",
                discount_percent=20, minimum_price_cents=1500, timezone="UTC",
            ),
        ])
        self.db.commit()
        promo = ListingPromotion(
            id="promo-b1", batch_id="b", conversation_id="conv-b1", marketplace="poshmark",
            action="bundle_offer", listing_id=p1, automation_id="bundle-rule-fl",
            floor_cents=3500, expires_at=_now() + timedelta(minutes=10),
        )
        floors_map = listing_promotions._poshmark_bundle_floors(self.db, promo)
        self.assertEqual(floors_map[p1], 3500)
        self.assertEqual(floors_map[p2], 1500)

    def test_regular_scan_enforces_waterfall_step_1_delay_before_sending(self):
        # Finding #2: regular scan (like_events=None) persists first-seen eligible like timestamp and waits delay_minutes
        import asyncio
        from unittest.mock import AsyncMock

        from anteroom.models.marketplace_listing import MarketplaceListing

        conv = Conversation(id="conv-wf-1", title="Waterfall Coat", status="listed")
        posh_id = "5f0000000000000000000301"
        self.db.add(conv)
        self.db.add(MarketplaceListing(
            conversation_id=conv.id,
            marketplace="poshmark",
            state="published",
            listing_id=posh_id,
            listing_url=f"https://poshmark.com/listing/{posh_id}",
            listed_at=_now() - timedelta(days=5),
        ))
        rule = PromotionAutomation(
            id="wf-regular-rule",
            marketplace="poshmark",
            action="offer",
            name="Waterfall Offer",
            enabled=True,
            discount_percent=10,
            min_likes=1,
            minimum_price_cents=1000,
            timezone="UTC",
            next_run_at=_now() - timedelta(minutes=1),
            waterfall_steps=[
                {"step": 1, "discount_percent": 10, "delay_minutes": 30},
                {"step": 2, "discount_percent": 15, "delay_minutes": 1440},
            ],
        )
        self.db.add(rule)
        self.db.commit()

        async def fake_review(send, _conv_id, *, new_like_at=None):
            send.listing_id = posh_id
            send.like_count = 2
            send.price_cents = 5000
            send.offer_price_cents = 4500
            send.floor_cents = 1000
            send.shipping_options = []

        perform_mock = AsyncMock(return_value={"state": "done"})

        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.listing_promotions.review", side_effect=fake_review),
            patch("anteroom.services.listing_promotions.perform", perform_mock),
        ):
            # Scan 1 (regular scan): sees eligible likes, persists waiting marker, does NOT send yet
            run1 = asyncio.run(promotion_automations.run_round(rule.id))
            assert run1 is not None
            self.assertEqual(run1.sent, 0)
            self.assertEqual(run1.skipped, 1)
            perform_mock.assert_not_awaited()

            # Verify persisted first-seen timestamp
            self.db.expire_all()
            first_seen = listing_promotions.first_seen_waterfall_like_at(self.db, "poshmark", posh_id, "offer")
            self.assertIsNotNone(first_seen)

            # Scan 2 (still before 30m delay): still skipped, does NOT send
            run2 = asyncio.run(promotion_automations.run_round(rule.id))
            assert run2 is not None
            self.assertEqual(run2.sent, 0)
            self.assertEqual(run2.skipped, 1)
            perform_mock.assert_not_awaited()

            # Backdate the first-seen waiting row past the 30-minute delay -> Scan 3 sends step 1
            waiting_row = self.db.query(ListingPromotion).filter_by(
                marketplace="poshmark", listing_id=posh_id, state="skipped", waterfall_step=1,
            ).one()
            waiting_row.created_at = _now() - timedelta(minutes=35)
            self.db.commit()

            run3 = asyncio.run(promotion_automations.run_round(rule.id))
            assert run3 is not None
            self.assertEqual(run3.sent, 1)
            perform_mock.assert_awaited_once()

    def test_abandoned_account_send_releases_follow_budget_while_unknown_keeps_it(self):
        # Finding #23: _abandon_account_send clears follow_budget to zero (not None!) so subsequent runs aren't blocked
        import asyncio

        now = _now()
        row = ListingPromotion(
            id="follow-abandon-1",
            batch_id="b-ab",
            marketplace="poshmark",
            action="follow",
            listing_id="__account__",
            state="sending",
            follow_budget={"active": 100, "followed": 1000, "unfollowed": 500},
            sent_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        self.db.add(row)
        self.db.commit()

        asyncio.run(listing_promotions._abandon_account_send((row.id, {}, False)))
        self.db.expire_all()
        settled = self.db.get(ListingPromotion, row.id)
        assert settled is not None
        self.assertEqual(settled.state, "failed")
        self.assertEqual(settled.follow_budget, {"active": 0, "followed": 0, "unfollowed": 0})

        # Full follow allowance is restored after abandonment
        payload, reservation = listing_promotions._follow_allowance(
            self.db, "poshmark", {"reciprocal_24h": True, "new_active_sellers_cap": 100, "unfollow_cap": 40},
        )
        self.assertEqual(payload["remaining_follow_cap"], 1000)
        self.assertEqual(reservation, {"active": 100, "followed": 1000, "unfollowed": 40})

    def test_community_share_rechecks_approval_atomically_and_clamps_daily_cap_including_unknowns(self):
        # Findings #24 and #25
        import asyncio
        from unittest.mock import AsyncMock

        now = _now()
        rule = PromotionAutomation(
            id="comm-cap-rule",
            marketplace="poshmark",
            action="share",
            name="Community Share",
            enabled=True,
            daily_cap=20,
            share_modes=["community"],
            timezone="UTC",
        )
        self.db.add(rule)
        # Existing unknown community share with reserved budget 12 + failed abandoned share with released budget 0
        self.db.add_all([
            ListingPromotion(
                id="comm-unk-1", batch_id="b1", marketplace="poshmark", action="share",
                listing_id="__account__", state="unknown", recipient_count=None,
                follow_budget={"max_shares": 12}, sent_at=now - timedelta(minutes=5), expires_at=now,
            ),
            ListingPromotion(
                id="comm-abandoned-1", batch_id="b2", marketplace="poshmark", action="share",
                listing_id="__account__", state="failed", recipient_count=None,
                follow_budget={"max_shares": 0}, sent_at=now - timedelta(minutes=3), expires_at=now,
            ),
        ])
        self.db.commit()

        # _shares_today charges 12 for unknown and 0 for abandoned failed row
        self.assertEqual(promotion_automations._shares_today(self.db, "poshmark", now), 12)

        # Next community share request for 50 is clamped to remaining 8 (20 - 12) and reserves {"max_shares": 8}
        captured_payload: dict = {}

        async def fake_cloud(_market, msg, **_kwargs):
            captured_payload.update(msg["payload"])
            return {"ok": True, "state": "done", "share_mode": "community", "recipient_count": 8}

        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", side_effect=fake_cloud),
        ):
            res = asyncio.run(listing_promotions.perform_account_action(
                "poshmark",
                "share",
                automation_id=rule.id,
                batch_id="b3",
                extra_payload={"share_mode": "community", "max_shares": 50},
            ))
        self.assertEqual(captured_payload.get("max_shares"), 8)
        self.assertEqual(res["state"], "done")
        self.assertEqual(promotion_automations._shares_today(self.db, "poshmark", now), 20)

        # Now cap is exhausted -> next community share raises daily_cap without calling cloud
        cloud_mock = AsyncMock()
        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", cloud_mock),
            self.assertRaises(listing_promotions.PromotionError) as cap_ctx,
        ):
            asyncio.run(listing_promotions.perform_account_action(
                "poshmark",
                "share",
                automation_id=rule.id,
                batch_id="b4",
                extra_payload={"share_mode": "community", "max_shares": 10},
            ))
        self.assertEqual(cap_ctx.exception.code, "daily_cap")
        cloud_mock.assert_not_awaited()

        # Finding #24: removing "community" from share_modes or disabling rule aborts atomically with "paused"
        rule.daily_cap = 100
        rule.share_modes = ["self"]
        self.db.commit()
        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", cloud_mock),
            self.assertRaises(listing_promotions.PromotionError) as paused_ctx,
        ):
            asyncio.run(listing_promotions.perform_account_action(
                "poshmark",
                "share",
                automation_id=rule.id,
                batch_id="b5",
                extra_payload={"share_mode": "community", "max_shares": 10},
            ))
        self.assertEqual(paused_ctx.exception.code, "paused")
        cloud_mock.assert_not_awaited()

    def test_poshmark_gap_ms_clamped_and_cloud_signed_out_does_not_pause_rule(self):
        # M9: SHARE_SPEEDS * INTENSITY_GAP_MULTIPLIER is clamped to [4000, 30000].
        # M10: cloud executor signed_out stops the run without pausing the rule for Chrome.
        import asyncio
        from unittest.mock import AsyncMock
        from anteroom.models.marketplace_listing import MarketplaceListing

        # M9: fast (4000ms) * high (<1) clamps to 4000; slow (15000ms) * low (2.25) clamps to 30000.
        conv = Conversation(id="conv-gap-1", title="Gap Coat", status="listed")
        posh_id = "5f0000000000000000000401"
        self.db.add(conv)
        self.db.add(MarketplaceListing(
            conversation_id=conv.id, marketplace="poshmark", state="published",
            listing_id=posh_id, listing_url=f"https://poshmark.com/listing/{posh_id}",
            listed_at=_now() - timedelta(days=5),
        ))
        fast_rule = PromotionAutomation(
            id="posh-fast-agg", marketplace="poshmark", action="share", name="Fast Agg",
            enabled=True, daily_cap=50, speed="fast", share_modes=["self"],
            hourly_schedule={str(h): "high" for h in range(24)},
            timezone="UTC", next_run_at=_now() - timedelta(minutes=1),
        )
        slow_rule = PromotionAutomation(
            id="posh-slow-gentle", marketplace="poshmark", action="share", name="Slow Gentle",
            enabled=True, daily_cap=50, speed="slow", share_modes=["self"],
            hourly_schedule={str(h): "low" for h in range(24)},
            timezone="UTC", next_run_at=_now() - timedelta(minutes=1),
        )
        follow_rule = PromotionAutomation(
            id="posh-follow-signout", marketplace="poshmark", action="follow", name="Follow Signout",
            enabled=True, daily_cap=50, follow_config={"source": "followers"},
            timezone="UTC", next_run_at=_now() - timedelta(minutes=1),
        )
        self.db.add_all([fast_rule, slow_rule, follow_rule])
        self.db.commit()

        seen_gaps: list[int | None] = []

        async def fake_review(send, _conv_id, *, new_like_at=None):
            send.listing_id = posh_id
            send.price_cents = 5000

        async def fake_perform(_send_id, _shipping, *, new_like_at=None, gap_ms=None, extra_payload=None, include_executor=False):
            seen_gaps.append(gap_ms)
            return {"state": "done"}

        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.listing_promotions.review", side_effect=fake_review),
            patch("anteroom.services.listing_promotions.perform", side_effect=fake_perform),
            patch.dict(promotion_automations.INTENSITY_GAP_MULTIPLIER, {"high": 0.2, "low": 2.25}),
        ):
            asyncio.run(promotion_automations.run_round(fast_rule.id))
            asyncio.run(promotion_automations.run_round(slow_rule.id))
        self.assertEqual(seen_gaps, [4000, 30000])

        # M10: cloud signed_out does NOT pause the follow rule.
        cloud_signed_out = AsyncMock(return_value={
            "state": "failed", "stop": True, "error_code": "signed_out",
            "executor": "cloud", "note": "Sign in to Poshmark in cloud.",
        })
        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.listing_promotions.perform_account_action", cloud_signed_out),
        ):
            asyncio.run(promotion_automations.run_round(follow_rule.id))
        self.db.expire_all()
        self.assertTrue(self.db.get(PromotionAutomation, follow_rule.id).enabled)

        # Chrome signed_out DOES pause the follow rule.
        chrome_signed_out = AsyncMock(return_value={
            "state": "failed", "stop": True, "error_code": "signed_out",
            "executor": "chrome", "note": "Sign in to Poshmark in Chrome.",
        })
        with (
            patch("anteroom.services.listing_promotions.require_chrome", return_value=None),
            patch("anteroom.services.listing_promotions.perform_account_action", chrome_signed_out),
        ):
            asyncio.run(promotion_automations.run_round(follow_rule.id))
        self.db.expire_all()
        self.assertFalse(self.db.get(PromotionAutomation, follow_rule.id).enabled)


if __name__ == "__main__":
    unittest.main()

