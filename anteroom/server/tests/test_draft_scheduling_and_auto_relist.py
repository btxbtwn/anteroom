from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.models.automation import MarketplaceRelistLog, MarketplaceRelistRule, ScheduledPublishLog
from anteroom.models.conversation import Conversation, Photo, new_id
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_promotion import MarketplaceOfferFloor
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.services import auto_relist, draft_scheduling, listing_persistence
from anteroom.services.vendoo_import import merge_notes


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _valid_listing_json(**overrides) -> dict:
    base = {
        "title": "Patagonia L Vintage Fleece Jacket Gray",
        "description": "Warm heather gray quarter-zip fleece jacket in great pre-owned condition.\n\nFlaws: None\nMeasurements: Chest 22 in, Length 28 in",
        "brand": "Patagonia",
        "category": "Men > Coats & Jackets",
        "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Jeans",
        "condition": "Good",
        "price": 58.0,
        "quantity": 1,
        "sku": "PAT-001",
        "size": "L",
        "color": "Gray",
        "weight_lb": 1,
        "weight_oz": 4,
        "package_dimensions_in": "13x10x3",
    }
    base.update(overrides)
    return base


class DraftSchedulingAndAutoRelistTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        app.dependency_overrides[get_db] = lambda: self.db
        self.enterContext(patch("anteroom.database.SessionLocal", self.Session))
        self.enterContext(patch("anteroom.services.validation.get_selected_marketplaces", return_value=[]))
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _create_draft(self, title: str = "Vintage Jacket", *, with_photo: bool = True, **listing_overrides) -> tuple[Conversation, Listing, ListingRevision]:
        full_title = title if title.startswith("Patagonia L ") else f"Patagonia L Vintage {title} Gray"
        conv = Conversation(id=new_id(), title=full_title, status="draft")
        self.db.add(conv)
        self.db.flush()
        if with_photo:
            self.db.add(Photo(
                id=new_id(),
                conversation_id=conv.id,
                original_filename="photo1.jpg",
                stored_filename="photo1.jpg",
                mime_type="image/jpeg",
                size_bytes=1024,
                display_order=0,
            ))
        rev = listing_persistence.save_revision(
            self.db,
            conv.id,
            _valid_listing_json(title=full_title, **listing_overrides),
            source="manual",
            commit=True,
        )
        listing = self.db.query(Listing).filter_by(conversation_id=conv.id).one()
        return conv, listing, rev

    def test_defaults_are_off_and_confirm_required(self):
        conv, _listing, _rev = self._create_draft()
        res = self.client.get(f"/api/conversations/{conv.id}/schedule")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["ready_to_list"])
        self.assertIsNone(body["scheduled_publish_at"])

        # Scheduling without confirm=True is refused.
        future = (_now() + timedelta(hours=2)).isoformat() + "Z"
        denied = self.client.post(
            f"/api/conversations/{conv.id}/schedule",
            json={"publish_at": future, "marketplaces": ["poshmark"], "confirm": False},
        )
        self.assertEqual(denied.status_code, 400)

        # Auto-relist rules default OFF across all 4 marketplaces.
        rules_res = self.client.get("/api/automations/auto-relist")
        self.assertEqual(rules_res.status_code, 200)
        rules = rules_res.json()["rules"]
        self.assertEqual([r["marketplace"] for r in rules], ["poshmark", "ebay", "mercari", "depop"])
        self.assertTrue(all(r["enabled"] is False for r in rules))

        # Enabling auto-relist without confirm=True is refused.
        refused = self.client.put(
            "/api/automations/auto-relist/poshmark",
            json={"enabled": True, "confirm": False, "min_age_days": 30, "daily_cap": 20},
        )
        self.assertEqual(refused.status_code, 400)

    def test_schedule_unschedule_on_edit_and_bulk_operations(self):
        c1, _l1, rev1 = self._create_draft("Draft One")
        c2, _l2, _rev2 = self._create_draft("Draft Two")
        c_bad, _lb, _rb = self._create_draft("No Photos", with_photo=False)

        # Toggle Ready to list on valid draft succeeds; invalid (no photos) fails.
        ok_ready = self.client.put(f"/api/conversations/{c1.id}/schedule/ready", json={"ready_to_list": True})
        self.assertEqual(ok_ready.status_code, 200)
        self.assertTrue(ok_ready.json()["ready_to_list"])

        bad_ready = self.client.put(f"/api/conversations/{c_bad.id}/schedule/ready", json={"ready_to_list": True})
        self.assertEqual(bad_ready.status_code, 400)

        # Bulk schedule valid + invalid drafts reports partial failure cleanly.
        future = (_now() + timedelta(hours=3)).isoformat() + "Z"
        bulk_res = self.client.post(
            "/api/automations/scheduled-drafts/bulk-schedule",
            json={
                "conversation_ids": [c1.id, c2.id, c_bad.id],
                "publish_at": future,
                "marketplaces": ["poshmark", "ebay"],
                "confirm": True,
            },
        )
        self.assertEqual(bulk_res.status_code, 200)
        bulk_data = bulk_res.json()
        self.assertEqual(len(bulk_data["scheduled"]), 2)
        self.assertEqual(len(bulk_data["failed"]), 1)
        self.assertEqual(bulk_data["failed"][0]["conversation_id"], c_bad.id)

        # Editing a scheduled draft automatically unschedules it so stale changes never publish.
        listing_persistence.save_revision(
            self.db,
            c1.id,
            _valid_listing_json(title="Draft One Edited", price=62.0),
            source="manual",
            parent_revision_id=rev1.id,
            expected_revision_id=rev1.id,
            commit=True,
        )
        after_edit = self.client.get(f"/api/conversations/{c1.id}/schedule").json()
        self.assertIsNone(after_edit["scheduled_publish_at"])
        self.assertIsNone(after_edit["scheduled_status"])

        # Bulk unschedule cancels remaining scheduled draft.
        unsched = self.client.post(
            "/api/automations/scheduled-drafts/bulk-unschedule",
            json={"conversation_ids": [c2.id], "reason": "Seller paused launch"},
        )
        self.assertEqual(unsched.status_code, 200)
        self.assertEqual(len(unsched.json()["unscheduled"]), 1)

        logs = self.client.get("/api/automations/scheduled-drafts/logs").json()["logs"]
        statuses = [entry["status"] for entry in logs]
        self.assertIn("scheduled", statuses)
        self.assertIn("unscheduled", statuses)

    def test_run_due_scheduled_publishes_named_markets_and_records_failures(self):
        c_ok, _l1, rev_ok = self._create_draft("Scheduled Success")
        c_fail, _l2, rev_fail = self._create_draft("Scheduled Failure")
        base_now = _now()
        due_time = base_now + timedelta(minutes=10)

        draft_scheduling.schedule_draft(
            self.db, c_ok.id, publish_at=due_time, marketplaces=["poshmark"], confirm=True, now=base_now
        )
        draft_scheduling.schedule_draft(
            self.db, c_fail.id, publish_at=due_time, marketplaces=["mercari"], confirm=True, now=base_now
        )

        # Before due time: nothing publishes.
        early = asyncio.run(draft_scheduling.run_due_scheduled_publishes(now=base_now + timedelta(minutes=5)))
        self.assertEqual(early, [])

        calls: list[tuple[str, list[str], str]] = []

        async def fake_publisher(conv_id: str, markets: list[str], rev_id: str) -> dict:
            calls.append((conv_id, markets, rev_id))
            if conv_id == c_ok.id:
                return {"ok": True, "marketplaces": {m: {"state": "published"} for m in markets}}
            return {"ok": False, "error": "Mercari session expired."}

        outcomes = asyncio.run(
            draft_scheduling.run_due_scheduled_publishes(now=due_time + timedelta(seconds=5), publisher=fake_publisher)
        )
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(calls, [(c_ok.id, ["poshmark"], rev_ok.id), (c_fail.id, ["mercari"], rev_fail.id)])

        self.db.expire_all()
        l_ok = self.db.query(Listing).filter_by(conversation_id=c_ok.id).one()
        l_fail = self.db.query(Listing).filter_by(conversation_id=c_fail.id).one()
        self.assertEqual(l_ok.scheduled_status, "published")
        self.assertEqual(l_fail.scheduled_status, "failed")
        self.assertEqual(l_fail.scheduled_error, "Mercari session expired.")
        audit_rows = self.db.query(ScheduledPublishLog).filter(ScheduledPublishLog.status.in_(("published", "failed"))).all()
        self.assertEqual({row.status for row in audit_rows}, {"published", "failed"})

    def test_auto_relist_validation_pricing_filters_and_ebay_gtc_window(self):
        # < 30 days refused on Poshmark/Mercari/Depop
        bad_age = self.client.put(
            "/api/automations/auto-relist/poshmark",
            json={"enabled": True, "confirm": True, "min_age_days": 14, "daily_cap": 20},
        )
        self.assertEqual(bad_age.status_code, 400)

        # > 100 daily cap refused by pydantic/service
        bad_cap = self.client.put(
            "/api/automations/auto-relist/poshmark",
            json={"enabled": True, "confirm": True, "min_age_days": 30, "daily_cap": 150},
        )
        self.assertIn(bad_cap.status_code, (400, 422))

        # eBay GTC recreate rule (~30 min before 30-day renewal)
        ebay_rule = auto_relist.save_rule(
            self.db,
            "ebay",
            {
                "enabled": True,
                "min_age_days": 30,
                "ebay_recreate_before_gtc_minutes": 30,
                "daily_cap": 50,
                "price_adjust_mode": "percent",
                "price_adjust_value": 10,
                "price_floor": 45.0,
                "filters": {"min_likes": 1, "max_likes": 10, "conditions": ["Good"], "keywords": ["fleece"], "labels": ["winter"]},
            },
            confirm=True,
        )
        self.assertTrue(ebay_rule["enabled"])
        row = self.db.query(MarketplaceRelistRule).filter_by(marketplace="ebay").one()
        now = _now()
        # 29d 23h 20m -> 40 minutes before GTC -> not due yet
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30) + timedelta(minutes=40), row, now))
        # 29d 23h 35m -> 25 minutes before GTC -> due!
        self.assertTrue(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30) + timedelta(minutes=25), row, now))

        # Price adjustment respects floor
        self.assertEqual(auto_relist.compute_relist_price(100.0, row), 90.0)
        self.assertEqual(auto_relist.compute_relist_price(48.0, row), 45.0)

        # Filter matching
        base_item = {
            "likes": 4,
            "price": 60.0,
            "condition": "Good",
            "title": "Patagonia Fleece",
            "description": "Warm",
            "labels": ["Winter", "Outerwear"],
        }
        self.assertTrue(auto_relist.matches_filters(base_item, row.filters))
        self.assertFalse(auto_relist.matches_filters({**base_item, "likes": 0}, row.filters))
        self.assertFalse(auto_relist.matches_filters({**base_item, "condition": "Poor"}, row.filters))
        self.assertFalse(auto_relist.matches_filters({**base_item, "labels": ["Summer"]}, row.filters))

    def test_run_auto_relist_skips_active_offers_enforces_cap_and_swaps_id_atomically(self):
        now = _now()
        old_time = now - timedelta(days=45)

        old_posh_1 = "5f0000000000000000000001"
        old_posh_2 = "5f0000000000000000000002"
        old_posh_3 = "5f0000000000000000000003"
        new_posh_1 = "5f0000000000000000000099"

        # Item 1: eligible stale Poshmark listing
        c1, _l1, _r1 = self._create_draft("Eligible Coat", price=100.0)
        c1.notes = merge_notes(c1.notes, {
            "marketplaceMarks": {"poshmark": {"state": "listed", "listing_id": old_posh_1, "url": f"https://poshmark.com/listing/{old_posh_1}"}},
        })
        ml1 = MarketplaceListing(
            id=new_id(),
            conversation_id=c1.id,
            marketplace="poshmark",
            state="published",
            listing_id=old_posh_1,
            listing_url=f"https://poshmark.com/listing/{old_posh_1}",
            listed_at=old_time,
            listed_price=100.0,
        )
        floor1 = MarketplaceOfferFloor(marketplace="poshmark", listing_id=old_posh_1, floor_cents=7500)
        self.db.add_all([ml1, floor1])

        # Item 2: has active incoming buyer offer -> MUST be skipped
        c2, _l2, _r2 = self._create_draft("Active Offer Coat", price=80.0)
        ml2 = MarketplaceListing(
            id=new_id(),
            conversation_id=c2.id,
            marketplace="poshmark",
            state="published",
            listing_id=old_posh_2,
            listing_url=f"https://poshmark.com/listing/{old_posh_2}",
            listed_at=old_time - timedelta(days=2),
            listed_price=80.0,
            inventory_observation={"has_active_offer": True, "active_offers_count": 1},
        )
        self.db.add(ml2)

        # Item 3: eligible but daily_cap=2 (item 2 skipped + item 1 eligible; set daily_cap=2 and check item 3 excluded by cap)
        c3, _l3, _r3 = self._create_draft("Younger Stale Coat", price=90.0)
        ml3 = MarketplaceListing(
            id=new_id(),
            conversation_id=c3.id,
            marketplace="poshmark",
            state="published",
            listing_id=old_posh_3,
            listing_url=f"https://poshmark.com/listing/{old_posh_3}",
            listed_at=old_time + timedelta(days=5),
            listed_price=90.0,
        )
        self.db.add(ml3)
        self.db.commit()

        # Enable Poshmark rule with daily_cap=2 and $5 price drop with $70 floor
        auto_relist.save_rule(
            self.db,
            "poshmark",
            {
                "enabled": True,
                "min_age_days": 30,
                "daily_cap": 2,
                "price_adjust_mode": "amount",
                "price_adjust_value": 5,
                "price_floor": 70.0,
                "skip_active_offers": True,
            },
            confirm=True,
        )

        delist_calls: list[tuple[str, str]] = []
        list_calls: list[tuple[str, str, str]] = []

        async def fake_delister(conv_id: str, market: str) -> dict:
            delist_calls.append((conv_id, market))
            return {"ok": True}

        async def fake_lister(conv_id: str, market: str, rev_id: str) -> dict:
            list_calls.append((conv_id, market, rev_id))
            return {
                "ok": True,
                "listing_id": new_posh_1,
                "listing_url": f"https://poshmark.com/listing/{new_posh_1}",
            }

        outcomes = asyncio.run(
            auto_relist.run_auto_relist(now=now, delister=fake_delister, lister=fake_lister)
        )
        self.assertEqual(len(outcomes), 2)
        statuses = {o["conversation_id"]: o["status"] for o in outcomes}
        self.assertEqual(statuses[c2.id], "skipped_active_offer")
        self.assertEqual(statuses[c1.id], "completed")
        self.assertEqual(delist_calls, [(c1.id, "poshmark")])
        self.assertEqual(len(list_calls), 1)

        # Verify atomic listing ID swap across MarketplaceListing, offer floor, and notes!
        self.db.expire_all()
        updated_ml = self.db.query(MarketplaceListing).filter_by(conversation_id=c1.id, marketplace="poshmark").one()
        self.assertEqual(updated_ml.listing_id, new_posh_1)
        self.assertEqual(updated_ml.listed_price, 95.0)
        swapped_floor = self.db.query(MarketplaceOfferFloor).filter_by(marketplace="poshmark", listing_id=new_posh_1).first()
        self.assertIsNotNone(swapped_floor)
        self.assertEqual(swapped_floor.floor_cents, 7500)

        # Audit logs recorded
        logs = self.db.query(MarketplaceRelistLog).all()
        self.assertEqual(len(logs), 2)


if __name__ == "__main__":
    unittest.main()
