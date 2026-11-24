"""Tests for the Streamable HTTP MCP server (/api/mcp), MCP access tokens, and hosted seller isolation."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.mcp_token import McpAccessToken, McpOAuthConnection
from anteroom.models.sourcing import SourceBox
from anteroom.services import mcp_http, mcp_oauth, sales_ledger


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
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot

    with SessionLocal() as db:
        for model in (
            McpAccessToken,
            McpOAuthConnection,
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


def test_mcp_every_tool_has_a_human_readable_title():
    """Claude and ChatGPT show the title when asking to run a tool; Anthropic's directory requires one."""
    tools = _rpc(TestClient(app), "tools/list").json()["result"]["tools"]
    assert tools
    for tool in tools:
        title = tool.get("title")
        assert isinstance(title, str) and title.strip(), tool["name"]
        assert "_" not in title, tool["name"]
        # 2025-03-26 clients read the title from the annotations.
        assert tool["annotations"].get("title") == title, tool["name"]
    titles = [tool["title"] for tool in tools]
    assert len(set(titles)) == len(titles)


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
        "get_listing_photos",
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
        "dismiss_attention_items",
        "restore_attention_items",
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
    assert any(sale["conversation_id"] == "conv-live" for sale in sales["recent_sales"])

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


def test_desktop_mcp_draft_delete_removes_original_video(tmp_path, monkeypatch):
    from anteroom.models.listing_video import ListingVideo
    from anteroom.services import listing_videos

    monkeypatch.setattr(listing_videos, "VIDEOS_DIR", tmp_path)
    client = TestClient(app)
    draft, error = _call_tool(client, "create_draft_listing", {"title": "Draft with a video"})
    assert not error, draft
    conv_id = draft["conversation_id"]
    clip = Path(__file__).with_name("fixtures") / "listing-video.mp4"
    upload = client.post(
        f"/api/conversations/{conv_id}/videos", files={"file": ("clip.mp4", clip.read_bytes(), "video/mp4")},
    )
    assert upload.status_code == 200, upload.text
    video_id = upload.json()["id"]
    with SessionLocal() as db:
        video = db.get(ListingVideo, video_id)
        assert video is not None
        original = listing_videos.video_path(video)
    assert original.is_file()

    _, error = _call_tool(client, "delete_draft_listing", {"conversation_id": conv_id, "confirm": False})
    assert error
    assert original.is_file()

    result, error = _call_tool(client, "delete_draft_listing", {"conversation_id": conv_id, "confirm": True})
    assert not error, result
    assert result["deleted"] and result["deleted_photos"] == 0
    with SessionLocal() as db:
        assert db.get(ListingVideo, video_id) is None
    assert not original.exists()


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


def test_hosted_mcp_enforces_seller_isolation_and_blocks_non_mcp_routes(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice_headers = headers(signing_keys, USER_ID)
    bob_headers = headers(signing_keys, BOB)

    # Hosted /api/settings/agent exposes the remote MCP URL and setup prompts.
    agent_info = client.get("/api/settings/agent", headers=alice_headers)
    assert agent_info.status_code == 200
    agent_body = agent_info.json()
    assert agent_body["hosted"] is True
    assert agent_body["mcp_url"].endswith("/api/mcp")
    assert {c["id"] for c in agent_body["remote_clients"]} == {"claude", "chatgpt", "antigravity", "generic"}
    for mcp_client in agent_body["remote_clients"]:
        assert agent_body["mcp_url"] in mcp_client["prompt"]

    # Alice and Bob each mint their own MCP access token
    write = {"permissions": {"listings": "write"}}
    alice_tok_resp = client.post(
        "/api/settings/mcp-tokens", headers=alice_headers, json={"name": "Alice Claude", **write},
    )
    bob_tok_resp = client.post("/api/settings/mcp-tokens", headers=bob_headers, json={"name": "Bob ChatGPT", **write})
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

    # Both photo removal and draft deletion use seller-scoped Storage for PAT calls.
    import httpx
    from anteroom.services import hosted_photos

    deleted_keys = []

    def delete_objects(method, _url, **kwargs):
        assert method == "DELETE"
        deleted_keys.extend(kwargs["json"]["prefixes"])
        return httpx.Response(200)

    monkeypatch.setenv("SUPABASE_URL", "https://storage.example.test")
    monkeypatch.setenv("SUPABASE_SERVER_SECRET_KEY", "fixture-only")
    monkeypatch.setattr(hosted_photos._storage, "request", delete_objects)
    photo_keys = [f"{USER_ID}/{alice_conv_id}/{'a' * 32}.jpg", f"{USER_ID}/{alice_conv_id}/{'b' * 32}.jpg"]
    message_key, task_key = (f"{USER_ID}/{alice_conv_id}/{char * 32}.jpg" for char in ("c", "d"))
    with Session(engine) as db:
        db.add(Message(
            user_id=USER_ID, conversation_id=alice_conv_id, role="user", text="Inspect this",
            attachments=[{"id": message_key}],
        ))
        db.add(BackgroundTask(
            user_id=USER_ID, conversation_id=alice_conv_id, kind="chat", status="completed",
            payload={"attachments": [{"id": task_key}]},
        ))
        for i, key in enumerate(photo_keys):
            db.add(Photo(
                id=f"hosted-mcp-photo-{i}", user_id=USER_ID, conversation_id=alice_conv_id,
                original_filename="original.jpg", stored_filename=key, mime_type="image/jpeg",
                size_bytes=1234, display_order=i,
            ))
        db.commit()

    removed, err = _call_tool(
        client, "manage_listing_photos",
        {"conversation_id": alice_conv_id, "action": "remove", "photo_id": "hosted-mcp-photo-0"},
        extra_headers=alice_mcp,
    )
    assert not err, removed
    assert deleted_keys == hosted_photos.with_renditions(photo_keys[0])
    deleted, err = _call_tool(
        client, "delete_draft_listing", {"conversation_id": alice_conv_id, "confirm": True},
        extra_headers=alice_mcp,
    )
    assert not err, deleted
    assert deleted["deleted_photos"] == 1
    assert sorted(deleted_keys) == sorted(
        key for original in [*photo_keys, message_key, task_key] for key in hosted_photos.with_renditions(original)
    )

    # Revoking Alice's token immediately cuts off her MCP access
    rev = client.delete(f"/api/settings/mcp-tokens/{alice_tok_resp.json()['id']}", headers=alice_headers)
    assert rev.status_code == 200
    assert _rpc(client, "ping", extra_headers=alice_mcp).status_code == 401


def test_hosted_mcp_draft_delete_holds_conversation_lock_at_idle_check(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys, USER_ID)
    created, error = _call_tool(client, "create_draft_listing", {"title": "Locked draft"}, extra_headers=alice)
    assert not error, created
    identity = created["conversation_id"]
    assert_not_busy = mcp_http._assert_not_busy

    def check_lock(db, conv_id):
        assert conv_id == identity
        with Session(engine) as other, pytest.raises(OperationalError) as exc:
            other.scalar(select(Conversation.id).where(Conversation.id == conv_id).with_for_update(nowait=True))
        assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
        assert_not_busy(db, conv_id)

    monkeypatch.setattr(mcp_http, "_assert_not_busy", check_lock)
    result, error = _call_tool(
        client, "delete_draft_listing", {"conversation_id": identity, "confirm": True}, extra_headers=alice,
    )
    assert not error, result
    assert result["deleted"]


def test_failed_draft_in_mcp_batch_is_not_committed():
    def draft(req_id, title, **fields):
        return {
            "jsonrpc": "2.0", "method": "tools/call", "id": req_id,
            "params": {"name": "create_draft_listing", "arguments": {"title": title, "price": 10, **fields}},
        }

    response = TestClient(app).post("/api/mcp", json=[
        draft(1, "Saved before the rejection"),
        draft(2, "Rejected Best Offer", ebay_specifics={"bestOffer": {"enabled": True, "autoAcceptPrice": 20}}),
        draft(3, "Saved after the rejection"),
    ])
    assert response.status_code == 200
    assert [reply["result"]["isError"] for reply in response.json()] == [False, True, False]
    with SessionLocal() as db:
        assert {title for (title,) in db.query(Conversation.title)} == {
            "Saved before the rejection", "Saved after the rejection",
        }


@pytest.mark.parametrize(("tool", "args"), [
    ("edit_listing", {"fields": {"price": 20}}),
    ("manage_labels_and_notes", {"add_labels": ["new"]}),
])
def test_mcp_edit_conflicts_when_a_new_revision_is_saved_after_read(monkeypatch, tool, args):
    from anteroom.services import listing_editor

    client = TestClient(app)
    created, error = _call_tool(client, "create_draft_listing", {"title": "Revision race", "price": 10})
    assert not error
    conv_id = created["conversation_id"]
    save = listing_editor.save_listing_edit

    def save_after_intervening_edit(conv_id, doc, db, **kwargs):
        with SessionLocal() as other:
            save(conv_id, {"title": "Revision race", "price": 10, "sku": "newer"}, other)
        return save(conv_id, doc, db, **kwargs)

    monkeypatch.setattr(listing_editor, "save_listing_edit", save_after_intervening_edit)
    result, error = _call_tool(client, tool, {"conversation_id": conv_id, **args})
    assert error, result
    assert "changed since you reviewed it" in result["error"]
    stored, error = _call_tool(client, "get_listing", {"conversation_id": conv_id})
    assert not error
    assert stored["listing"]["sku"] == "newer"


def test_hosted_pat_photo_cleanup_invokes_hosted_storage_delete(monkeypatch):
    from types import SimpleNamespace
    from anteroom.services import hosted_storage, mcp_http

    calls: list[tuple[str, str, float]] = []
    monkeypatch.setattr(hosted_storage, "photo_call", lambda method, key, data=None, *, timeout=30: calls.append((method, key, timeout)) or b"")
    fake_hosted_db = SimpleNamespace(user_id=USER_ID)
    stored_key = f"{USER_ID}/conv-1/{'a' * 32}.jpg"
    source_key = f"{USER_ID}/conv-1/{'b' * 32}.jpg"

    mcp_http._best_effort_delete_photo_files(
        fake_hosted_db,  # type: ignore[arg-type]
        stored_key,
        source_key,
        bearer_token="arm_test_token_value",
    )
    assert [(method, key) for method, key, _ in calls] == [("DELETE", stored_key), ("DELETE", source_key)]
    assert 0 < calls[1][2] <= calls[0][2] <= 15


def test_hosted_today_concurrent_mutations_stay_seller_scoped(cloud, signing_keys):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, _ = cloud
    alice = headers(signing_keys, USER_ID)
    bob = headers(signing_keys, BOB)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def concurrently(method, path, payloads):
            barrier = Barrier(2, timeout=10)

            def send(payload):
                barrier.wait()
                return client.request(method, path, headers=alice, json=payload)

            return list(pool.map(send, payloads))

        goals = concurrently("POST", "/api/home/goals", [
            {"period": "weekly", "metric": "sales", "target": 50},
            {"period": "weekly", "metric": "sales", "target": 50},
        ])
        assert [result.status_code for result in goals] == [200, 200]
        goal_id = goals[0].json()["goal_id"]
        assert goals[1].json()["goal_id"] == goal_id

        updates = concurrently("PATCH", f"/api/home/goals/{goal_id}", [
            {"metric": "sell_through_rate"},
            {"target": 200},
        ])
        assert sorted(result.status_code for result in updates) == [200, 422]
        goal = client.get("/api/home", headers=alice).json()["goals"][0]
        assert (goal["metric"], goal["target"]) in {("sell_through_rate", 50), ("sales", 200)}
        assert client.get("/api/home", headers=bob).json()["goals"] == []

        dismissals = concurrently("POST", "/api/home/attention/dismiss", [
            {"item_key": "shared-alert", "kind": "first"},
            {"item_key": "shared-alert", "kind": "second"},
        ])
        assert [result.status_code for result in dismissals] == [200, 200]


READ_TOOLS = {
    "search_inventory", "get_listing", "get_listing_photos", "get_sales_analytics", "get_offer_activity",
    "get_sourcing_roi", "run_etsy_audit", "get_needs_attention",
}
LISTING_WRITE_TOOLS = {
    "create_draft_listing", "edit_listing", "manage_listing_photos",
    "manage_labels_and_notes", "delete_draft_listing", "set_favorite",
}
ATTENTION_WRITE_TOOLS = {"dismiss_attention_items", "restore_attention_items"}
ALL_TOOLS = READ_TOOLS | LISTING_WRITE_TOOLS | ATTENTION_WRITE_TOOLS


def _tool_names(client: TestClient, extra_headers: dict | None = None) -> set[str]:
    listed = _rpc(client, "tools/list", extra_headers=extra_headers)
    assert listed.status_code == 200, listed.text
    return {tool["name"] for tool in listed.json()["result"]["tools"]}


def test_every_mcp_tool_needs_a_named_permission():
    assert {tool["name"] for tool in mcp_http.tool_definitions()} == set(mcp_http.TOOL_PERMISSIONS)


def test_desktop_mcp_token_permissions_limit_tools_and_apply_on_the_next_call():
    client = TestClient(app)

    created = client.post("/api/settings/mcp-tokens", json={"name": "Claude Desktop"})
    assert created.status_code == 201
    token = created.json()
    assert token["permissions"] == {"listings": "read", "analytics": "read", "attention": "read"}
    bearer = {"Authorization": f"Bearer {token['token']}"}

    # A new token reads but cannot change anything.
    assert _tool_names(client, bearer) == READ_TOOLS
    refused, is_error = _call_tool(client, "create_draft_listing", {"title": "Blocked draft"}, extra_headers=bearer)
    assert is_error
    assert "not allowed to use create_draft_listing" in refused["error"]
    assert "Listings to Read and write" in refused["error"]
    with SessionLocal() as db:
        assert db.query(Conversation).count() == 0

    # Turning on write applies to the very next request, with the same token.
    patched = client.patch(f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"listings": "write"}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["permissions"] == {"listings": "write", "analytics": "read", "attention": "read"}
    assert _tool_names(client, bearer) == READ_TOOLS | LISTING_WRITE_TOOLS
    draft, is_error = _call_tool(client, "create_draft_listing", {"title": "Allowed draft"}, extra_headers=bearer)
    assert not is_error, draft

    # Turning an area off hides and refuses its tools.
    client.patch(
        f"/api/settings/mcp-tokens/{token['id']}",
        json={"permissions": {"analytics": "none", "attention": "none", "listings": "none"}},
    )
    assert _tool_names(client, bearer) == set()
    _, is_error = _call_tool(client, "get_sales_analytics", {}, extra_headers=bearer)
    assert is_error
    _, is_error = _call_tool(client, "search_inventory", {}, extra_headers=bearer)
    assert is_error

    # The Mac's own loopback requests without a token keep every tool.
    assert _tool_names(client) == ALL_TOOLS

    # Unknown areas and levels are refused; a revoked token cannot be changed.
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"analytics": "write"}},
    ).status_code == 422
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"marketplaces": "write"}},
    ).status_code == 422
    assert client.patch("/api/settings/mcp-tokens/missing", json={"permissions": {}}).status_code == 404
    client.delete(f"/api/settings/mcp-tokens/{token['id']}")
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"listings": "read"}},
    ).status_code == 422


def test_mcp_token_created_with_chosen_permissions():
    client = TestClient(app)
    created = client.post(
        "/api/settings/mcp-tokens",
        json={"name": "Analytics only", "permissions": {"listings": "none", "attention": "none"}},
    )
    assert created.status_code == 201
    bearer = {"Authorization": f"Bearer {created.json()['token']}"}
    assert _tool_names(client, bearer) == {"get_sales_analytics", "get_offer_activity", "get_sourcing_roi", "run_etsy_audit"}
    listed = client.get("/api/settings/mcp-tokens").json()["tokens"][0]
    assert listed["permissions"] == {"listings": "none", "analytics": "read", "attention": "none"}
    bad = client.post("/api/settings/mcp-tokens", json={"name": "Bad", "permissions": {"listings": "admin"}})
    assert bad.status_code == 422


def test_hosted_mcp_token_permissions_are_enforced_and_seller_scoped(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys, USER_ID)
    bob = headers(signing_keys, BOB)
    created = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice reader"})
    assert created.status_code == 201, created.text
    token = created.json()
    bearer = {"Authorization": f"Bearer {token['token']}"}

    assert _tool_names(client, bearer) == READ_TOOLS
    _, is_error = _call_tool(client, "create_draft_listing", {"title": "Blocked"}, extra_headers=bearer)
    assert is_error

    # Bob cannot change Alice's token; Alice can, and it applies on the next call.
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", headers=bob, json={"permissions": {"listings": "write"}},
    ).status_code == 404
    assert _tool_names(client, bearer) == READ_TOOLS
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", headers=alice, json={"permissions": {"listings": "write"}},
    ).status_code == 200
    draft, is_error = _call_tool(client, "create_draft_listing", {"title": "Allowed"}, extra_headers=bearer)
    assert not is_error, draft

    # A token cannot change its own permissions, and the seller's session keeps every tool.
    assert client.patch(
        f"/api/settings/mcp-tokens/{token['id']}", headers=bearer, json={"permissions": {"analytics": "none"}},
    ).status_code == 401
    assert _tool_names(client, alice) == ALL_TOOLS


def test_hosted_connected_app_permissions_are_seller_scoped_and_can_be_turned_off(cloud, signing_keys, monkeypatch):  # noqa: F811
    from test_seller_sessions import session_token

    client, _ = cloud
    app_id = str(uuid.uuid4())
    alice, bob = headers(signing_keys, USER_ID), headers(signing_keys, BOB)
    alice_app = {"Authorization": "Bearer " + session_token(signing_keys, client_id=app_id)}
    bob_app = {"Authorization": "Bearer " + session_token(signing_keys, sub=BOB, client_id=app_id)}

    # Connected before Anteroom saw its approval: read-only until the seller chooses more.
    assert _tool_names(client, alice_app) == READ_TOOLS
    _, is_error = _call_tool(client, "create_draft_listing", {"title": "Blocked"}, extra_headers=alice_app)
    assert is_error

    approved = client.put(f"/api/settings/mcp-connections/{app_id}", headers=alice,
                          json={"client_name": "Claude", "permissions": {"listings": "write"}})
    assert approved.status_code == 200, approved.text
    assert approved.json()["client_name"] == "Claude"
    draft, is_error = _call_tool(client, "create_draft_listing", {"title": "Allowed"}, extra_headers=alice_app)
    assert not is_error, draft

    # Bob's grant of the same app is his own; he cannot see or change Alice's.
    assert _tool_names(client, bob_app) == READ_TOOLS
    assert [c["client_name"] for c in client.get("/api/settings/mcp-connections", headers=bob).json()["connections"]] == [None]
    assert client.get("/api/settings/mcp-connections", headers=alice).json()["connections"][0]["permissions"]["listings"] == "write"

    # The app cannot reach Settings to widen its own access.
    assert client.patch(f"/api/settings/mcp-connections/{app_id}", headers=alice_app,
                        json={"permissions": {"attention": "write"}}).status_code == 401

    # Turned off: refused on its next call until the seller approves it again.
    ended = []
    monkeypatch.setattr(mcp_oauth, "_delete_grant", lambda authorization, client_id: ended.append(
        (authorization, client_id)) or True)
    turned_off = client.delete(f"/api/settings/mcp-connections/{app_id}", headers=alice)
    assert turned_off.status_code == 200
    assert turned_off.json()["signed_out"] is True
    assert turned_off.json()["signed_out_at"] is not None
    # Supabase is asked with the seller's own session, never the app's.
    assert ended == [(alice["Authorization"], app_id)]
    refused = _rpc(client, "tools/list", extra_headers=alice_app)
    assert refused.status_code == 403
    assert _tool_names(client, bob_app) == READ_TOOLS
    # Saving its permissions on the approval page does not turn it back on; only
    # the approval Supabase accepted does.
    assert client.put(f"/api/settings/mcp-connections/{app_id}", headers=alice, json={}).status_code == 200
    assert _rpc(client, "tools/list", extra_headers=alice_app).status_code == 403
    assert client.put(f"/api/settings/mcp-connections/{app_id}", headers=alice, json={"enable": True}).status_code == 200
    assert _tool_names(client, alice_app) == READ_TOOLS


def test_existing_mcp_tokens_keep_full_access_after_migration(migration_engine):  # noqa: F811
    from alembic import command

    from test_seller_ownership import migration_config

    engine = migration_engine
    command.upgrade(migration_config(engine), "3f6a9d2c7b14")
    owner = str(uuid.uuid4())
    with engine.begin() as db:
        db.execute(sa.text("""
            INSERT INTO mcp_access_tokens (id, user_id, name, token_prefix, token_hash, created_at)
            VALUES ('old-token', :owner, 'Old Claude', 'arm_old', :hash, :now)
        """), {"owner": owner, "hash": "a" * 64, "now": datetime.now(UTC)})
    command.upgrade(migration_config(engine), "5c2e8f1a9d37")
    with engine.connect() as db:
        stored = db.execute(sa.select(McpAccessToken.__table__.c.permissions)).scalar_one()
        default = db.execute(sa.text("""
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'mcp_access_tokens' AND column_name = 'permissions'
        """)).scalar() if engine.dialect.name == "postgresql" else None
    assert stored == {"listings": "write", "analytics": "read", "attention": "read"}
    assert default is None


def _seed_failed_job(db, conv_id: str, title: str, **owner) -> str:
    conv = Conversation(id=conv_id, title=title, status="draft", **owner)
    db.add(conv)
    db.flush()
    job = Job(
        conversation_id=conv_id, approved_revision_id="rev-1", listing_snapshot={"title": title},
        status="failed", last_error="A delist did not finish.", **owner,
    )
    db.add(job)
    db.flush()
    return f"failed-job:{conv_id}:{job.id}"


def _attention_keys(client: TestClient, extra_headers: dict) -> set[str]:
    summary, is_error = _call_tool(client, "get_needs_attention", {}, extra_headers=extra_headers)
    assert not is_error, summary
    return {item["key"] for item in summary["needs_attention"]}


def test_mcp_token_with_needs_attention_write_dismisses_and_restores_alerts():
    client = TestClient(app)
    with SessionLocal() as db:
        key = _seed_failed_job(db, "conv-stale-delist", "Polo Quarter-Zip")
        db.commit()
    created = client.post("/api/settings/mcp-tokens", json={"name": "Triage"}).json()
    bearer = {"Authorization": f"Bearer {created['token']}"}

    # Read access sees the alert but cannot dismiss it.
    assert key in _attention_keys(client, bearer)
    assert ATTENTION_WRITE_TOOLS.isdisjoint(_tool_names(client, bearer))
    refused, is_error = _call_tool(client, "dismiss_attention_items", {"item_keys": [key]}, extra_headers=bearer)
    assert is_error and "Needs Attention to Read and write" in refused["error"]
    assert key in _attention_keys(client, bearer)

    client.patch(f"/api/settings/mcp-tokens/{created['id']}", json={"permissions": {"attention": "write"}})
    result, is_error = _call_tool(
        client, "dismiss_attention_items", {"item_keys": [key, "failed-job:gone:gone"]}, extra_headers=bearer,
    )
    assert not is_error, result
    assert [row["item_key"] for row in result["dismissed"]] == [key]
    assert result["dismissed"][0]["reappears_in_days"] == 7
    assert result["not_found"] == ["failed-job:gone:gone"]
    assert key not in _attention_keys(client, bearer)
    # The app's own Home view hides it too.
    home = client.get("/api/home?timezone=UTC")
    assert home.status_code == 200, home.text
    assert key not in {item["key"] for item in home.json()["needs_attention"]}

    restored, is_error = _call_tool(
        client, "restore_attention_items", {"item_keys": [key, "never-dismissed"]}, extra_headers=bearer,
    )
    assert not is_error, restored
    assert restored == {"restored": [key], "not_dismissed": ["never-dismissed"]}
    assert key in _attention_keys(client, bearer)

    _, is_error = _call_tool(client, "dismiss_attention_items", {"item_keys": []}, extra_headers=bearer)
    assert is_error
    _, is_error = _call_tool(
        client, "dismiss_attention_items", {"item_keys": ["k"] * 101}, extra_headers=bearer,
    )
    assert is_error


def test_hosted_mcp_dismissal_belongs_to_the_tokens_seller(cloud, signing_keys):  # noqa: F811
    from anteroom.models.home import AttentionDismissal

    client, engine = cloud
    alice = headers(signing_keys, USER_ID)
    bob = headers(signing_keys, BOB)
    with Session(engine) as db:
        alice_key = _seed_failed_job(db, "alice-stale", "Alice Tee", user_id=USER_ID)
        bob_key = _seed_failed_job(db, "bob-stale", "Bob Tee", user_id=BOB)
        db.commit()
    created = client.post(
        "/api/settings/mcp-tokens", headers=alice, json={"name": "Alice triage", "permissions": {"attention": "write"}},
    ).json()
    bearer = {"Authorization": f"Bearer {created['token']}"}

    result, is_error = _call_tool(
        client, "dismiss_attention_items", {"item_keys": [alice_key, bob_key]}, extra_headers=bearer,
    )
    assert not is_error, result
    assert [row["item_key"] for row in result["dismissed"]] == [alice_key]
    assert result["not_found"] == [bob_key]
    with Session(engine) as db:
        rows = db.execute(select(AttentionDismissal.user_id, AttentionDismissal.item_key)).all()
    assert [(row.user_id, row.item_key) for row in rows] == [(USER_ID, alice_key)]
    bob_token = client.post(
        "/api/settings/mcp-tokens", headers=bob, json={"name": "Bob", "permissions": {"attention": "read"}},
    ).json()
    assert bob_key in _attention_keys(client, {"Authorization": f"Bearer {bob_token['token']}"})

def test_mcp_l8_batch_cap_auth_rate_limit_and_timeout(monkeypatch):
    from anteroom.routes import mcp as mcp_route
    from anteroom.services import mcp_http, mcp_tokens

    client = TestClient(app)

    # L8: JSON-RPC batch over MAX_RPC_BATCH_SIZE returns 400.
    too_many = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcp_http.MAX_RPC_BATCH_SIZE + 1)]
    batch_resp = client.post("/api/mcp", json=too_many)
    assert batch_resp.status_code == 400
    assert "batch" in batch_resp.json()["error"]["message"].lower()

    # L8: RPC timeout returns 504. Keep the short deadline inside this block so
    # later auth checks are not 504'd under a loaded Postgres worker.
    async def stall(_fn):
        import asyncio
        await asyncio.sleep(1.0)
        return ({}, 200, {})

    with monkeypatch.context() as mp:
        mp.setattr(mcp_route, "_MCP_RPC_TIMEOUT_SECONDS", 0.05)
        mp.setattr(mcp_route, "in_worker_session", stall)
        timed_out = _rpc(client, "ping")
    assert timed_out.status_code == 504

    # L8: per-IP failed arm_ Bearer auth attempts rate-limit at 429 with Retry-After,
    # and desktop requests cannot bypass the limiter by rotating Fly-Client-IP.
    mcp_tokens.clear_auth_failures()
    try:
        for idx in range(mcp_tokens.AUTH_FAIL_MAX):
            resp = _rpc(
                client,
                "ping",
                extra_headers={
                    "Authorization": "Bearer arm_wrong_token",
                    "Fly-Client-IP": f"203.0.113.{idx + 1}",
                },
            )
            assert resp.status_code == 401
        throttled = _rpc(
            client,
            "ping",
            extra_headers={
                "Authorization": "Bearer arm_wrong_token",
                "Fly-Client-IP": "203.0.113.250",
            },
        )
        assert throttled.status_code == 429
        assert throttled.headers.get("Retry-After") == "60"
    finally:
        mcp_tokens.clear_auth_failures()


def test_mcp_m7_hosted_photo_delete_runs_after_db_session_closes(monkeypatch):
    from anteroom.routes import mcp as mcp_route
    from anteroom.services import mcp_http

    events: list[str] = []

    async def fake_in_worker(fn):
        events.append("session_enter")
        try:
            return fn(object())
        finally:
            events.append("session_exit")

    def fake_run_call(_db, _payload, *, deferred_deletes, **_kwargs):
        deferred_deletes.append("user/conv/photo1.jpg")
        return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}, 200, {}

    def fake_delete(keys, *, bearer_token=None, deadline_seconds=15.0, server_key=False):
        events.append(f"storage_delete:{','.join(keys)}:{deadline_seconds}")

    monkeypatch.setattr(mcp_route, "in_worker_session", fake_in_worker)
    monkeypatch.setattr(mcp_route, "_run_mcp_call", fake_run_call)
    monkeypatch.setattr(mcp_http, "delete_hosted_storage_keys", fake_delete)

    client = TestClient(app)
    resp = _rpc(client, "ping")
    assert resp.status_code == 200
    assert events == [
        "session_enter",
        "session_exit",
        f"storage_delete:user/conv/photo1.jpg:{mcp_route._MCP_STORAGE_CLEANUP_TIMEOUT_SECONDS}",
    ]


def test_batch_does_not_dispatch_later_items_after_deadline(monkeypatch):
    from anteroom.services import mcp_http

    calls = []

    def handle(_db, item, **_kwargs):
        calls.append(item)
        return None, None

    monkeypatch.setattr(mcp_http, "_handle_single", handle)
    with pytest.raises(TimeoutError):
        mcp_http.handle_rpc_payload(object(), [1, 2], expired=lambda: bool(calls))
    assert calls == [1]


@pytest.mark.parametrize("ending", ["timeout", "exception", "cancel"])
def test_mcp_deferred_delete_survives_abnormal_completion(monkeypatch, ending):
    import asyncio
    from types import SimpleNamespace

    from anteroom.routes import mcp as mcp_route
    from anteroom.services import mcp_http

    async def exercise():
        entered, release, deleted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []

        async def fake_worker(fn):
            try:
                outcome = fn(object())
                entered.set()
                await release.wait()
                if ending == "exception":
                    raise RuntimeError("later batch item failed")
                return outcome
            finally:
                events.append("session closed")

        def fake_call(_db, _payload, *, deferred_deletes, **_kwargs):
            deferred_deletes.append("seller/item/photo.jpg")
            return {}, 200, {}

        loop = asyncio.get_running_loop()

        def fake_delete(keys, **_kwargs):
            events.append(keys[0])
            loop.call_soon_threadsafe(deleted.set)

        async def fake_body(*_args, **_kwargs):
            return b'{"jsonrpc":"2.0","id":1,"method":"ping"}'

        monkeypatch.setattr(mcp_route, "in_worker_session", fake_worker)
        monkeypatch.setattr(mcp_route, "_run_mcp_call", fake_call)
        monkeypatch.setattr(mcp_route, "read_bounded_body", fake_body)
        monkeypatch.setattr(mcp_http, "delete_hosted_storage_keys", fake_delete)
        if ending == "timeout":
            monkeypatch.setattr(mcp_route, "_MCP_RPC_TIMEOUT_SECONDS", 0.01)
        request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace()),
                                  headers={}, client=None)
        call = asyncio.create_task(mcp_route.mcp_post(request))
        await asyncio.wait_for(entered.wait(), 2)
        if ending == "timeout":
            assert (await call).status_code == 504
            release.set()
        elif ending == "cancel":
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            release.set()
        else:
            release.set()
            with pytest.raises(RuntimeError, match="later batch item failed"):
                await call
        await asyncio.wait_for(deleted.wait(), 2)
        assert events == ["session closed", "seller/item/photo.jpg"]

    asyncio.run(exercise())


def test_supabase_sign_out_of_a_connected_app_has_a_deadline(monkeypatch):
    import asyncio
    import time as clock

    monkeypatch.setattr(mcp_oauth, "GRANT_REVOKE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(mcp_oauth, "_delete_grant", lambda *_: clock.sleep(3) or True)

    async def timed():
        started = clock.monotonic()
        outcome = await mcp_oauth.end_supabase_grant("Bearer seller", "app")
        return outcome, clock.monotonic() - started

    outcome, waited = asyncio.run(timed())
    assert outcome is False
    assert waited < 2.9
    assert asyncio.run(mcp_oauth.end_supabase_grant(None, "app")) is False


def test_connected_app_starts_read_only_and_stays_off_once_turned_off():
    with SessionLocal() as db:
        assert mcp_oauth.permissions_for(db, "app-1") == {"listings": "read", "analytics": "read", "attention": "read"}
        mcp_oauth.revoke(db, "app-1")
        assert mcp_oauth.permissions_for(db, "app-1") is None
        assert mcp_oauth.mark_signed_out(db, "app-1")["signed_out_at"] is not None
        pending = mcp_oauth.approve(db, "app-1", client_name="ChatGPT", permissions={"attention": "write"})
        assert pending["revoked_at"] is not None
        assert mcp_oauth.permissions_for(db, "app-1") is None
        approved = mcp_oauth.approve(db, "app-1", client_name="ChatGPT", permissions={"attention": "write"}, enable=True)
        assert approved["revoked_at"] is None and approved["signed_out_at"] is None
        assert mcp_oauth.permissions_for(db, "app-1")["attention"] == "write"
        with pytest.raises(mcp_oauth.McpConnectionError):
            mcp_oauth.approve(db, "app-1", client_name=None, permissions={"publishing": "write"})
        with pytest.raises(mcp_oauth.McpConnectionError):
            mcp_oauth.approve(db, "", client_name=None, permissions={})


def test_connected_app_calls_do_not_refresh_settings_on_every_device():
    from datetime import timedelta

    with SessionLocal() as db:
        mcp_oauth.permissions_for(db, "app-2")
        first = db.query(McpOAuthConnection).filter_by(client_id="app-2").one().last_used_at
        mcp_oauth.permissions_for(db, "app-2")
        assert db.query(McpOAuthConnection).filter_by(client_id="app-2").one().last_used_at == first
        assert "quiet_changes" not in db.info
        row = db.query(McpOAuthConnection).filter_by(client_id="app-2").one()
        row.last_used_at = first - timedelta(minutes=5)
        db.commit()
        mcp_oauth.permissions_for(db, "app-2")
        assert db.query(McpOAuthConnection).filter_by(client_id="app-2").one().last_used_at > first - timedelta(minutes=5)


def test_connected_app_photo_cleanup_uses_the_server_key(monkeypatch):
    """Storage refuses a connected app's token, so its deletions run on the server key."""
    from anteroom.services import hosted_photos, hosted_storage

    calls = []
    monkeypatch.setattr(hosted_storage, "photo_call", lambda method, key, **_: calls.append(("server", key)))
    monkeypatch.setattr(hosted_photos, "storage_call", lambda method, key, token, **_: calls.append(("seller", key)))
    mcp_http.delete_hosted_storage_keys(["u/a.jpg"], bearer_token="eyJ.app.token", server_key=True)
    mcp_http.delete_hosted_storage_keys(["u/b.jpg"], bearer_token="eyJ.seller.token")
    assert calls == [("server", "u/a.jpg"), ("seller", "u/b.jpg")]


def test_search_inventory_live_marketplaces_leave_out_delisted_and_sold_ones():
    """Imported marketplaces Anteroom delisted, marked not listed or sold on are not live (#1825)."""
    client = TestClient(app)
    notes = {
        "vendooMarketplaces": ["depop", "ebay", "etsy", "mercari", "poshmark"],
        "vendooListingUrls": {"ebay": "https://www.ebay.com/itm/111", "mercari": "https://www.mercari.com/us/item/m222/"},
        "marketplaceDelists": {"ebay": {"state": "deleted", "listing_id": "111", "url": "https://www.ebay.com/itm/111"}},
        "marketplaceMarks": {"mercari": {"state": "not_listed", "listing_id": "m222",
                                         "url": "https://www.mercari.com/us/item/m222/"}},
        "vendooDates": {"soldByMarketplace": {"poshmark": "2026-11-01T00:00:00Z"}},
    }
    with SessionLocal() as db:
        db.add(Conversation(id="conv-imported", title="Baroque Blouse", status="sold", notes=json.dumps(notes)))
        db.commit()

    inv, err = _call_tool(client, "search_inventory", {"query": "Baroque"})
    assert not err
    item = inv["items"][0]
    assert item["live_marketplaces"] == ["depop", "etsy"]
    assert item["sold_marketplaces"] == ["poshmark"]

    delisted, err = _call_tool(client, "search_inventory", {"marketplace": "ebay"})
    assert not err and delisted["total"] == 0
    still_live, err = _call_tool(client, "search_inventory", {"marketplace": "etsy"})
    assert not err and still_live["total"] == 1


def test_an_mcp_write_that_finishes_after_the_timeout_is_not_saved(monkeypatch):
    import time

    from anteroom.routes import mcp as mcp_route
    from anteroom.services import listing_editor

    real_save = listing_editor.save_listing_edit

    def slow_save(*args, **kwargs):
        time.sleep(0.6)  # still working when the request gives up
        return real_save(*args, **kwargs)

    monkeypatch.setattr(mcp_route, "_MCP_RPC_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(listing_editor, "save_listing_edit", slow_save)
    with TestClient(app) as client:
        timed_out = _rpc(client, "tools/call", {"name": "create_draft_listing", "arguments": {"title": "Late draft"}})
        assert timed_out.status_code == 504
        assert "not saved" in timed_out.json()["error"]["message"]
        deadline = time.monotonic() + 5
        while mcp_route._active_calls and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not mcp_route._active_calls
    with SessionLocal() as db:
        assert db.query(Conversation).filter(Conversation.title == "Late draft").count() == 0


def _jpeg(width: int, height: int, color: str = "navy") -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def _call_tool_raw(client: TestClient, name: str, arguments: dict, extra_headers: dict | None = None) -> dict:
    resp = _rpc(client, "tools/call", {"name": name, "arguments": arguments}, extra_headers=extra_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _image_sides(item: dict) -> tuple[int, int]:
    import base64
    import io

    from PIL import Image

    assert item["type"] == "image" and item["mimeType"] == "image/jpeg"
    assert set(item) == {"type", "mimeType", "data"}
    with Image.open(io.BytesIO(base64.b64decode(item["data"]))) as image:
        assert image.format == "JPEG"
        return image.size


def test_desktop_mcp_returns_listing_photos_as_images_in_display_order():
    from anteroom.services.photo_store import photo_store

    client = TestClient(app)
    draft, error = _call_tool(client, "create_draft_listing", {"title": "Shirt to check"})
    assert not error, draft
    conv_id = draft["conversation_id"]
    store = photo_store()
    with SessionLocal() as db:
        for order, (photo_id, size) in enumerate([("front", (2400, 1800)), ("tag", (600, 750)), ("missing", None)]):
            key = store.new_key(conv_id, ".jpg")
            if size is not None:
                store.put(key, _jpeg(*size))
            db.add(Photo(
                id=f"{conv_id}-{photo_id}", conversation_id=conv_id, original_filename=f"{photo_id}.jpg",
                stored_filename=key, mime_type="image/jpeg", size_bytes=1, display_order=order,
            ))
        db.commit()
    revision = client.get(f"/api/conversations/{conv_id}/listing").json()

    result = _call_tool_raw(client, "get_listing_photos", {"conversation_id": conv_id, "max_side": 800})
    assert result["isError"] is False
    summary = json.loads(result["content"][0]["text"])
    assert [p["id"] for p in summary["photos"]] == [f"{conv_id}-front", f"{conv_id}-tag", f"{conv_id}-missing"]
    images = result["content"][1:]
    assert [_image_sides(images[0]), _image_sides(images[1])] == [(800, 600), (600, 750)]
    # A photo that cannot be read is named instead of failing the whole call.
    assert images[2] == {"type": "text", "text": f"Photo {conv_id}-missing could not be loaded."}
    assert summary["pushed_to_marketplaces"] is False

    only_tag = _call_tool_raw(client, "get_listing_photos", {"conversation_id": conv_id, "photo_ids": [f"{conv_id}-tag"]})
    assert [item["type"] for item in only_tag["content"]] == ["text", "image"]

    unknown, error = _call_tool(client, "get_listing_photos", {"conversation_id": conv_id, "photo_ids": ["nope"]})
    assert error and "nope" in unknown["error"]
    missing, error = _call_tool(client, "get_listing_photos", {"conversation_id": "no-such-listing"})
    assert error and "not found" in missing["error"].lower()
    # Reading photos never changes the listing.
    assert client.get(f"/api/conversations/{conv_id}/listing").json() == revision


def test_mcp_listing_photos_load_after_db_session_closes(monkeypatch):
    from anteroom.routes import mcp as mcp_route

    events: list[str] = []

    async def fake_in_worker(fn):
        events.append("session_enter")
        try:
            return fn(object())
        finally:
            events.append("session_exit")

    def fake_run_call(_db, _payload, **_kwargs):
        content = [{"type": "text", "text": "{}"}, mcp_http._photo_placeholder("p1", "user/conv/key.jpg", 512)]
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": content, "isError": False}}, 200, {}

    def fake_read(key, *, timeout):
        events.append(f"read:{key}")
        assert 0 < timeout <= mcp_route._MCP_RPC_TIMEOUT_SECONDS
        return _jpeg(1000, 500)

    monkeypatch.setattr(mcp_route, "in_worker_session", fake_in_worker)
    monkeypatch.setattr(mcp_route, "_run_mcp_call", fake_run_call)
    monkeypatch.setattr(mcp_http, "_read_photo", fake_read)

    resp = _rpc(TestClient(app), "ping")
    assert resp.status_code == 200
    assert events == ["session_enter", "session_exit", "read:user/conv/key.jpg"]
    assert _image_sides(resp.json()["result"]["content"][1]) == (512, 256)


def test_mcp_listing_photos_apply_exif_orientation(monkeypatch):
    import io

    from PIL import Image

    # Stored 1000x500 with EXIF "rotate 90", as a phone camera saves a portrait shot.
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (1000, 500), "white").save(buf, "JPEG", exif=exif)
    monkeypatch.setattr(mcp_http, "_read_photo", lambda _key, *, timeout: buf.getvalue())

    content = [mcp_http._photo_placeholder("p1", "conv/key.jpg", 400)]
    mcp_http.load_pending_photos([(content, 0)], deadline=float("inf"))
    assert _image_sides(content[0]) == (200, 400)


def test_hosted_mcp_listing_photos_are_seller_scoped(cloud, signing_keys, monkeypatch):  # noqa: F811
    import httpx

    from anteroom.services import hosted_photos

    client, engine = cloud
    tokens = {}
    for seller in (USER_ID, BOB):
        made = client.post(
            "/api/settings/mcp-tokens", headers=headers(signing_keys, seller),
            json={"name": "Photo check", "permissions": {"listings": "read"}},
        )
        assert made.status_code == 201, made.text
        tokens[seller] = {"Authorization": f"Bearer {made.json()['token']}"}
    conv_id = f"photos-{uuid.uuid4().hex[:8]}"
    present, gone = (f"{USER_ID}/{conv_id}/{char * 32}.jpg" for char in ("a", "b"))
    with Session(engine) as db:
        db.add(Conversation(id=conv_id, user_id=USER_ID, status="draft", title="Hosted shirt"))
        for order, key in enumerate((present, gone)):
            db.add(Photo(
                id=f"{conv_id}-{order}", user_id=USER_ID, conversation_id=conv_id, original_filename="p.jpg",
                stored_filename=key, mime_type="image/jpeg", size_bytes=1, display_order=order,
            ))
        db.commit()

    requested: list[str] = []

    def storage(method, url, **kwargs):
        assert method == "GET" and kwargs["timeout"] > 0
        requested.append(url.rsplit("/anteroom-photos/", 1)[1])
        return httpx.Response(200, content=_jpeg(1600, 1600)) if url.endswith(present) else httpx.Response(404)

    monkeypatch.setenv("SUPABASE_URL", "https://storage.example.test")
    monkeypatch.setenv("SUPABASE_SERVER_SECRET_KEY", "fixture-only")
    monkeypatch.setattr(hosted_photos._storage, "request", storage)

    result = _call_tool_raw(client, "get_listing_photos", {"conversation_id": conv_id}, extra_headers=tokens[USER_ID])
    assert result["isError"] is False, result
    assert requested == [present, gone]
    assert _image_sides(result["content"][1]) == (1024, 1024)
    assert result["content"][2] == {"type": "text", "text": f"Photo {conv_id}-1 could not be loaded."}

    requested.clear()
    bob, error = _call_tool(client, "get_listing_photos", {"conversation_id": conv_id}, extra_headers=tokens[BOB])
    assert error and "not found" in bob["error"].lower()
    assert requested == []
