from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.diagnostics import DiagnosticRun, FieldObservation  # noqa: F401
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import activity


class ActivityRegistryTest(unittest.IsolatedAsyncioTestCase):
    """Chat is only "done" once nothing registered for the listing is still running."""

    def tearDown(self):
        activity.reset()

    def test_exact_clear_takes_precedence_over_another_field_spelling(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertEqual(activity.field_value({"etsy_specifics": {
                    "whoMade": value, "who_made": "Another company or person"}}, "etsy_specifics.whoMade"), value)

    async def test_task_is_listed_until_it_finishes(self):
        gate = asyncio.Event()
        task = activity.track_task("c1", "Filling discovered fields…", asyncio.create_task(gate.wait()))
        self.assertEqual(activity.running("c1"), ["Filling discovered fields…"])
        gate.set()
        await task
        await asyncio.sleep(0)
        self.assertEqual(activity.running("c1"), [])

    async def test_cancel_stops_the_task_and_clears_the_listing(self):
        task = activity.track_task("c1", "Saving listing…", asyncio.create_task(asyncio.sleep(60)))
        inline = activity.begin("c1", "Answering…")
        activity.cancel("c1")
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(inline.cancelled)
        self.assertEqual(activity.running("c1"), [])
        self.assertEqual(activity.running("other"), [])


class ConversationActivityRouteTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = ConversationRepo(self.db).create(title="Nike tee")

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        activity.reset()
        self.db.close()

    def activity(self) -> dict:
        response = self.client.get(f"/api/conversations/{self.conv.id}/activity")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add_job(self, status: str) -> Job:
        revision = listing_persistence.save_revision(self.db, self.conv.id, {"title": "Nike tee"}, "generate")
        return JobRepo(self.db).create(self.conv.id, revision.id, revision.listing_json, status=status)

    def test_idle_listing_reports_done_and_the_latest_message(self):
        message = ConversationRepo(self.db).add_message(self.conv.id, "assistant", "Listing saved.")
        body = self.activity()
        self.assertFalse(body["busy"])
        self.assertEqual(body["items"], [])
        self.assertEqual(body["message_count"], 1)
        self.assertEqual(body["last_message_id"], message.id)

    def test_scoped_agent_revision_preserves_other_fields_and_reports_its_lock(self):
        repo = ListingRepo(self.db)
        listing_persistence.save_revision(repo.db, self.conv.id, {"title": "New seller title", "price": 30,
                           "ebay_specifics": {"Size": "34"}}, "generate")
        work = activity.begin(self.conv.id, "Editing size")
        work.fields = ("ebay_specifics.Size",)
        self.assertEqual(self.activity()["locked_fields"], ["ebay_specifics.Size"])
        token = activity.edit_fields.set(work.fields)
        try:
            listing_persistence.save_revision(repo.db, self.conv.id, {"title": "Old agent title", "price": 10,
                               "ebay_specifics": {"Size": "36"}}, "model_refinement")
        finally:
            activity.edit_fields.reset(token)
        saved = repo.get_revision(repo.get_current(self.conv.id).current_revision_id).listing_json
        self.assertEqual(saved["title"], "New seller title")
        self.assertEqual(saved["price"], 30)
        self.assertEqual(saved["ebay_specifics"]["Size"], "36")
        activity.end(work)
        self.assertEqual(self.activity()["locked_fields"], [])

    def test_manual_save_of_agent_locked_field_is_rejected_without_touching_the_listing(self):
        from fastapi import HTTPException
        from vendoo_studio.services.listing_editor import save_listing_edit

        repo = ListingRepo(self.db)
        original = listing_persistence.save_revision(repo.db, self.conv.id, {"title": "Tee", "ebay_specifics": {"Size": "34"}}, "generate")
        work = activity.begin(self.conv.id, "Editing size")
        work.fields = ("ebay_specifics.Size",)
        with self.assertRaises(HTTPException) as error:
            save_listing_edit(self.conv.id, {"title": "Tee", "ebay_specifics": {"Size": "36"}}, self.db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(repo.get_current(self.conv.id).current_revision_id, original.id)

    def test_scoped_etsy_patch_survives_key_normalization_and_readback(self):
        from vendoo_studio.services.etsy_fields import etsy_who_raw, etsy_when_raw
        from vendoo_studio.services.chat_listing import save_listing_revision
        from vendoo_studio.services.listing_patch import apply_json_patch

        repo = ListingRepo(self.db)
        before = {"title": "Vintage tee", "price": 30, "etsy_specifics": {},
                  "ebay_specifics": {"Size": "XL"}}
        listing_persistence.save_revision(repo.db, self.conv.id, before, "model")
        ops = [{"op": "add", "path": "/etsy_specifics/whoMade", "value": "Another company or person"},
               {"op": "add", "path": "/etsy_specifics/whenMade", "value": "2005"}]
        token = activity.edit_fields.set(("etsy_specifics.whoMade", "etsy_specifics.whenMade"))
        try:
            save_listing_revision(self.db, self.conv.id, apply_json_patch(before, ops), operations=ops)
        finally:
            activity.edit_fields.reset(token)
        self.db.expire_all()
        saved = repo.get_revision(repo.get_current(self.conv.id).current_revision_id).listing_json
        self.assertEqual(etsy_who_raw(saved["etsy_specifics"]), "Another company or person")
        self.assertTrue(etsy_when_raw(saved["etsy_specifics"], saved))
        self.assertEqual(saved["price"], 30)
        self.assertEqual(saved["ebay_specifics"], {"Size": "XL"})

    def test_background_work_keeps_the_listing_busy(self):
        activity.begin(self.conv.id, "Filling discovered fields…")
        body = self.activity()
        self.assertTrue(body["busy"])
        self.assertEqual(body["items"], ["Filling discovered fields…"])

    def test_an_active_vendoo_job_keeps_the_listing_busy(self):
        self.add_job("dispatched")
        self.assertTrue(self.activity()["busy"])

    def test_direct_actions_name_the_marketplace_and_action(self):
        job = self.add_job("dispatched")
        job.current_step = "native_poshmark_save_draft"
        self.db.commit()
        self.assertEqual(self.activity()["items"], ["Poshmark: Saving the listing…"])

    def test_finished_jobs_do_not(self):
        self.add_job("completed")
        self.assertFalse(self.activity()["busy"])

    def test_stop_ends_background_work_and_jobs(self):
        work = activity.begin(self.conv.id, "Answering…")
        job = self.add_job("queued")

        response = self.client.post(f"/api/conversations/{self.conv.id}/stop")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(work.cancelled)
        self.db.expire_all()
        self.assertEqual(JobRepo(self.db).get(job.id).status, "cancelled")
        self.assertFalse(self.activity()["busy"])


if __name__ == "__main__":
    unittest.main()


class FieldLockLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        activity.reset()

    async def test_locks_survive_the_stream_until_save_finishes_and_release_on_cancel(self):
        work = activity.begin('item', 'Editing size')
        work.fields = ('ebay_specifics.Size',)
        gate = asyncio.Event()
        task = activity.track_task('item', 'Saving', asyncio.create_task(gate.wait()), fields=work.fields)
        activity.end(work)
        self.assertEqual(activity.locked_fields('item'), ['ebay_specifics.Size'])
        self.assertEqual(activity.locked_fields('other'), [])
        gate.set()
        await task
        await asyncio.sleep(0)
        self.assertEqual(activity.locked_fields('item'), [])
        work = activity.begin('item', 'Editing size')
        work.fields = ('ebay_specifics.Size',)
        activity.cancel('item')
        self.assertEqual(activity.locked_fields('item'), [])

    def test_targeted_save_preserves_unrelated_newer_seller_edits(self):
        current = {'title': 'New seller title', 'price': 30, 'ebay_specifics': {'Size': '34'},
                   'seller_fields': ['title']}
        candidate = {'title': 'Old title', 'price': 10, 'ebay_specifics': {'size': '36'},
                     'seller_fields': ['ebay_specifics.Size']}
        saved = activity.scoped_listing(current, candidate, ('ebay_specifics.Size',))
        self.assertEqual(saved['title'], 'New seller title')
        self.assertEqual(saved['price'], 30)
        self.assertEqual(saved['ebay_specifics']['Size'], '36')
        self.assertEqual(saved['seller_fields'], ['ebay_specifics.Size', 'title'])
        self.assertEqual(activity.scoped_listing(current, candidate, ()), current)
        current['seller_fields'].append('ebay_specifics.Size')
        candidate.pop('seller_fields')
        self.assertIn('ebay_specifics.Size', activity.scoped_listing(current, candidate, ('ebay_specifics.Size',))['seller_fields'])
        self.assertEqual(activity.field_value({'ebay_specifics': {'sizeType': 'Regular'}}, 'ebay_specifics.Size Type'), 'Regular')
