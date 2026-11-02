from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.job import Job
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services.extension_connection import (
    ExtensionManager,
    extension_status,
    handshake_extension,
)


class FakeSocket:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("websocket closed")
        self.sent.append(message)

    async def close(self):
        self.closed = True


class ExtensionManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_send_clears_stale_connection(self):
        manager = ExtensionManager()
        manager.connection = FakeSocket(fail=True)
        manager.paired = True

        sent = await manager.send_message({"type": "marketplace.execute"})

        self.assertFalse(sent)
        self.assertIsNone(manager.connection)
        self.assertFalse(manager.paired)

    async def test_successful_send_keeps_connection(self):
        manager = ExtensionManager()
        socket = FakeSocket()
        manager.connection = socket
        manager.paired = True

        sent = await manager.send_message({"type": "marketplace.execute", "job_id": "abc"})

        self.assertTrue(sent)
        self.assertIs(manager.connection, socket)
        self.assertEqual(socket.sent[0]["type"], "marketplace.execute")


class JobRepoActiveTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.conv = Conversation(title="Nike tee")
        self.db.add(self.conv)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _job(self, status: str) -> Job:
        job = Job(
            conversation_id=self.conv.id,
            approved_revision_id="rev1",
            listing_snapshot={"title": "Nike tee"},
            status=status,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def test_get_active_includes_dispatched_jobs(self):
        dispatched = self._job("dispatched")
        self._job("completed")
        active = JobRepo(self.db).get_active()
        self.assertEqual([job.id for job in active], [dispatched.id])

    def test_get_running_only_includes_dispatched(self):
        queued = self._job("queued")
        dispatched = self._job("dispatched")
        running = JobRepo(self.db).get_running()
        self.assertEqual([job.id for job in running], [dispatched.id])
        self.assertNotIn(queued.id, [job.id for job in running])

    def test_add_event_persists_without_refresh(self):
        job = self._job("dispatched")
        event = JobRepo(self.db).add_event(job.id, "cancelled")
        self.assertEqual(event.event_type, "cancelled")
        self.assertEqual(JobRepo(self.db).get_events(job.id)[-1].id, event.id)

    def test_cancel_leftover_jobs_clears_dispatched_jobs(self):
        from vendoo_studio.routes.updates import _cancel_leftover_jobs

        job = self._job("dispatched")
        cancelled = _cancel_leftover_jobs(self.db)
        self.assertEqual(cancelled, [job.id])
        self.db.refresh(job)
        self.assertEqual(job.status, "cancelled")


class ExtensionHandshakeTest(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_accepts_without_reloading_tabs(self):
        socket = FakeSocket()
        with patch("vendoo_studio.services.extension_connection.install_bundled_extension", return_value=True), patch(
            "vendoo_studio.services.extension_connection.extension_reload_token_if_needed", return_value=None
        ), patch("vendoo_studio.services.extension_connection.clear_extension_reload_pending") as clear:
            accepted = await handshake_extension(socket, None)

        self.assertTrue(accepted)
        self.assertEqual(socket.sent[0]["type"], "connection.accepted")
        clear.assert_called_once()

    async def test_handshake_reloads_once_when_chrome_is_behind(self):
        socket = FakeSocket()
        with patch("vendoo_studio.services.extension_connection.install_bundled_extension", return_value=True), patch(
            "vendoo_studio.services.extension_connection.extension_reload_token_if_needed", return_value="gen-1"
        ) as reload_token, patch("vendoo_studio.services.extension_connection.clear_extension_reload_pending") as clear:
            accepted = await handshake_extension(socket, None, "0.2.15")

        self.assertFalse(accepted)
        reload_token.assert_called_once_with("0.2.15", None, None)
        self.assertEqual(socket.sent[0]["type"], "extension.reload")
        self.assertEqual(socket.sent[0]["payload"]["generation"], "gen-1")
        clear.assert_not_called()

    async def test_handshake_accepts_after_reload_generation_matches(self):
        socket = FakeSocket()
        with patch("vendoo_studio.services.extension_connection.install_bundled_extension", return_value=True), patch(
            "vendoo_studio.services.extension_connection.extension_reload_token_if_needed", return_value=None
        ), patch("vendoo_studio.services.extension_connection.clear_extension_reload_pending") as clear:
            accepted = await handshake_extension(socket, "gen-1", "0.2.15")

        self.assertTrue(accepted)
        self.assertEqual(socket.sent[0]["type"], "connection.accepted")
        clear.assert_called_once()


class ExtensionStatusRouteTest(unittest.TestCase):
    def test_status_includes_build_fields(self):
        manager = ExtensionManager()
        manager.version = "0.2.5"
        manager.reload_generation = None
        manager.capabilities = frozenset({"marketplace.actions"})
        with patch("vendoo_studio.services.extension_connection.extension_manager", manager), patch(
            "vendoo_studio.services.extension_connection.extension_build_status",
            return_value={
                "expected_version": "0.2.6",
                "version": "0.2.5",
                "up_to_date": False,
                "reload_pending": False,
                "files_in_sync": True,
            },
        ) as build:
            payload = extension_status()

        build.assert_called_once_with("0.2.5", None, None)
        self.assertFalse(payload["connected"])
        self.assertFalse(payload["up_to_date"])
        # An earlier copy that declares marketplace actions can still run them.
        self.assertTrue(payload["runs_marketplace_actions"])
        self.assertEqual(payload["expected_version"], "0.2.6")
        self.assertEqual(payload["version"], "0.2.5")


class PrepareListingSnapshotRetryTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = Conversation(title="Casa tee")
        self.db.add(self.conv)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_retry_keeps_refined_mens_category_over_stale_override(self):
        import json

        from vendoo_studio.services.job_snapshot import prepare_listing_snapshot
        from vendoo_studio.services.registry import MEN_TSHIRT_PATH

        self.conv.notes = json.dumps({
            "categoryOverride": "Clothing, Shoes & Accessories > Women > Women's Clothing > Tops",
        })
        self.db.commit()
        snapshot = prepare_listing_snapshot(
            self.db,
            self.conv,
            {
                "title": "Casa San Bord M Graphic T-Shirt",
                "department": "Men",
                "category_path": MEN_TSHIRT_PATH,
                "ebay_specifics": {"department": "Men", "type": "T-Shirt"},
            },
            prefer_listing_category=True,
        )
        self.assertEqual(snapshot["category_path"], MEN_TSHIRT_PATH)
        self.assertNotIn("Women", snapshot["category_path"])

    def test_item_details_labels_cog_and_notes_reach_vendoo_form(self):
        import json

        from vendoo_studio.services.job_snapshot import prepare_listing_snapshot
        from vendoo_studio.services.vendoo_item_build import build_vendoo_item

        self.conv.notes = json.dumps({
            "vendooLabels": "A19, To List",
            "sku": "KEEP-ME-SKU",
            "cog": "3.50",
            "sellerNotes": "Bin 4, small hole on hem",
            "poshmarkOriginalPrice": "80",
        })
        self.db.commit()
        snapshot = prepare_listing_snapshot(
            self.db,
            self.conv,
            {"title": "Tee", "sku": "NEW-SLUG", "cost": 9, "internal_notes": "stale"},
        )
        self.assertEqual(snapshot["labels"], ["A19", "To List"])
        self.assertEqual(snapshot["sku"], "KEEP-ME-SKU")
        self.assertEqual(snapshot["cost"], 3.5)
        self.assertEqual(snapshot["internal_notes"], "Bin 4, small hole on hem")
        self.assertEqual(snapshot["poshmark_specifics"]["originalPrice"], 0)

        item, _ = build_vendoo_item(snapshot, None, images=[], user_id="u", item_id="i")
        self.assertEqual(item["listings"]["poshmark"]["marketplaceSpecifics"]["originalPrice"], "0")
        general = item["generalDetails"]
        self.assertEqual(item["labels"], ["A19", "To List"])
        self.assertEqual(general["sku"], "KEEP-ME-SKU")
        self.assertEqual(general["cost"], "3.5")
        self.assertEqual(general["notes"], "Bin 4, small hole on hem")

    def test_blank_item_details_keep_listing_cost_and_notes(self):
        from vendoo_studio.services.job_snapshot import prepare_listing_snapshot

        snapshot = prepare_listing_snapshot(
            self.db, self.conv, {"title": "Tee", "cost": 9, "internal_notes": "Bin 2"},
        )
        self.assertEqual(snapshot["cost"], 9)
        self.assertEqual(snapshot["internal_notes"], "Bin 2")


if __name__ == "__main__":
    unittest.main()
