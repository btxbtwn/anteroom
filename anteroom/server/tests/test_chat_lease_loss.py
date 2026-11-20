"""A revoked hosted lease stops chat without attempting another fenced save."""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom import database
from anteroom.repositories.queries import ConversationRepo
from anteroom.routes import chat
from anteroom.services import activity, background_tasks


@pytest.fixture
def conversation(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        conv_id = ConversationRepo(db).create(title="Offline draft").id
    monkeypatch.setattr(database, "SessionLocal", sessions)

    class Provider:
        name = "test"
        listing_model = "offline"

        async def chat(self, _messages, stream=True):
            yield "An offline answer."

    monkeypatch.setattr(chat, "get_listing_provider", lambda: Provider())
    monkeypatch.setattr(chat, "build_chat_messages", AsyncMock(return_value=[]))
    yield conv_id, sessions
    engine.dispose()


@pytest.mark.parametrize("failure", [background_tasks.TaskStopped, background_tasks.TaskLeaseExpired])
@pytest.mark.parametrize("phase", ["prepare", "persist"])
def test_revoked_lease_reaches_worker_without_error_or_cleanup_write(conversation, monkeypatch, caplog, failure, phase):
    conv_id, _sessions = conversation
    rejected = failure()
    persist = AsyncMock(return_value=(None, False))
    monkeypatch.setattr(chat, "persist_chat_result", persist)
    if phase == "persist":
        persist.side_effect = rejected
    else:
        monkeypatch.setattr(chat, "build_chat_messages", AsyncMock(side_effect=rejected))
    reset = Mock(wraps=chat._reset_status)
    stopped_answer = Mock(wraps=chat._save_stopped_answer)
    monkeypatch.setattr(chat, "_reset_status", reset)
    monkeypatch.setattr(chat, "_save_stopped_answer", stopped_answer)
    frames = []

    async def consume():
        response = await chat.run_message(conv_id, chat.ChatMessage(text="Explain this draft"), stored_attachments=[])
        with pytest.raises(failure) as caught:
            async for frame in response.body_iterator:
                frames.append(frame)
        assert caught.value is rejected
        await asyncio.sleep(0)  # Finish the tracked save's activity callback.

    with caplog.at_level(logging.ERROR, logger=chat.log.name):
        asyncio.run(consume())
    reset.assert_not_called()
    stopped_answer.assert_not_called()
    assert not any("Error:" in frame or "event: error" in frame or "listing_updated" in frame for frame in frames)
    assert not [record for record in caplog.records if record.name == chat.log.name and record.levelno >= logging.ERROR]
    assert activity.running(conv_id) == []
    assert persist.await_count == (1 if phase == "persist" else 0)


def test_other_save_failures_still_report_error_and_clear_busy_status(conversation, monkeypatch, caplog):
    conv_id, sessions = conversation
    monkeypatch.setattr(chat, "persist_chat_result", AsyncMock(side_effect=ValueError("offline save failed")))

    async def consume():
        response = await chat.run_message(conv_id, chat.ChatMessage(text="Explain this draft"), stored_attachments=[])
        return [frame async for frame in response.body_iterator]

    with caplog.at_level(logging.ERROR, logger=chat.log.name):
        frames = asyncio.run(consume())
    assert frames[-1] == "data: [DONE]\n\n"
    assert any("failed to persist chat result" in record.message for record in caplog.records)
    with sessions() as db:
        assert ConversationRepo(db).get(conv_id).status == "draft"
    assert activity.running(conv_id) == []
