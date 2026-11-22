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


if __name__ == "__main__":
    unittest.main()
