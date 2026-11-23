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
        "ebay_specifics": {
            "brand": "Patagonia",
            "department": "Women",
            "size": "L",
            "sizeType": "Regular",
            "style": "Straight",
            "color": "Gray",
            "type": "Jeans",
        },
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
            with self.Session() as probe:
                self.assertEqual(probe.query(Listing).filter_by(scheduled_status="publishing").count(), 1)
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

        from unittest.mock import AsyncMock
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", AsyncMock(return_value={
                "ok": True, "verified": True, "has_active_offer": False,
            })),
        ):
            # Advance past M3 retry backoff so c1 and c2 are evaluated again under daily_cap=2.
            outcomes = asyncio.run(auto_relist.run_auto_relist(
                now=now + timedelta(hours=7), delister=fake_delister, lister=fake_lister,
            ))
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
        assert swapped_floor is not None
        self.assertEqual(swapped_floor.floor_cents, 7500)

        # Audit logs recorded
        logs = self.db.query(MarketplaceRelistLog).all()
        self.assertEqual(len(logs), 4)

    def test_scheduled_relist_uses_marketplace_verification_before_delist(self):
        from unittest.mock import AsyncMock
        from anteroom.services.automation_scheduler import _run_auto_relist

        conv, _, _ = self._create_draft("Scheduler relist")
        conv.status = "listed"
        old_id = "5f0000000000000000000081"
        t0 = _now()
        self.db.add(MarketplaceListing(id=new_id(), conversation_id=conv.id, marketplace="poshmark",
                                      state="published", listing_id=old_id, listed_at=t0 - timedelta(days=45),
                                      listing_url=f"https://poshmark.com/listing/{old_id}",
                                      listed_price=58.0))
        self.db.add(MarketplaceRelistRule(id=new_id(), marketplace="poshmark", enabled=True))
        self.db.commit()
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", AsyncMock(return_value={
                "ok": True, "verified": True,
            })) as check,
            patch("anteroom.services.marketplace_delist.delist", AsyncMock(return_value={
                "poshmark": {"state": "deleted"},
            })) as delete,
            patch("anteroom.services.native_marketplaces.list_now", AsyncMock(return_value={
                "state": "published", "listing_id": "5f0000000000000000000082",
            })),
        ):
            self.assertEqual(asyncio.run(_run_auto_relist(t0)), 0)
            delete.assert_not_awaited()
            # A seller turning the rule off during the external check revokes the planned deletion.
            def pause(*args, **kwargs):
                with self.Session() as db:
                    db.query(MarketplaceRelistRule).update({"enabled": False})
                    db.commit()
                return {"ok": True, "verified": True, "has_active_offer": False}

            check.side_effect = pause
            self.assertEqual(asyncio.run(_run_auto_relist(t0 + timedelta(hours=7))), 0)
            delete.assert_not_awaited()
            self.db.query(MarketplaceRelistRule).update({"enabled": True})
            self.db.commit()
            check.side_effect = None
            check.return_value = {"ok": True, "verified": True, "has_active_offer": False}
            self.assertEqual(asyncio.run(_run_auto_relist(t0 + timedelta(hours=7, minutes=10))), 1)
            delete.assert_awaited_once()
        self.assertEqual(self.db.query(MarketplaceRelistLog).filter_by(status="completed").count(), 1)

    def _relist_after_opt_out(self, *, disable_during_delete_start: bool):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock

        from anteroom.services import marketplace_delist

        conv, _, _ = self._create_draft("Opt out")
        conv.status = "listed"
        old_id = "5f0000000000000000000081"
        self.db.add(MarketplaceListing(id=new_id(), conversation_id=conv.id, marketplace="poshmark",
                                      state="published", listing_id=old_id, listed_at=_now() - timedelta(days=45),
                                      listing_url=f"https://poshmark.com/listing/{old_id}", listed_price=58.0))
        self.db.add(MarketplaceRelistRule(id=new_id(), marketplace="poshmark", enabled=True))
        self.db.commit()

        def disable():
            with self.Session() as db:
                auto_relist.save_rule(db, "poshmark", {"enabled": False})

        open_delist = marketplace_delist._open_delist

        def disable_after_open(*args):
            result = open_delist(*args)
            if disable_during_delete_start:
                disable()
            return result

        @asynccontextmanager
        async def disable_on_turn(_marketplace):
            disable()
            yield

        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", AsyncMock(return_value={
                "ok": True, "state": "deleted",
            })) as delete,
            patch.object(marketplace_delist, "_open_delist", side_effect=disable_after_open),
            patch("anteroom.services.automation_actions._turn", disable_on_turn),
            patch("anteroom.services.native_marketplaces.list_now", AsyncMock(return_value={
                "state": "published", "listing_id": "5f0000000000000000000082",
            })) as publish,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(offer_checker=AsyncMock(return_value=False)))
            self.assertEqual([entry["status"] for entry in outcomes], ["skipped_rule_disabled"])
            publish.assert_not_awaited()
            if disable_during_delete_start:
                delete.assert_not_awaited()
            else:
                delete.assert_awaited_once()

    def test_auto_relist_opt_out_before_delete_start(self):
        self._relist_after_opt_out(disable_during_delete_start=True)

    def test_auto_relist_opt_out_while_waiting_for_publish_turn(self):
        self._relist_after_opt_out(disable_during_delete_start=False)

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
                assert self._xml_text is not None
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
        c1, l1, _r1 = self._create_draft("Rescheduled Draft")
        due_time = _now() - timedelta(minutes=1)
        draft_scheduling.schedule_draft(
            self.db, c1.id, marketplaces=["poshmark"], publish_at=due_time, confirm=True,
            now=due_time - timedelta(minutes=5),
        )

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
            return {"ok": False, "timed_out": True, "native_job_id": "job-timeout-1", "error": "Timed out waiting for poshmark."}

        res = asyncio.run(auto_relist.run_auto_relist(
            marketplace="poshmark",
            delister=fake_delister,
            lister=fake_timeout_lister,
            offer_checker=AsyncMock(return_value=False),
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

        ml_row.job_id = "another-operation"
        self.db.commit()
        auto_relist._reconcile_pending_relists(self.db, _now(), "poshmark")
        self.db.refresh(pending_log)
        self.assertEqual(pending_log.status, "relist_pending")
        ml_row.job_id = job.id
        self.db.commit()

        auto_relist._reconcile_pending_relists(self.db, _now(), "poshmark")
        self.db.expire_all()
        reconciled = self.db.query(MarketplaceRelistLog).filter_by(conversation_id=conv.id).one()
        self.assertEqual(reconciled.status, "completed")
        self.assertEqual(reconciled.new_listing_id, new_posh)

    def test_interrupted_delete_reconciles_only_with_matching_delete_receipt(self):
        moment = _now().replace(microsecond=0)
        started = moment - timedelta(minutes=20)
        old_id = "5f0000000000000000000081"
        attempts = {}
        for case, deleted_id, deleted_at in (
            ("confirmed", old_id, started + timedelta(seconds=1)),
            ("another listing", "5f0000000000000000000082", started + timedelta(seconds=1)),
            ("older receipt", old_id, started - timedelta(seconds=1)),
            ("unknown", None, started),
        ):
            conv, _, _ = self._create_draft(f"Interrupted delete: {case}")
            if deleted_id is not None:
                conv.notes = merge_notes(conv.notes, {"marketplaceDelists": {"poshmark": {
                    "state": "deleted", "listing_id": deleted_id, "at": deleted_at.replace(tzinfo=UTC).isoformat(),
                }}})
            attempt = MarketplaceRelistLog(
                id=new_id(), conversation_id=conv.id, marketplace="poshmark", old_listing_id=old_id,
                status="relist_pending", reason="stage:deleting | Delisting old listing before relist.",
                created_at=started,
            )
            self.db.add(attempt)
            attempts[case] = attempt
        self.db.commit()

        pending = auto_relist._reconcile_pending_relists(self.db, moment, "poshmark")

        self.assertEqual(attempts["confirmed"].status, "down_needs_relist")
        self.assertEqual(pending, {(attempt.conversation_id, "poshmark")
                                   for case, attempt in attempts.items() if case != "confirmed"})

    def test_schedule_validates_target_marketplaces_and_blocks_mutations_while_publishing(self):
        # Finding #4: missing eBay specifics is rejected when scheduling for ["ebay"] even if selected marketplaces is []
        c_missing, _lm, _rm = self._create_draft("Missing Ebay Fields", ebay_specifics={})
        future = _now() + timedelta(hours=2)
        with self.assertRaises(draft_scheduling.SchedulePublishError) as ctx:
            draft_scheduling.schedule_draft(
                self.db, c_missing.id, publish_at=future, marketplaces=["ebay"], confirm=True,
            )
        self.assertEqual(ctx.exception.status_code, 400)

        # Finding #6: unschedule, ready=False, and reschedule all return 409 while publishing
        c_pub, l_pub, _rp = self._create_draft("Currently Publishing")
        l_pub.ready_to_list = True
        l_pub.scheduled_publish_at = _now() - timedelta(minutes=1)
        l_pub.scheduled_marketplaces = ["poshmark"]
        l_pub.scheduled_revision_id = l_pub.current_revision_id
        l_pub.scheduled_confirmed_at = _now() - timedelta(minutes=5)
        l_pub.scheduled_status = "publishing"
        self.db.commit()

        with self.assertRaises(draft_scheduling.SchedulePublishError) as u_ctx:
            draft_scheduling.unschedule_draft(self.db, c_pub.id)
        self.assertEqual(u_ctx.exception.status_code, 409)

        with self.assertRaises(draft_scheduling.SchedulePublishError) as r_ctx:
            draft_scheduling.set_ready_to_list(self.db, c_pub.id, False)
        self.assertEqual(r_ctx.exception.status_code, 409)

        with self.assertRaises(draft_scheduling.SchedulePublishError) as s_ctx:
            draft_scheduling.schedule_draft(
                self.db, c_pub.id, publish_at=future, marketplaces=["poshmark"], confirm=True,
            )
        self.assertEqual(s_ctx.exception.status_code, 409)

    def test_abandoned_publishing_recovery_and_cancellation_settlement(self):
        # Finding #5: stale publishing rows recover to published when live, or needs_review when unknown
        c_live, l_live, r_live = self._create_draft("Abandoned Live Draft")
        c_unk, l_unk, r_unk = self._create_draft("Abandoned Unknown Draft")
        stale_ts = _now() - draft_scheduling.ABANDONED_PUBLISH_GRACE - timedelta(minutes=5)

        for listing, rev in ((l_live, r_live), (l_unk, r_unk)):
            listing.ready_to_list = True
            listing.scheduled_publish_at = stale_ts - timedelta(minutes=1)
            listing.scheduled_marketplaces = ["poshmark"]
            listing.scheduled_revision_id = rev.id
            listing.scheduled_confirmed_at = stale_ts - timedelta(minutes=5)
            listing.scheduled_status = "publishing"
            listing.updated_at = stale_ts
        self.db.add(MarketplaceListing(
            id=new_id(),
            conversation_id=c_live.id,
            marketplace="poshmark",
            state="published",
            listing_id="5f0000000000000000000999",
            listing_url="https://poshmark.com/listing/5f0000000000000000000999",
            listed_at=stale_ts + timedelta(seconds=10),
        ))
        self.db.commit()

        asyncio.run(draft_scheduling.run_due_scheduled_publishes(now=_now()))
        self.db.expire_all()
        self.assertEqual(self.db.query(Listing).filter_by(conversation_id=c_live.id).one().scheduled_status, "published")
        self.assertEqual(self.db.query(Listing).filter_by(conversation_id=c_unk.id).one().scheduled_status, "needs_review")

    def test_auto_relist_enforces_offer_check_ebay_upper_bound_and_rechecks_disabled_rule(self):
        from unittest.mock import AsyncMock

        # Finding #7: save_rule forces skip_active_offers=True even if caller sends False
        saved = auto_relist.save_rule(
            self.db,
            "poshmark",
            {"enabled": True, "min_age_days": 30, "daily_cap": 5, "skip_active_offers": False},
            confirm=True,
        )
        self.assertTrue(saved["skip_active_offers"])

        # Finding #10: eBay GTC is NOT due once age >= 30 days
        ebay_rule = MarketplaceRelistRule(
            id=new_id(),
            marketplace="ebay",
            enabled=True,
            min_age_days=30,
            ebay_recreate_before_gtc_minutes=30,
            daily_cap=5,
            skip_active_offers=True,
        )
        now = _now()
        self.assertTrue(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30) + timedelta(minutes=15), ebay_rule, now))
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30), ebay_rule, now))
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=35), ebay_rule, now))

        # Finding #7 + #26: legacy skip_active_offers=False row still checks offers; disabling mid-run skips cleanly
        conv, _l, _r = self._create_draft("Disable Mid Run Jacket")
        conv.status = "listed"
        posh_id = "5f0000000000000000000777"
        self.db.add(MarketplaceListing(
            id=new_id(),
            conversation_id=conv.id,
            marketplace="poshmark",
            state="published",
            listing_id=posh_id,
            listing_url=f"https://poshmark.com/listing/{posh_id}",
            listed_at=now - timedelta(days=45),
            listed_price=58.0,
        ))
        rule = self.db.query(MarketplaceRelistRule).filter_by(marketplace="poshmark").one()
        rule.skip_active_offers = False  # simulate legacy DB row
        self.db.commit()

        # Active offer blocks relist even when DB row has skip_active_offers=False
        offer_checker = AsyncMock(return_value=True)
        skipped_offer = asyncio.run(auto_relist.run_auto_relist(
            marketplace="poshmark",
            offer_checker=offer_checker,
            delister=AsyncMock(return_value={"ok": True}),
            lister=AsyncMock(return_value={"ok": True, "listing_id": "5f0000000000000000000778"}),
        ))
        offer_checker.assert_awaited_once_with("poshmark", posh_id)
        self.assertEqual(skipped_offer[0]["status"], "skipped_active_offer")

        # Disabling the rule immediately before delist stops cleanly with skipped_rule_disabled (no failure)
        delister_called = False

        async def should_not_delist(*_args):
            nonlocal delister_called
            delister_called = True
            return {"ok": True}

        async def disable_during_offer_check(_market: str, _lid: str) -> bool:
            with self.Session() as s:
                r = s.query(MarketplaceRelistRule).filter_by(marketplace="poshmark").one()
                r.enabled = False
                s.commit()
            return False

        disabled_res = asyncio.run(auto_relist.run_auto_relist(
            marketplace="poshmark",
            offer_checker=disable_during_offer_check,
            delister=should_not_delist,
            lister=AsyncMock(return_value={"ok": True}),
            respect_backoff=False,
        ))
        self.assertFalse(delister_called)
        self.assertEqual(disabled_res[0]["status"], "skipped_rule_disabled")

    def test_auto_relist_default_delister_pins_expected_listing_id_and_persists_job_id_before_wait(self):
        from unittest.mock import AsyncMock

        from anteroom.services import automation_actions, native_marketplaces

        conv, _l, _r = self._create_draft("Pinned Relist Jacket")
        conv.status = "listed"
        posh_id = "5f0000000000000000000881"
        self.db.add(MarketplaceListing(
            id=new_id(),
            conversation_id=conv.id,
            marketplace="poshmark",
            state="published",
            listing_id=posh_id,
            listing_url=f"https://poshmark.com/listing/{posh_id}",
            listed_at=_now() - timedelta(days=45),
            listed_price=58.0,
        ))
        rule = MarketplaceRelistRule(
            id=new_id(),
            marketplace="poshmark",
            enabled=True,
            min_age_days=30,
            daily_cap=5,
            skip_active_offers=True,
        )
        self.db.add(rule)
        self.db.commit()

        # Finding #8: default delister passes expected={marketplace: expected_listing_id}
        captured_expected: dict[str, str] = {}

        async def fake_delist(_conv_id, _markets, **kwargs):
            captured_expected.update(kwargs.get("expected") or {})
            return {"poshmark": {"state": "deleted"}}

        # Finding #9: native_job_id is persisted BEFORE wait_for_native_result starts
        observed_job_id_before_wait: list[str | None] = []

        async def fake_wait(_conv_id, _market, _started):
            with self.Session() as s:
                log_row = s.query(MarketplaceRelistLog).filter_by(conversation_id=conv.id).one()
                observed_job_id_before_wait.append(log_row.native_job_id)
            return {
                "state": "published",
                "listing_id": "5f0000000000000000000882",
                "listing_url": "https://poshmark.com/listing/5f0000000000000000000882",
            }

        with (
            patch("anteroom.services.marketplace_delist.delist", side_effect=fake_delist),
            patch.object(native_marketplaces, "list_now", return_value={"job_id": "job-prewait-1", "state": "publishing"}),
            patch.object(automation_actions, "wait_for_native_result", side_effect=fake_wait),
        ):
            res = asyncio.run(auto_relist.run_auto_relist(
                marketplace="poshmark",
                offer_checker=AsyncMock(return_value=False),
            ))

        self.assertEqual(captured_expected, {"poshmark": posh_id})
        self.assertEqual(observed_job_id_before_wait, ["job-prewait-1"])
        self.assertEqual(res[0]["status"], "completed")
        self.assertEqual(res[0]["new_listing_id"], "5f0000000000000000000882")

    def test_h1_sold_item_is_unscheduled_and_never_published(self):
        from unittest.mock import AsyncMock
        from anteroom.models.marketplace_sale import MarketplaceSale
        from anteroom.services import automation_actions

        now = _now()
        # Case 1: unschedule_on_sale clears scheduled draft immediately when marked sold.
        c1, l1, _r1 = self._create_draft("Sold Before Due")
        draft_scheduling.schedule_draft(
            self.db, c1.id, marketplaces=["poshmark"], publish_at=now + timedelta(hours=1), confirm=True, now=now,
        )
        self.assertIsNotNone(l1.scheduled_publish_at)
        c1.status = "sold"
        self.assertTrue(draft_scheduling.unschedule_on_sale(self.db, c1.id))
        self.db.commit()
        self.db.refresh(l1)
        self.assertIsNone(l1.scheduled_publish_at)

        # Case 2: even if still scheduled when status=sold or a non-cancelled sale row exists,
        # claim refuses to publish it and clears the schedule.
        c2, l2, r2 = self._create_draft("Sold Race Item")
        draft_scheduling.schedule_draft(
            self.db, c2.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=5), confirm=True, now=now,
        )
        c2.status = "sold"
        self.db.commit()

        c3, l3, _r3 = self._create_draft("Sale Row Race Item")
        draft_scheduling.schedule_draft(
            self.db, c3.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=5), confirm=True, now=now,
        )
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-h1", line_id="line-h1",
            conversation_id=c3.id, quantity=1, order_status="paid",
            sold_at=now, received_at=now, state="new",
        ))
        self.db.commit()

        publisher = AsyncMock(return_value={"ok": True})
        outcomes = asyncio.run(draft_scheduling.run_due_scheduled_publishes(
            now=now + timedelta(minutes=10), publisher=publisher,
        ))
        publisher.assert_not_awaited()
        self.assertEqual(outcomes, [])
        self.db.expire_all()
        self.assertIsNone(self.db.get(Listing, l2.conversation_id).scheduled_publish_at)
        self.assertIsNone(self.db.get(Listing, l3.conversation_id).scheduled_publish_at)

        # Case 3: publish_revision itself also refuses sold items without dispatching.
        res = asyncio.run(automation_actions.publish_revision(
            c2.id, ["poshmark"], r2.id,
        ))
        self.assertFalse(res["ok"])
        self.assertIn("sold", res["error"].lower())

        # Case 4: multi-quantity item with stock remaining is NOT treated as sold until exhausted.
        c4, l4, r4 = self._create_draft("Multi Qty Shirt", quantity=3)
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-multi-1", line_id="line-multi-1",
            conversation_id=c4.id, quantity=1, order_status="paid",
            sold_at=now, received_at=now, state="new",
        ))
        self.db.commit()
        draft_scheduling.schedule_draft(
            self.db, c4.id, marketplaces=["poshmark"], publish_at=now + timedelta(hours=1), confirm=True, now=now,
        )
        self.assertFalse(draft_scheduling.unschedule_on_sale(self.db, c4.id))
        self.assertIsNotNone(l4.scheduled_publish_at)
        with patch.object(automation_actions, "_native_one", AsyncMock(return_value={"marketplace": "poshmark", "ok": True})):
            ok_partial = asyncio.run(automation_actions.publish_revision(c4.id, ["poshmark"], r4.id))
        self.assertTrue(ok_partial["ok"])

        # Selling the remaining 2 units exhausts stock and blocks publishing / unschedules the draft.
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-multi-2", line_id="line-multi-2",
            conversation_id=c4.id, quantity=2, order_status="paid",
            sold_at=now, received_at=now, state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling.unschedule_on_sale(self.db, c4.id))
        self.db.commit()
        self.db.refresh(l4)
        self.assertIsNone(l4.scheduled_publish_at)
        sold_out = asyncio.run(automation_actions.publish_revision(c4.id, ["poshmark"], r4.id))
        self.assertFalse(sold_out["ok"])
        self.assertIn("sold", sold_out["error"].lower())

    def test_paid_sales_across_marketplaces_exhaust_declared_stock(self):
        from anteroom.models.marketplace_sale import MarketplaceSale
        from anteroom.services import automation_actions

        now = _now()
        conv, listing, revision = self._create_draft(quantity=3)
        draft_scheduling.schedule_draft(
            self.db, conv.id, marketplaces=["poshmark"], publish_at=now + timedelta(hours=1), confirm=True, now=now,
        )
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=conv.id, marketplace="ebay", state="published", listing_id="1234",
            inventory_observation={"state": "active", "checked_at": now.isoformat(), "quantity": 2, "listing_id": "1234"},
        ))
        for market, status in (("ebay", "paid"), ("mercari", "paid"), ("poshmark", "cancellation_pending")):
            self.db.add(MarketplaceSale(
                id=new_id(), marketplace=market, order_id=market, line_id=market,
                conversation_id=conv.id, listing_id="1234", quantity=1, order_status=status,
                sold_at=now, received_at=now, state="new",
            ))
        self.db.commit()
        self.assertFalse(draft_scheduling.unschedule_on_sale(self.db, conv.id))
        self.db.query(MarketplaceSale).filter_by(marketplace="poshmark").one().order_status = "paid"
        self.db.commit()
        self.assertTrue(draft_scheduling.unschedule_on_sale(self.db, conv.id))
        self.db.commit()
        self.assertIsNone(listing.scheduled_publish_at)
        outcome = asyncio.run(automation_actions.publish_revision(conv.id, ["poshmark"], revision.id))
        self.assertIn("already sold", outcome["error"])

    def test_restock_and_cross_marketplace_observation_freshness(self):
        from anteroom.models.marketplace_sale import MarketplaceSale

        now = _now()
        # Observation strictly newer than all cross-marketplace paid sales proves remaining stock.
        c_obs, _l_obs, _r_obs = self._create_draft("Fresh Obs Multi Shirt", quantity=2)
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=c_obs.id, marketplace="ebay", state="published",
            listing_id="eb-fresh-1", listing_url="https://www.ebay.com/itm/eb-fresh-1",
            listed_at=now - timedelta(days=2),
            inventory_observation={
                "listing_id": "eb-fresh-1",
                "state": "active",
                "quantity": 1,
                "checked_at": (now + timedelta(minutes=5)).isoformat(),
            },
        ))
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-f1", line_id="l-f1",
            listing_id="eb-fresh-1", conversation_id=c_obs.id, quantity=1, order_status="paid",
            sold_at=now + timedelta(minutes=1), received_at=now + timedelta(minutes=1), state="new",
        ))
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="etsy", order_id="ord-f2", line_id="l-f2",
            conversation_id=c_obs.id, quantity=1, order_status="paid",
            sold_at=now + timedelta(minutes=2), received_at=now + timedelta(minutes=2), state="new",
        ))
        self.db.commit()
        # eBay's quantity does not reflect the Etsy sale without a quantity sync: two paid
        # sales exhaust the declared quantity of 2 even though eBay still reports 1 left.
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_obs))

        # Only same-marketplace sales and a fresh remaining read keep the draft scheduled,
        # even when the sale count reaches the declared quantity.
        c_same, _l_same, _r_same = self._create_draft("Same Market Multi Shirt", quantity=2)
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=c_same.id, marketplace="ebay", state="published",
            listing_id="eb-same-1", listing_url="https://www.ebay.com/itm/eb-same-1",
            listed_at=now - timedelta(days=2),
            inventory_observation={
                "listing_id": "eb-same-1",
                "state": "active",
                "quantity": 1,
                "checked_at": (now + timedelta(minutes=5)).isoformat(),
            },
        ))
        for n in (1, 2):
            self.db.add(MarketplaceSale(
                id=new_id(), marketplace="ebay", order_id=f"ord-s{n}", line_id=f"l-s{n}",
                listing_id="eb-same-1", conversation_id=c_same.id, quantity=1, order_status="paid",
                sold_at=now + timedelta(minutes=n), received_at=now + timedelta(minutes=n), state="new",
            ))
        self.db.commit()
        self.assertFalse(draft_scheduling._is_sold_or_has_sale(self.db, c_same))

        # Seller restock revision and sale_quantity_sync revision establish a new quantity baseline.
        c_res, _l_res, r_res = self._create_draft("Restocked Multi Shirt", quantity=3)
        past = now - timedelta(hours=2)
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-restock-1", line_id="line-restock-1",
            conversation_id=c_res.id, quantity=3, order_status="paid",
            sold_at=past, received_at=past, state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_res))
        r_restocked = listing_persistence.save_revision(
            self.db, c_res.id, {**r_res.listing_json, "quantity": 3},
            source="manual", parent_revision_id=r_res.id, commit=True,
        )
        r_restocked.created_at = now
        self.db.commit()
        self.assertFalse(draft_scheduling._is_sold_or_has_sale(self.db, c_res))
        r_synced = listing_persistence.save_revision(
            self.db, c_res.id, {**r_restocked.listing_json, "quantity": 1},
            source="sale_quantity_sync", parent_revision_id=r_restocked.id, commit=True,
        )
        r_synced.created_at = now + timedelta(minutes=5)
        self.db.commit()
        self.assertFalse(draft_scheduling._is_sold_or_has_sale(self.db, c_res))
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="poshmark", order_id="ord-restock-2", line_id="line-restock-2",
            conversation_id=c_res.id, quantity=1, order_status="paid",
            sold_at=now + timedelta(minutes=6), received_at=now + timedelta(minutes=6), state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_res))

        # Manual restock to 1 unit (without an inventory observation) also resets the baseline.
        c_one, _l_one, r_one = self._create_draft("Manual Restock One Unit", quantity=1)
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-one-1", line_id="line-one-1",
            conversation_id=c_one.id, quantity=1, order_status="paid",
            sold_at=past, received_at=past, state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_one))
        r_one_restocked = listing_persistence.save_revision(
            self.db, c_one.id, {**r_one.listing_json, "quantity": 1},
            source="manual", parent_revision_id=r_one.id, commit=True,
        )
        r_one_restocked.created_at = now
        self.db.commit()
        self.assertFalse(draft_scheduling._is_sold_or_has_sale(self.db, c_one))
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="poshmark", order_id="ord-one-2", line_id="line-one-2",
            conversation_id=c_one.id, quantity=1, order_status="paid",
            sold_at=now + timedelta(minutes=1), received_at=now + timedelta(minutes=1), state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_one))

    def test_due_legacy_schedule_requires_new_confirmation(self):
        now = _now()
        conv, listing, _revision = self._create_draft()
        draft_scheduling.schedule_draft(
            self.db, conv.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=5), confirm=True, now=now,
        )
        listing.scheduled_content_digest = None
        self.db.commit()

        self.assertEqual(draft_scheduling._claim_due_drafts(self.db, now + timedelta(minutes=6)), [])
        self.assertIsNone(listing.scheduled_publish_at)
        self.assertIn("review and reschedule", self.db.query(ScheduledPublishLog).filter_by(status="unscheduled").one().error)

    def test_native_preflight_refuses_media_edited_after_claim(self):
        from anteroom.services import native_marketplaces

        now = _now()
        conv, listing, _revision = self._create_draft()
        draft_scheduling.schedule_draft(
            self.db, conv.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=5), confirm=True, now=now,
        )

        async def publisher(conv_id, market, revision_id):
            body = native_marketplaces.ListRequest(revision_id=revision_id, confirm=True)
            with self.Session() as db:
                with patch.object(native_marketplaces.marketplace_media, "photos_for", return_value=([], "")) as filter_photos:
                    native_marketplaces._preflight(db, conv_id, market[0], body)
                    self.assertEqual(filter_photos.call_args.args[1][0].stored_filename, "photo1.jpg")
                db.query(Photo).filter_by(conversation_id=conv_id).one().stored_filename = "changed.jpg"
                db.commit()
                native_marketplaces._preflight(db, conv_id, market[0], body)
            return {"ok": True}

        with patch("anteroom.services.extension_connection.marketplace_actions_refusal", return_value=None):
            outcomes = asyncio.run(draft_scheduling.run_due_scheduled_publishes(
                now=now + timedelta(minutes=6), publisher=publisher,
            ))
        self.assertIn("review and reschedule", outcomes[0]["error"])
        self.db.refresh(listing)
        self.assertIsNone(listing.scheduled_publish_at)
        self.assertIsNone(listing.scheduled_content_digest)

    def test_open_job_rechecks_sold_state_for_list_and_save_draft(self):
        from anteroom.models.job import Job
        from anteroom.models.marketplace_sale import MarketplaceSale
        from anteroom.services import approved_photos, native_marketplaces

        now = _now()
        conv, _listing, revision = self._create_draft("Sold Mid Preflight")
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-mid-1", line_id="line-mid-1",
            conversation_id=conv.id, quantity=1, order_status="paid",
            sold_at=now, received_at=now, state="new",
        ))
        self.db.commit()

        for kind, market in (("list", "poshmark"), ("save_draft", "poshmark")):
            body = native_marketplaces.ListRequest(revision_id=revision.id, confirm=True)
            snapshot = {**revision.listing_json, approved_photos.KEY: []}
            with (
                patch.object(approved_photos, "release") as release_mock,
                self.assertRaises(native_marketplaces.NativeMarketplaceError) as ctx,
            ):
                native_marketplaces._open_job(self.db, conv.id, market, body, kind, snapshot, [], False)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("sold", str(ctx.exception).lower())
            release_mock.assert_called_once_with(snapshot)
        self.assertEqual(self.db.query(Job).filter_by(conversation_id=conv.id).count(), 0)

    def test_m1_abandoned_claim_wakes_scheduler_and_recovers(self):
        from anteroom.services.automation_scheduler import _has_due_scheduled_drafts

        now = _now()
        _c, listing, _r = self._create_draft("Stuck Claim")
        listing.scheduled_publish_at = None
        listing.scheduled_status = "publishing"
        listing.updated_at = now - timedelta(minutes=25)
        self.db.commit()

        self.assertTrue(draft_scheduling.has_due_or_abandoned_publishes(self.db, now))
        self.assertTrue(_has_due_scheduled_drafts(self.db, now))
        asyncio.run(draft_scheduling.run_due_scheduled_publishes(now=now))
        self.db.expire_all()
        self.assertEqual(self.db.get(Listing, listing.conversation_id).scheduled_status, "needs_review")
        self.assertFalse(draft_scheduling.has_due_or_abandoned_publishes(self.db, now))

    def test_m2_photo_and_pricing_rule_changes_unschedule_and_l3_native_schema_advances_revision(self):
        from unittest.mock import AsyncMock
        from anteroom.services import native_contract, pricing_rules

        now = _now()
        c, listing, r = self._create_draft("Digest Check Jacket")
        draft_scheduling.schedule_draft(
            self.db, c.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=10), confirm=True, now=now,
        )
        self.assertIsNotNone(listing.scheduled_content_digest)

        # L3: native_schema save adding only private _native_contract advances scheduled_revision_id.
        r_schema = listing_persistence.save_revision(
            self.db,
            c.id,
            {**r.listing_json, native_contract.KEY: {"poshmark": {"v": 1}}},
            source="native_schema",
            commit=True,
        )
        self.db.refresh(listing)
        self.assertIsNotNone(listing.scheduled_publish_at)
        self.assertEqual(listing.scheduled_revision_id, r_schema.id)

        # L3: native_schema save mutating public fields DOES unschedule.
        listing_persistence.save_revision(
            self.db,
            c.id,
            {**r.listing_json, "title": "Patagonia L Vintage Fleece Jacket Black"},
            source="native_schema",
            commit=True,
        )
        self.db.refresh(listing)
        self.assertIsNone(listing.scheduled_publish_at)

        # M2: photo add/reorder/delete unschedules a scheduled draft.
        draft_scheduling.schedule_draft(
            self.db, c.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=10), confirm=True, now=now,
        )
        p1 = listing_persistence.add_photo(
            self.db, c.id, "a.jpg", "a-stored.jpg", "image/jpeg", 100, "chk1", 100, 100,
        )
        self.db.refresh(listing)
        self.assertIsNone(listing.scheduled_publish_at)

        p2 = listing_persistence.add_photo(
            self.db, c.id, "b.jpg", "b-stored.jpg", "image/jpeg", 100, "chk2", 100, 100,
        )
        draft_scheduling.schedule_draft(
            self.db, c.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=10), confirm=True, now=now,
        )
        listing_persistence.reorder_photos(self.db, c.id, [p2.id, p1.id])
        self.db.refresh(listing)
        self.assertIsNone(listing.scheduled_publish_at)
        self.assertEqual((p2.display_order, p1.display_order), (0, 1))

        # M2: changing active pricing rules after scheduling causes digest mismatch and skips publish.
        with patch.object(pricing_rules, "active_rules", return_value=None):
            draft_scheduling.schedule_draft(
                self.db, c.id, marketplaces=["poshmark"], publish_at=now + timedelta(minutes=10), confirm=True, now=now,
            )
        changed_rules = {
            "enabled": True,
            "anchor": "general",
            "rules": {"poshmark": {"percent": 10.0, "fixed": 0.0, "rounding": "whole"}},
        }
        publisher = AsyncMock(return_value={"ok": True})
        with patch.object(pricing_rules, "active_rules", return_value=changed_rules):
            asyncio.run(draft_scheduling.run_due_scheduled_publishes(
                now=now + timedelta(minutes=15), publisher=publisher,
            ))
        publisher.assert_not_awaited()
        self.db.expire_all()
        self.assertIsNone(self.db.get(Listing, listing.conversation_id).scheduled_publish_at)

    def test_m3_m4_m5_l2_auto_relist_hardening(self):
        from unittest.mock import AsyncMock
        from anteroom.services.automation_scheduler import _run_auto_relist

        now = _now()
        # L2: compute_relist_price never raises a price already below price_floor.
        rule_floor = MarketplaceRelistRule(
            id=new_id(), marketplace="poshmark", enabled=True,
            price_adjust_mode="amount", price_adjust_value=5.0, price_floor=50.0,
        )
        self.assertEqual(auto_relist.compute_relist_price(40.0, rule_floor), 40.0)
        self.assertEqual(auto_relist.compute_relist_price(52.0, rule_floor), 50.0)

        # M5: eBay GTC 30-day cycle triggers near end of cycle 1 (29d23h30m) and cycle 2 (59d23h30m), not day 31.
        ebay_rule = MarketplaceRelistRule(
            id=new_id(), marketplace="ebay", enabled=True, ebay_recreate_before_gtc_minutes=60,
        )
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=29, hours=22), ebay_rule, now))
        self.assertTrue(auto_relist.is_due_for_relist("ebay", now - timedelta(days=29, hours=23, minutes=30), ebay_rule, now))
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=31), ebay_rule, now))
        self.assertTrue(auto_relist.is_due_for_relist("ebay", now - timedelta(days=59, hours=23, minutes=30), ebay_rule, now))

        # M3: failed attempts consume daily_cap and trigger 6h backoff; manual run bypasses backoff.
        c, _l, _r = self._create_draft("Failing Relist Item")
        c.status = "listed"
        posh_id = "5f0000000000000000000991"
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=c.id, marketplace="poshmark", state="published",
            listing_id=posh_id, listing_url=f"https://poshmark.com/listing/{posh_id}",
            listed_at=now - timedelta(days=45), listed_price=60.0,
        ))
        self.db.add(MarketplaceRelistRule(
            id=new_id(), marketplace="poshmark", enabled=True, min_age_days=30, daily_cap=1,
        ))
        self.db.commit()

        failing_delist = AsyncMock(return_value={"ok": False, "error": "boom"})
        first = asyncio.run(auto_relist.run_auto_relist(
            now=now, marketplace="poshmark", offer_checker=AsyncMock(return_value=False),
            delister=failing_delist, lister=AsyncMock(return_value={"ok": True}),
        ))
        self.assertEqual(first[0]["status"], "failed")
        # Daily cap=1 is now consumed by the failed attempt (M3), so even with respect_backoff=False nothing runs today.
        second = asyncio.run(auto_relist.run_auto_relist(
            now=now + timedelta(minutes=1), marketplace="poshmark",
            offer_checker=AsyncMock(return_value=False),
            delister=failing_delist, lister=AsyncMock(return_value={"ok": True}),
            respect_backoff=False,
        ))
        self.assertEqual(second, [])
        self.assertEqual(failing_delist.await_count, 1)

        # M4: scheduled run stamps last_run_at and throttles within 5 minutes.
        asyncio.run(_run_auto_relist(now + timedelta(days=1)))
        self.assertFalse(auto_relist.has_due_relist_rules(self.db, now + timedelta(days=1, minutes=2)))
        self.assertTrue(auto_relist.has_due_relist_rules(self.db, now + timedelta(days=1, minutes=6)))

        # Vendoo note-backed imported listing (no MarketplaceListing row) is still discovered.
        import json

        imported_id = "5f0000000000000000000992"
        c_imp, _l_imp, _r_imp = self._create_draft("Vendoo Imported Coat")
        c_imp.status = "listed"
        c_imp.notes = json.dumps({
            "vendooMarketplaces": ["Poshmark"],
            "vendooListingUrls": {
                "poshmark": f"https://poshmark.com/listing/vendoo-coat-{imported_id}",
            },
            "vendooDates": {
                "listedByMarketplace": {
                    "Poshmark": (now - timedelta(days=45)).isoformat(),
                },
            },
        })
        self.db.commit()
        posh_rule = MarketplaceRelistRule(
            id=new_id(), marketplace="poshmark", enabled=True, min_age_days=30, daily_cap=5,
        )
        found = auto_relist._eligible_candidates(
            self.db, posh_rule, now + timedelta(days=2), conv_id=c_imp.id,
        )
        self.assertEqual([item["conversation_id"] for item in found], [c_imp.id])
        self.assertEqual(found[0]["listing_id"], imported_id)

        # Published MarketplaceListing row with null listing_id and valid listing_url is also discovered.
        url_only_id = "5f0000000000000000000993"
        c_url, _l_url, _r_url = self._create_draft("URL Only Listing Coat")
        c_url.status = "listed"
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=c_url.id, marketplace="poshmark", state="published",
            listing_id=None, listing_url=f"https://poshmark.com/listing/url-only-{url_only_id}",
            listed_at=now - timedelta(days=45), listed_price=55.0,
        ))
        self.db.commit()
        found_url = auto_relist._eligible_candidates(
            self.db, posh_rule, now + timedelta(days=2), conv_id=c_url.id,
        )
        self.assertEqual([item["conversation_id"] for item in found_url], [c_url.id])
        self.assertEqual(found_url[0]["listing_id"], url_only_id)


if __name__ == "__main__":
    unittest.main()

