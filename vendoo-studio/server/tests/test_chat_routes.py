from __future__ import annotations

import unittest
from pathlib import Path

from vendoo_studio.services import listing_persistence
from vendoo_studio.services.listing_generate import photo_analysis_usable


class AnalyzePhotosStoresTheReadingTest(unittest.TestCase):
    """Re-analysing must change what the next generation reads.

    The route stored "Photo analysis complete. Found: N photos analyzed.",
    which is not an analysis. ``latest_photo_analysis`` skips it and keeps
    finding the previous reading, so listings were generated from photos that
    had since been read again — including after the vision prompt started
    asking for the department, which is how a women's tee kept coming out as
    menswear even once the model was reporting it correctly.
    """

    def route_source(self) -> str:
        source = (
            Path(__file__).resolve().parents[1] / "vendoo_studio" / "routes" / "chat.py"
        ).read_text(encoding="utf-8")
        body = source[source.index("async def analyze_photos("):]
        marker = "\n@router"
        return body[: body.index(marker)] if marker in body else body

    def test_it_stores_the_analysis_not_a_summary(self):
        route = self.route_source()
        self.assertIn("analysis_text", route)
        self.assertNotIn("photos analyzed.", route)

    def test_what_it_stores_is_what_generation_looks_for(self):
        self.assertTrue(
            photo_analysis_usable("Photo analysis:\n- brand: Belle\n- department: Women")
        )
        # The old summary was not, which is why it never replaced anything.
        self.assertFalse(photo_analysis_usable("Photo analysis complete. Found: 7 photos analyzed."))


if __name__ == "__main__":
    unittest.main()


class SendMessageStreamTest(unittest.TestCase):
    """A chat send answers at once and does its slow preparation inside the stream.

    Photo analysis and sold-comps research ran before the response started, so
    the chat sat silent for minutes, and the request session held a pool
    connection for the whole stream until sends piled up into pool timeouts.
    """

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from vendoo_studio.database import Base
        from vendoo_studio.repositories.queries import ConversationRepo

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        db = self.Session()
        conv = ConversationRepo(db).create(title="Tee")
        listing_persistence.save_revision(db, conv.id, {"title": "Vintage Tee"}, source="user")
        self.conv_id = conv.id
        db.close()

    def send(self, build, provider):
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from vendoo_studio.database import get_db
        from vendoo_studio.main import app

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            with patch("vendoo_studio.routes.chat.get_listing_provider", return_value=provider), \
                 patch("vendoo_studio.routes.chat.SessionLocal", self.Session), \
                 patch("vendoo_studio.database.SessionLocal", self.Session), \
                 patch("vendoo_studio.routes.chat.build_chat_messages", new=build), \
                 patch("vendoo_studio.routes.chat.persist_chat_result", new=AsyncMock(return_value=(None, False))):
                return TestClient(app).post(f"/api/conversations/{self.conv_id}/messages", json={"text": "Make it blue"})
        finally:
            app.dependency_overrides.pop(get_db, None)

    def status(self) -> str:
        from vendoo_studio.repositories.queries import ConversationRepo

        return ConversationRepo(self.Session()).get(self.conv_id).status

    def test_the_stream_reports_progress_before_preparing_the_prompt(self):
        class Provider:
            name = "chatgpt"
            model = "test"

            async def chat(self, messages, stream=True):
                yield "Done."

        async def build(conv_id, db, text, option_catalog=None):
            return [{"role": "user", "content": text}]

        response = self.send(build, Provider())
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.text.index("event: status"), response.text.index("data: Done."))

    def test_the_stream_sends_keepalives_while_preparing_the_prompt(self):
        """Photo analysis can outlast the browser's 20 s silence limit (STREAM_IDLE_MS)."""
        import asyncio
        from unittest.mock import patch

        from vendoo_studio.services.streaming import KEEPALIVE

        class Provider:
            name = "chatgpt"
            model = "test"

            async def chat(self, messages, stream=True):
                yield "Done."

        async def build(conv_id, db, text, option_catalog=None):
            await asyncio.sleep(0.5)
            return [{"role": "user", "content": text}]

        with patch("vendoo_studio.routes.chat.PREPARE_KEEPALIVE_SECONDS", 0.1):
            response = self.send(build, Provider())
        self.assertEqual(response.status_code, 200)
        before_reply = response.text[:response.text.index("data: Done.")]
        self.assertGreaterEqual(before_reply.count(KEEPALIVE), 3)

    def test_a_failed_photo_analysis_ends_the_stream_with_an_error(self):
        from vendoo_studio.services.listing_generate import PhotoAnalysisError

        async def build(conv_id, db, text, option_catalog=None):
            raise PhotoAnalysisError("Photo analysis failed. Retry to analyze the photos again.")

        response = self.send(build, object())
        self.assertEqual(response.status_code, 200)
        self.assertIn("data: Error: Photo analysis failed.", response.text)
        self.assertEqual(self.status(), "draft")


class StoppedChatAnswerIsKeptTest(SendMessageStreamTest):
    """Stop keeps what the assistant had already written, as ChatGPT and Claude do.

    The stream returned at Stop without saving anything, so the reply the seller
    had been reading vanished from the chat. Only its prose is kept: half a
    listing edit is never applied.
    """

    def messages(self):
        from vendoo_studio.repositories.queries import ConversationRepo

        return [(m.role, m.text) for m in ConversationRepo(self.Session()).get_messages(self.conv_id)]

    def test_stop_saves_the_partial_prose_and_applies_nothing(self):
        from vendoo_studio.services import activity

        conv_id = self.conv_id

        class Provider:
            name = "chatgpt"
            model = "test"

            async def chat(self, messages, stream=True):
                yield "Blue suits it. I'll change the color"
                yield "\n\n```json\n{\"color\": \"Bl"
                activity.cancel(conv_id)
                yield "ue\"}\n```"

        async def build(conv_id, db, text, option_catalog=None):
            return [{"role": "user", "content": text}]

        response = self.send(build, Provider())
        self.assertEqual(response.status_code, 200)
        assistant = [text for role, text in self.messages() if role == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertTrue(assistant[0].startswith("Blue suits it. I'll change the color"))
        self.assertIn("Stopped", assistant[0])
        self.assertNotIn("json", assistant[0])
