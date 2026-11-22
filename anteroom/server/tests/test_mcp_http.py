"""Tests for the Streamable HTTP MCP server (/api/mcp), MCP access tokens, and hosted seller isolation."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.mcp_token import McpAccessToken
from anteroom.models.sourcing import SourceBox
from anteroom.services import mcp_http, sales_ledger


def _rpc(client: TestClient, method: str, params: dict | None = None, req_id: int | None = 1, extra_headers: dict | None = None):
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        payload["id"] = req_id
    if params is not None:
        payload["params"] = params
    return client.post("/api/mcp", json=payload, headers=extra_headers or {})


def _call_tool(client: TestClient, name: str, arguments: dict | None = None, extra_headers: dict | None = None) -> tuple[dict, bool]:
    resp = _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, extra_headers=extra_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "result" in body, body
    result = body["result"]
    content_text = result["content"][0]["text"]
    is_error = bool(result.get("isError"))
    if is_error:
        return {"error": content_text}, True
    return json.loads(content_text), False


@pytest.fixture(autouse=True)
def _clean_mcp_tables():
    from anteroom.models.background_task import BackgroundTask
    from anteroom.models.conversation import Message
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot

    with SessionLocal() as db:
        for model in (
            McpAccessToken,
            BackgroundTask,
            ListingCorrection,
            SaleSnapshot,
            Message,
            Job,
            MarketplaceSale,
            MarketplaceListing,
            Photo,
            Listing,
            ListingRevision,
            Conversation,
            SourceBox,
        ):
            db.query(model).delete()
        db.commit()
    yield


def test_mcp_protocol_handshake_sse_and_errors():
    client = TestClient(app)

    # Initialize (negotiates requested supported version)
    init = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "pytest"}})
    assert init.status_code == 200
    data = init.json()
    assert data["result"]["protocolVersion"] == "2025-03-26"
    assert data["result"]["serverInfo"]["name"] == "anteroom"
    assert "tools" in data["result"]["capabilities"]

    # Initialize without requested version defaults to LATEST_PROTOCOL_VERSION
    init_latest = _rpc(client, "initialize", {"clientInfo": {"name": "pytest"}})
    assert init_latest.status_code == 200
    assert init_latest.json()["result"]["protocolVersion"] == mcp_http.LATEST_PROTOCOL_VERSION

    # Ping
    ping = _rpc(client, "ping")
    assert ping.status_code == 200
    assert ping.json()["result"] == {}

    # Notification returns 202 Accepted with empty body
    notif = client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notif.status_code == 202

    # SSE response when Accept: text/event-stream is requested without application/json
    sse = _rpc(client, "ping", extra_headers={"Accept": "text/event-stream"})
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    assert sse.text.startswith("event: message\ndata: ")

    # GET returns 405, DELETE returns 204
    assert client.get("/api/mcp").status_code == 405
    assert client.delete("/api/mcp").status_code == 204

    # Unknown method -> -32601
    unknown = _rpc(client, "does/not/exist")
    assert unknown.status_code == 200
    assert unknown.json()["error"]["code"] == -32601

    # Invalid JSON -> -32700
    bad = client.post("/api/mcp", content=b"{not-json", headers={"Content-Type": "application/json"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == -32700


def test_mcp_tools_list_and_read_write_lifecycle_respects_safety_invariant():
    client = TestClient(app)
    now = datetime.now(UTC).replace(tzinfo=None)

    # List tools and confirm NO publish/delist/push tools exist
    listed = _rpc(client, "tools/list")
    assert listed.status_code == 200
    tools = listed.json()["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "search_inventory",
        "get_listing",
        "get_sales_analytics",
        "get_offer_activity",
        "get_sourcing_roi",
        "run_etsy_audit",
        "get_needs_attention",
        "create_draft_listing",
        "edit_listing",
        "manage_listing_photos",
        "manage_labels_and_notes",
        "delete_draft_listing",
    }
    assert expected_tools.issubset(tool_names)
    for forbidden in ("publish", "delist", "push_live", "send_to_marketplace"):
        assert all(forbidden not in name for name in tool_names)

    # Seed box + live listing + sale for read tools
    with SessionLocal() as db:
        box = SourceBox(store="Local Estate", title="Estate Lot A", price=100.0, pieces=2)
        db.add(box)
        db.flush()

        live_conv = Conversation(id="conv-live", title="Vintage Brass Teapot", status="active", box_id=box.id)
        db.add(live_conv)
        db.flush()
        live_rev = ListingRevision(
            id="rev-live-1",
            conversation_id=live_conv.id,
            listing_json={"title": "Vintage Brass Teapot", "price": 65.0, "cost": 20.0, "sku": "SKU-TP1"},
            source="test",
        )
        db.add(live_rev)
        db.flush()
        db.add(Listing(conversation_id=live_conv.id, current_revision_id=live_rev.id))
        db.add(
            MarketplaceListing(
                conversation_id=live_conv.id,
                marketplace="ebay",
                state="published",
                listing_id="eb-live-1",
                revision_id=live_rev.id,
                listed_price=65.0,
                listed_at=now,
            )
        )
        sale = MarketplaceSale(
            conversation_id=live_conv.id,
            marketplace="ebay",
            order_id="ord-live-1",
            line_id="line-live-1",
            listing_id="eb-live-1",
            quantity=1,
            sold_at=now,
        )
        db.add(sale)
        db.flush()
        sale.financials = {
            "currency": "USD",
            "item_revenue": "65.00",
            "shipping_credit": "0.00",
            "refunds": "0.00",
            "fees": "8.00",
            "shipping_cost": "7.00",
            "item_cost": "20.00",
            "order_basis": sales_ledger.order_basis(sale),
            "source": "seller",
        }
        db.commit()

    # 1) Read tools
    inv, err = _call_tool(client, "search_inventory", {"query": "Teapot"})
    assert not err
    assert inv["total"] == 1
    assert inv["items"][0]["conversation_id"] == "conv-live"

    detail, err = _call_tool(client, "get_listing", {"conversation_id": "conv-live"})
    assert not err
    assert detail["listing"]["title"] == "Vintage Brass Teapot"
    assert detail["listing"]["sku"] == "SKU-TP1"

    sales, err = _call_tool(client, "get_sales_analytics", {"range": "30d"})
    assert not err
    assert sales["sales"]["count"] >= 1
    assert sales["sales"]["revenue"] >= 65.0

    offers, err = _call_tool(client, "get_offer_activity", {"limit": 10})
    assert not err
    assert "summary" in offers and "recent_offers" in offers

    sourcing, err = _call_tool(client, "get_sourcing_roi", {})
    assert not err
    assert sourcing["box_count"] == 1

    etsy, err = _call_tool(client, "run_etsy_audit", {})
    assert not err
    assert "count" in etsy and "items" in etsy

    attention, err = _call_tool(client, "get_needs_attention", {})
    assert not err
    assert "today" in attention and "needs_attention" in attention

    # 2) Safe write tools: create_draft_listing -> edit_listing -> photos -> labels/notes -> delete_draft_listing
    created, err = _call_tool(
        client,
        "create_draft_listing",
        {
            "title": "Handcrafted Ceramic Mug",
            "description": "Wheel-thrown stoneware mug.",
            "price": 34.0,
            "cost": 6.0,
            "sku": "MUG-01",
            "labels": ["ceramics", "kitchen"],
            "private_notes": "No chips or cracks.",
        },
    )
    assert not err, created
    draft_id = created["conversation_id"]
    assert created["status"] == "draft"
    assert created["published"] is False

    # Edit draft fields
    edited, err = _call_tool(
        client,
        "edit_listing",
        {
            "conversation_id": draft_id,
            "fields": {
                "title": "Handcrafted Stoneware Mug",
                "price": 38.0,
                "condition": "Pre-owned",
            },
        },
    )
    assert not err, edited
    assert edited["pushed_to_marketplaces"] is False
    assert edited["listing"]["title"] == "Handcrafted Stoneware Mug"
    assert edited["listing"]["price"] == pytest.approx(38.0)

    # Edit a LIVE listing -> stages local unsent edit, NEVER mutates MarketplaceListing live state
    live_edit, err = _call_tool(
        client,
        "edit_listing",
        {"conversation_id": "conv-live", "fields": {"price": 72.0}},
    )
    assert not err, live_edit
    assert live_edit["pushed_to_marketplaces"] is False
    assert live_edit["unsent_live_edit"] is True
    with SessionLocal() as db:
        ml = db.query(MarketplaceListing).filter(MarketplaceListing.conversation_id == "conv-live").one()
        assert ml.state == "published"
        assert ml.listed_price == pytest.approx(65.0)  # Unchanged on live marketplace!
        assert ml.revision_id == "rev-live-1"  # Marketplace still points to previous sent revision!

    # Reorder & remove photos on draft
    with SessionLocal() as db:
        p1 = Photo(
            id="ph-1",
            conversation_id=draft_id,
            original_filename="a.jpg",
            stored_filename="a.jpg",
            mime_type="image/jpeg",
            size_bytes=1234,
            display_order=0,
        )
        p2 = Photo(
            id="ph-2",
            conversation_id=draft_id,
            original_filename="b.jpg",
            stored_filename="b.jpg",
            mime_type="image/jpeg",
            size_bytes=1234,
            display_order=1,
        )
        db.add_all([p1, p2])
        db.commit()

    reordered, err = _call_tool(
        client,
        "manage_listing_photos",
        {"conversation_id": draft_id, "action": "reorder", "ordered_photo_ids": ["ph-2", "ph-1"]},
    )
    assert not err, reordered
    assert reordered["ordered_photo_ids"] == ["ph-2", "ph-1"]

    removed, err = _call_tool(
        client,
        "manage_listing_photos",
        {"conversation_id": draft_id, "action": "remove", "photo_id": "ph-1"},
    )
    assert not err, removed
    assert removed["remaining_photo_ids"] == ["ph-2"]

    # Manage labels and notes
    labeled, err = _call_tool(
        client,
        "manage_labels_and_notes",
        {
            "conversation_id": draft_id,
            "add_labels": ["gift-idea"],
            "remove_labels": ["kitchen"],
            "append_note": "Packed in box B.",
        },
    )
    assert not err, labeled
    assert "gift-idea" in labeled["labels"]
    assert "kitchen" not in labeled["labels"]
    assert "Packed in box B." in labeled["private_notes"]

    # Safety invariant: delete_draft_listing MUST refuse live listings AND require confirm=True
    refuse_live, err = _call_tool(client, "delete_draft_listing", {"conversation_id": "conv-live", "confirm": True})
    assert err is True
    assert "unsent local drafts" in refuse_live["error"]

    refuse_unconfirmed, err = _call_tool(client, "delete_draft_listing", {"conversation_id": draft_id, "confirm": False})
    assert err is True
    assert "confirm=true" in refuse_unconfirmed["error"]

    # Delete unsent draft with confirm=True succeeds
    deleted, err = _call_tool(client, "delete_draft_listing", {"conversation_id": draft_id, "confirm": True})
    assert not err, deleted
    assert deleted["deleted"] is True


def test_desktop_mcp_token_crud_and_optional_bearer_validation():
    client = TestClient(app)

    # Create token
    created = client.post("/api/settings/mcp-tokens", json={"name": "Claude Desktop"})
    assert created.status_code == 201
    token_data = created.json()
    raw_token = token_data["token"]
    assert raw_token.startswith("arm_")
    assert token_data["name"] == "Claude Desktop"

    # List tokens never leaks raw token
    listed = client.get("/api/settings/mcp-tokens").json()
    assert len(listed["tokens"]) == 1
    assert "token" not in listed["tokens"][0]
    assert listed["tokens"][0]["token_prefix"] == raw_token[:12]

    # Valid Bearer token works on /api/mcp
    ok = _rpc(client, "ping", extra_headers={"Authorization": f"Bearer {raw_token}"})
    assert ok.status_code == 200

    # Invalid Bearer token is rejected with 401
    bad = _rpc(client, "ping", extra_headers={"Authorization": "Bearer arm_invalid_token_xyz"})
    assert bad.status_code == 401

    # Revoke token -> subsequent call returns 401
    rev = client.delete(f"/api/settings/mcp-tokens/{token_data['id']}")
    assert rev.status_code == 200
    after_revoke = _rpc(client, "ping", extra_headers={"Authorization": f"Bearer {raw_token}"})
    assert after_revoke.status_code == 401


def test_hosted_mcp_enforces_seller_isolation_and_blocks_non_mcp_routes(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice_headers = headers(signing_keys, USER_ID)
    bob_headers = headers(signing_keys, BOB)

    # Hosted /api/settings/agent exposes remote MCP URL + Claude/ChatGPT/Gemini setup
    agent_info = client.get("/api/settings/agent", headers=alice_headers)
    assert agent_info.status_code == 200
    agent_body = agent_info.json()
    assert agent_body["hosted"] is True
    assert agent_body["mcp_url"].endswith("/api/mcp")
    assert {c["id"] for c in agent_body["remote_clients"]} == {"claude", "chatgpt", "gemini"}

    # Alice and Bob each mint their own MCP access token
    alice_tok_resp = client.post("/api/settings/mcp-tokens", headers=alice_headers, json={"name": "Alice Claude"})
    bob_tok_resp = client.post("/api/settings/mcp-tokens", headers=bob_headers, json={"name": "Bob ChatGPT"})
    assert alice_tok_resp.status_code == 201
    assert bob_tok_resp.status_code == 201
    alice_mcp = {"Authorization": f"Bearer {alice_tok_resp.json()['token']}"}
    bob_mcp = {"Authorization": f"Bearer {bob_tok_resp.json()['token']}"}

    # Unauthenticated /api/mcp is rejected with 401
    assert _rpc(client, "ping").status_code == 401

    # MCP bearer token MUST NOT work on non-MCP endpoints
    assert client.get("/api/conversations", headers=alice_mcp).status_code == 401
    assert client.get("/api/settings/mcp-tokens", headers=alice_mcp).status_code == 401

    # Alice creates a draft via MCP
    created, err = _call_tool(
        client,
        "create_draft_listing",
        {"title": f"Alice Secret Coat {uuid.uuid4().hex[:6]}", "price": 99.0},
        extra_headers=alice_mcp,
    )
    assert not err
    alice_conv_id = created["conversation_id"]

    # Alice sees her draft; Bob sees zero items and cannot read or edit Alice's draft
    alice_inv, err = _call_tool(client, "search_inventory", {}, extra_headers=alice_mcp)
    assert not err
    assert alice_inv["total"] == 1

    bob_inv, err = _call_tool(client, "search_inventory", {}, extra_headers=bob_mcp)
    assert not err
    assert bob_inv["total"] == 0

    bob_get, err = _call_tool(client, "get_listing", {"conversation_id": alice_conv_id}, extra_headers=bob_mcp)
    assert err is True
    assert "not found" in bob_get["error"].lower()

    # Revoking Alice's token immediately cuts off her MCP access
    rev = client.delete(f"/api/settings/mcp-tokens/{alice_tok_resp.json()['id']}", headers=alice_headers)
    assert rev.status_code == 200
    assert _rpc(client, "ping", extra_headers=alice_mcp).status_code == 401
