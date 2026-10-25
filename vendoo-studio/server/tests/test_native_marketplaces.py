from __future__ import annotations

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
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services.native_marketplaces import UNKNOWN
from vendoo_studio.services.browser_bridge import BrowserBridgeError
from vendoo_studio.services.job_watchdog import fail_stalled_jobs

URLS = {"mercari": "https://www.mercari.com/sell/draft/testNativeDraft/",
        "depop": "https://www.depop.com/sellinghub/drafts/edit/123456/"}
LISTING = {"title": "ANTEROOM DRAFT CHECK - DO NOT PUBLISH", "description": "Private test draft.", "price": 12}


class NativeMarketplacesTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title=LISTING["title"])
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, LISTING, "model")
        ConversationRepo(self.db).add_photo(self.conv.id, "test.jpg", "test.jpg", "image/jpeg", 1)
        self.base = f"/api/conversations/{self.conv.id}/native-marketplaces"
        self.bridge = self.enterContext(patch("vendoo_studio.services.native_marketplaces.browser_bridge.request", new_callable=AsyncMock))
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=True, build="test-build")))
        self.enterContext(patch("vendoo_studio.services.native_marketplaces.expected_extension_build", return_value="test-build"))
        self.enterContext(patch("vendoo_studio.routes.extension.schedule_advance_job_queue"))

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def save(self, platform="mercari"):
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS[platform]}
        result = self.client.post(f"{self.base}/{platform}/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "draft")
        return result

    def publish_body(self, platform="mercari"):
        return {"revision_id": self.rev.id, "draft_url": URLS[platform], "confirm": True}

    def test_each_marketplace_saves_without_publishing(self):
        for platform in URLS:
            self.save(platform)
            payload = self.bridge.call_args.args[2]
            self.assertEqual(payload["action"], "save_draft")
            self.assertFalse(payload["confirm"])
            self.assertEqual(payload["marketplace"], platform)
        self.assertEqual(self.bridge.call_count, len(URLS))

    SELLWILD = {"sellwild_category_id": 272, "sellwild_condition_id": 2}

    def test_sellwild_saves_locally_without_chrome_or_a_job(self):
        with patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)):
            result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id, **self.SELLWILD})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["storage"], "local")
        self.assertEqual(result.json()["state"], "draft")
        self.assertEqual(result.json()["sellwild_category_id"], 272)
        self.assertNotIn("draft_url", result.json())
        self.assertNotIn("job_id", result.json())
        self.bridge.assert_not_called()
        self.assertFalse(JobRepo(self.db).list_all())

    def test_sellwild_save_requires_a_current_category_and_condition(self):
        for extra in ({}, {"sellwild_category_id": 183, "sellwild_condition_id": 2}, {"sellwild_category_id": 272, "sellwild_condition_id": 9}):
            result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id, **extra})
            self.assertEqual(result.status_code, 400, extra)

    def test_sellwild_saves_with_the_listing_own_category_when_none_is_chosen(self):
        listing = {**LISTING, "title": "Levi's 501 jeans", "condition": "Pre-Owned - Good",
                   "marketplace_category_objects": {"poshmark": {"displayPath": ["Men", "Jeans", "Straight"]}}}
        rev = ListingRepo(self.db).save_revision(self.conv.id, listing, "model")
        result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()["sellwild_category_id"], result.json()["sellwild_condition_id"]), (273, 2))

    def test_sellwild_match_prefers_marketplace_categories_and_ignores_sleeve_words(self):
        from vendoo_studio.services.native_marketplaces import suggest_sellwild

        tee = {"title": "Graphic tee", "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
               "marketplace_category_objects": {"poshmark": {"displayPath": ["Men", "Shirts", "Tees - Short Sleeve"]}}}
        self.assertEqual(suggest_sellwild(tee)["category_id"], 270)
        self.assertEqual(suggest_sellwild({"title": "Kids hoodie", "category_path": "Clothing, Shoes & Accessories > Kids > Boys"})["category_id"], 246)
        self.assertEqual(suggest_sellwild({"title": "Jacket", "sellwild_specifics": {"nativeCategoryId": 269}})["category_id"], 269)

    def test_etsy_uses_shop_defaults_and_the_listing_package(self):
        from vendoo_studio.services.native_marketplaces import listing_package

        self.assertEqual(listing_package({"weight_lb": 1, "weight_oz": 4, "package_dimensions_in": "13x10x3"}),
                         {"item_weight": 20.0, "item_length": 13.0, "item_width": 10.0, "item_height": 3.0,
                          "item_weight_unit": "oz", "item_dimensions_unit": "in"})
        self.assertIsNone(listing_package({"weight_oz": 0, "package_dimensions_in": "13x10x3"}))

    def test_sellwild_options_suggest_from_the_listing(self):
        listing = {**LISTING, "title": "Vintage Patagonia fleece jacket", "condition": "Pre-Owned - Good",
                   "marketplace_category_objects": {"sellwild": {"displayPath": ["Fashion", "Mens Clothing"]}}}
        ListingRepo(self.db).save_revision(self.conv.id, listing, "model")
        result = self.client.get(f"{self.base}/sellwild/options").json()
        self.assertEqual(result["suggested"], {"category_id": 272, "condition_id": 2})
        self.assertIn({"id": 270, "label": "Fashion > Tops & Shirts"}, result["categories"])
        self.assertEqual(len(result["conditions"]), 5)

    def prepare_sellwild(self):
        result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id, **self.SELLWILD})
        self.assertEqual(result.status_code, 200, result.text)

    def test_sellwild_publish_requires_local_save_revision_and_confirmation(self):
        body = {"revision_id": self.rev.id, "confirm": True}
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.prepare_sellwild()
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json={**body, "confirm": False}).status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json={**body, "revision_id": "stale"}).status_code, 409)
        self.bridge.assert_not_called()
        self.assertIn(self.client.post(f"{self.base}/sellwild/review", json=body).status_code, {404, 405})

    def test_sellwild_publish_sends_the_saved_choice_once(self):
        self.prepare_sellwild()
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": "https://sellwild.com/product/123/test"}
        body = {"revision_id": self.rev.id, "confirm": True}
        result = self.client.post(f"{self.base}/sellwild/publish", json=body)
        self.assertEqual(result.json()["state"], "published", result.text)
        self.assertEqual(result.json()["listing_url"], "https://sellwild.com/product/123/test")
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["confirm"]), ("publish", True))
        self.assertEqual(payload["sellwild"], {"category_id": 272, "condition_id": 2})

    def test_sellwild_reports_moderation_and_never_repeats_publishing(self):
        self.prepare_sellwild()
        self.bridge.return_value = {"ok": True, "state": "pending_review", "listing_url": "https://sellwild.com/product/123/test"}
        body = {"revision_id": self.rev.id, "confirm": True}
        result = self.client.post(f"{self.base}/sellwild/publish", json=body)
        self.assertEqual(result.json()["state"], "pending_review", result.text)
        self.assertTrue(self.bridge.call_args.args[2]["confirm"])
        count = self.bridge.call_count
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id, **self.SELLWILD}).status_code, 409)
        self.assertEqual(self.bridge.call_count, count)

    def test_sellwild_changed_photos_require_saving_again(self):
        self.prepare_sellwild()
        ConversationRepo(self.db).add_photo(self.conv.id, "second.jpg", "second.jpg", "image/jpeg", 1)
        result = self.client.post(f"{self.base}/sellwild/publish", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 409)
        self.bridge.assert_not_called()

    def test_uncertain_sellwild_publish_is_checked_without_another_write(self):
        self.prepare_sellwild()
        self.bridge.side_effect = BrowserBridgeError("lost reply")
        body = {"revision_id": self.rev.id, "confirm": True}
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 502)
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": "https://sellwild.com/product/123/test"}}
        result = self.client.post(f"{self.base}/sellwild/check")
        self.assertEqual(result.json()["state"], "published", result.text)
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertTrue(self.bridge.call_args.args[2]["context"]["local_draft_id"])

    POSH_URL = "https://poshmark.com/listing/6adc371572e62463d992ffef"

    def test_poshmark_lists_in_one_confirmed_step(self):
        self.assertEqual(self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id}).status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/poshmark/publish", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id}).status_code, 400)
        self.bridge.assert_not_called()
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": self.POSH_URL}
        result = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()["state"], result.json()["listing_url"]), ("published", self.POSH_URL))
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["confirm"]), ("list", True))
        count = self.bridge.call_count
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.assertEqual(self.bridge.call_count, count)

    def test_a_refused_poshmark_listing_can_be_tried_again(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size Poshmark offers in this category."}
        result = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).json()
        self.assertEqual(result["state"], "not_saved")
        self.assertIn("size", result["error"])
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": self.POSH_URL}
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).json()["state"], "published")

    def test_list_creates_and_publishes_in_one_confirmation(self):
        self.bridge.side_effect = [
            {"ok": True, "state": "draft", "draft_url": URLS["mercari"]},
            {"ok": True, "state": "published", "draft_url": URLS["mercari"],
             "listing_url": "https://www.mercari.com/us/item/m123456789/"},
        ]
        result = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "published")
        actions = [call.args[2]["action"] for call in self.bridge.call_args_list]
        self.assertEqual(actions, ["save_draft", "publish"])
        self.assertFalse(self.bridge.call_args_list[0].args[2]["confirm"])
        self.assertTrue(self.bridge.call_args_list[1].args[2]["confirm"])
        self.assertEqual(self.bridge.call_args_list[1].args[2]["draft_url"], URLS["mercari"])

    def test_list_does_not_publish_when_preparation_fails(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Mercari needs a brand."}
        result = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "not_saved")
        self.assertEqual(self.bridge.call_count, 1)
        self.assertEqual(self.bridge.call_args.args[2]["action"], "save_draft")

    def test_list_finishes_the_current_marketplace_record_without_creating_another(self):
        self.save("depop")
        self.bridge.reset_mock()
        self.bridge.return_value = {"ok": True, "state": "published", "draft_url": URLS["depop"]}
        result = self.client.post(f"{self.base}/depop/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.json()["state"], "published", result.text)
        self.assertEqual(self.bridge.call_count, 1)
        self.assertEqual(self.bridge.call_args.args[2]["action"], "publish")
        self.assertEqual(self.bridge.call_args.args[2]["draft_url"], URLS["depop"])

    def test_list_requires_confirmation_before_any_write(self):
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id}).status_code, 400)
        self.bridge.assert_not_called()

    def test_a_lost_list_reply_is_not_retried(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 502)
        self.assertEqual(self.client.get(self.base).json()["mercari"]["state"], "unknown")
        self.assertEqual(self.bridge.call_count, 1)
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.assertEqual(self.bridge.call_count, 1)

    def test_sellwild_list_records_the_choice_then_lists_once(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": "https://sellwild.com/product/123/test"}
        result = self.client.post(f"{self.base}/sellwild/list", json={"revision_id": self.rev.id, "confirm": True, **self.SELLWILD})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "published")
        self.assertEqual(self.bridge.call_count, 1)
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["confirm"]), ("publish", True))
        self.assertEqual(payload["sellwild"], {"category_id": 272, "condition_id": 2})

    def test_etsy_list_creates_then_activates_in_one_confirmation(self):
        etsy_draft = "https://www.etsy.com/your/shops/me/listing-editor/edit/123456/"
        with patch("vendoo_studio.services.native_marketplaces.etsy_listings.connected_shop_id", return_value="1"), \
             patch("vendoo_studio.services.native_marketplaces.etsy_listings.execute", new_callable=AsyncMock) as execute, \
             patch("vendoo_studio.services.user_settings.get_etsy_listing_defaults",
                   return_value={"shipping_profile_id": "10", "readiness_state_id": "20"}):
            execute.side_effect = [
                {"ok": True, "state": "draft", "draft_url": etsy_draft},
                {"ok": True, "state": "published", "draft_url": etsy_draft, "listing_url": "https://www.etsy.com/listing/123456"},
            ]
            result = self.client.post(f"{self.base}/etsy/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "published")
        self.assertEqual([call.args[0] for call in execute.call_args_list], ["save_draft", "publish"])
        self.assertIs(execute.call_args_list[1].kwargs["confirm"], True)

    def test_a_lost_poshmark_reply_is_unknown_until_checked(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 502)
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": self.POSH_URL}}
        self.assertEqual(self.client.post(f"{self.base}/poshmark/check").json()["state"], "published")
        self.assertEqual(self.bridge.call_args.args[2]["context"]["action"], "list")

    def test_inspection_is_read_only_and_does_not_create_a_job(self):
        self.bridge.return_value = {"ok": True, "fields": [], "scripts": []}
        for marketplace in ("poshmark", "mercari", "depop", "etsy", "sellwild"):
            response = self.client.post(f"{self.base}/{marketplace}/inspect", json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.bridge.call_args.args[1], "marketplace.inspect")
        self.assertFalse(JobRepo(self.db).list_all())
        self.assertEqual(self.client.post(f"{self.base}/unknown/inspect", json={}).status_code, 422)

    def test_publish_requires_confirmation_marketplace_draft_and_revision(self):
        self.save()
        self.bridge.reset_mock()
        for changes, status in [({"confirm": False}, 400), ({"draft_url": URLS["depop"]}, 409), ({"revision_id": "stale"}, 409)]:
            response = self.client.post(f"{self.base}/mercari/publish", json={**self.publish_body(), **changes})
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.client.post(f"{self.base}/depop/publish", json=self.publish_body("depop")).status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/all/publish", json=self.publish_body()).status_code, 422)
        self.bridge.assert_not_called()

    def test_confirmed_publish_runs_once_and_cannot_be_repeated(self):
        for platform in URLS:
            self.save(platform)
            self.bridge.return_value = {"ok": True, "state": "published", "draft_url": URLS[platform]}
            result = self.client.post(f"{self.base}/{platform}/publish", json=self.publish_body(platform))
            self.assertEqual(result.json()["state"], "published", result.text)
            self.assertEqual(self.bridge.call_args.args[2]["action"], "publish")
            self.assertTrue(self.bridge.call_args.args[2]["confirm"])
            before = self.bridge.call_count
            self.assertEqual(self.client.post(f"{self.base}/{platform}/publish", json=self.publish_body(platform)).status_code, 409)
            self.assertEqual(self.bridge.call_count, before)

    def test_disconnect_never_retries_and_check_reads_the_journal(self):
        self.bridge.side_effect = BrowserBridgeError("lost reply")
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.client.get(self.base).json()["mercari"]["state"], "unknown")
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.bridge.call_count, 1)
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}}
        checked = self.client.post(f"{self.base}/mercari/check")
        self.assertEqual(checked.json()["state"], "draft", checked.text)
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")

    def test_recovery_checks_a_pasted_native_link_without_repeating_the_write(self):
        self.bridge.side_effect = BrowserBridgeError("lost reply")
        self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}}
        result = self.client.post(f"{self.base}/mercari/check", json={"draft_url": URLS["mercari"]})
        self.assertEqual(result.json()["state"], "draft", result.text)
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertEqual(self.bridge.call_args.args[2]["context"]["draft_url"], URLS["mercari"])
        jobs = self.client.get(f"/api/jobs?conversation_id={self.conv.id}").json()
        self.assertEqual(jobs[0]["mode"], "native_marketplace")
        self.assertEqual(jobs[0]["status"], "completed")

    def test_old_bridge_is_rejected_before_making_a_job_or_saving(self):
        with patch("vendoo_studio.services.native_marketplaces.expected_extension_build", return_value="new-build"):
            result = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 409)
        self.bridge.assert_not_called()
        self.assertFalse(JobRepo(self.db).list_all())

    def test_failed_update_does_not_approve_an_unsaved_revision(self):
        self.save()
        old_revision = self.rev.id
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "price": 15}, "user")
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Sign in"}
        result = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.json()["revision_id"], old_revision)
        response = self.client.post(f"{self.base}/mercari/publish", json=self.publish_body())
        self.assertEqual(response.status_code, 409)

    def test_depop_recovers_the_same_empty_draft_without_approving_an_unsaved_revision(self):
        self.bridge.side_effect = BrowserBridgeError("lost create reply")
        self.client.post(f"{self.base}/depop/draft", json={"revision_id": self.rev.id})
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": False, "safe_to_retry": True,
            "draft_url": URLS["depop"], "error": "Finish photos in the same draft."}}
        response = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(response.json()["state"], "draft")
        self.assertEqual(response.json()["draft_url"], URLS["depop"])
        self.assertIsNone(response.json()["revision_id"])
        self.assertEqual(self.client.post(f"{self.base}/depop/publish", json=self.publish_body("depop")).status_code, 409)
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
        self.client.post(f"{self.base}/depop/draft", json={"revision_id": self.rev.id})
        self.assertEqual(self.bridge.call_args.args[2]["draft_url"], URLS["depop"])

    def test_unexpected_saved_link_is_unknown(self):
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": "https://example.com/item"}
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.client.get(self.base).json()["mercari"]["error"], UNKNOWN)

    def test_other_jobs_block_native_mutations(self):
        JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING)
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 409)
        self.bridge.assert_not_called()

    def test_native_jobs_are_never_requeued_on_reconnect_or_restart(self):
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        repo.requeue_interrupted()
        self.assertEqual(job.status, "failed")
        self.assertFalse(repo.requeue_interrupted_send(job.id))
        second = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_save_draft")
        second.updated_at = utcnow() - timedelta(seconds=10)
        self.db.commit()
        self.assertIn(second.id, fail_stalled_jobs(self.db))
        self.assertEqual(second.status, "failed")
        self.assertFalse(repo.get_dispatchable())

    def test_reconnect_keeps_a_native_action_running_in_this_process(self):
        from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job

        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        claim_api_job(job.id)
        try:
            repo.requeue_interrupted()
            self.assertEqual(job.status, "dispatched")
            self.assertFalse(repo.get_dispatchable())
        finally:
            release_api_job(job.id)
