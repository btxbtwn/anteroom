"""A seller stars the items they care about, on the Mac and on a hosted account."""
from __future__ import annotations

from datetime import datetime

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.conversation import Conversation

PREVIOUS_HEAD = "b0731d829c7b"


def _create(title: str) -> str:
    with SessionLocal() as db:
        row = Conversation(title=title, status="draft", updated_at=datetime(2026, 11, 1, 9, 30))
        db.add(row)
        db.commit()
        return row.id


def _row(client: TestClient, conv_id: str, **kw) -> dict:
    listed = client.get("/api/conversations", **kw)
    assert listed.status_code == 200, listed.text
    return next(row for row in listed.json() if row["id"] == conv_id)


def test_desktop_star_shows_in_inventory_and_keeps_the_last_updated_time():
    client = TestClient(app)
    conv_id = _create("Levi's 501 jeans")
    before = _row(client, conv_id)
    assert before["favorite"] is False

    starred = client.put(f"/api/conversations/{conv_id}/favorite", json={"favorite": True})
    assert starred.status_code == 200, starred.text
    assert starred.json() == {"id": conv_id, "favorite": True}

    after = _row(client, conv_id)
    assert after["favorite"] is True
    # Starring is not an edit: the item keeps its place in Recent activity.
    assert after["updated_at"] == before["updated_at"]
    assert client.get(f"/api/conversations/{conv_id}").json()["favorite"] is True

    unstarred = client.put(f"/api/conversations/{conv_id}/favorite", json={"favorite": False})
    assert unstarred.json() == {"id": conv_id, "favorite": False}
    assert _row(client, conv_id)["favorite"] is False


def test_desktop_star_refuses_a_missing_item_and_a_malformed_body():
    client = TestClient(app)
    conv_id = _create("Patagonia fleece")
    assert client.put("/api/conversations/missing/favorite", json={"favorite": True}).status_code == 404
    assert client.put(f"/api/conversations/{conv_id}/favorite", json={}).status_code == 422
    assert client.put(f"/api/conversations/{conv_id}/favorite", json={"favorite": "yes"}).status_code == 422


def test_hosted_star_is_private_to_the_seller(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    created = client.post("/api/conversations", headers=alice, json={"title": "Coach bag"})
    assert created.status_code == 200, created.text
    conv_id = created.json()["id"]
    before = _row(client, conv_id, headers=alice)
    assert before["favorite"] is False

    assert client.put(f"/api/conversations/{conv_id}/favorite", headers=bob, json={"favorite": True}).status_code == 404
    starred = client.put(f"/api/conversations/{conv_id}/favorite", headers=alice, json={"favorite": True})
    assert starred.status_code == 200, starred.text
    assert starred.json() == {"id": conv_id, "favorite": True}
    after = _row(client, conv_id, headers=alice)
    assert after["favorite"] is True
    assert after["updated_at"] == before["updated_at"]
    assert client.get(f"/api/conversations/{conv_id}", headers=alice).json()["favorite"] is True


def test_previous_release_items_upgrade_unstarred(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, PREVIOUS_HEAD)
    assert "favorite" not in {column["name"] for column in inspect(engine).get_columns("conversations")}
    conversations = Table("conversations", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(conversations.insert(), {"id": "legacy-item", "title": "Made before favorites",
                                                    "status": "draft", "notes": "{}"})

    command.upgrade(config, "head")
    columns = {column["name"]: column for column in inspect(engine).get_columns("conversations")}
    assert columns["favorite"]["nullable"] is False
    with Session(engine) as db:
        legacy = db.get(Conversation, "legacy-item")
        assert legacy is not None and legacy.favorite is False and legacy.title == "Made before favorites"

    command.downgrade(config, PREVIOUS_HEAD)
    assert "favorite" not in {column["name"] for column in inspect(engine).get_columns("conversations")}
