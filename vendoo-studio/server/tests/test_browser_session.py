"""Interactive Vendoo browser: routes, WebSocket replies, and directed fills."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services.extension_connection import ExtensionManager


class FakeConnection:
    """Answers each browser request the way the extension would."""

    def __init__(self, manager: ExtensionManager, reply: dict):
        self.manager = manager
        self.reply = reply
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)
        request_id = (message.get("payload") or {}).get("request_id")
        if request_id:
            asyncio.get_running_loop().call_soon(self.manager.resolve_wait, request_id, dict(self.reply))




class BrowserResultOverWebSocketTest(unittest.TestCase):
    def test_browser_result_resolves_pending_request(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        manager = ExtensionManager()
        resolved = {}

        async def handshake(ws, *_):
            return True

        original_resolve = manager.resolve_wait

        def record(request_id, payload):
            resolved[request_id] = payload
            original_resolve(request_id, payload)

        with patch("vendoo_studio.services.extension_connection.SessionLocal", Session), \
             patch("vendoo_studio.services.extension_connection.extension_manager", manager), \
             patch.object(manager, "verify_token", return_value=True), \
             patch.object(manager, "resolve_wait", side_effect=record), \
             patch("vendoo_studio.services.extension_connection._websocket_allowed", return_value=True), \
             patch("vendoo_studio.services.extension_connection.handshake_extension", side_effect=handshake):
            client = TestClient(app)
            with client.websocket_connect("/api/extension/ws") as ws:
                ws.send_json({"type": "extension.ready", "payload": {"token": "test"}})
                ws.send_json({"type": "browser.result", "payload": {"request_id": "abc", "ok": True, "fields": []}})
                ws.send_json({"type": "pong"})
                for _ in range(50):
                    if "abc" in resolved:
                        break
                    client.get("/api/health")
        self.assertEqual(resolved["abc"]["fields"], [])




class FinishedJobRepliesTest(unittest.TestCase):
    """Sent listings are still read and updated through their finished job."""

    def _run(self, status: str, wait: bool):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        conv = ConversationRepo(db).create(title="Tee")
        revision = listing_persistence.save_revision(db, conv.id, {"title": "Tee"}, source="user")
        job = JobRepo(db).create(conv_id=conv.id, approved_revision_id=revision.id, listing_snapshot={"title": "Tee"},
                                  status=status)
        job_id = job.id
        db.close()
        manager = ExtensionManager()
        resolved = {}

        async def handshake(ws, *_):
            if wait:
                future = manager.register_wait("req-1", job_id)
                future.add_done_callback(lambda f: resolved.setdefault("req-1", f.result()))
            return True

        with patch("vendoo_studio.services.extension_connection.SessionLocal", Session), \
             patch("vendoo_studio.services.extension_connection.extension_manager", manager), \
             patch.object(manager, "verify_token", return_value=True), \
             patch("vendoo_studio.services.extension_connection._websocket_allowed", return_value=True), \
             patch("vendoo_studio.services.extension_connection.handshake_extension", side_effect=handshake):
            client = TestClient(app)
            with client.websocket_connect("/api/extension/ws") as ws:
                ws.send_json({"type": "extension.ready", "payload": {"token": "test"}})
                ws.send_json({"type": "browser.result", "job_id": job_id, "message_id": "req-1", "payload": {
                    "request_id": "req-1", "ok": True, "state": "published",
                }})
                ws.send_json({"type": "pong"})
                for _ in range(50):
                    if resolved:
                        break
                    client.get("/api/health")
        events = [event.event_type for event in JobRepo(Session()).get_events(job_id)]
        return resolved, events

    def test_reply_reaches_waiter_for_completed_job(self):
        resolved, events = self._run("completed", wait=True)
        self.assertEqual(resolved["req-1"]["state"], "published")
        self.assertNotIn("ignored_late_result", events)

    def test_unrequested_reply_for_completed_job_is_dropped(self):
        resolved, events = self._run("completed", wait=False)
        self.assertEqual(resolved, {})
        self.assertEqual(events, [])




if __name__ == "__main__":
    unittest.main()
