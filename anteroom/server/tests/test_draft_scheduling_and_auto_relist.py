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

        blocked = asyncio.run(auto_relist.run_auto_relist(now=now, delister=fake_delister, lister=fake_lister))
        self.assertIn("skipped_offer_unverified", {item["status"] for item in blocked})
        self.assertEqual(delist_calls, [])

        async def confirmed_no_offer(_market: str, _listing_id: str) -> bool:
            return False

        outcomes = asyncio.run(
            auto_relist.run_auto_relist(now=now, delister=fake_delister, lister=fake_lister,
                                       offer_checker=confirmed_no_offer)
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
        self.assertEqual(len(logs), 4)

    def test_check_active_buyer_offer_ebay_poshmark_and_mercari(self):
        import xml.etree.ElementTree as ET
        from unittest.mock import AsyncMock, patch
        from anteroom.services.ebay_client import EbayFailure

        ns = "urn:ebay:apis:eBLBaseComponents"

        class FakeEbayClient:
            def __init__(self, xml_text=None, exc=None):
                self._xml_text = xml_text
                self._exc = exc

            async def trading(self, call, fields):
                assert call == "GetBestOffers"
                assert fields["BestOfferStatus"] == "Active"
                if self._exc is not None:
                    raise self._exc
                return {"xml": ET.fromstring(self._xml_text)}

        # 1. eBay active buyer offer -> True
        active_xml = f'<GetBestOffersResponse xmlns="{ns}"><BestOfferArray><BestOffer><Status>Pending</Status></BestOffer></BestOfferArray></GetBestOffersResponse>'
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock(return_value=FakeEbayClient(active_xml))):
            self.assertIs(asyncio.run(auto_relist.check_active_buyer_offer("ebay", "123456789012")), True)

        # 2. eBay no active offers -> False
        empty_xml = f'<GetBestOffersResponse xmlns="{ns}"><BestOfferArray/></GetBestOffersResponse>'
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock(return_value=FakeEbayClient(empty_xml))):
            self.assertIs(asyncio.run(auto_relist.check_active_buyer_offer("ebay", "123456789012")), False)

        # 3. eBay Best Offer not enabled (21916260) -> False
        not_enabled = EbayFailure("Best Offer is not enabled.", code="rejected", marketplace_message="21916260: Best Offer is not enabled")
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock(return_value=FakeEbayClient(exc=not_enabled))):
            self.assertIs(asyncio.run(auto_relist.check_active_buyer_offer("ebay", "123456789012")), False)

        # 4. eBay transient error -> None (fail closed)
        transient = EbayFailure("Timeout", code="network")
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock(return_value=FakeEbayClient(exc=transient))):
            self.assertIsNone(asyncio.run(auto_relist.check_active_buyer_offer("ebay", "123456789012")))

        # 5. Poshmark Cloud Runner verified active offer vs verified clean
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch(
                "anteroom.services.cloud_marketplace.execute",
                AsyncMock(return_value={"ok": True, "verified": True, "has_active_offer": True}),
            ),
        ):
            self.assertIs(asyncio.run(auto_relist.check_active_buyer_offer("poshmark", "5f0000000000000000000001")), True)

        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch(
                "anteroom.services.cloud_marketplace.execute",
                AsyncMock(return_value={"ok": True, "verified": True, "has_active_offer": False}),
            ),
        ):
            self.assertIs(asyncio.run(auto_relist.check_active_buyer_offer("poshmark", "5f0000000000000000000001")), False)

        # 6. Mercari unverified -> None (fail closed)
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch(
                "anteroom.services.cloud_marketplace.execute",
                AsyncMock(return_value={"ok": True, "verified": False, "has_active_offer": False}),
            ),
        ):
            self.assertIsNone(asyncio.run(auto_relist.check_active_buyer_offer("mercari", "m123456789")))

    def test_settle_publish_does_not_overwrite_replacement_schedule(self):
        c1, l1, r1 = self._create_draft("Rescheduled Draft")
        l1.ready_to_list = True
        due_time = _now() - timedelta(minutes=1)
        l1.scheduled_publish_at = due_time
        l1.scheduled_marketplaces = ["poshmark"]
        l1.scheduled_revision_id = r1.id
        l1.scheduled_confirmed_at = due_time - timedelta(minutes=5)
        l1.scheduled_status = "scheduled"
        self.db.commit()

        claimed = draft_scheduling._claim_due_drafts(self.db, _now())
        self.assertEqual(len(claimed), 1)

        # Simulate seller replacing the schedule while the earlier publish is still in flight
        new_time = _now() + timedelta(hours=3)
        l1.scheduled_publish_at = new_time
        l1.scheduled_confirmed_at = _now()
        l1.scheduled_status = "scheduled"
        self.db.commit()

        draft_scheduling._settle_publish(self.db, claimed[0], {"ok": False, "error": "stale failure"}, _now())
        self.db.expire_all()
        fresh = self.db.query(Listing).filter_by(conversation_id=c1.id).one()
        self.assertEqual(fresh.scheduled_status, "scheduled")
        self.assertEqual(fresh.scheduled_publish_at, new_time)

    def test_wait_timeout_cancels_and_reconciles_pending_relist(self):
        from unittest.mock import AsyncMock

        from anteroom.models.job import Job
        from anteroom.services import automation_actions

        conv, _l, rev = self._create_draft("Pending Relist Jacket")
        conv.status = "listed"
        old_posh = "5f0000000000000000000081"
        new_posh = "5f0000000000000000000089"
        self.db.add(MarketplaceListing(
            id=new_id(),
            conversation_id=conv.id,
            marketplace="poshmark",
            state="published",
            listing_id=old_posh,
            listing_url=f"https://poshmark.com/listing/{old_posh}",
            listed_at=_now() - timedelta(days=45),
            listed_price=58.0,
        ))
        self.db.add(MarketplaceRelistRule(
            id=new_id(),
            marketplace="poshmark",
            enabled=True,
            min_age_days=30,
            daily_cap=5,
            skip_active_offers=False,
        ))
        job = Job(
            id="job-timeout-1",
            conversation_id=conv.id,
            approved_revision_id=rev.id,
            status="dispatched",
            listing_snapshot={},
        )
        self.db.add(job)
        self.db.commit()

        # 1. wait_for_native_result requests cancellation on timeout and returns timed_out=True
        ml_row = self.db.query(MarketplaceListing).filter_by(conversation_id=conv.id, marketplace="poshmark").one()
        ml_row.state = "publishing"
        ml_row.job_id = job.id
        ml_row.operation_id = "op-timeout-1"
        ml_row.executor = "cloud"
        self.db.commit()

        with (
            patch.object(automation_actions, "IDLE_TIMEOUT_S", 0.0),
            patch("anteroom.services.native_marketplaces._record_cancel", return_value=True) as mock_rec_cancel,
            patch("anteroom.services.cloud_marketplace.cancel", AsyncMock(return_value=True)) as mock_cloud_cancel,
        ):
            timed_res = asyncio.run(automation_actions.wait_for_native_result(
                conv.id,
                "poshmark",
                {"job_id": job.id, "state": "publishing"},
                session_factory=self.Session,
            ))
        self.assertTrue(timed_res.get("timed_out"))
        mock_rec_cancel.assert_called_once()
        mock_cloud_cancel.assert_awaited_once_with("op-timeout-1")

        # 2. run_auto_relist records relist_pending (not down_needs_relist) on timeout
        ml_row.state = "published"
        ml_row.listing_id = old_posh
        ml_row.listed_at = _now() - timedelta(days=45)
        self.db.commit()

        async def fake_delister(_conv_id, _market):
            return {"ok": True}

        async def fake_timeout_lister(_conv_id, _market, _rev_id):
            return {"ok": False, "timed_out": True, "error": "Timed out waiting for poshmark."}

        res = asyncio.run(auto_relist.run_auto_relist(
            marketplace="poshmark",
            delister=fake_delister,
            lister=fake_timeout_lister,
        ))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "relist_pending")
        pending_log = self.db.query(MarketplaceRelistLog).filter_by(conversation_id=conv.id).one()
        self.assertEqual(pending_log.status, "relist_pending")

        # 3. While job is still running, next sweep does NOT trigger a duplicate relist
        plan_while_active = auto_relist._load_enabled_rules_and_candidates(self.db, _now(), "poshmark")
        self.assertEqual(plan_while_active, [])
        self.db.refresh(pending_log)
        self.assertEqual(pending_log.status, "relist_pending")

        # 4. Once the late-completing publish writes a new listing_id, reconcile promotes to completed & swaps ID
        ml_row = self.db.query(MarketplaceListing).filter_by(conversation_id=conv.id, marketplace="poshmark").one()
        ml_row.state = "published"
        ml_row.listing_id = new_posh
        ml_row.listing_url = f"https://poshmark.com/listing/{new_posh}"
        job.status = "completed"
        self.db.commit()

        auto_relist._reconcile_pending_relists(self.db, _now(), "poshmark")
        self.db.expire_all()
        reconciled = self.db.query(MarketplaceRelistLog).filter_by(conversation_id=conv.id).one()
        self.assertEqual(reconciled.status, "completed")
        self.assertEqual(reconciled.new_listing_id, new_posh)


if __name__ == "__main__":
    unittest.main()

