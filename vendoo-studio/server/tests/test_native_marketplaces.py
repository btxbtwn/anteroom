from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.api_job_lock import api_job_is_live, claim_api_job, release_api_job
from vendoo_studio.services.browser_bridge import BrowserBridgeError
from vendoo_studio.services.job_watchdog import fail_stalled_jobs
from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

URLS = {"mercari": "https://www.mercari.com/sell/draft/testNativeDraft/",
        "depop": "https://www.depop.com/sellinghub/drafts/edit/123456/"}
LIVE = {"mercari": "https://www.mercari.com/us/item/m123456789/", "depop": None}
POSH_URL = "https://poshmark.com/listing/6adc371572e62463d992ffef"
SELLWILD_URL = "https://sellwild.com/product/123/test"
LISTING = {"title": "ANTEROOM DRAFT CHECK - DO NOT PUBLISH", "description": "Private test draft.", "price": 12,
           "weight_oz": 8, "package_dimensions_in": "12x9x2",
           "marketplace_category_ids": {"ebay": "11450", "poshmark": "5c3c3d4a6a2b3c4d5e6f7081", "mercari": "1934", "depop": "menswear_tshirts"},
           "depop_specifics": {"parcelSize": "Medium"}}
SELLWILD = {"sellwild_category_id": 272, "sellwild_condition_id": 2}


class NativeMarketplacesTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        self.db = self.sessions()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title=LISTING["title"])
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, LISTING, "model")
        ConversationRepo(self.db).add_photo(self.conv.id, "test.jpg", "test.jpg", "image/jpeg", 1)
        self.base = f"/api/conversations/{self.conv.id}/native-marketplaces"
        self.bridge = self.enterContext(patch("vendoo_studio.services.native_marketplaces.browser_bridge.request", new_callable=AsyncMock))
        self.manager = SimpleNamespace(connected=True, build="test-build", send_message=AsyncMock(return_value=True))
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", self.manager))
        self.enterContext(patch("vendoo_studio.services.native_marketplaces.expected_extension_build", return_value="test-build"))
        self.enterContext(patch("vendoo_studio.routes.extension.schedule_advance_job_queue"))
        self.enterContext(patch("vendoo_studio.database.SessionLocal", self.sessions))
        self.enterContext(patch.object(native, "SETTLE_POLL_S", 0))
        self.launched = []
        self.enterContext(patch.object(native, "_launch", self.launched.append))

    def tearDown(self):
        for coro in self.launched:
            coro.close()
        app.dependency_overrides.clear()
        self.db.close()

    # --- helpers ---------------------------------------------------------------

    def run_jobs(self):
        while self.launched:
            asyncio.run(self.launched.pop(0))
        self.db.expire_all()

    def list(self, platform="mercari", **extra):
        response = self.client.post(f"{self.base}/{platform}/list", json={"revision_id": self.rev.id, "confirm": True, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["running"])
        self.run_jobs()
        return self.status()[platform]

    def status(self):
        return self.client.get(self.base).json()

    def test_a_poshmark_size_and_a_saved_category_are_settled_before_list(self):
        preview = {
            "ok": True,
            "categories": {"poshmark": "dept/shirts/tees", "depop": "menswear_tshirts"},
            "gaps": {
                "poshmark": [{"field": "size", "message": "Poshmark needs a size Poshmark offers in this category.", "blocking": True}],
                "depop": [{"field": "depop_specifics.parcelSize", "message": 'Depop does not offer "Medium" as a parcel size.', "blocking": True}],
            },
            "parcels_checked": True,
        }
        with patch("vendoo_studio.services.chrome_catalog.live_preview", new=AsyncMock(return_value=preview)), \
             patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=AsyncMock(return_value={})):
            body = self.client.get(f"{self.base}/preflight").json()
        self.assertFalse(body["marketplaces"]["poshmark"]["ready"])
        self.assertFalse(body["marketplaces"]["depop"]["ready"])
        self.db.expire_all()
        saved = ListingRepo(self.db).get_revision(self.rev.id).listing_json
        self.assertEqual(saved["direct_category_ids"]["poshmark"], "dept/shirts/tees")
        self.assertEqual(saved["direct_category_ids"]["depop"], "menswear_tshirts")
        self.assertEqual(saved["marketplace_category_ids"]["poshmark"], LISTING["marketplace_category_ids"]["poshmark"])
        self.assertEqual(saved["marketplace_category_ids"]["depop"], LISTING["marketplace_category_ids"]["depop"])

    def record(self, platform):
        return MarketplaceListingRepo(self.db).get(self.conv.id, platform)

    def actions(self):
        return [call.args[2]["action"] for call in self.bridge.call_args_list if call.args[1] == "marketplace.execute"]

    def published(self, platform):
        return {"ok": True, "state": "published", "draft_url": URLS[platform], **({"listing_url": LIVE[platform]} if LIVE[platform] else {})}

    # --- listing -----------------------------------------------------------------

    def test_list_saves_then_publishes_in_one_job_that_holds_the_slot(self):
        for platform in URLS:
            seen = []

            async def bridge(job, kind, payload, timeout=None, platform=platform, seen=seen):
                seen.append([j.id for j in JobRepo(self.sessions()).get_running()])
                return ({"ok": True, "state": "draft", "draft_url": URLS[platform]} if payload["action"] == "save_draft"
                        else self.published(platform))

            self.bridge.side_effect = bridge
            self.bridge.reset_mock()
            record = self.list(platform)
            self.assertEqual(record["state"], "published", record)
            self.assertEqual(self.actions(), ["save_draft", "publish"])
            calls = self.bridge.call_args_list
            self.assertFalse(calls[0].args[2]["confirm"])
            self.assertTrue(calls[1].args[2]["confirm"])
            self.assertEqual(calls[1].args[2]["draft_url"], URLS[platform])
            # Each step is journaled under its own id, and one job held the slot for both.
            self.assertEqual(calls[0].args[2]["operation_id"], record["job_id"])
            self.assertEqual(calls[1].args[2]["operation_id"], f"{record['job_id']}-2")
            self.assertEqual(seen, [[record["job_id"]], [record["job_id"]]])
            self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")
            self.assertFalse(api_job_is_live(record["job_id"]))

    def test_list_does_not_publish_when_preparation_fails(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Mercari needs a brand.", "error_code": "missing_fields",
                                    "fields": [{"field": "brand", "message": "Mercari needs a brand."}],
                                    "marketplace_message": "Brand is required"}
        record = self.list()
        self.assertEqual(record["state"], "not_saved")
        self.assertEqual(self.actions(), ["save_draft"])
        # A definite refusal says why, without the "unconfirmed" warning.
        self.assertEqual(record["error"], "Mercari needs a brand.")
        self.assertEqual((record["error_code"], record["marketplace_message"]), ("missing_fields", "Brand is required"))
        self.assertEqual(record["error_fields"], [{"field": "brand", "message": "Mercari needs a brand."}])
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "failed")
        events = [event.payload for event in JobRepo(self.db).get_events(record["job_id"]) if event.event_type == "native_result"]
        self.assertEqual(events[-1]["marketplace_message"], "Brand is required")

    def test_list_finishes_the_current_draft_without_creating_another(self):
        self.bridge.side_effect = [{"ok": False, "safe_to_retry": True, "draft_url": URLS["depop"], "error": "Finish photos."}]
        self.list("depop")
        self.assertEqual(self.record("depop")["state"], "draft")
        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"revision_id": self.rev.id, "error": None})
        self.bridge.reset_mock()
        self.bridge.side_effect = [self.published("depop")]
        self.assertEqual(self.list("depop")["state"], "published")
        self.assertEqual(self.actions(), ["publish"])
        self.assertEqual(self.bridge.call_args.args[2]["draft_url"], URLS["depop"])

    def test_list_requires_confirmation_before_any_write(self):
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id}).status_code, 400)
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)

    def test_save_and_publish_are_not_separate_endpoints(self):
        for path in ("draft", "publish"):
            self.assertIn(self.client.post(f"{self.base}/mercari/{path}", json={"revision_id": self.rev.id, "confirm": True}).status_code, {404, 405})

    def test_poshmark_lists_in_one_step_and_updates_the_same_listing(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        record = self.list("poshmark")
        self.assertEqual((record["state"], record["listing_url"]), ("published", POSH_URL))
        self.assertEqual(self.actions(), ["list"])
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        revised = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "title": "Updated tee"}, "user")
        self.rev = revised
        self.assertEqual(self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id}).status_code, 400)
        self.bridge.reset_mock()
        response = self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.run_jobs()
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["listing_id"], payload["confirm"]), ("update", "6adc371572e62463d992ffef", True))
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.run_jobs()
        refused = self.record("poshmark")
        self.assertEqual(refused["state"], "published")
        self.assertIn("size", refused["error"])
        mercari = self.client.post(f"{self.base}/mercari/update", json={"revision_id": revised.id, "confirm": True})
        self.assertEqual(mercari.status_code, 409)
        self.assertIn("cannot be updated", mercari.text)

    def test_a_refused_poshmark_listing_can_be_tried_again(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.assertEqual(self.list("poshmark")["state"], "not_saved")
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.assertEqual(self.list("poshmark")["state"], "published")

    def test_sellwild_records_the_choice_then_lists_once(self):
        self.bridge.return_value = {"ok": True, "state": "pending_review", "listing_url": SELLWILD_URL}
        record = self.list("sellwild", **SELLWILD)
        self.assertEqual(record["state"], "pending_review")
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["confirm"]), ("publish", True))
        self.assertEqual(payload["sellwild"], {"category_id": 272, "condition_id": 2})
        self.assertTrue(payload["local_draft_id"])
        self.assertEqual(self.client.post(f"{self.base}/sellwild/list", json={"revision_id": self.rev.id, "confirm": True, **SELLWILD}).status_code, 409)
        self.assertEqual(self.bridge.call_count, 1)

    def test_sellwild_needs_a_category_and_condition(self):
        for extra in ({"sellwild_category_id": 183, "sellwild_condition_id": 2}, {"sellwild_category_id": 272, "sellwild_condition_id": 9}):
            response = self.client.post(f"{self.base}/sellwild/list", json={"revision_id": self.rev.id, "confirm": True, **extra})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertFalse(body["running"])
            self.assertEqual(body["error_code"], "missing_fields")
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)

    def test_sellwild_match_prefers_marketplace_categories_and_ignores_sleeve_words(self):
        tee = {"title": "Graphic tee", "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
               "marketplace_category_objects": {"poshmark": {"displayPath": ["Men", "Shirts", "Tees - Short Sleeve"]}}}
        self.assertEqual(native.suggest_sellwild(tee)["category_id"], 270)
        self.assertEqual(native.suggest_sellwild({"title": "Kids hoodie", "category_path": "Clothing, Shoes & Accessories > Kids > Boys"})["category_id"], 246)
        self.assertEqual(native.suggest_sellwild({"title": "Jacket", "sellwild_specifics": {"nativeCategoryId": 269}})["category_id"], 269)

    def test_etsy_uses_the_listing_package(self):
        self.assertEqual(native.listing_package({"weight_lb": 1, "weight_oz": 4, "package_dimensions_in": "13x10x3"}),
                         {"item_weight": 20.0, "item_length": 13.0, "item_width": 10.0, "item_height": 3.0,
                          "item_weight_unit": "oz", "item_dimensions_unit": "in"})
        self.assertIsNone(native.listing_package({"weight_oz": 0, "package_dimensions_in": "13x10x3"}))

    def test_sellwild_options_suggest_from_the_listing(self):
        listing = {**LISTING, "title": "Vintage Patagonia fleece jacket", "condition": "Pre-Owned - Good",
                   "marketplace_category_ids": {"ebay": "11450"},
                   "marketplace_category_objects": {"sellwild": {"displayPath": ["Fashion", "Mens Clothing"]}}}
        listing.pop("depop_specifics", None)
        ListingRepo(self.db).save_revision(self.conv.id, listing, "model")
        result = self.client.get(f"{self.base}/sellwild/options").json()
        self.assertEqual(result["suggested"], {"category_id": 272, "condition_id": 3})
        self.assertEqual(len(result["conditions"]), 5)

    def test_a_deleted_depop_draft_is_forgotten_and_a_replacement_is_kept(self):
        old = URLS["depop"]
        new = "https://www.depop.com/sellinghub/drafts/edit/newdraft1/"
        record = {"state": "draft", "draft_url": old, "revision_id": "current"}
        gone = native._outcome("depop", "publish", record, {
            "ok": False, "safe_to_retry": True, "error": "Depop refused the request (HTTP 404).",
            "marketplace_message": "Could not find draft with id: [hidden]", "error_code": "rejected",
            "draft_url": old,
        })
        self.assertIsNone(gone["draft_url"])
        self.assertEqual(gone["state"], "not_saved")
        kept = native._outcome("depop", "save_draft", record, {
            "ok": True, "state": "draft", "draft_url": new, "draft_replaced": True,
        })
        self.assertEqual(kept["draft_url"], new)
        with self.assertRaises(native.NativeMarketplaceError):
            native._outcome("depop", "save_draft", record, {"ok": True, "state": "draft", "draft_url": new})
        refused = native._outcome("depop", "save_draft", record, {
            "ok": False, "safe_to_retry": True, "draft_replaced": True, "draft_url": new,
            "error": "Depop refused the request (HTTP 400).", "error_code": "rejected",
            "marketplace_message": "parcel_size is required",
        })
        self.assertEqual(refused["draft_url"], new)
        self.assertEqual(refused["state"], "draft")
        self.assertIn("HTTP 400", refused["error"])
        self.assertEqual(refused["marketplace_message"], "parcel_size is required")
        unrelated = native._outcome("depop", "save_draft", record, {
            "ok": False, "safe_to_retry": True, "draft_url": new,
            "error": "Depop refused the request (HTTP 400).", "error_code": "rejected",
        })
        self.assertNotIn("draft_url", unrelated)
        self.assertEqual(unrelated["state"], "draft")
        self.assertIn("HTTP 400", unrelated["error"])
        self.assertNotEqual(unrelated["state"], "unknown")

    def test_listing_again_replaces_a_deleted_depop_draft_in_the_same_list(self):
        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {
            "state": "draft", "draft_url": URLS["depop"], "revision_id": self.rev.id,
        })
        new = "https://www.depop.com/sellinghub/drafts/edit/newdraft1/"
        self.bridge.side_effect = [
            {"ok": False, "safe_to_retry": True, "error": "Depop refused the request (HTTP 404).",
             "marketplace_message": "Could not find draft with id: [hidden]", "error_code": "rejected"},
            {"ok": True, "state": "draft", "draft_url": new},
            {"ok": True, "state": "published", "draft_url": new},
        ]
        record = self.list("depop")
        self.assertEqual(record["state"], "published")
        self.assertEqual(record["draft_url"], new)
        actions = [call.args[2]["action"] for call in self.bridge.call_args_list]
        self.assertEqual(actions, ["publish", "save_draft", "publish"])
        self.assertEqual(self.bridge.call_args_list[0].args[2]["draft_url"], URLS["depop"])
        self.assertIsNone(self.bridge.call_args_list[1].args[2]["draft_url"])

    def test_listing_links_are_validated_for_every_marketplace(self):
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]},
                                   {"ok": True, "state": "published", "draft_url": URLS["mercari"], "listing_url": "https://www.mercari.com/us/search/"}]
        record = self.list()
        self.assertEqual(record["state"], "unknown")
        self.assertIn("invalid listing link", record["error"])
        self.bridge.side_effect = None
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": "https://poshmark.com/closet/someone"}
        self.assertEqual(self.list("poshmark")["state"], "unknown")
        self.assertEqual(native.listing_url("mercari", "https://www.mercari.com/us/item/m123456789/?ref=x"),
                         "https://www.mercari.com/us/item/m123456789/")

    # --- lost replies, check, reset ------------------------------------------------

    def test_a_late_chrome_reply_keeps_the_slot_until_chrome_reports_then_a_queued_send_can_start(self):
        replies = iter([{"finished": False}, {"finished": True, "result": self.published("mercari")}])
        running_during_wait = []

        async def bridge(job, kind, payload, timeout=None):
            if kind == "marketplace.execute":
                if payload["action"] == "save_draft":
                    # The seller approves a Vendoo Send while the List runs: it waits in the queue.
                    queued.append(JobRepo(self.sessions()).create(self.conv.id, self.rev.id, LISTING, status="queued",
                                                                  current_step="vendoo_api_queued").id)
                    return {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
                raise BrowserBridgeError("Chrome did not answer in time.")
            running_during_wait.append([j.current_step for j in JobRepo(self.sessions()).get_running()])
            return next(replies)

        queued = []
        self.bridge.side_effect = bridge
        record = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).json()
        self.run_jobs()
        # Chrome was told to stop the late step, by the id it journals it under.
        cancel = self.manager.send_message.call_args_list[0].args[0]
        self.assertEqual((cancel["type"], cancel["job_id"]), ("job.cancel", f"{record['job_id']}-2"))
        self.assertEqual(running_during_wait, [["native_mercari_publish"], ["native_mercari_publish"]])
        self.assertEqual(self.record("mercari")["state"], "published")
        # Only now is the slot free for the queued Send.
        self.assertFalse(JobRepo(self.db).get_running())
        self.assertEqual([job.id for job in JobRepo(self.db).get_dispatchable()], queued)

    def test_no_journal_is_surfaced_and_the_seller_can_reset(self):
        async def bridge(job, kind, payload, timeout=None):
            if kind == "marketplace.execute":
                raise BrowserBridgeError("lost")
            return {"finished": False}

        self.bridge.side_effect = bridge
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            record = self.list()
        self.assertEqual((record["state"], record["error_code"]), ("unknown", "uncertain"))
        self.assertIn("Chrome has not said whether this Mercari action finished", record["error"])
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        checked = self.client.post(f"{self.base}/mercari/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["state"], "unknown")
        self.assertIn("I checked it myself", checked.json()["error"])
        self.assertEqual(self.client.post(f"{self.base}/mercari/reset", json={}).status_code, 400)
        writes = self.actions()
        reset = self.client.post(f"{self.base}/mercari/reset", json={"confirm": True})
        self.assertEqual(reset.json()["state"], "not_saved", reset.text)
        self.assertIsNone(reset.json().get("error"))
        self.assertEqual(self.actions(), writes)
        events = [event for event in JobRepo(self.db).get_events(record["job_id"]) if event.event_type == "native_reset"]
        self.assertEqual(events[0].payload, {"marketplace": "mercari", "from": "unknown", "to": "not_saved"})
        self.assertEqual(self.client.post(f"{self.base}/mercari/reset", json={"confirm": True}).status_code, 409)
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]}, self.published("mercari")]
        self.assertEqual(self.list()["state"], "published")

    def test_check_reads_the_journal_and_verifies_the_job(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            record = self.list("poshmark")
        self.assertEqual(record["state"], "unknown")
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}
        checked = self.client.post(f"{self.base}/poshmark/check").json()
        self.assertEqual(checked["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertEqual(self.bridge.call_args.args[2]["context"]["action"], "list")
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")
        jobs = self.client.get(f"/api/jobs?conversation_id={self.conv.id}").json()
        self.assertTrue(all(job["mode"] == "native_marketplace" for job in jobs))

    def test_a_pasted_draft_link_is_checked_without_repeating_the_write(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            self.list()
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}}
        result = self.client.post(f"{self.base}/mercari/check", json={"draft_url": URLS["mercari"]}).json()
        self.assertEqual(result["state"], "draft", result)
        self.assertEqual(self.bridge.call_args.args[2]["context"]["draft_url"], URLS["mercari"])

    def test_cancel_stops_a_running_list_before_it_publishes(self):
        started = []

        async def bridge(job, kind, payload, timeout=None):
            started.append(payload["action"])
            # The seller presses Cancel while the draft saves.
            self.assertEqual(self.client.post(f"{self.base}/mercari/cancel").status_code, 200)
            return {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}

        self.bridge.side_effect = bridge
        response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).json()
        self.run_jobs()
        self.assertEqual(started, ["save_draft"])
        self.assertEqual(self.record("mercari")["state"], "draft")
        self.assertEqual(JobRepo(self.db).get(response["job_id"]).status, "cancelled")
        sent = self.manager.send_message.call_args.args[0]
        self.assertEqual((sent["type"], sent["job_id"]), ("job.cancel", response["job_id"]))
        self.assertEqual(self.client.post(f"{self.base}/mercari/cancel").status_code, 409)

    def test_an_ebay_list_whose_reply_was_lost_is_checked_before_the_job_lets_go(self):
        from vendoo_studio.services import ebay_listings

        async def execute(action, snapshot, photos, binding, record, active, **kwargs):
            record({"ebay_sku": "ar-x", "ebay_offer_id": "9", "ebay_inventory_written": True})
            return {"ok": False, "safe_to_retry": False, "error": "eBay did not confirm the listing.", "error_code": "uncertain"}

        checked = AsyncMock(return_value={"ok": True, "state": "published", "listing_url": "https://www.ebay.com/itm/123456789012"})
        with patch("vendoo_studio.services.ebay_client.EbayClient.connect", AsyncMock()), \
             patch.object(ebay_listings, "execute", execute), patch.object(ebay_listings, "check", checked):
            record = self.list("ebay")
        self.assertEqual((record["state"], record["listing_url"]), ("published", "https://www.ebay.com/itm/123456789012"))
        self.assertEqual(checked.await_args.args[0]["ebay_offer_id"], "9")
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")

    def test_a_refused_ebay_publish_leaves_a_resumable_draft_with_its_ids(self):
        from vendoo_studio.services import ebay_listings

        async def execute(action, snapshot, photos, binding, record, active, **kwargs):
            record({"ebay_sku": "ar-x", "ebay_offer_id": "9", "ebay_inventory_written": True})
            return {"ok": False, "safe_to_retry": True, "error": "eBay returned HTTP 400.", "error_code": "rejected",
                    "marketplace_message": "25002: Color is missing."}

        with patch("vendoo_studio.services.ebay_client.EbayClient.connect", AsyncMock()), patch.object(ebay_listings, "execute", execute):
            record = self.list("ebay")
            self.assertEqual((record["state"], record["ebay_offer_id"], record["error_code"]), ("draft", "9", "rejected"))
            self.assertEqual(record["marketplace_message"], "25002: Color is missing.")
            # List again is allowed and starts from the same offer.
            self.assertEqual(self.client.post(f"{self.base}/ebay/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 200)

    # --- records outlive the editor ---------------------------------------------------

    def test_clear_and_regenerate_keep_records_and_stale_notes_cannot_overwrite_them(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        record = self.list("poshmark")
        stale = merge_notes(self.conv.notes, {"nativeMarketplaceDrafts": {"poshmark": {"state": "not_saved"}}, "brand": "Levi"})
        self.assertEqual(self.client.patch(f"/api/conversations/{self.conv.id}", json={"notes": stale}).status_code, 200)
        self.assertEqual(self.record("poshmark")["state"], "published")
        for body in ({"keep_inputs": True}, {}):
            response = self.client.post(f"/api/conversations/{self.conv.id}/reset", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.db.expire_all()
            self.assertEqual(self.record("poshmark")["listing_url"], POSH_URL)
            # The job the record names stays, so Check can still read its snapshot.
            self.assertIsNotNone(JobRepo(self.db).get(record["job_id"]))
        self.assertEqual(self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]["listing_id"],
                         "6adc371572e62463d992ffef")
        claim_api_job(record["job_id"])
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "updating"})
        try:
            self.assertEqual(self.client.post(f"/api/conversations/{self.conv.id}/reset", json={}).status_code, 409)
        finally:
            release_api_job(record["job_id"])

    def test_deleting_the_item_deletes_its_records(self):
        from vendoo_studio.services.listing_delete import delete_listing

        MarketplaceListingRepo(self.db).save(self.conv.id, "ebay", {"state": "published"})
        delete_listing(self.db, self.conv.id)
        self.assertEqual(MarketplaceListingRepo(self.db).for_conversation(self.conv.id), {})

    def test_a_failed_direct_listing_does_not_relabel_the_item(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.list("poshmark")
        ConversationRepo(self.db).reconcile_job_statuses()
        self.db.refresh(self.conv)
        self.assertNotEqual(self.conv.status, "failed")

    # --- the slot, restarts and reconnects ------------------------------------------------

    def test_inspection_is_read_only_and_holds_the_slot(self):
        seen = []

        async def bridge(job, kind, payload, timeout=None):
            seen.append(([j.current_step for j in JobRepo(self.sessions()).get_running()], kind))
            return {"ok": True, "fields": [], "scripts": []}

        self.bridge.side_effect = bridge
        self.assertEqual(self.client.post(f"{self.base}/mercari/inspect", json={}).status_code, 200)
        self.assertEqual(seen, [(["native_mercari_inspect"], "marketplace.inspect")])
        self.assertFalse(JobRepo(self.db).get_running())
        self.assertEqual(self.client.post(f"{self.base}/unknown/inspect", json={}).status_code, 422)

    def test_old_bridge_is_rejected_before_making_a_job(self):
        with patch("vendoo_studio.services.native_marketplaces.expected_extension_build", return_value="new-build"):
            result = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 409)
        self.assertFalse(JobRepo(self.db).list_all())

    def test_other_jobs_block_native_actions(self):
        JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING)
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.bridge.assert_not_called()

    def test_restart_marks_cut_off_actions_unconfirmed_and_reconcile_reads_them(self):
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_poshmark_list")
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "publishing", "action": "list", "job_id": job.id,
                                                                       "operation_id": job.id})
        repo.requeue_interrupted()
        self.assertEqual(job.status, "failed")
        self.assertFalse(repo.requeue_interrupted_send(job.id))
        self.assertEqual(self.record("poshmark")["state"], "unknown")
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}
        # Startup reconciles eBay and Etsy only; Chrome's records wait for Chrome.
        self.assertEqual(asyncio.run(native.reconcile(chrome=False)), 0)
        self.assertEqual(asyncio.run(native.reconcile(chrome=True)), 1)
        self.db.expire_all()
        self.assertEqual(self.record("poshmark")["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertFalse(any(call.args[1] == "marketplace.execute" for call in self.bridge.call_args_list))

    def test_the_watchdog_fails_dead_native_jobs_and_leaves_live_ones_alone(self):
        repo = JobRepo(self.db)
        dead = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_save_draft")
        dead.updated_at = utcnow() - timedelta(seconds=10)
        live = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_depop_publish")
        live.updated_at = utcnow() - timedelta(minutes=30)
        self.db.commit()
        MarketplaceListingRepo(self.db).save(self.conv.id, "mercari", {"state": "saving", "job_id": dead.id})
        claim_api_job(live.id)
        try:
            self.assertEqual(fail_stalled_jobs(self.db), [dead.id])
            self.assertEqual(live.status, "dispatched")
        finally:
            release_api_job(live.id)
        self.assertEqual(self.record("mercari")["state"], "unknown")
        self.assertFalse(repo.get_dispatchable())

    def test_reconnect_keeps_a_native_action_running_in_this_process(self):
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        claim_api_job(job.id)
        try:
            repo.requeue_interrupted()
            self.assertEqual(job.status, "dispatched")
        finally:
            release_api_job(job.id)


class MigrationFromNotesTest(unittest.TestCase):
    def test_records_move_out_of_notes(self):
        import importlib.util
        from pathlib import Path

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        import vendoo_studio

        path = next((Path(vendoo_studio.__file__).parent / "migrations" / "versions").glob("292cc5d3b9a5_*.py"))
        spec = importlib.util.spec_from_file_location("migration_292cc5d3b9a5", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in ("conversations",)])
        db = sessionmaker(bind=engine)()
        conv = ConversationRepo(db).create(title="Old item")
        other = ConversationRepo(db).create(title="Untouched")
        conv.notes = merge_notes(None, {"brand": "Levi", "nativeMarketplaceDrafts": {
            "ebay": {"state": "published", "job_id": "j1", "ebay_sku": "arabc", "ebay_offer_id": "1", "ebay_inventory_written": True,
                     "ebay_image_urls": ["https://i.ebayimg.com/a.jpg"], "listing_url": "https://www.ebay.com/itm/123456789012"},
            "etsy": {"state": "draft", "transport": "etsy_api", "etsy_image_ids": {"p1": "100"}, "etsy_photo_ids": ["p1"],
                     "etsy_photos_complete": True, "draft_url": "https://www.etsy.com/your/shops/me/listing-editor/edit/1"},
            "sellwild": {"state": "draft", "storage": "local", "sellwild_category_id": 272, "local_draft_id": "abc"},
            "mercari": {"state": "unknown", "job_id": "j2", "error": "lost"}, "vinted": {"state": "draft"}}})
        other.notes = merge_notes(None, {"brand": "Lee"})
        db.commit()
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        db.expire_all()
        records = MarketplaceListingRepo(db).for_conversation(conv.id)
        self.assertEqual(set(records), {"ebay", "etsy", "sellwild", "mercari"})
        self.assertEqual(records["ebay"]["ebay_images"], [{"photo_id": None, "checksum": None, "url": "https://i.ebayimg.com/a.jpg"}])
        self.assertTrue(records["ebay"]["ebay_inventory_written"])
        self.assertEqual(records["ebay"]["operation_id"], "j1")
        self.assertEqual(records["etsy"]["etsy_image_ids"], {"p1": "100"})
        self.assertNotIn("transport", records["etsy"])
        self.assertEqual(records["sellwild"]["sellwild_category_id"], 272)
        self.assertEqual((records["mercari"]["state"], records["mercari"]["error_code"]), ("unknown", "uncertain"))
        self.assertEqual(parse_notes(ConversationRepo(db).get(conv.id).notes), {"brand": "Levi"})
        self.assertEqual(parse_notes(ConversationRepo(db).get(other.id).notes), {"brand": "Lee"})
        db.close()
