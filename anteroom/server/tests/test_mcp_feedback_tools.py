"""``send_feedback`` over MCP: saved for the seller, emailed only from the hosted app."""
from __future__ import annotations

import json
import smtplib
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.conversation import Conversation
from anteroom.models.mcp_feedback import McpFeedback
from anteroom.models.mcp_token import McpAccessToken, McpOAuthConnection
from anteroom.services import mcp_feedback_tools, mcp_http, mcp_tokens
from anteroom.version import app_version


def _rpc(client: TestClient, method: str, params: dict | None = None, extra_headers: dict | None = None):
    payload: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post("/api/mcp", json=payload, headers=extra_headers or {})


def _send(client: TestClient, arguments: dict, extra_headers: dict | None = None) -> tuple[dict, bool]:
    resp = _rpc(client, "tools/call", {"name": "send_feedback", "arguments": arguments}, extra_headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert len(result["content"]) == 1, result
    text = result["content"][0]["text"]
    return ({"error": text}, True) if result.get("isError") else (json.loads(text), False)


def _tool_names(client: TestClient, extra_headers: dict | None = None) -> set[str]:
    listed = _rpc(client, "tools/list", extra_headers=extra_headers)
    assert listed.status_code == 200, listed.text
    return {tool["name"] for tool in listed.json()["result"]["tools"]}


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    """No test may reach a mail server; a test that expects a send installs its own."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("send_feedback tried to send email")

    monkeypatch.setattr(mcp_feedback_tools, "_send_email", refuse)
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.delenv("ANTEROOM_ERROR_EMAIL", raising=False)
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)


@pytest.fixture
def desktop():
    with SessionLocal() as db:
        for model in (McpFeedback, McpAccessToken, McpOAuthConnection, Conversation):
            db.query(model).delete()
        db.commit()
    yield TestClient(app)
    with SessionLocal() as db:
        db.query(McpFeedback).delete()
        db.commit()


def _rows() -> list[McpFeedback]:
    with SessionLocal() as db:
        return list(db.query(McpFeedback).order_by(McpFeedback.created_at))


def test_any_area_needs_some_access_to_one_area():
    assert mcp_tokens.allows({"listings": "none", "analytics": "read", "attention": "none"}, mcp_tokens.ANY_AREA, "read")
    assert mcp_tokens.allows({"listings": "write", "analytics": "none", "attention": "none"}, mcp_tokens.ANY_AREA, "read")
    assert not mcp_tokens.allows({"listings": "none", "analytics": "none", "attention": "none"}, mcp_tokens.ANY_AREA, "read")


def test_tool_is_open_world_and_tells_the_assistant_to_ask_first():
    tool = next(t for t in mcp_http.tool_definitions() if t["name"] == "send_feedback")
    assert tool["annotations"] == {
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True,
        "title": "Send feedback to Anteroom",
    }
    assert "only what the seller asked" in tool["description"]
    assert "Show the seller the exact message first" in tool["description"]
    assert tool["inputSchema"]["required"] == ["message", "kind"]
    assert tool["inputSchema"]["properties"]["kind"]["enum"] == ["bug", "feature_request", "question", "other"]


def test_mac_saves_feedback_and_says_it_was_not_sent(desktop):
    with SessionLocal() as db:
        db.add(Conversation(id="coat-1", title="Grandma's coat for Jane Buyer"))
        db.commit()
    data, is_error = _send(desktop, {
        "message": "The size dropdown is empty. My api_key=FAKEFAKEFAKEFAKE shows up in the log.",
        "kind": "bug",
        "conversation_id": "coat-1",
    })
    assert not is_error, data
    assert data["saved"] is True
    assert data["delivered"] is False
    assert data["delivery"] == "not_sent"
    assert "not sent" in data["note"] and "never sends email" in data["note"]
    mailto = data["send_it_yourself"]["mailto"]
    assert data["send_it_yourself"]["email"] == "support@myanteroom.com"
    assert mailto.startswith("mailto:support@myanteroom.com?subject=")
    assert "FAKEFAKEFAKEFAKE" not in mailto
    assert "Jane%20Buyer" not in mailto and "coat-1" in mailto

    [row] = _rows()
    assert row.id == data["feedback_id"]
    assert (row.kind, row.conversation_id, row.delivery) == ("bug", "coat-1", "not_sent")
    assert row.source == "Anteroom on this Mac, without a token"
    assert "FAKEFAKEFAKEFAKE" not in row.message
    assert "The size dropdown is empty." in row.message


def test_mac_sandbox_never_sends_even_with_operator_email_on(desktop, monkeypatch):
    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL", "1")
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    data, is_error = _send(desktop, {"message": "Love the new editor", "kind": "other"})
    assert not is_error, data
    assert data["delivery"] == "not_sent"
    assert "sandbox never sends email" in data["note"]


@pytest.mark.parametrize(("arguments", "error"), [
    ({"kind": "bug"}, "message is required"),
    ({"message": "   ", "kind": "bug"}, "message is required"),
    ({"message": "x" * 4001, "kind": "bug"}, "4000 characters or fewer"),
    ({"message": "Hi", "kind": "complaint"}, "kind must be one of"),
    ({"message": "Hi", "kind": "bug", "conversation_id": "missing"}, "Listing not found"),
])
def test_bad_feedback_is_refused_and_not_saved(desktop, arguments, error):
    data, is_error = _send(desktop, arguments)
    assert is_error
    assert error in data["error"]
    assert _rows() == []


def test_a_looping_assistant_is_stopped_after_a_handful_an_hour(desktop):
    for n in range(mcp_feedback_tools.MAX_PER_HOUR):
        _, is_error = _send(desktop, {"message": f"Same thing again {n}", "kind": "bug"})
        assert not is_error
    data, is_error = _send(desktop, {"message": "Same thing again", "kind": "bug"})
    assert is_error
    assert "Nothing was sent" in data["error"] and "Try again in about" in data["error"]
    assert len(_rows()) == mcp_feedback_tools.MAX_PER_HOUR


def test_any_token_with_some_access_can_send_feedback_and_is_named(desktop):
    created = desktop.post("/api/settings/mcp-tokens", json={
        "name": "Claude Desktop", "permissions": {"listings": "none", "attention": "none"},
    })
    assert created.status_code == 201, created.text
    token = created.json()
    bearer = {"Authorization": f"Bearer {token['token']}"}
    assert "send_feedback" in _tool_names(desktop, bearer)
    data, is_error = _send(desktop, {"message": "Analytics chart is confusing", "kind": "question"}, bearer)
    assert not is_error, data
    [row] = _rows()
    assert row.source == "AI assistant “Claude Desktop”"
    assert token["token"] not in row.source

    # A token switched off everywhere reaches nothing, feedback included.
    desktop.patch(f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"analytics": "none"}})
    assert "send_feedback" not in _tool_names(desktop, bearer)
    refused, is_error = _send(desktop, {"message": "Hello", "kind": "other"}, bearer)
    assert is_error
    assert "set at least one area to Read" in refused["error"]
    assert len(_rows()) == 1


# --- Hosted -----------------------------------------------------------------


@pytest.fixture
def hosted(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL", "1")
    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "100")
    monkeypatch.setenv("FLY_REGION", "iad")
    alice = headers(signing_keys, USER_ID)
    created = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice's Claude"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    return client, engine, alice, {"Authorization": f"Bearer {token}"}, token


def _hosted_row(engine, feedback_id: str) -> McpFeedback:
    with Session(engine) as db:
        row = db.get(McpFeedback, feedback_id)
        assert row is not None
        db.expunge(row)
        return row


def test_hosted_feedback_is_emailed_after_the_session_closes(hosted, monkeypatch):
    from anteroom.routes import mcp as mcp_route

    client, engine, alice, bearer, raw_token = hosted
    draft = client.post("/api/drafts", headers=alice, json={"title": "Coat for Jane Buyer at 12 Elm St"})
    assert draft.status_code == 201, draft.text
    listing_id = draft.json()["id"]

    events: list[str] = []
    sent: list[tuple[str, str, dict]] = []
    real_session = mcp_route.in_worker_session

    async def spy_session(work, *args):
        events.append("session open")
        try:
            return await real_session(work, *args)
        finally:
            events.append("session closed")

    def fake_send(subject, body, **kwargs):
        events.append("email")
        sent.append((subject, body, kwargs))

    monkeypatch.setattr(mcp_route, "in_worker_session", spy_session)
    monkeypatch.setattr(mcp_feedback_tools, "_send_email", fake_send)

    data, is_error = _send(client, {
        "message": "Etsy import skipped my shipping profile.\nAuthorization: Bearer abc.def.ghi",
        "kind": "bug",
        "conversation_id": listing_id,
    }, bearer)
    assert not is_error, data
    assert data["delivered"] is True
    assert data["delivery"] == "sent"
    assert data["note"] == "Saved and emailed to the Anteroom team."
    assert "send_it_yourself" not in data
    assert events == ["session open", "session closed", "email"]

    [(subject, body, kwargs)] = sent
    assert subject.startswith("[Anteroom feedback] Bug: Etsy import skipped my shipping profile.")
    assert kwargs["sender"] == "Anteroom Feedback"
    assert kwargs["timeout"] == mcp_feedback_tools.SMTP_TIMEOUT_SECONDS
    for expected in ("Kind         Bug", f"Seller id    {USER_ID}", "Sent by      AI assistant “Alice's Claude”",
                     f"Listing id   {listing_id}", f"App version  {app_version()}", "Environment  hosted (iad)",
                     f"Feedback id  {data['feedback_id']}", "Etsy import skipped my shipping profile."):
        assert expected in body, expected
    for leaked in (raw_token, "abc.def.ghi", "Jane Buyer", "12 Elm St"):
        assert leaked not in body and leaked not in subject

    row = _hosted_row(engine, data["feedback_id"])
    assert (row.user_id, row.delivery, row.kind) == (USER_ID, "sent", "bug")
    assert row.delivered_at is not None
    assert "abc.def.ghi" not in row.message


def test_hosted_feedback_is_private_to_its_seller(hosted, signing_keys):  # noqa: F811
    from anteroom.services.hosted_database import SellerDatabaseSession

    client, engine, _alice, bearer, _token = hosted
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "0")
        data, is_error = _send(client, {"message": "Private to Alice", "kind": "other"}, bearer)
    assert not is_error, data
    with SellerDatabaseSession(bind=engine, user_id=BOB) as db:
        assert db.query(McpFeedback).count() == 0
    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
        assert [row.id for row in db.query(McpFeedback)] == [data["feedback_id"]]


def test_hosted_failed_email_keeps_the_feedback_and_pauses_the_relay(hosted, monkeypatch):
    from anteroom.repositories.error_email import EmailStore

    client, engine, _alice, bearer, _token = hosted

    def refused(*_args, **_kwargs):
        raise smtplib.SMTPDataError(550, b"Daily user sending limit exceeded")

    monkeypatch.setattr(mcp_feedback_tools, "_send_email", refused)
    data, is_error = _send(client, {"message": "Feature: bulk price drop", "kind": "feature_request"}, bearer)
    assert not is_error, data
    assert data["delivered"] is False
    assert data["delivery"] == "failed"
    assert "email to the Anteroom team failed" in data["note"]
    assert data["send_it_yourself"]["mailto"].startswith("mailto:support@myanteroom.com")
    assert _hosted_row(engine, data["feedback_id"]).delivery == "failed"
    state, _pending = EmailStore(engine).summary()
    assert state["failure"] == "quota_paused"
    assert state["paused_until"] > time.time()

    # While the relay is paused nothing is attempted; the feedback is still saved.
    monkeypatch.setattr(mcp_feedback_tools, "_send_email", lambda *_a, **_k: pytest.fail("sent while paused"))
    data, is_error = _send(client, {"message": "Another idea", "kind": "feature_request"}, bearer)
    assert not is_error, data
    assert data["delivery"] == "not_sent"
    assert _hosted_row(engine, data["feedback_id"]).delivery == "not_sent"


def test_hosted_email_uses_the_shared_budget_and_leaves_errors_their_reserve(hosted, monkeypatch):
    from anteroom.repositories.error_email import EmailStore

    client, engine, _alice, bearer, _token = hosted
    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "4")
    sent: list[str] = []
    monkeypatch.setattr(mcp_feedback_tools, "_send_email", lambda subject, *_a, **_k: sent.append(subject))
    deliveries = [_send(client, {"message": f"Idea {n}", "kind": "other"}, bearer)[0]["delivery"] for n in range(4)]
    # A quarter of four (one slot) stays for error reports.
    assert deliveries == ["sent", "sent", "sent", "not_sent"]
    assert len(sent) == 3
    state, _pending = EmailStore(engine).summary()
    assert len(state["attempts"]) == 3


def test_hosted_slow_email_is_reported_unconfirmed_and_recorded_when_it_finishes(hosted, monkeypatch):
    client, engine, _alice, bearer, _token = hosted
    release = threading.Event()
    monkeypatch.setattr(mcp_feedback_tools, "SEND_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(mcp_feedback_tools, "_send_email", lambda *_a, **_k: release.wait(5))

    data, is_error = _send(client, {"message": "Slow relay", "kind": "bug"}, bearer)
    assert not is_error, data
    assert data["delivered"] is False
    assert data["delivery"] == "unconfirmed"
    assert "may or may not arrive" in data["note"]
    assert _hosted_row(engine, data["feedback_id"]).delivery == "sending"

    release.set()
    deadline = time.monotonic() + 5
    while _hosted_row(engine, data["feedback_id"]).delivery != "sent":
        assert time.monotonic() < deadline, "the late email was never recorded"
        time.sleep(0.05)


def test_hosted_sandbox_and_disabled_mail_never_send(hosted, monkeypatch):
    client, _engine, _alice, bearer, _token = hosted
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    data, is_error = _send(client, {"message": "From the sandbox", "kind": "other"}, bearer)
    assert not is_error, data
    assert (data["delivery"], data["delivered"]) == ("not_sent", False)
    assert "sandbox never sends email" in data["note"]

    monkeypatch.delenv("ANTEROOM_SANDBOX")
    monkeypatch.delenv("ANTEROOM_ERROR_EMAIL")
    data, is_error = _send(client, {"message": "Mail off", "kind": "other"}, bearer)
    assert not is_error, data
    assert data["delivery"] == "not_sent"
    assert "team email is turned off" in data["note"]


def test_hosted_connected_app_is_named_by_its_app_name(hosted, signing_keys, monkeypatch):  # noqa: F811
    from test_seller_sessions import session_token

    client, engine, alice, _bearer, _token = hosted
    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "0")
    app_id = str(uuid.uuid4())
    approved = client.put(f"/api/settings/mcp-connections/{app_id}", headers=alice,
                          json={"client_name": "ChatGPT", "permissions": {"listings": "none"}})
    assert approved.status_code == 200, approved.text
    connected = {"Authorization": "Bearer " + session_token(signing_keys, client_id=app_id)}
    data, is_error = _send(client, {"message": "Hello from ChatGPT", "kind": "question"}, connected)
    assert not is_error, data
    assert _hosted_row(engine, data["feedback_id"]).source == "AI assistant “ChatGPT”"
