"""Facts the business assistant keeps, and rewrites, across conversations."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.services import assistant_memory, business_assistant


@pytest.fixture
def workspace(monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_route, "SessionLocal", factory)
    db = factory()
    app = FastAPI()
    app.include_router(assistant_route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield db, client
    db.close()
    engine.dispose()


def test_a_reply_keeps_its_words_and_hands_over_memory_and_changes():
    words, memory, changes = assistant_memory.peel(
        'Three sold.\n```changes\n[{"listing": "abc"}]\n```\n'
        '```memory\n[{"op": "add", "text": "Ships from Austin."}]\n```'
    )
    assert words == "Three sold."
    assert json.loads(changes) == [{"listing": "abc"}]
    assert json.loads(memory) == [{"op": "add", "text": "Ships from Austin."}]
    assert assistant_memory.visible_text("Three sold.\n```mem") == "Three sold.\n"


def test_facts_are_added_updated_and_dropped_and_a_repeat_is_not_stored_twice(workspace, monkeypatch):
    db, client = workspace
    monkeypatch.setattr(assistant_memory, "MAX_MEMORIES", 2)
    assistant_memory.apply(db, json.dumps([
        {"op": "add", "text": "  Does not source Nike.  "},
        {"op": "add", "text": "does not source nike."},
        {"op": "ADD", "text": "Wants $15 profit per item."},
        {"op": "none"},
        "skip me",
    ]))
    db.commit()
    kept = assistant_memory.list_memories(db)
    assert {fact["text"] for fact in kept} == {"Wants $15 profit per item.", "Does not source Nike."}

    nike = next(fact for fact in kept if fact["text"] == "Does not source Nike.")
    assistant_memory.apply(db, json.dumps([
        {"op": "update", "id": nike["id"], "text": "Does not source Nike or Adidas."},
        {"op": "update", "id": "missing", "text": "Ignored."},
        {"op": "add", "text": "This third fact does not fit."},
    ]))
    db.commit()
    assert [fact["text"] for fact in assistant_memory.list_memories(db)] == [
        "Does not source Nike or Adidas.",
        "Wants $15 profit per item.",
    ]

    profit = next(fact for fact in assistant_memory.list_memories(db) if "profit" in fact["text"])
    assistant_memory.apply(db, json.dumps([{"op": "delete", "id": profit["id"]}, {"op": "delete", "id": "missing"}]))
    db.commit()
    assert [fact["text"] for fact in assistant_memory.list_memories(db)] == ["Does not source Nike or Adidas."]

    long = "y" * 300
    assistant_memory.apply(db, json.dumps([{"op": "add", "text": long}]))
    db.commit()
    stored = next(fact["text"] for fact in assistant_memory.list_memories(db) if fact["text"].startswith("y"))
    assert len(stored) == assistant_memory.MAX_FACT_CHARS


def test_an_update_that_repeats_another_fact_drops_the_duplicate(workspace):
    db, _client = workspace
    assistant_memory.apply(db, json.dumps([
        {"op": "add", "text": "Ships from Austin."},
        {"op": "add", "text": "Ships from Dallas."},
    ]))
    db.commit()
    dallas = next(fact for fact in assistant_memory.list_memories(db) if "Dallas" in fact["text"])
    assistant_memory.apply(db, json.dumps([{"op": "update", "id": dallas["id"], "text": "Ships from Austin."}]))
    db.commit()
    assert [fact["text"] for fact in assistant_memory.list_memories(db)] == ["Ships from Austin."]


def test_the_shop_brief_includes_every_fact_and_clearing_a_chat_keeps_them(workspace):
    db, client = workspace
    assistant_memory.apply(db, json.dumps([{"op": "add", "text": "Lists about 20 items a week."}]))
    db.commit()
    brief = business_assistant.read_shop(db).brief
    fact = assistant_memory.list_memories(db)[0]
    assert f"--- What you remember ---\n- {fact['id']}: Lists about 20 items a week." in brief

    business_assistant.add_message(db, "user", "How is the shop doing?")
    business_assistant.clear_messages(db)
    assert assistant_memory.list_memories(db) == [fact]

    listed = client.get("/api/assistant/memory").json()["memories"]
    assert listed == [fact]
    assert client.delete(f"/api/assistant/memory/{fact['id']}").json() == {"memories": []}
    assert client.delete(f"/api/assistant/memory/{fact['id']}").status_code == 404
    assert "Nothing yet." in business_assistant.read_shop(db).brief
    assert assistant_memory.apply(db, "not json") is None
