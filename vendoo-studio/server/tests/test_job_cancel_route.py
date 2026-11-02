from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.services.extension_connection import extension_manager


class CancelJobRouteTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.db = Session()
        self.conv = Conversation(title="Nike tee")
        self.db.add(self.conv)
        self.db.commit()
        self.job = Job(
            conversation_id=self.conv.id,
            approved_revision_id="rev1",
            listing_snapshot={"title": "Nike tee"},
            status="completed",
        )
        self.db.add(self.job)
        self.db.commit()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        extension_manager.connection = None
        extension_manager.paired = False

    def tearDown(self):
        app.dependency_overrides.clear()
        extension_manager.connection = None
        extension_manager.paired = False

    def test_cancel_on_finished_job_is_not_an_error(self):
        res = self.client.post(f"/api/jobs/{self.job.id}/cancel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "completed")
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, "completed")
        self.db.close()

class ExtensionSendTimeoutTest(unittest.IsolatedAsyncioTestCase):
    """A message the extension stops reading gives up instead of waiting forever."""

    def setUp(self):
        self.sent: list[dict] = []
        extension_manager.paired = True

    def tearDown(self):
        extension_manager.connection = None
        extension_manager.paired = False

    def _connection(self, *, hang: bool = False):
        connection = MagicMock()

        async def send_json(message):
            if hang:
                await asyncio.Event().wait()
            self.sent.append(message)

        connection.send_json = send_json
        connection.close = AsyncMock()
        extension_manager.connection = connection
        return connection

    async def test_a_send_that_never_goes_out_gives_up_and_drops_the_socket(self):
        connection = self._connection(hang=True)
        with patch("vendoo_studio.services.extension_connection.SEND_TIMEOUT_SEC", 0.05):
            self.assertFalse(await extension_manager.send_message({"type": "marketplace.sessions", "payload": {}}))
        self.assertFalse(extension_manager.connected)
        connection.close.assert_awaited()


if __name__ == "__main__":
    unittest.main()
