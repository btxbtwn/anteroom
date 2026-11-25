from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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
        inventory = {row["id"]: row for row in self.client.get("/api/conversations").json()}
        self.assertIsNone(inventory[c1.id]["scheduled_publish_at"])

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

        inventory = {row["id"]: row for row in self.client.get("/api/conversations").json()}
        self.assertEqual(inventory[c1.id]["scheduled_publish_at"], future)
        self.assertEqual(inventory[c2.id]["scheduled_publish_at"], future)
        self.assertIsNone(inventory[c_bad.id]["scheduled_publish_at"])

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

        inventory = {row["id"]: row for row in self.client.get("/api/conversations").json()}
        self.assertIsNone(inventory[c1.id]["scheduled_publish_at"])
        self.assertIsNone(inventory[c2.id]["scheduled_publish_at"])

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

    def test_run_auto_relist_relists_listings_with_buyer_offers_enforces_cap_and_swaps_id_atomically(self):
        now = _now()
        old_time = now - timedelta(days=45)

        old_posh_1 = "5f0000000000000000000001"
        old_posh_2 = "5f0000000000000000000002"
        old_posh_3 = "5f0000000000000000000003"
        new_posh_1 = "5f0000000000000000000099"
        new_posh_2 = "5f0000000000000000000098"

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

        # Item 2: has an active incoming buyer offer -> relisted anyway (seller request 2026-11-24)
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

        # Item 3: eligible but the newest, so daily_cap=2 leaves it for a later day
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
            },
            confirm=True,
        )

        delist_calls: list[tuple[str, str]] = []
        list_calls: list[tuple[str, str, str]] = []

        async def fake_delister(conv_id: str, market: str) -> dict:
            delist_calls.append((conv_id, market))
            return {"ok": True}

        new_ids = {c1.id: new_posh_1, c2.id: new_posh_2}

        async def fake_lister(conv_id: str, market: str, rev_id: str) -> dict:
            list_calls.append((conv_id, market, rev_id))
            return {
                "ok": True,
                "listing_id": new_ids[conv_id],
                "listing_url": f"https://poshmark.com/listing/{new_ids[conv_id]}",
            }

        with patch("anteroom.services.cloud_marketplace.execute") as marketplace_read:
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now, delister=fake_delister, lister=fake_lister))
        # Nothing asks the marketplace about buyer offers before deleting a listing.
        marketplace_read.assert_not_called()
        self.assertEqual(len(outcomes), 2)
        statuses = {o["conversation_id"]: o["status"] for o in outcomes}
        self.assertEqual(statuses, {c2.id: "completed", c1.id: "completed"})
        self.assertEqual(delist_calls, [(c2.id, "poshmark"), (c1.id, "poshmark")])
        self.assertEqual(len(list_calls), 2)

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
        self.assertEqual(len(logs), 2)

    def test_scheduled_relist_deletes_and_relists_without_checking_buyer_offers(self):
        from unittest.mock import AsyncMock
        from anteroom.services.automation_scheduler import _run_auto_relist

        conv, _, _ = self._create_draft("Scheduler relist")
        conv.status = "listed"
        old_id = "5f0000000000000000000081"
        t0 = _now()
        self.db.add(MarketplaceListing(id=new_id(), conversation_id=conv.id, marketplace="poshmark",
                                      state="published", listing_id=old_id, listed_at=t0 - timedelta(days=45),
                                      listing_url=f"https://poshmark.com/listing/{old_id}",
                                      listed_price=58.0,
                                      inventory_observation={"has_active_offer": True, "active_offers_count": 1}))
        self.db.add(MarketplaceRelistRule(id=new_id(), marketplace="poshmark", enabled=True))
        self.db.commit()
        with (
            patch("anteroom.services.cloud_marketplace.execute") as marketplace_read,
            patch("anteroom.services.marketplace_delist.delist", AsyncMock(return_value={
                "poshmark": {"state": "deleted"},
            })) as delete,
            patch("anteroom.services.native_marketplaces.list_now", AsyncMock(return_value={
                "state": "published", "listing_id": "5f0000000000000000000082",
            })),
        ):
            self.assertEqual(asyncio.run(_run_auto_relist(t0)), 1)
            delete.assert_awaited_once()
            marketplace_read.assert_not_called()
        self.assertEqual(self.db.query(MarketplaceRelistLog).filter_by(status="completed").count(), 1)

    def test_relist_stops_only_the_marketplace_that_requires_a_browser_check(self):
        from unittest.mock import AsyncMock

        now = _now()
        ids = {}
        for index, (market, url, old_id) in enumerate([
            ("mercari", "https://www.mercari.com/us/item/m123456789/", "m123456789"),
            ("mercari", "https://www.mercari.com/us/item/m123456790/", "m123456790"),
            ("depop", "https://www.depop.com/products/seller-fleece/", "seller-fleece"),
        ]):
            conv, _, rev = self._create_draft(f"Connection Check {index}")
            conv.status = "listed"
            ids[old_id] = (conv.id, rev.id)
            self.db.add(MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace=market, state="published",
                listing_id=old_id, listing_url=url, listed_price=58.0,
                listed_at=now - timedelta(days=45 - index),
            ))
        self.db.add_all(
            MarketplaceRelistRule(id=new_id(), marketplace=market, enabled=True,
                                  price_adjust_mode="percent", price_adjust_value=10)
            for market in ("mercari", "depop")
        )
        self.db.commit()

        async def delete(market, command, **_kwargs):
            if command["type"] == "marketplace.form":
                return {"ok": True, "relist_shipping_checked": True}
            if market == "mercari":
                return {"ok": False, "error_code": "challenge", "error": "Mercari asked for a browser check."}
            return {"ok": True, "state": "deleted"}

        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", side_effect=delete) as execute,
            patch("anteroom.services.native_marketplaces.list_now", AsyncMock(return_value={
                "state": "published", "listing_id": "seller-new-fleece",
                "listing_url": "https://www.depop.com/products/seller-new-fleece/",
            })) as publish,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now))

        self.assertEqual([(call.args[0], call.args[1]["type"]) for call in execute.await_args_list], [
            ("mercari", "marketplace.delete"), ("depop", "marketplace.form"), ("depop", "marketplace.delete"),
        ])
        self.assertEqual(execute.await_args_list[-1].args[1]["payload"]["action"], "relist")
        self.assertEqual([row["status"] for row in outcomes], ["failed", "completed"])
        self.assertEqual(publish.await_count, 1)
        self.assertEqual(publish.await_args.args[:2], (ids["seller-fleece"][0], "depop"))
        self.db.expire_all()
        for old_id in ("m123456789", "m123456790"):
            conv_id, revision_id = ids[old_id]
            current = self.db.query(Listing).filter_by(conversation_id=conv_id).one()
            prices = self.db.get(ListingRevision, current.current_revision_id)
            assert prices is not None
            self.assertEqual(auto_relist.price_for(prices.listing_json, "mercari"), 58.0)
            if old_id == "m123456790":
                self.assertEqual(current.current_revision_id, revision_id)

    def test_depop_relist_refuses_unavailable_shipping_before_delisting(self):
        now = _now()
        conv, _, original = self._create_draft("Jeans", price=12, depop_specifics={"parcelSize": "Extra extra small"})
        original_id = original.id
        old_id = "seller-jeans"
        self.db.add_all([
            MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace="depop", state="published",
                listing_id=old_id, listing_url=f"https://www.depop.com/products/{old_id}/",
                listed_price=12, listed_at=now - timedelta(days=45),
            ),
            MarketplaceRelistRule(id=new_id(), marketplace="depop", enabled=True,
                                  price_adjust_mode="percent", price_adjust_value=10),
        ])
        self.db.commit()
        reason = "Depop needs a package size it offers for this item (it does not offer Extra extra small)."
        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", AsyncMock(return_value={
                "ok": False, "error_code": "missing_fields", "error": reason,
            })) as execute,
            patch("anteroom.services.marketplace_delist.delist", AsyncMock(return_value={
                "depop": {"state": "deleted"},
            })) as delete,
            patch("anteroom.services.native_marketplaces.list_now", AsyncMock(return_value={
                "state": "published", "listing_id": "seller-new-jeans",
            })) as publish,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now, marketplace="depop"))

        execute.assert_awaited_once()
        command = execute.await_args.args[1]
        self.assertEqual(command["type"], "marketplace.form")
        self.assertTrue(command["payload"]["check_relist_shipping"])
        self.assertEqual(command["payload"]["listing"]["price"], 10.80)
        self.assertEqual(command["payload"]["listing"]["depop_specifics"]["parcelSize"], "Extra extra small")
        delete.assert_not_awaited()
        publish.assert_not_awaited()
        self.assertEqual([(row["status"], row["reason"]) for row in outcomes], [("failed", reason)])
        self.db.expire_all()
        listing = self.db.query(Listing).filter_by(conversation_id=conv.id).one()
        self.assertEqual(listing.current_revision_id, original_id)
        live = self.db.query(MarketplaceListing).filter_by(conversation_id=conv.id, marketplace="depop").one()
        self.assertEqual((live.state, live.listing_id, live.listed_price), ("published", old_id, 12))

    def test_depop_relist_skips_shipping_if_another_run_fills_the_daily_cap(self):
        now = _now()
        conv, _, _ = self._create_draft("Shirt", price=13)
        self.db.add_all([
            MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace="depop", state="published",
                listing_id="seller-shirt", listing_url="https://www.depop.com/products/seller-shirt/",
                listed_price=13, listed_at=now - timedelta(days=45),
            ),
            MarketplaceRelistRule(id=new_id(), marketplace="depop", enabled=True, daily_cap=1),
        ])
        self.db.commit()
        load = auto_relist._load_enabled_rules_and_candidates

        def load_before_concurrent_relist(db, *args, **kwargs):
            plan = load(db, *args, **kwargs)
            self.assertEqual(len(plan), 1)
            db.add(MarketplaceRelistLog(
                id=new_id(), conversation_id=conv.id, marketplace="depop",
                old_listing_id="seller-previous-shirt", new_listing_id="seller-shirt",
                status="completed", created_at=now,
            ))
            db.commit()
            return plan

        with (
            patch.object(auto_relist, "_load_enabled_rules_and_candidates", side_effect=load_before_concurrent_relist),
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=True),
            patch("anteroom.services.cloud_marketplace.execute", AsyncMock(return_value={
                "ok": True, "relist_shipping_checked": True,
            })) as execute,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now, marketplace="depop"))

        self.assertEqual(outcomes, [])
        execute.assert_not_awaited()

    def test_depop_relist_waits_after_a_busy_publish_start(self):
        from anteroom.services import automation_actions, native_marketplaces

        now = _now()
        conv, _, _ = self._create_draft("Shirt", price=13)
        self.db.add_all([
            MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace="depop", state="published",
                listing_id="seller-shirt", listing_url="https://www.depop.com/products/seller-shirt/",
                listed_price=13, listed_at=now - timedelta(days=45),
            ),
            MarketplaceRelistRule(id=new_id(), marketplace="depop", enabled=True,
                                  price_adjust_mode="percent", price_adjust_value=10),
        ])
        self.db.commit()
        delete = AsyncMock(return_value={"ok": True})
        busy = native_marketplaces.NativeMarketplaceError(409, "Another Depop action is running.",
                                                          reason=native_marketplaces.BUSY)
        with (
            patch.object(automation_actions, "_wait_until_free", AsyncMock(return_value=True)) as wait,
            patch.object(native_marketplaces, "list_now", AsyncMock(side_effect=[
                busy, {"state": "published", "listing_id": "seller-new-shirt"},
            ])) as publish,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now, marketplace="depop", delister=delete))

        delete.assert_awaited_once()
        self.assertEqual(publish.await_count, 2)
        self.assertEqual(wait.await_count, 2)
        self.assertEqual(outcomes[0]["status"], "completed")
        self.assertEqual(outcomes[0]["new_listing_id"], "seller-new-shirt")

    def test_relist_stops_deleting_after_a_replacement_job_fails(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from anteroom.models.job import Job
        from anteroom.repositories.marketplace_listings import MarketplaceListingRepo

        now = _now()
        for age, slug in [(45, "seller-fleece"), (44, "seller-coat")]:
            conv, _, _ = self._create_draft(slug)
            conv.status = "listed"
            self.db.add(MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace="depop", state="published",
                listing_id=slug, listing_url=f"https://www.depop.com/products/{slug}/",
                listed_price=58.0, listed_at=now - timedelta(days=age),
            ))
        self.db.add(MarketplaceRelistRule(id=new_id(), marketplace="depop", enabled=True))
        self.db.commit()

        async def publish(conv_id, marketplace, body, **_kwargs):
            with self.Session() as db:
                job_id = new_id()
                error = "Depop asked to sign in again."
                db.add(Job(id=job_id, conversation_id=conv_id, approved_revision_id=body.revision_id,
                           status="failed", listing_snapshot={}, last_error=error))
                MarketplaceListingRepo(db).save(conv_id, marketplace, {
                    "state": "not_saved", "job_id": job_id, "error": error, "error_code": "signed_out",
                })
            return {"state": "saving", "job_id": job_id}

        with (
            patch("anteroom.services.cloud_sessions.has_active_session", return_value=False),
            patch("anteroom.services.extension_connection.current_extension", return_value=SimpleNamespace(connected=True)),
            patch("anteroom.services.browser_bridge.request", AsyncMock(side_effect=[
                {"ok": True, "relist_shipping_checked": True},
                {"ok": True, "state": "deleted"},
            ])) as bridge,
            patch("anteroom.services.native_marketplaces.list_now", side_effect=publish) as list_now,
        ):
            outcomes = asyncio.run(auto_relist.run_auto_relist(now=now))

        self.assertEqual([row["status"] for row in outcomes], ["down_needs_relist"])
        self.assertEqual(outcomes[0]["reason"], "Depop asked to sign in again.")
        self.assertEqual([call.args[1] for call in bridge.await_args_list], ["marketplace.form", "marketplace.delete"])
        self.assertEqual(bridge.await_args.args[1:3], ("marketplace.delete", {
            "marketplace": "depop", "listing_id": "seller-fleece", "confirm": True, "action": "relist",
        }))
        list_now.assert_awaited_once()
        self.db.expire_all()
        untouched = self.db.query(MarketplaceListing).filter_by(marketplace="depop", listing_id="seller-coat").one()
        self.assertEqual(untouched.state, "published")

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
            outcomes = asyncio.run(auto_relist.run_auto_relist())
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

    def test_ebay_recreate_is_not_due_once_the_listing_reaches_its_gtc_renewal(self):
        # Finding #10: eBay GTC is NOT due once age >= 30 days
        ebay_rule = MarketplaceRelistRule(
            id=new_id(),
            marketplace="ebay",
            enabled=True,
            min_age_days=30,
            ebay_recreate_before_gtc_minutes=30,
            daily_cap=5,
        )
        now = _now()
        self.assertTrue(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30) + timedelta(minutes=15), ebay_rule, now))
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=30), ebay_rule, now))
        self.assertFalse(auto_relist.is_due_for_relist("ebay", now - timedelta(days=35), ebay_rule, now))

    def test_auto_relist_default_delister_pins_expected_listing_id_and_persists_job_id_before_wait(self):

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

        # Seller restock revision (quantity change) and sale_quantity_sync establish a new quantity baseline.
        c_res, _l_res, r_res = self._create_draft("Restocked Multi Shirt", quantity=3)
        past = now - timedelta(hours=2)
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-restock-1", line_id="line-restock-1",
            conversation_id=c_res.id, quantity=3, order_status="paid",
            sold_at=past, received_at=past, state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_res))
        r_res_zero = listing_persistence.save_revision(
            self.db, c_res.id, {**r_res.listing_json, "quantity": 0},
            source="sale_quantity_sync", parent_revision_id=r_res.id, commit=True,
        )
        r_res_zero.created_at = past + timedelta(minutes=1)
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_res))
        r_restocked = listing_persistence.save_revision(
            self.db, c_res.id, {**r_res_zero.listing_json, "quantity": 3},
            source="manual", parent_revision_id=r_res_zero.id, commit=True,
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

        # Manual edit that only changes title on a sold 1-unit item must keep it sold;
        # a genuine restock from 0 to 1 resets the baseline.
        c_one, _l_one, r_one = self._create_draft("Manual Restock One Unit", quantity=1)
        self.db.add(MarketplaceSale(
            id=new_id(), marketplace="ebay", order_id="ord-one-1", line_id="line-one-1",
            conversation_id=c_one.id, quantity=1, order_status="paid",
            sold_at=past, received_at=past, state="new",
        ))
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_one))
        r_one_typo = listing_persistence.save_revision(
            self.db, c_one.id, {**r_one.listing_json, "title": "Patagonia L Vintage Edited Title Gray"},
            source="manual", parent_revision_id=r_one.id, commit=True,
        )
        r_one_typo.created_at = now - timedelta(minutes=30)
        self.db.commit()
        self.assertTrue(draft_scheduling._is_sold_or_has_sale(self.db, c_one))
        r_one_zero = listing_persistence.save_revision(
            self.db, c_one.id, {**r_one_typo.listing_json, "quantity": 0},
            source="sale_quantity_sync", parent_revision_id=r_one_typo.id, commit=True,
        )
        r_one_zero.created_at = now - timedelta(minutes=10)
        self.db.commit()
        r_one_restocked = listing_persistence.save_revision(
            self.db, c_one.id, {**r_one_zero.listing_json, "quantity": 1},
            source="manual", parent_revision_id=r_one_zero.id, commit=True,
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
        r_one_post_sale_edit = listing_persistence.save_revision(
            self.db, c_one.id, {**r_one_restocked.listing_json, "title": "Patagonia L Vintage Post Sale Title Gray"},
            source="manual", parent_revision_id=r_one_restocked.id, commit=True,
        )
        r_one_post_sale_edit.created_at = now + timedelta(minutes=2)
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

        # Poshmark only takes whole dollars: a drop rounds down, never under the floor.
        posh_pct = MarketplaceRelistRule(
            id=new_id(), marketplace="poshmark", enabled=True,
            price_adjust_mode="percent", price_adjust_value=10.0, price_floor=None,
        )
        self.assertEqual(auto_relist.compute_relist_price(9.0, posh_pct), 8.0)
        self.assertEqual(auto_relist.compute_relist_price(5.0, posh_pct), 4.0)
        self.assertEqual(auto_relist.compute_relist_price(4.0, posh_pct), 3.0)
        posh_pct.price_floor = 7.5
        self.assertEqual(auto_relist.compute_relist_price(8.0, posh_pct), 8.0)
        self.assertEqual(auto_relist.compute_relist_price(9.0, posh_pct), 8.0)
        # Mercari's form takes whole dollars too.
        mercari_pct = MarketplaceRelistRule(
            id=new_id(), marketplace="mercari", enabled=True,
            price_adjust_mode="percent", price_adjust_value=10.0, price_floor=None,
        )
        self.assertEqual(auto_relist.compute_relist_price(6.0, mercari_pct), 5.0)
        self.assertEqual(auto_relist.compute_relist_price(30.0, mercari_pct), 27.0)
        # Marketplaces that take cents keep them.
        ebay_pct = MarketplaceRelistRule(
            id=new_id(), marketplace="ebay", enabled=True,
            price_adjust_mode="percent", price_adjust_value=10.0, price_floor=None,
        )
        self.assertEqual(auto_relist.compute_relist_price(9.0, ebay_pct), 8.1)

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
            now=now, marketplace="poshmark",
            delister=failing_delist, lister=AsyncMock(return_value={"ok": True}),
        ))
        self.assertEqual(first[0]["status"], "failed")
        # Daily cap=1 is now consumed by the failed attempt (M3), so even with respect_backoff=False nothing runs today.
        second = asyncio.run(auto_relist.run_auto_relist(
            now=now + timedelta(minutes=1), marketplace="poshmark",
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

        # An empty native tracking row must not hide the imported listing.
        tracking = MarketplaceListing(conversation_id=c_imp.id, marketplace="poshmark")
        self.db.add(tracking)
        for state, expected_ids in (("not_saved", [imported_id]), ("published", [imported_id]), ("publishing", [])):
            with self.subTest(state=state):
                tracking.state = state
                self.db.commit()
                found = auto_relist._eligible_candidates(
                    self.db, posh_rule, now + timedelta(days=2), conv_id=c_imp.id,
                )
                self.assertEqual([item["listing_id"] for item in found], expected_ids)

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


    def _pricing_rules_on(self) -> None:
        from anteroom.services import pricing_rules
        from anteroom.services.pricing_rules import PRICING_RULES_KEY
        from anteroom.services.user_settings import update_settings

        self.addCleanup(update_settings, lambda payload: payload.pop(PRICING_RULES_KEY, None))
        config = pricing_rules.normalize({"enabled": True, "anchor": "general"})
        config["rules"]["poshmark"] = {"percent": 25.0, "fixed": 0.0, "rounding": "whole"}
        pricing_rules.set_pricing_rules(enabled=True, anchor="general", rules=config["rules"])

    def _stale_poshmark_draft(self, *, general: float, listed_price: float | None, drop: float | None,
                             mode: str = "amount"):
        from anteroom.services.seller_overrides import price_for

        conv, _listing, rev = self._create_draft("Priced Coat", price=general)
        old_id = "5f00000000000000000000a1"
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=conv.id, marketplace="poshmark", state="published",
            listing_id=old_id, listing_url=f"https://poshmark.com/listing/{old_id}",
            listed_at=_now() - timedelta(days=45), listed_price=listed_price,
        ))
        self.db.commit()
        auto_relist.save_rule(self.db, "poshmark", {
            "enabled": True, "min_age_days": 30, "daily_cap": 5,
            **({"price_adjust_mode": mode, "price_adjust_value": drop, "price_floor": 1.0} if drop else {}),
        }, confirm=True)
        return conv, rev, {market: price_for(rev.listing_json, market) for market in ("poshmark", "ebay", "mercari")}

    def _relist(self, *, delist_ok: bool = True):
        listed: list[str] = []

        async def delister(conv_id: str, market: str) -> dict:
            return {"ok": True} if delist_ok else {"ok": False, "error": "Poshmark refused the delete."}

        async def lister(conv_id: str, market: str, rev_id: str) -> dict:
            listed.append(rev_id)
            return {"ok": True, "listing_id": "5f00000000000000000000b2"}

        outcomes = asyncio.run(auto_relist.run_auto_relist(
            now=_now(), delister=delister, lister=lister,
        ))
        self.db.expire_all()
        return outcomes, listed

    def test_poshmark_percent_drop_relists_at_a_whole_dollar_price(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        conv, rev, before = self._stale_poshmark_draft(general=12, listed_price=13, drop=10, mode="percent")
        preview = auto_relist.preview_status(self.db, marketplace="poshmark")["rules"][0]["candidates"][0]
        # 10% off $13 is $11.70; Poshmark takes whole dollars, so the drop lands on $11.
        self.assertEqual(preview["proposed_price"], 11)
        self.assertNotIn("price", [gap["field"] for gap in preview["listing_gaps"]])

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(price_for(published.listing_json, "poshmark"), 11)
        self.assertEqual(price_for(published.listing_json, "mercari"), before["mercari"])

    def test_relist_with_pricing_rules_keeps_the_live_price(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        conv, rev, before = self._stale_poshmark_draft(general=20.0, listed_price=25.0, drop=None)
        self.assertEqual(before["poshmark"], 25)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(price_for(published.listing_json, "poshmark"), 25)
        self.assertEqual(published.listing_json["price"], 20.0)
        self.assertEqual({m: price_for(published.listing_json, m) for m in before}, before)

    def test_relist_price_drop_changes_only_the_relisted_marketplace(self):
        from anteroom.services.seller_overrides import locked_paths, price_for

        self._pricing_rules_on()
        conv, rev, before = self._stale_poshmark_draft(general=30.0, listed_price=38.0, drop=5)
        self.assertEqual(before["poshmark"], 38)
        self._live_on_ebay(conv.id)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(price_for(published.listing_json, "poshmark"), 33)
        # General saves the relist price; the marketplaces the item is live on keep theirs.
        self.assertEqual(published.listing_json["price"], 33.0)
        self.assertEqual(price_for(published.listing_json, "ebay"), before["ebay"])
        # Not listed on Mercari, so it follows the new General price through its rule.
        self.assertNotIn("mercari_specifics.price", locked_paths(published.listing_json))
        self.assertNotEqual(price_for(published.listing_json, "mercari"), before["mercari"])
        listing = self.db.query(Listing).filter_by(conversation_id=conv.id).one()
        self.assertEqual(listing.current_revision_id, published.id)

    def test_relist_price_drop_saves_general_without_pricing_rules(self):
        from anteroom.services.seller_overrides import price_for

        from anteroom.services.seller_overrides import locked_paths

        conv, rev, before = self._stale_poshmark_draft(general=38.0, listed_price=38.0, drop=5)
        self.assertEqual(before, {"poshmark": 38, "ebay": 38, "mercari": 38})
        self._live_on_ebay(conv.id)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(published.listing_json["price"], 33.0)
        self.assertEqual(price_for(published.listing_json, "poshmark"), 33)
        # Live on eBay: it keeps its price. Not listed on Mercari: it follows General.
        self.assertEqual(price_for(published.listing_json, "ebay"), 38)
        self.assertEqual(price_for(published.listing_json, "mercari"), 33)
        locks = locked_paths(published.listing_json)
        self.assertIn("ebay_specifics.price", locks)
        for market in ("mercari", "depop", "etsy"):
            self.assertNotIn(f"{market}_specifics.price", locks)

    def _live_on_ebay(self, conv_id: str) -> None:
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=conv_id, marketplace="ebay", state="published",
            listing_id="1234567890", listing_url="https://www.ebay.com/itm/1234567890",
            listed_at=_now() - timedelta(days=5), listed_price=38.0,
        ))
        self.db.commit()

    def test_relist_without_a_recorded_live_price_starts_from_the_marketplace_price(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        conv, rev, before = self._stale_poshmark_draft(general=30.0, listed_price=None, drop=5)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        # 38 on Poshmark minus the $5 drop, never the $30 General price minus it.
        self.assertEqual(price_for(published.listing_json, "poshmark"), 33)

    def test_failed_delist_leaves_the_saved_prices_as_they_were(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        conv, rev, before = self._stale_poshmark_draft(general=30.0, listed_price=38.0, drop=5)

        outcomes, listed = self._relist(delist_ok=False)

        self.assertEqual([o["status"] for o in outcomes], ["failed"])
        self.assertEqual(listed, [])
        current = self.db.query(Listing).filter_by(conversation_id=conv.id).one()
        saved = self.db.get(ListingRevision, current.current_revision_id)
        assert saved is not None
        self.assertEqual(saved.listing_json.get("price"), 30.0)
        self.assertEqual({m: price_for(saved.listing_json, m) for m in before}, before)
        self.assertNotIn("poshmark_specifics.poshmarkPrice", saved.listing_json.get("seller_fields") or [])


    def _poshmark_form_draft(self, *, general: float, listed_price: float, locked_poshmark: float | None = None):
        from anteroom.services import native_contract

        extra: dict = {native_contract.KEY: {"poshmark": {"fields": [
            {"path": "poshmark_specifics.brand", "label": "Brand", "input_type": "text"},
        ]}}, "poshmark_specifics": {"brand": "Patagonia"}}
        if locked_poshmark is not None:
            extra["poshmark_specifics"]["poshmarkPrice"] = locked_poshmark
            extra["seller_fields"] = ["poshmark_specifics.poshmarkPrice"]
        conv, _listing, rev = self._create_draft("Form Coat", price=general, **extra)
        self.assertIn(native_contract.KEY, rev.listing_json)
        old_id = "5f00000000000000000000e1"
        self.db.add(MarketplaceListing(
            id=new_id(), conversation_id=conv.id, marketplace="poshmark", state="published",
            listing_id=old_id, listing_url=f"https://poshmark.com/listing/{old_id}",
            listed_at=_now() - timedelta(days=45), listed_price=listed_price,
        ))
        self.db.commit()
        auto_relist.save_rule(self.db, "poshmark", {
            "enabled": True, "min_age_days": 30, "daily_cap": 5,
            "price_adjust_mode": "amount", "price_adjust_value": 5, "price_floor": 1.0,
        }, confirm=True)
        return conv

    def test_relist_price_survives_a_listing_with_captured_marketplace_forms(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        self._poshmark_form_draft(general=30.0, listed_price=38.0)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(price_for(published.listing_json, "poshmark"), 33)
        self.assertEqual(published.listing_json["price"], 33.0)
        self.assertEqual(published.listing_json["poshmark_specifics"]["brand"], "Patagonia")

    def test_relist_price_replaces_a_price_the_seller_locked_on_that_marketplace(self):
        from anteroom.services.seller_overrides import price_for

        self._pricing_rules_on()
        self._poshmark_form_draft(general=30.0, listed_price=40.0, locked_poshmark=40.0)

        outcomes, listed = self._relist()

        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        published = self.db.get(ListingRevision, listed[0])
        assert published is not None
        self.assertEqual(price_for(published.listing_json, "poshmark"), 35)


    def test_one_relist_pass_stops_starting_relists_when_its_time_is_up(self):
        old_time = _now() - timedelta(days=45)
        for index in range(3):
            conv, _listing, _rev = self._create_draft(f"Budget Coat {index}", price=50.0)
            listing_id = f"5f00000000000000000000c{index}"
            self.db.add(MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace="poshmark", state="published",
                listing_id=listing_id, listing_url=f"https://poshmark.com/listing/{listing_id}",
                listed_at=old_time + timedelta(hours=index), listed_price=50.0,
            ))
        self.db.commit()
        auto_relist.save_rule(self.db, "poshmark", {"enabled": True, "min_age_days": 30, "daily_cap": 10}, confirm=True)
        delisted: list[str] = []

        async def delister(conv_id: str, market: str) -> dict:
            delisted.append(conv_id)
            return {"ok": True}

        async def lister(conv_id: str, market: str, rev_id: str) -> dict:
            return {"ok": True, "listing_id": f"5f0000000000000000000{len(delisted):03d}"}

        # A pass whose time is already up finishes the relist it started and leaves the rest.
        outcomes = asyncio.run(auto_relist.run_auto_relist(
            now=_now(), delister=delister, lister=lister, time_budget=timedelta(0),
        ))
        self.assertEqual([o["status"] for o in outcomes], ["completed"])
        self.assertEqual(len(delisted), 1)

        # The next pass picks up where it stopped.
        outcomes = asyncio.run(auto_relist.run_auto_relist(
            now=_now(), delister=delister, lister=lister,
        ))
        self.assertEqual([o["status"] for o in outcomes], ["completed", "completed"])
        self.assertEqual(len(set(delisted)), 3)

    def test_the_scheduler_gives_each_relist_pass_a_time_budget(self):
        from unittest.mock import AsyncMock

        from anteroom.services import automation_scheduler

        with patch("anteroom.services.auto_relist.run_auto_relist", AsyncMock(return_value=[])) as run:
            asyncio.run(automation_scheduler._run_auto_relist(_now()))
        budget = run.await_args.kwargs.get("time_budget")
        self.assertIsNotNone(budget)
        self.assertLessEqual(budget, timedelta(minutes=5))


    def test_a_short_pass_relists_the_oldest_listing_whatever_its_marketplace(self):
        old_time = _now() - timedelta(days=40)
        for market, age, listing_id, url in (
            ("poshmark", timedelta(0), "5f00000000000000000000d1", "https://poshmark.com/listing/5f00000000000000000000d1"),
            ("mercari", timedelta(days=10), "m12345678902", "https://www.mercari.com/us/item/m12345678902/"),
        ):
            conv, _listing, _rev = self._create_draft(f"{market} coat", price=50.0)
            self.db.add(MarketplaceListing(
                id=new_id(), conversation_id=conv.id, marketplace=market, state="published",
                listing_id=listing_id, listing_url=url,
                listed_at=old_time - age, listed_price=50.0,
            ))
            self.db.commit()
            auto_relist.save_rule(self.db, market, {"enabled": True, "min_age_days": 30, "daily_cap": 10}, confirm=True)
        delisted: list[str] = []

        async def delister(conv_id: str, market: str) -> dict:
            delisted.append(market)
            return {"ok": True}

        async def lister(conv_id: str, market: str, rev_id: str) -> dict:
            return {"ok": True, "listing_id": f"new-{market}"}

        outcomes = asyncio.run(auto_relist.run_auto_relist(
            now=_now(), delister=delister, lister=lister, time_budget=timedelta(0),
        ))
        self.assertEqual(delisted, ["mercari"], outcomes)


if __name__ == "__main__":
    unittest.main()

