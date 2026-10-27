"""Images the seller attaches in the composer: stored on disk, kept on the message, seen by the model."""
from __future__ import annotations

import base64
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.routes import attachments as attachments_route
from vendoo_studio.routes import chat as chat_route
from vendoo_studio.routes import conversations as conversations_route
from vendoo_studio.services import chat_attachments


def png_data_url(width=4, height=3, color=(200, 30, 30)) -> str:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments, "attachments_dir", lambda: tmp_path)
    return tmp_path


def test_an_image_is_stored_on_disk_and_described_on_the_message(store):
    saved = chat_attachments.save_attachment("tag.png", png_data_url(4, 3))
    assert saved["mime_type"] == "image/png"
    assert (saved["width"], saved["height"]) == (4, 3)
    assert saved["name"] == "tag.png"
    assert (store / saved["id"]).is_file()
    assert chat_attachments.attachment_path(saved["id"]) == store / saved["id"]


def test_what_is_not_an_image_is_refused_and_nothing_is_kept(store):
    good = ("ok.png", png_data_url())
    bad = ("notes.txt", "data:text/plain;base64," + base64.b64encode(b"hello").decode())
    with pytest.raises(ValueError, match="not a JPEG, PNG, WebP or HEIC image"):
        chat_attachments.save_attachments([good, bad])
    assert list(store.iterdir()) == []
    with pytest.raises(ValueError, match="at most 6"):
        chat_attachments.save_attachments([good] * 7)
    with pytest.raises(ValueError, match="image files"):
        chat_attachments.save_attachment("x.png", "https://example.com/x.png")


def test_ids_that_are_not_attachments_never_reach_the_disk(store):
    assert chat_attachments.attachment_path("../vendoo_studio.db") is None
    assert chat_attachments.attachment_path("0" * 32 + ".exe") is None


def test_only_the_newest_question_carries_its_images(store):
    saved = chat_attachments.save_attachment("a.png", png_data_url())
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "What is this stain?"},
    ]
    out = chat_attachments.attach_to_last_user_turn(messages, [saved])
    assert out[1]["content"] == "earlier"
    text, image = out[3]["content"]
    assert text == {"type": "text", "text": "What is this stain?"}
    assert image["type"] == "image_url" and image["image_url"]["url"].startswith("data:image/png;base64,")
    assert chat_attachments.attach_to_last_user_turn(messages, []) is messages


@pytest.fixture
def listing_chat(store, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    conv = ConversationRepo(db).create(title="Tee")
    ListingRepo(db).save_revision(conv.id, {"title": "Vintage Tee"}, source="user")
    app = FastAPI()
    app.include_router(chat_route.router)
    app.include_router(conversations_route.router)
    app.include_router(attachments_route.router)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(chat_route, "SessionLocal", factory)
    with TestClient(app) as client:
        yield client, conv.id
    db.close()
    engine.dispose()


class SeeingProvider:
    name = "chatgpt"
    listing_model = "test"

    def __init__(self):
        self.calls: list[list[dict]] = []

    async def chat(self, messages, stream=True):
        self.calls.append(messages)
        yield "A red tee."


def test_a_listing_chat_message_sends_its_images_and_keeps_them(listing_chat):
    client, conv_id = listing_chat
    provider = SeeingProvider()

    async def build(conv_id, db, text):
        return [{"role": "system", "content": "rules"}, {"role": "user", "content": text}]

    with patch.object(chat_route, "get_listing_provider", return_value=provider), \
         patch.object(chat_route, "build_chat_messages", new=build), \
         patch.object(chat_route, "persist_chat_result", new=AsyncMock(return_value=(None, False))):
        response = client.post(f"/api/conversations/{conv_id}/messages", json={
            "text": "What color is it?",
            "attachments": [{"name": "front.png", "data_url": png_data_url()}],
        })
    assert response.status_code == 200
    assert "data: A red tee." in response.text
    turn = provider.calls[0][-1]["content"]
    assert turn[0] == {"type": "text", "text": "What color is it?"}
    assert turn[1]["type"] == "image_url"

    saved = [m for m in client.get(f"/api/conversations/{conv_id}/messages").json() if m["role"] == "user"][-1]
    assert saved["text"] == "What color is it?"
    [attachment] = saved["attachments"]
    assert attachment["name"] == "front.png"
    served = client.get(f"/api/chat-attachments/{attachment['id']}")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert client.get("/api/chat-attachments/nope.png").status_code == 404


def test_a_bad_attachment_refuses_the_message(listing_chat):
    client, conv_id = listing_chat
    response = client.post(f"/api/conversations/{conv_id}/messages", json={
        "text": "Look",
        "attachments": [{"name": "x.gif", "data_url": "data:image/gif;base64,R0lGODlhAQABAAAAACw="}],
    })
    assert response.status_code == 400
    assert not [m for m in client.get(f"/api/conversations/{conv_id}/messages").json() if m["role"] == "user"]


def test_the_assistant_takes_an_image_without_a_question(store, monkeypatch, tmp_path):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_route, "SessionLocal", factory)
    monkeypatch.setattr("vendoo_studio.services.box_scout.state_path", lambda: tmp_path / "sourcing.json")
    provider = SeeingProvider()
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)
    db = factory()
    app = FastAPI()
    app.include_router(assistant_route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.post("/api/assistant/messages", json={
            "text": "", "attachments": [{"name": "receipt.png", "data_url": png_data_url()}],
        })
        assert response.status_code == 200
        question = provider.calls[0][-1]["content"]
        assert question[0] == {"type": "text", "text": "See the attached images."}
        assert question[1]["type"] == "image_url"
        saved = client.get("/api/assistant/messages").json()[0]
        assert saved["text"] == "" and saved["attachments"][0]["name"] == "receipt.png"
        assert client.post("/api/assistant/messages", json={"text": "  "}).status_code == 422
    db.close()
    engine.dispose()


def test_a_chat_image_grid_is_a_small_jpeg_and_deleting_removes_it(store):
    saved = chat_attachments.save_attachment("front.png", png_data_url(800, 600))
    app = FastAPI()
    app.include_router(attachments_route.router)
    with TestClient(app) as client:
        original = client.get(f"/api/chat-attachments/{saved['id']}")
        assert original.status_code == 200
        assert original.headers["content-type"] == "image/png"

        thumb = client.get(f"/api/chat-attachments/{saved['id']}", params={"size": 192})
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(thumb.content)) as image:
            assert max(image.size) <= 192
        assert len(thumb.content) < len(original.content)

        cached = store / f"{saved['id'].removesuffix('.png')}_192.jpg"
        assert cached.is_file()
        written_at = cached.stat().st_mtime_ns
        again = client.get(f"/api/chat-attachments/{saved['id']}", params={"size": 192})
        assert again.status_code == 200
        assert cached.stat().st_mtime_ns == written_at
        assert client.get(f"/api/chat-attachments/{saved['id']}", params={"size": 1000}).status_code == 400

    chat_attachments.delete_attachments([saved])
    assert not (store / saved["id"]).exists()
    assert not cached.exists()
