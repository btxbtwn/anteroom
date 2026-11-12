"""Every wait on something outside Anteroom ends, and the listing stops being busy.

The sandbox provider never answers a call that carries ``STALL_MARKER``, the
way a provider that stopped responding behaves. Each long action is driven
against it with short deadlines and must end in an error that names what
stalled, with nothing left marked as running.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio import sandbox
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.providers.sandbox import STALL_MARKER, SandboxProvider
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.routes import chat as chat_routes
from vendoo_studio.services import (
    activity,
    listing_generation,
    listing_persistence,
    listing_provider,
    provider_deadlines,
    streaming,
)

SHORT = 0.2
# Far longer than SHORT; reaching it means a wait had no deadline.
HUNG = 10.0
STALLED = "The sandbox AI stopped responding while"


def _deadlines():
    return [
        patch.object(provider_deadlines, "STREAM_IDLE_SEC", SHORT),
        patch.object(provider_deadlines, "CALL_DEADLINE_SEC", SHORT),
        patch.dict("os.environ", {sandbox.ENV_VAR: "1"}),
    ]


class ProviderDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for p in _deadlines():
            p.start()
            self.addCleanup(p.stop)

    async def test_sandbox_mode_runs_the_provider_with_deadlines(self):
        provider = listing_provider.get_listing_provider()
        self.assertIsInstance(provider_deadlines.unwrap(provider), SandboxProvider)
        self.assertIsNot(provider, provider_deadlines.unwrap(provider))
        self.assertEqual(provider.name, "sandbox")

    async def test_a_stream_that_stops_ends_in_a_named_error(self):
        provider = listing_provider.get_listing_provider()
        started = time.monotonic()
        with self.assertRaises(provider_deadlines.ProviderStalled) as caught:
            async with asyncio.timeout(HUNG):
                async for _ in provider.chat([{"role": "user", "content": f"Hello {STALL_MARKER}"}]):
                    pass
        self.assertLess(time.monotonic() - started, HUNG / 2)
        self.assertIn(f"{STALLED} writing its answer", str(caught.exception))

    async def test_a_photo_read_that_never_returns_ends_in_a_named_error(self):
        provider = listing_provider.get_photo_provider()
        with self.assertRaises(provider_deadlines.ProviderStalled) as caught:
            async with asyncio.timeout(HUNG):
                await provider.analyze_photos(["a.jpg"], notes=STALL_MARKER)
        self.assertIn(f"{STALLED} reading the photos", str(caught.exception))

    async def test_a_long_answer_that_keeps_coming_is_not_cut_off(self):
        class Slow:
            name = "claude"

            async def chat(self, messages, stream=True):
                for piece in "slow but steady":
                    await asyncio.sleep(SHORT / 4)
                    yield piece

        provider = provider_deadlines.with_deadlines(Slow())
        text = "".join([piece async for piece in provider.chat([])])
        # Far longer than the idle limit in total, never idle for that long.
        self.assertEqual(text, "slow but steady")

    async def test_answers_and_missing_operations_pass_through(self):
        provider = listing_provider.get_listing_provider()
        reply = "".join([piece async for piece in provider.chat([{"role": "user", "content": "Hi"}])])
        self.assertEqual(reply, "Sandbox reply: no model was called.")
        self.assertIsNone(getattr(provider, "quick_chat", None))
        self.assertEqual(provider_deadlines.provider_label("openai"), "OpenAI")


class _Workspace(unittest.IsolatedAsyncioTestCase):
    notes = STALL_MARKER

    async def asyncSetUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(app.dependency_overrides.pop, get_db, None)
        db = self.Session()
        repo = ConversationRepo(db)
        conv = repo.create(title="New Listing")
        conv.notes = self.notes
        db.commit()
        listing_persistence.add_photo(db, conv.id, "a.jpg", "a.jpg", "image/jpeg", 10)
        self.conv_id = conv.id
        db.close()
        patches = [
            *_deadlines(),
            patch("vendoo_studio.database.SessionLocal", self.Session),
            patch.object(listing_generation, "load_skill_rules", return_value="rules"),
            patch.object(listing_generation, "comps_search_available", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addAsyncCleanup(streaming.reset_generations)
        self.addCleanup(activity.reset)

    def client(self) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:4318")

    async def stream(self, client: AsyncClient, path: str, **kwargs) -> str:
        async def read():
            async with client.stream("POST", path, **kwargs) as resp:
                self.assertEqual(resp.status_code, 200)
                return "".join([chunk async for chunk in resp.aiter_text()])

        return await asyncio.wait_for(read(), HUNG)

    def status(self) -> str:
        db = self.Session()
        try:
            return ConversationRepo(db).get(self.conv_id).status
        finally:
            db.close()

    def system_messages(self) -> list[str]:
        db = self.Session()
        try:
            return [m.text or "" for m in ConversationRepo(db).get_messages(self.conv_id) if m.role == "system"]
        finally:
            db.close()

    def assert_not_busy(self):
        self.assertIsNone(streaming.active_generation(self.conv_id))
        self.assertEqual(activity.running(self.conv_id), [])
        self.assertEqual(self.status(), "draft")


class StalledGenerationTest(_Workspace):
    async def test_generate_ends_with_the_stalled_step_named(self):
        async with self.client() as client:
            body = await self.stream(client, f"/api/conversations/{self.conv_id}/generate")
        self.assertIn(f"Error: Photo analysis failed: {STALLED} reading the photos", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))
        self.assert_not_busy()
        self.assertTrue(any(STALLED in text for text in self.system_messages()))

    async def test_regenerate_during_a_stalled_run_ends_too(self):
        async with self.client() as client:
            first = asyncio.create_task(self.stream(client, f"/api/conversations/{self.conv_id}/generate"))
            while streaming.active_generation(self.conv_id) is None:
                await asyncio.sleep(0.01)
            reset = await client.post(f"/api/conversations/{self.conv_id}/reset", json={"keep_inputs": True})
            self.assertEqual(reset.status_code, 200)
            body = await self.stream(client, f"/api/conversations/{self.conv_id}/generate")
            await asyncio.wait_for(first, HUNG)
        self.assertIn(STALLED, body)
        self.assert_not_busy()

    async def test_a_stalled_listing_stream_ends_too(self):
        # The photos were read already; the listing model is what stalls.
        db = self.Session()
        try:
            photos = ConversationRepo(db).get_photos(self.conv_id)
            ConversationRepo(db).add_message(
                self.conv_id, "system", "Photo analysis:\n- brand: Levi's",
                provider="sandbox", model="sandbox", photo_fingerprint=listing_generation.photo_fingerprint(photos),
            )
        finally:
            db.close()
        with patch.object(listing_generation, "prepare_generation_schema", new=AsyncMock(return_value={})):
            async with self.client() as client:
                body = await self.stream(client, f"/api/conversations/{self.conv_id}/generate")
        self.assertIn(f"Error: {STALLED} writing its answer", body)
        self.assert_not_busy()

    async def test_reading_the_photos_alone_answers_with_the_stalled_step(self):
        async with self.client() as client:
            response = await asyncio.wait_for(
                client.post(f"/api/conversations/{self.conv_id}/analyze-photos"), HUNG,
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn(f"{STALLED} reading the photos", response.json()["detail"])


class StalledChatTest(_Workspace):
    notes = ""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        db = self.Session()
        try:
            listing_persistence.save_revision(db, self.conv_id, {"title": "Vintage Tee"}, source="user")
        finally:
            db.close()

    async def test_a_chat_send_ends_with_the_stalled_step_named(self):
        async def build(conv_id, text, option_catalog=None):
            return [{"role": "user", "content": text}]

        with patch.object(chat_routes, "build_chat_messages", new=build):
            async with self.client() as client:
                body = await self.stream(
                    client, f"/api/conversations/{self.conv_id}/messages", json={"text": f"Make it blue {STALL_MARKER}"},
                )
        self.assertIn(f"Error: {STALLED} writing its answer", body)
        self.assert_not_busy()


@pytest.fixture
def assistant(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from vendoo_studio.database import load_models
    from vendoo_studio.routes import assistant as assistant_route

    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_route, "SessionLocal", factory)
    monkeypatch.setattr("vendoo_studio.database.SessionLocal", factory)
    monkeypatch.setattr("vendoo_studio.services.box_scout.state_path", lambda: tmp_path / "sourcing.json")
    monkeypatch.setenv(sandbox.ENV_VAR, "1")
    monkeypatch.setattr(provider_deadlines, "STREAM_IDLE_SEC", SHORT)
    monkeypatch.setattr(provider_deadlines, "CALL_DEADLINE_SEC", SHORT)
    db = factory()
    test_app = FastAPI()
    test_app.include_router(assistant_route.router)
    test_app.dependency_overrides[get_db] = lambda: db
    with TestClient(test_app) as client:
        yield assistant_route, client
    db.close()
    engine.dispose()


def test_an_assistant_answer_that_stalls_ends_with_a_named_error(assistant):
    assistant_route, client = assistant
    answered: list = []
    asking = threading.Thread(target=lambda: answered.append(
        client.post("/api/assistant/messages", json={"text": f"Which sizes sell? {STALL_MARKER}"})), daemon=True)
    asking.start()
    asking.join(HUNG)
    if not answered:
        pytest.fail("The assistant answer never ended")
    [response] = answered
    assert f"event: error\ndata: {STALLED} writing its answer" in response.text
    assert streaming.active_generation(assistant_route._answer_run()) is None
    # The question stays, unanswered, so Retry can answer it.
    assert [row["text"] for row in client.get("/api/assistant/messages").json()] == [
        f"Which sizes sell? {STALL_MARKER}",
    ]


class ChromeThatNeverRepliesTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_chrome_request_ends_and_forgets_its_wait(self):
        from types import SimpleNamespace

        from vendoo_studio.services import browser_bridge
        from vendoo_studio.services.extension_connection import ReplyWaits

        class SilentChrome(ReplyWaits):
            connected = True
            sent: list[dict] = []

            async def send_message(self, message: dict) -> bool:
                self.sent.append(message)
                return True

        chrome = SilentChrome()
        with patch.object(browser_bridge, "_manager", return_value=chrome):
            with self.assertRaises(browser_bridge.BrowserBridgeError) as caught:
                await asyncio.wait_for(
                    browser_bridge.request(SimpleNamespace(id="job-1"), "marketplace.inspect", timeout=SHORT), HUNG,
                )
        self.assertIn("Chrome did not answer", str(caught.exception))
        request_id = chrome.sent[0]["message_id"]
        self.assertFalse(chrome.is_waiting(request_id))


class ClaudeProcessThatNeverClearsTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_process_stuck_on_clear_is_closed_and_frees_its_slot(self):
        from vendoo_studio.providers import claude_sessions

        disconnected = asyncio.Event()

        class StuckClient:
            async def query(self, prompt):
                return None

            async def receive_response(self):
                await asyncio.Event().wait()
                yield None

            async def disconnect(self):
                disconnected.set()

        pool = claude_sessions._Pool(asyncio.get_running_loop())
        pool.live = 1
        session = claude_sessions._Session(client=StuckClient(), key=("k",))
        with patch.object(claude_sessions, "CLEAR_TIMEOUT_SEC", SHORT):
            await asyncio.wait_for(pool.release(session, healthy=True), HUNG)
            await asyncio.wait_for(disconnected.wait(), HUNG)
        self.assertEqual(pool.live, 0)
        self.assertEqual(pool.idle, {})


class KeychainThatNeverAnswersTest(unittest.TestCase):
    def test_a_blocked_read_gives_up_and_is_not_started_twice(self):
        from vendoo_studio.services import keychain

        release = threading.Event()
        calls: list[str] = []

        class BlockedKeyring:
            def get_password(self, service, account):
                calls.append(account)
                release.wait(HUNG)
                return json.dumps({"x": "1"})

        self.addCleanup(release.set)
        with patch.object(keychain, "_keyring", return_value=BlockedKeyring()), \
                patch.object(keychain, "KEYCHAIN_TIMEOUT_SEC", SHORT):
            for _ in range(2):
                started = time.monotonic()
                with self.assertRaises(keychain._Unavailable):
                    keychain._read_item("studio-secrets")
                self.assertLess(time.monotonic() - started, HUNG / 2)
            # The second read joined the first, still waiting on the Keychain prompt.
            self.assertEqual(calls, ["studio-secrets"])
            release.set()
            for _ in range(50):
                try:
                    self.assertEqual(keychain._read_item("studio-secrets"), json.dumps({"x": "1"}))
                    break
                except keychain._Unavailable:
                    time.sleep(SHORT / 4)
            else:
                self.fail("The Keychain answered, but the read never picked it up")
