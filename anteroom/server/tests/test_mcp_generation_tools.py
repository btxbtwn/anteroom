"""``generate_listings`` over MCP: Generate for the drafts an AI assistant names, never a publish."""
from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_mcp_photo_tools import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.mcp_token import McpAccessToken
from anteroom.routes import chat
from anteroom.services import listing_provider, mcp_http, mcp_tokens


@pytest.fixture(autouse=True)
def _clean():
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot
    from anteroom.models.marketplace_listing import MarketplaceListing

    with SessionLocal() as db:
        for model in (McpAccessToken, BackgroundTask, ListingCorrection, SaleSnapshot, Message, Job,
                      MarketplaceListing, Photo, Listing, ListingRevision, Conversation):
            db.query(model).delete()
        db.commit()
    mcp_tokens.clear_auth_failures()
    yield
    mcp_tokens.clear_auth_failures()


@pytest.fixture
def provider(monkeypatch):
    """A connected listing model; generation itself is replaced where a test starts it."""
    monkeypatch.setattr(listing_provider, "get_listing_provider", lambda: object())


@pytest.fixture
def started(monkeypatch):
    """Record each draft the Mac starts generating instead of running a model."""
    ids: list[str] = []

    async def generate(conv_id, resume=False):
        ids.append(conv_id)

    monkeypatch.setattr(chat, "generate_listing", generate)
    return ids


def _rpc(client, method, params=None, extra_headers=None):
    return client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                       headers=extra_headers or {})


def _call(client, name, arguments, extra_headers=None):
    resp = _rpc(client, "tools/call", {"name": name, "arguments": arguments}, extra_headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert [item["type"] for item in result["content"]] == ["text"], result
    text = result["content"][0]["text"]
    if result["isError"]:
        return {"error": text}, True
    return json.loads(text), False


def _jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def _draft(client, title="A4-1", photos=1, extra_headers=None) -> str:
    made, error = _call(client, "create_draft_listing", {"title": title}, extra_headers)
    assert not error, made
    if photos:
        added, error = _call(client, "add_listing_photos", {
            "conversation_id": made["conversation_id"], "photos": [{"data": _jpeg()}] * photos,
        }, extra_headers)
        assert not error, added
    return made["conversation_id"]


def _status(conv_id, status):
    with SessionLocal() as db:
        db.get(Conversation, conv_id).status = status
        db.commit()


def test_generate_listings_is_listed_and_needs_listings_write(provider, started):
    assert "generate_listings" in {tool["name"] for tool in mcp_http.tool_definitions()}
    assert mcp_http.TOOL_PERMISSIONS["generate_listings"] == ("listings", "write")

    client = TestClient(app)
    conv_id = _draft(client)
    made = client.post("/api/settings/mcp-tokens", json={"name": "Reader", "permissions": {"listings": "read"}})
    reader = {"Authorization": f"Bearer {made.json()['token']}"}
    listed = {t["name"] for t in _rpc(client, "tools/list", extra_headers=reader).json()["result"]["tools"]}
    assert "generate_listings" not in listed
    refused, error = _call(client, "generate_listings", {"conversation_ids": [conv_id]}, reader)
    assert error and "not allowed to use generate_listings" in refused["error"]
    assert started == []


def test_generate_listings_starts_each_draft_with_photos_and_skips_the_rest(provider, started):
    client = TestClient(app)
    first, second = _draft(client, "A4-1"), _draft(client, "A4-2")
    bare = _draft(client, "No photos", photos=0)
    live = _draft(client, "Listed")
    _status(live, "active")
    busy = _draft(client, "Busy")
    _status(busy, "in_progress")

    result, error = _call(client, "generate_listings", {
        "conversation_ids": [first, bare, live, busy, "missing", second, first],
    })
    assert not error, result
    assert result["started"] == started == [first, second]
    assert result["published"] is False and result["pushed_to_marketplaces"] is False
    reasons = {row["conversation_id"]: row["reason"] for row in result["skipped"]}
    assert set(reasons) == {bare, live, busy, "missing"}
    assert "No photos" in reasons[bare]
    assert "Not a draft" in reasons[live]
    assert reasons[busy] == "Already generating."
    assert reasons["missing"] == "Listing not found."


def test_a_draft_live_on_a_marketplace_is_never_regenerated(provider, started):
    from anteroom.models.marketplace_listing import MarketplaceListing

    client = TestClient(app)
    conv_id = _draft(client)
    with SessionLocal() as db:
        db.add(MarketplaceListing(conversation_id=conv_id, marketplace="ebay", state="published"))
        db.commit()
    result, error = _call(client, "generate_listings", {"conversation_ids": [conv_id]})
    assert not error, result
    assert result["started"] == [] and "Live on a marketplace" in result["skipped"][0]["reason"]
    assert started == []


def test_a_draft_that_cannot_start_does_not_stop_the_others(provider, monkeypatch):
    client = TestClient(app)
    bad, good = _draft(client, "Bad"), _draft(client, "Good")
    ids: list[str] = []

    async def generate(conv_id, resume=False):
        if conv_id == bad:
            raise HTTPException(400, "Connect an AI provider in Settings first.")
        ids.append(conv_id)

    monkeypatch.setattr(chat, "generate_listing", generate)
    result, error = _call(client, "generate_listings", {"conversation_ids": [bad, good]})
    assert not error, result
    assert result["started"] == ids == [good]
    assert result["skipped"] == [{"conversation_id": bad, "reason": "Connect an AI provider in Settings first."}]


def test_generate_listings_needs_a_connected_model(monkeypatch, started):
    monkeypatch.setattr(listing_provider, "get_listing_provider", lambda: None)
    client = TestClient(app)
    conv_id = _draft(client)
    refused, error = _call(client, "generate_listings", {"conversation_ids": [conv_id]})
    assert error and "Connect an AI provider" in refused["error"]
    assert started == []


def test_generate_listings_bounds_its_input(provider, started):
    client = TestClient(app)
    for arguments, message in [
        ({"conversation_ids": []}, "at least one"),
        ({"conversation_ids": ["x"] * 51}, "at most 50"),
        ({"conversation_ids": [""]}, "listing ID"),
    ]:
        refused, error = _call(client, "generate_listings", arguments)
        assert error and message in refused["error"], refused


# ---------------------------------------------------------------------------
# Hosted
# ---------------------------------------------------------------------------


def _hosted_token(client, keys, seller):
    made = client.post("/api/settings/mcp-tokens", headers=headers(keys, seller),
                       json={"name": "Lister", "permissions": {"listings": "write"}})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def test_hosted_queues_the_sellers_durable_generate_task(cloud, signing_keys, storage, provider):  # noqa: F811
    client, engine = cloud
    alice = _hosted_token(client, signing_keys, USER_ID)
    bob = _hosted_token(client, signing_keys, BOB)
    conv_id = _draft(client, extra_headers=alice)

    refused, error = _call(client, "generate_listings", {"conversation_ids": [conv_id]}, bob)
    assert not error and refused["started"] == [] and refused["skipped"][0]["reason"] == "Listing not found."

    result, error = _call(client, "generate_listings", {"conversation_ids": [conv_id]}, alice)
    assert not error, result
    assert result["started"] == [conv_id]
    with Session(engine) as db:
        tasks = db.query(BackgroundTask).filter(BackgroundTask.conversation_id == conv_id).all()
    assert [(task.kind, task.user_id) for task in tasks] == [("generate", USER_ID)]
