"""Set price: a push onto a live listing names its marketplace and carries confirm."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.routes import listing_commands


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def saved(monkeypatch):
    """Skip the database: the price saves and three marketplaces are live."""
    live = {name: {"state": "live", "listing_id": f"{name}-1"} for name in ("ebay", "poshmark", "depop")}

    async def fake_session(_call, _conv_id, body):
        return {"market": str(body.marketplace or "").lower(), "price": body.price, "revision_id": "r2",
                "current_revision_id": "r2", "live": live}

    monkeypatch.setattr(listing_commands, "in_worker_session", fake_session)
    updated: list[str] = []

    async def fake_update(_conv_id, name, request):
        assert request.confirm is True
        updated.append(name)
        return {}

    from vendoo_studio.services import native_marketplaces

    monkeypatch.setattr(native_marketplaces, "update_live", fake_update)
    return updated


def test_a_push_without_a_named_marketplace_is_refused(client, saved):
    reply = client.post("/api/conversations/c1/set-price", json={"price": 10, "push": True, "confirm": True})
    assert reply.status_code == 400
    assert "marketplace" in reply.json()["detail"].lower()
    assert saved == []


def test_a_push_needs_confirm(client, saved):
    for body in ({"price": 10, "push": True, "marketplace": "ebay"},
                 {"price": 10, "push": True, "marketplace": "ebay", "confirmed": True}):
        reply = client.post("/api/conversations/c1/set-price", json=body)
        assert reply.status_code == 400, body
    assert saved == []


def test_a_push_updates_only_the_named_marketplace(client, saved):
    reply = client.post("/api/conversations/c1/set-price",
                        json={"price": 10, "push": True, "confirm": True, "marketplace": "poshmark"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["pushed"] == ["poshmark"]
    assert saved == ["poshmark"]


def test_a_price_without_push_touches_no_marketplace(client, saved):
    reply = client.post("/api/conversations/c1/set-price", json={"price": 10})
    assert reply.status_code == 200, reply.text
    assert saved == []
