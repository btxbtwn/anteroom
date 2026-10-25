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

URLS = {"poshmark": "https://poshmark.com/edit-listing/6adc371572e62463d992ffef",
        "mercari": "https://www.mercari.com/sell/draft/testNativeDraft/",
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

    def save(self, platform="poshmark"):
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS[platform]}
        result = self.client.post(f"{self.base}/{platform}/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "draft")
        return result

    def publish_body(self, platform="poshmark"):
        return {"revision_id": self.rev.id, "draft_url": URLS[platform], "confirm": True}

    def test_each_marketplace_saves_without_publishing(self):
        for platform in URLS:
            self.save(platform)
            payload = self.bridge.call_args.args[2]
            self.assertEqual(payload["action"], "save_draft")
            self.assertFalse(payload["confirm"])
            self.assertEqual(payload["marketplace"], platform)
        self.assertEqual(self.bridge.call_count, len(URLS))

    def test_sellwild_saves_locally_without_chrome_or_a_job(self):
        with patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)):
            result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["storage"], "local")
        self.assertEqual(result.json()["state"], "draft")
        self.assertNotIn("draft_url", result.json())
        self.assertNotIn("job_id", result.json())
        self.bridge.assert_not_called()
        self.assertFalse(JobRepo(self.db).list_all())

    def prepare_sellwild(self):
        self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id})
        self.bridge.return_value = {"ok": True, "state": "draft", "review_ready": True}
        result = self.client.post(f"{self.base}/sellwild/review", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["review_ready"])
        self.assertEqual(self.bridge.call_args.args[2]["action"], "review")
        self.assertFalse(self.bridge.call_args.args[2]["confirm"])

    def test_sellwild_publish_requires_local_save_review_revision_and_confirmation(self):
        body = {"revision_id": self.rev.id, "confirm": True}
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id})
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.bridge.assert_not_called()
        self.prepare_sellwild()
        self.bridge.reset_mock()
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json={**body, "confirm": False}).status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json={**body, "revision_id": "stale"}).status_code, 409)
        self.bridge.assert_not_called()

    def test_sellwild_reports_moderation_and_never_repeats_publishing(self):
        self.prepare_sellwild()
        self.bridge.return_value = {"ok": True, "state": "pending_review", "listing_url": "https://sellwild.com/product/123/test"}
        body = {"revision_id": self.rev.id, "confirm": True}
        result = self.client.post(f"{self.base}/sellwild/publish", json=body)
        self.assertEqual(result.json()["state"], "pending_review", result.text)
        self.assertTrue(self.bridge.call_args.args[2]["confirm"])
        count = self.bridge.call_count
        self.assertEqual(self.client.post(f"{self.base}/sellwild/publish", json=body).status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id}).status_code, 409)
        self.assertEqual(self.bridge.call_count, count)

    def test_sellwild_changed_photos_require_saving_and_reviewing_again(self):
        self.prepare_sellwild()
        ConversationRepo(self.db).add_photo(self.conv.id, "second.jpg", "second.jpg", "image/jpeg", 1)
        result = self.client.post(f"{self.base}/sellwild/publish", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 409)
        result = self.client.post(f"{self.base}/sellwild/draft", json={"revision_id": self.rev.id})
        self.assertFalse(result.json()["review_ready"])

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
        for changes, status in [({"confirm": False}, 400), ({"draft_url": URLS["mercari"]}, 409), ({"revision_id": "stale"}, 409)]:
            response = self.client.post(f"{self.base}/poshmark/publish", json={**self.publish_body(), **changes})
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.client.post(f"{self.base}/mercari/publish", json=self.publish_body("mercari")).status_code, 409)
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
        response = self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.client.get(self.base).json()["poshmark"]["state"], "unknown")
        response = self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.bridge.call_count, 1)
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "draft", "draft_url": URLS["poshmark"]}}
        checked = self.client.post(f"{self.base}/poshmark/check")
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
        result = self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.json()["revision_id"], old_revision)
        response = self.client.post(f"{self.base}/poshmark/publish", json=self.publish_body())
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
        response = self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.client.get(self.base).json()["poshmark"]["error"], UNKNOWN)

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
        second = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_poshmark_save_draft")
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
