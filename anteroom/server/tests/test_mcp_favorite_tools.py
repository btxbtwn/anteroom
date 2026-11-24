"""Agents star items with set_favorite and find them with search_inventory."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from test_mcp_http import _call_tool, _clean_mcp_tables, _tool_names  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.conversation import Conversation

UPDATED = datetime(2026, 11, 1, 9, 30)


def _create(*titles: str) -> list[str]:
    with SessionLocal() as db:
        rows = [Conversation(title=title, status="draft", updated_at=UPDATED) for title in titles]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def _favorites() -> dict[str, bool]:
    with SessionLocal() as db:
        return {row.id: row.favorite for row in db.query(Conversation).all()}


def test_set_favorite_stars_one_or_many_items_and_reports_unknown_ids():
    client = TestClient(app)
    jeans, coat, bag = _create("Levi's jeans", "Wool coat", "Coach bag")

    one, is_error = _call_tool(client, "set_favorite", {"conversation_ids": [jeans], "favorite": True})
    assert not is_error, one
    assert one["updated"] == [jeans] and one["favorite"] is True and one["not_found"] == []
    assert _favorites() == {jeans: True, coat: False, bag: False}

    many, is_error = _call_tool(client, "set_favorite", {"conversation_ids": [coat, bag, coat, "nope"], "favorite": True})
    assert not is_error, many
    assert sorted(many["updated"]) == sorted([coat, bag])
    assert many["not_found"] == ["nope"]
    assert "never" in many["note"].lower()

    off, is_error = _call_tool(client, "set_favorite", {"conversation_ids": [jeans], "favorite": False})
    assert not is_error, off
    assert _favorites() == {jeans: False, coat: True, bag: True}
    # Starring is not an edit: the items keep their last-updated time.
    with SessionLocal() as db:
        assert {row.updated_at for row in db.query(Conversation).all()} == {UPDATED}


def test_set_favorite_refuses_bad_arguments():
    client = TestClient(app)
    (item,) = _create("Nike tee")
    for arguments in ({"conversation_ids": [item]}, {"conversation_ids": [], "favorite": True},
                      {"conversation_ids": item, "favorite": True}, {"conversation_ids": [item], "favorite": "yes"},
                      {"conversation_ids": ["x"] * 101, "favorite": True}):
        result, is_error = _call_tool(client, "set_favorite", arguments)
        assert is_error, (arguments, result)
    none, is_error = _call_tool(client, "set_favorite", {"conversation_ids": ["missing"], "favorite": True})
    assert is_error and "No items found" in none["error"]


def test_search_inventory_filters_to_favorites_and_marks_them():
    client = TestClient(app)
    jeans, coat = _create("Levi's jeans", "Wool coat")
    _call_tool(client, "set_favorite", {"conversation_ids": [coat], "favorite": True})

    everything, _ = _call_tool(client, "search_inventory", {})
    assert {item["conversation_id"]: item["favorite"] for item in everything["items"]} == {jeans: False, coat: True}
    starred, _ = _call_tool(client, "search_inventory", {"favorites_only": True})
    assert [item["conversation_id"] for item in starred["items"]] == [coat]
    assert starred["total"] == 1


def test_set_favorite_needs_listings_write():
    client = TestClient(app)
    created = client.post("/api/settings/mcp-tokens", json={"name": "Reader"})
    bearer = {"Authorization": f"Bearer {created.json()['token']}"}
    (item,) = _create("Nike tee")
    assert "set_favorite" not in _tool_names(client, bearer)
    refused, is_error = _call_tool(client, "set_favorite", {"conversation_ids": [item], "favorite": True}, extra_headers=bearer)
    assert is_error and "Listings to Read and write" in refused["error"]
    assert _favorites() == {item: False}

    client.patch(f"/api/settings/mcp-tokens/{created.json()['id']}", json={"permissions": {"listings": "write"}})
    assert "set_favorite" in _tool_names(client, bearer)
    done, is_error = _call_tool(client, "set_favorite", {"conversation_ids": [item], "favorite": True}, extra_headers=bearer)
    assert not is_error, done
    assert _favorites() == {item: True}
