"""MCP marketplace actions: drafts, List, Update, Delist and their status, only where the seller allowed them.

The marketplace services are replaced by recorders: these tests check what the
MCP layer lets through and what it tells the agent, never a real marketplace.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.mcp_token import McpAccessToken, McpOAuthConnection
from anteroom.repositories.queries import ConversationRepo, JobRepo
from anteroom.services import (
    draft_scheduling,
    marketplace_delist,
    mcp_marketplace_tools,
    mcp_oauth,
    mcp_tokens,
    native_marketplaces,
)
from anteroom.services.listing_persistence import save_revision

MARKETPLACE_TOOLS = {"save_marketplace_draft", "publish_listing", "send_unsent_edits", "delist_listing"}
PUBLISH_TOOLS = MARKETPLACE_TOOLS - {"save_marketplace_draft"}


@pytest.fixture(autouse=True)
def _clean_tables():
    with SessionLocal() as db:
        for model in (McpAccessToken, McpOAuthConnection, Message, Job, MarketplaceListing, Photo, Listing,
                      ListingRevision, Conversation):
            db.query(model).delete()
        db.commit()
    yield


class Recorder:
    """Stands in for a marketplace service: records each call and answers as told."""

    def __init__(self, answer=None, error: BaseException | None = None):
        self.calls: list[tuple[tuple, dict]] = []
        self.answer = answer
        self.error = error

    async def __call__(self, *args, **kwargs):
        from anteroom.services.service_context import current

        context = current()
        self.calls.append((args, {**kwargs, "_seller": context.seller_id if context else None}))
        if self.error is not None:
            raise self.error
        answer = self.answer
        return answer(*args, **kwargs) if callable(answer) else answer


def _started(conv_id, marketplace, *_args, **_kwargs):
    return {"state": "publishing", "job_id": f"job-{marketplace}", "running": True}


@pytest.fixture
def services(monkeypatch):
    recorders = {
        "list_now": Recorder(_started),
        "update_live": Recorder(lambda conv_id, marketplace, *_a, **_k: {"state": "updating", "job_id": f"job-{marketplace}"}),
        "save_draft": Recorder(lambda conv_id, marketplace, *_a, **_k: {"state": "saving", "job_id": f"job-{marketplace}"}),
        "delist": Recorder(lambda conv_id, names, *_a, **_k: {name: {"state": "deleted"} for name in names}),
    }
    for name in ("list_now", "update_live", "save_draft"):
        monkeypatch.setattr(native_marketplaces, name, recorders[name])
    monkeypatch.setattr(marketplace_delist, "delist", recorders["delist"])
    return recorders


def _rpc(client: TestClient, method: str, params: dict | None = None, extra_headers: dict | None = None):
    payload: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post("/api/mcp", json=payload, headers=extra_headers or {})


def _call(client: TestClient, name: str, arguments: dict, bearer: dict | None = None) -> tuple[dict, bool]:
    resp = _rpc(client, "tools/call", {"name": name, "arguments": arguments}, extra_headers=bearer)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert mcp_marketplace_tools.PENDING_KEY not in result
    text = result["content"][0]["text"]
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = {"error": text}
    return body, bool(result.get("isError"))


def _tool_names(client: TestClient, bearer: dict | None = None) -> set[str]:
    listed = _rpc(client, "tools/list", extra_headers=bearer)
    assert listed.status_code == 200, listed.text
    return {tool["name"] for tool in listed.json()["result"]["tools"]}


def _token(client: TestClient, name: str = "Claude on iPad", **permissions) -> tuple[dict, dict]:
    created = client.post("/api/settings/mcp-tokens", json={"name": name, "permissions": permissions})
    assert created.status_code in {200, 201}, created.text
    token = created.json()
    return token, {"Authorization": f"Bearer {token['token']}"}


def _listing(title: str = "Wool coat") -> tuple[str, str]:
    with SessionLocal() as db:
        conv = ConversationRepo(db).create(title=title)
        revision = save_revision(db, conv.id, {"title": title, "price": 40, "description": "Warm."}, "model")
        return conv.id, revision.id


# --- permissions --------------------------------------------------------------------

def test_new_and_existing_tokens_and_apps_have_no_marketplace_access():
    client = TestClient(app)
    token, bearer = _token(client)
    assert token["permissions"]["marketplaces"] == "none"
    names = _tool_names(client, bearer)
    assert MARKETPLACE_TOOLS.isdisjoint(names)
    assert "get_action_status" in names

    # Stored before the area existed: no access.
    assert mcp_tokens.stored_permissions({"listings": "write", "analytics": "read", "attention": "read"})["marketplaces"] == "none"
    with SessionLocal() as db:
        approved = mcp_oauth.approve(db, "app-1", client_name="Claude", permissions={"listings": "write"})
        assert approved["permissions"]["marketplaces"] == "none"
        assert mcp_oauth.permissions_for(db, "app-new")["marketplaces"] == "none"
    with pytest.raises(mcp_tokens.McpTokenError):
        mcp_tokens.normalize_permissions({"marketplaces": "everything"})


def test_tools_need_the_grant_and_are_annotated_for_client_approval():
    tools = {tool["name"]: tool for tool in mcp_marketplace_tools.TOOLS}
    for name in PUBLISH_TOOLS:
        assert tools[name]["annotations"]["destructiveHint"] is True
        assert tools[name]["annotations"]["openWorldHint"] is True
        assert "confirm" in tools[name]["inputSchema"]["required"]
    assert tools["save_marketplace_draft"]["annotations"]["openWorldHint"] is True
    assert tools["get_action_status"]["annotations"]["readOnlyHint"] is True
    for name in MARKETPLACE_TOOLS:
        schema = tools[name]["inputSchema"]["properties"]["marketplaces"]
        assert schema["minItems"] == 1 and "all" not in schema["items"]["enum"]


def test_the_sellers_own_session_and_the_mac_without_a_token_never_reach_marketplace_actions(services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    names = _tool_names(client)
    assert MARKETPLACE_TOOLS.isdisjoint(names)
    assert "get_action_status" in names
    refused, is_error = _call(client, "publish_listing", {
        "conversation_id": conv_id, "marketplaces": ["ebay"], "expected_revision_id": revision_id, "confirm": True,
    })
    assert is_error
    assert "needs an MCP access token or connected app" in refused["error"]
    assert services["list_now"].calls == []


def test_save_drafts_level_allows_only_marketplace_drafts(services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    _, bearer = _token(client, marketplaces="draft")
    names = _tool_names(client, bearer)
    assert "save_marketplace_draft" in names
    assert PUBLISH_TOOLS.isdisjoint(names)

    for tool in PUBLISH_TOOLS:
        refused, is_error = _call(client, tool, {
            "conversation_id": conv_id, "marketplaces": ["poshmark"], "expected_revision_id": revision_id, "confirm": True,
        }, bearer)
        assert is_error, tool
        assert "Marketplaces to Publish, update and delist" in refused["error"]
    assert services["list_now"].calls == services["update_live"].calls == services["delist"].calls == []

    saved, is_error = _call(client, "save_marketplace_draft", {
        "conversation_id": conv_id, "marketplaces": ["mercari", "depop"], "expected_revision_id": revision_id,
    }, bearer)
    assert not is_error, saved
    assert saved["marketplaces"]["mercari"]["status"] == "started"
    assert "Nothing is published" in saved["marketplaces"]["mercari"]["message"]
    calls = sorted(services["save_draft"].calls, key=lambda call: call[0][1])
    assert [(args[0], args[1], args[2]) for args, _ in calls] == [
        (conv_id, "depop", revision_id), (conv_id, "mercari", revision_id)]
    assert {kwargs["requested_by"] for _, kwargs in calls} == {"mcp:Claude on iPad"}


@pytest.mark.parametrize(("tool", "service"), [("publish_listing", "list_now"), ("send_unsent_edits", "update_live")])
def test_mcp_promotion_requires_the_exact_rate(tool, service, services):
    client = TestClient(app)
    _, bearer = _token(client, "Seller agent", marketplaces="publish")
    conv_id, _ = _listing()
    with SessionLocal() as db:
        revision_id = save_revision(db, conv_id, {"title": "Coat", "ebay_specifics": {
            "promotion": {"enabled": True, "campaignId": "42", "adRate": 7.5},
        }}, "user_form").id
    args = {"conversation_id": conv_id, "marketplaces": ["ebay"], "expected_revision_id": revision_id, "confirm": True}
    for rate in (None, "7"):
        refused, is_error = _call(client, tool, {**args, **({"confirmed_ebay_ad_rate": rate} if rate else {})}, bearer)
        assert is_error and "7.5%" in refused["error"]
    assert services[service].calls == []
    result, is_error = _call(client, tool, {**args, "confirmed_ebay_ad_rate": "7.50"}, bearer)
    assert not is_error, result
    assert result["marketplaces"]["ebay"]["confirmed_ebay_ad_rate"] == "7.5"
    assert services[service].calls[0][0][2].confirmed_ebay_ad_rate == "7.5"


def test_publish_names_marketplaces_carries_confirm_and_pins_the_revision(services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    _, bearer = _token(client, "Cursor", marketplaces="publish")
    assert MARKETPLACE_TOOLS <= _tool_names(client, bearer)
    base = {"conversation_id": conv_id, "marketplaces": ["ebay", "poshmark"], "expected_revision_id": revision_id}

    for arguments, expected in (
        (base, "confirm=true"),
        ({**base, "confirm": False}, "confirm=true"),
        ({**base, "confirm": True, "marketplaces": ["all"]}, "never acts on every marketplace"),
        ({**base, "confirm": True, "marketplaces": []}, "Name each marketplace"),
        ({**base, "confirm": True, "marketplaces": ["vinted"]}, "Unknown marketplace"),
        ({**base, "confirm": True, "expected_revision_id": "older"}, "changed since you read it"),
        ({**base, "confirm": True, "conversation_id": "missing"}, "Listing not found"),
    ):
        refused, is_error = _call(client, "publish_listing", arguments, bearer)
        assert is_error, arguments
        assert expected in refused["error"], refused
    assert services["list_now"].calls == []

    published, is_error = _call(client, "publish_listing", {**base, "confirm": True}, bearer)
    assert not is_error, published
    for marketplace in ("ebay", "poshmark"):
        outcome = published["marketplaces"][marketplace]
        assert outcome["status"] == "started"
        assert "not live until get_action_status reports it published" in outcome["message"]
    assert "published" not in {outcome["status"] for outcome in published["marketplaces"].values()}
    calls = {args[1]: (args, kwargs) for args, kwargs in services["list_now"].calls}
    assert set(calls) == {"ebay", "poshmark"}
    for args, kwargs in calls.values():
        body = args[2]
        assert (body.revision_id, body.confirm) == (revision_id, True)
        assert body.confirmed_ebay_ad_rate is None
        assert kwargs["requested_by"] == "mcp:Cursor"

    updated, is_error = _call(client, "send_unsent_edits", {**base, "marketplaces": ["ebay"], "confirm": True}, bearer)
    assert not is_error, updated
    assert updated["marketplaces"]["ebay"]["status"] == "started"
    assert services["update_live"].calls[0][0][2].confirm is True

    delisted, is_error = _call(client, "delist_listing", {
        "conversation_id": conv_id, "marketplaces": ["poshmark"], "confirm": True}, bearer)
    assert not is_error, delisted
    assert delisted["marketplaces"]["poshmark"]["status"] == "delisted"
    args, kwargs = services["delist"].calls[0]
    assert args == (conv_id, ["poshmark"], True) and kwargs["requested_by"] == "mcp:Cursor"
    _, is_error = _call(client, "delist_listing", {"conversation_id": conv_id, "marketplaces": ["poshmark"]}, bearer)
    assert is_error
    assert len(services["delist"].calls) == 1


def test_lowered_or_revoked_permission_applies_on_the_next_call(services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    token, bearer = _token(client, marketplaces="publish")
    arguments = {"conversation_id": conv_id, "marketplaces": ["etsy"], "expected_revision_id": revision_id, "confirm": True}
    _, is_error = _call(client, "publish_listing", arguments, bearer)
    assert not is_error

    assert client.patch(f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"marketplaces": "draft"}}).status_code == 200
    _, is_error = _call(client, "publish_listing", arguments, bearer)
    assert is_error
    assert client.patch(f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"marketplaces": "none"}}).status_code == 200
    assert MARKETPLACE_TOOLS.isdisjoint(_tool_names(client, bearer))
    client.delete(f"/api/settings/mcp-tokens/{token['id']}")
    assert _rpc(client, "tools/list", extra_headers=bearer).status_code == 401
    assert len(services["list_now"].calls) == 1


def test_a_marketplace_refusal_reaches_the_agent_and_nothing_claims_success(services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    _, bearer = _token(client, marketplaces="publish")
    services["list_now"].error = native_marketplaces.NativeMarketplaceError(409, "Connect Chrome first.")
    refused, is_error = _call(client, "publish_listing", {
        "conversation_id": conv_id, "marketplaces": ["depop"], "expected_revision_id": revision_id, "confirm": True,
    }, bearer)
    assert is_error
    assert refused["marketplaces"]["depop"] == {
        "status": "refused", "error": "Connect Chrome first.", "message": "Nothing was started on Depop."}

    services["delist"].error = marketplace_delist.DelistError(409, "Anteroom has no depop listing link for this item.")
    refused, is_error = _call(client, "delist_listing", {
        "conversation_id": conv_id, "marketplaces": ["depop"], "confirm": True}, bearer)
    assert is_error
    assert "no depop listing link" in refused["error"]

    services["delist"].error = None
    services["delist"].answer = {"depop": {"state": "failed", "error": "Chrome did not confirm the delete."}}
    failed, is_error = _call(client, "delist_listing", {
        "conversation_id": conv_id, "marketplaces": ["depop"], "confirm": True}, bearer)
    assert is_error
    assert failed["marketplaces"]["depop"]["status"] == "failed"
    assert "may still be live" in failed["marketplaces"]["depop"]["message"]


def test_the_real_delist_refuses_an_unlisted_item_without_starting_a_job():
    client = TestClient(app)
    conv_id, _ = _listing()
    _, bearer = _token(client, marketplaces="publish")
    refused, is_error = _call(client, "delist_listing", {
        "conversation_id": conv_id, "marketplaces": ["ebay"], "confirm": True}, bearer)
    assert is_error
    assert "no ebay listing link" in refused["error"]
    with SessionLocal() as db:
        assert db.query(Job).filter(Job.conversation_id == conv_id).count() == 0


def test_work_still_running_at_the_deadline_is_reported_as_unconfirmed_not_done(monkeypatch):
    release = None

    async def slow(*_args, **_kwargs):
        await release.wait()
        return {"state": "publishing", "job_id": "late"}

    monkeypatch.setattr(native_marketplaces, "list_now", slow)
    monkeypatch.setattr(marketplace_delist, "delist", slow)

    async def run(kind):
        nonlocal release
        release = asyncio.Event()
        spec = {"kind": kind, "conversation_id": "c1", "marketplaces": ["poshmark"], "revision_id": "r1", "agent": "mcp:Claude"}
        result = mcp_marketplace_tools.pending_result(spec)
        await mcp_marketplace_tools.run_pending(
            [result], deadline=monotonic() + mcp_marketplace_tools.MIN_START_SECONDS + 0.1)
        release.set()
        await asyncio.sleep(0)
        return json.loads(result["content"][0]["text"]), result["isError"]

    listed, is_error = asyncio.run(run("list"))
    assert listed["marketplaces"]["poshmark"]["status"] == "unconfirmed"
    assert "Call get_action_status before trying again" in listed["marketplaces"]["poshmark"]["message"]
    assert not is_error
    delisted, _ = asyncio.run(run("delist"))
    assert delisted["outcome"] == "running"
    assert "nothing is confirmed yet" in delisted["message"]


def test_a_pending_result_never_run_says_nothing_started(monkeypatch):
    result = mcp_marketplace_tools.pending_result({"kind": "list"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == mcp_marketplace_tools.NOT_STARTED

    # With the call's time nearly gone, nothing starts.
    started = Recorder(_started)
    monkeypatch.setattr(native_marketplaces, "list_now", started)
    spec = {"kind": "list", "conversation_id": "c1", "marketplaces": ["ebay"], "revision_id": "r1", "agent": None}
    late = mcp_marketplace_tools.pending_result(spec)
    asyncio.run(mcp_marketplace_tools.run_pending([late], deadline=monotonic() + 0.2))
    assert late["isError"] is True and late["content"][0]["text"] == mcp_marketplace_tools.NOT_STARTED
    assert mcp_marketplace_tools.PENDING_KEY not in late
    assert started.calls == []


def test_publish_at_schedules_through_scheduled_draft_publishing(monkeypatch, services):
    client = TestClient(app)
    conv_id, revision_id = _listing()
    _, bearer = _token(client, "Claude", marketplaces="publish")
    base = {"conversation_id": conv_id, "marketplaces": ["ebay"], "expected_revision_id": revision_id, "confirm": True}

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    refused, is_error = _call(client, "publish_listing", {**base, "publish_at": past}, bearer)
    assert is_error
    assert "future date" in refused["error"] and "Nothing was scheduled" in refused["error"]
    refused, is_error = _call(client, "publish_listing", {**base, "publish_at": "2030-01-01T09:00:00"}, bearer)
    assert is_error and "time zone" in refused["error"]

    scheduled_calls = []

    def schedule(db, conv, **kwargs):
        scheduled_calls.append((conv, kwargs))
        return {"scheduled_publish_at": "2030-01-01T14:00:00+00:00", "scheduled_marketplaces": kwargs["marketplaces"]}

    monkeypatch.setattr(draft_scheduling, "schedule_draft", schedule)
    scheduled, is_error = _call(client, "publish_listing", {**base, "publish_at": "2030-01-01T09:00:00-05:00"}, bearer)
    assert not is_error, scheduled
    assert scheduled["outcome"] == "scheduled"
    assert "Nothing is published yet" in scheduled["message"]
    (conv, kwargs), = scheduled_calls
    assert conv == conv_id
    assert kwargs["marketplaces"] == ["ebay"] and kwargs["confirm"] is True and kwargs["requested_by"] == "mcp:Claude"
    assert kwargs["publish_at"] == datetime(2030, 1, 1, 14, tzinfo=UTC)
    assert services["list_now"].calls == []


def test_the_schedule_log_names_the_agent():
    from anteroom.models.automation import ScheduledPublishLog

    with SessionLocal() as db:
        conv = Conversation(id=f"c-{uuid.uuid4().hex[:8]}", title="Scheduled", status="draft")
        db.add(conv)
        db.commit()
        db.add(ScheduledPublishLog(id="log-1", conversation_id=conv.id, marketplaces=["ebay"],
                                   scheduled_for=datetime(2030, 1, 1), status="scheduled",
                                   results={"requested_by": "mcp:Claude"}))
        db.commit()
        assert draft_scheduling.list_publish_logs(db)[0]["results"] == {"requested_by": "mcp:Claude"}
        db.query(ScheduledPublishLog).delete()
        db.commit()


def test_a_publish_an_agent_scheduled_names_it_on_the_jobs_that_publish_it(monkeypatch):
    from contextlib import asynccontextmanager

    from anteroom.services import automation_actions

    conv_id, revision_id = _listing()
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    with SessionLocal() as db:
        listing = db.query(Listing).filter_by(conversation_id=conv_id).one()
        digest, _rules = draft_scheduling._schedule_content_digest(db, conv_id, ["ebay"])
        listing.ready_to_list = True
        listing.scheduled_publish_at = past
        listing.scheduled_marketplaces = ["ebay"]
        listing.scheduled_revision_id = revision_id
        listing.scheduled_confirmed_at = past - timedelta(hours=1)
        listing.scheduled_status = "scheduled"
        listing.scheduled_content_digest = digest
        listing.scheduled_requested_by = "mcp:Claude"
        db.commit()

    @asynccontextmanager
    async def turn(_name):
        yield

    async def free(_name):
        return True

    started = Recorder({"state": "published"})
    monkeypatch.setattr(automation_actions, "_turn", turn)
    monkeypatch.setattr(automation_actions, "_wait_until_free", free)
    monkeypatch.setattr(native_marketplaces, "list_now", started)
    seen = []

    async def publisher(conv, markets, revision):
        seen.append(draft_scheduling.scheduled_requester())
        return await automation_actions._native_one(conv, markets[0], revision, "list")

    outcomes = asyncio.run(draft_scheduling.run_due_scheduled_publishes(publisher=publisher))
    assert seen == ["mcp:Claude"]
    assert outcomes[0]["status"] == "published", outcomes
    (args, kwargs), = started.calls
    assert args[:2] == (conv_id, "ebay") and kwargs["requested_by"] == "mcp:Claude"
    assert (args[2].revision_id, args[2].confirm, args[2].confirmed_ebay_ad_rate) == (revision_id, True, None)
    # Outside a scheduled publish, nobody is named.
    assert draft_scheduling.scheduled_requester() is None
    with SessionLocal() as db:
        listing = db.query(Listing).filter_by(conversation_id=conv_id).one()
        draft_scheduling.unschedule_draft(db, conv_id)
        db.refresh(listing)
        assert listing.scheduled_requested_by is None


def test_sandbox_refuses_publish_update_and_delist(monkeypatch, services):
    from anteroom import sandbox

    monkeypatch.setenv(sandbox.ENV_VAR, "1")
    client = TestClient(app)
    conv_id, revision_id = _listing()
    _, bearer = _token(client, marketplaces="publish")
    for tool in PUBLISH_TOOLS:
        refused, is_error = _call(client, tool, {
            "conversation_id": conv_id, "marketplaces": ["ebay"], "expected_revision_id": revision_id, "confirm": True,
        }, bearer)
        assert is_error and refused["error"] == sandbox.LISTING_DETAIL, tool
    assert services["list_now"].calls == services["update_live"].calls == services["delist"].calls == []


def test_jobs_an_agent_starts_name_it():
    conv_id, revision_id = _listing()
    with SessionLocal() as db:
        job = JobRepo(db).create(conv_id, revision_id, {"title": "Wool coat"}, status="dispatched",
                                 current_step="native_poshmark_list", requested_by="mcp:Claude")
        assert db.get(Job, job.id).requested_by == "mcp:Claude"
    listed = TestClient(app).get(f"/api/jobs?conversation_id={conv_id}").json()
    assert listed[0]["requested_by"] == "mcp:Claude"
    assert mcp_marketplace_tools.agent_name("mcp:Claude") == "Claude"
    assert mcp_marketplace_tools.agent_name(None) is None


def test_get_action_status_says_what_each_marketplace_action_did():
    client = TestClient(app)
    conv_id, revision_id = _listing()
    with SessionLocal() as db:
        for marketplace, values in {
            "poshmark": {"state": "unknown", "action": "list", "error": "Chrome stopped answering."},
            "mercari": {"state": "draft", "draft_url": "https://www.mercari.com/sell/draft/x/"},
            "depop": {"state": "not_saved", "action": "list", "error": "Add a size.", "error_code": "missing_fields"},
            "ebay": {"state": "published", "listing_url": "https://www.ebay.com/itm/1"},
        }.items():
            db.add(MarketplaceListing(conversation_id=conv_id, marketplace=marketplace, **values))
        JobRepo(db).create(conv_id, revision_id, {"title": "Wool coat"}, status="failed",
                           current_step="native_depop_list", requested_by="mcp:Claude")
        db.commit()
    token, bearer = _token(client)
    status, is_error = _call(client, "get_action_status", {"conversation_id": conv_id}, bearer)
    assert not is_error, status
    marketplaces = status["marketplaces"]
    assert marketplaces["poshmark"]["status"] == "uncertain"
    assert "press Check on the Poshmark row" in marketplaces["poshmark"]["what_to_check"]
    assert marketplaces["mercari"]["status"] == "draft_saved"
    assert marketplaces["depop"] == {
        "status": "failed", "action": "list", "error": "Add a size.", "error_code": "missing_fields",
        "message": "The last Depop action did not go through. Fix what the error names, then try again."}
    assert marketplaces["ebay"]["status"] == "published"
    assert "etsy" not in marketplaces
    assert status["recent_jobs"][0]["started_by"] == "Claude"
    assert status["recent_jobs"][0]["action"] == "Depop: Listing"

    # Reading status needs only Listings read.
    client.patch(f"/api/settings/mcp-tokens/{token['id']}", json={"permissions": {"listings": "none"}})
    _, is_error = _call(client, "get_action_status", {"conversation_id": conv_id}, bearer)
    assert is_error


def test_marketplace_status_vocabulary_for_delists_and_running_work():
    status = mcp_marketplace_tools.marketplace_status
    assert status("ebay", {}, {"state": "deleting"})["status"] == "delisting"
    assert status("ebay", {}, {"state": "failed", "error": "x"})["status"] == "delist_failed"
    assert status("ebay", {}, {"state": "deleted"})["status"] == "delisted"
    assert status("ebay", {}, {"state": "live", "url": "u"})["status"] == "published"
    assert status("ebay", {}, {"state": "live", "sold_here": True})["status"] == "sold"
    running = status("ebay", {"running": True, "action": "list", "progress": {"label": "Listing"}}, None)
    assert running["status"] == "running" and "Not finished yet" in running["message"]
    assert status("ebay", {"state": "pending_review"}, None)["status"] == "pending_review"
    assert status("ebay", {"state": "not_saved"}, None) is None


# --- hosted ---------------------------------------------------------------------------

def _hosted_listing(client: TestClient, bearer: dict) -> tuple[str, str]:
    draft, is_error = _call(client, "create_draft_listing", {"title": "Hosted coat", "price": 30}, bearer)
    assert not is_error, draft
    listing, is_error = _call(client, "get_listing", {"conversation_id": draft["conversation_id"]}, bearer)
    assert not is_error, listing
    return draft["conversation_id"], listing["current_revision_id"]


def test_hosted_tokens_act_only_for_their_seller_and_only_once_granted(cloud, signing_keys, services):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys, USER_ID), headers(signing_keys, BOB)
    created = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice agent",
                                                                         "permissions": {"listings": "write"}})
    assert created.status_code == 201, created.text
    token = created.json()
    bearer = {"Authorization": f"Bearer {token['token']}"}
    assert MARKETPLACE_TOOLS.isdisjoint(_tool_names(client, bearer))
    conv_id, revision_id = _hosted_listing(client, bearer)
    arguments = {"conversation_id": conv_id, "marketplaces": ["ebay"], "expected_revision_id": revision_id, "confirm": True}
    _, is_error = _call(client, "publish_listing", arguments, bearer)
    assert is_error

    # Bob cannot grant Alice's token anything; Alice can, and it applies on the next call.
    assert client.patch(f"/api/settings/mcp-tokens/{token['id']}", headers=bob,
                        json={"permissions": {"marketplaces": "publish"}}).status_code == 404
    assert client.patch(f"/api/settings/mcp-tokens/{token['id']}", headers=alice,
                        json={"permissions": {"marketplaces": "publish"}}).status_code == 200
    published, is_error = _call(client, "publish_listing", arguments, bearer)
    assert not is_error, published
    (args, kwargs), = services["list_now"].calls
    assert args[0] == conv_id and kwargs["requested_by"] == "mcp:Alice agent"
    # The marketplace work runs in Alice's own seller context, after the call's session closed.
    assert kwargs["_seller"] == USER_ID

    # Bob's granted token cannot reach Alice's listing.
    bob_token = client.post("/api/settings/mcp-tokens", headers=bob, json={
        "name": "Bob agent", "permissions": {"marketplaces": "publish"}}).json()
    refused, is_error = _call(client, "delist_listing", {"conversation_id": conv_id, "marketplaces": ["ebay"], "confirm": True},
                              {"Authorization": f"Bearer {bob_token['token']}"})
    assert is_error and refused["error"] == "Listing not found."
    assert services["delist"].calls == []

    # The seller's own session lists no marketplace action.
    assert MARKETPLACE_TOOLS.isdisjoint(_tool_names(client, alice))


def test_hosted_connected_app_needs_the_seller_to_choose_marketplaces(cloud, signing_keys, services):  # noqa: F811
    from test_seller_sessions import session_token

    client, _ = cloud
    app_id = str(uuid.uuid4())
    alice = headers(signing_keys, USER_ID)
    app_bearer = {"Authorization": "Bearer " + session_token(signing_keys, client_id=app_id)}
    assert client.put(f"/api/settings/mcp-connections/{app_id}", headers=alice,
                      json={"client_name": "Claude", "permissions": {"listings": "write"}}).status_code == 200
    assert MARKETPLACE_TOOLS.isdisjoint(_tool_names(client, app_bearer))
    conv_id, revision_id = _hosted_listing(client, app_bearer)

    assert client.patch(f"/api/settings/mcp-connections/{app_id}", headers=alice,
                        json={"permissions": {"marketplaces": "draft"}}).status_code == 200
    names = _tool_names(client, app_bearer)
    assert "save_marketplace_draft" in names and PUBLISH_TOOLS.isdisjoint(names)
    saved, is_error = _call(client, "save_marketplace_draft", {
        "conversation_id": conv_id, "marketplaces": ["mercari"], "expected_revision_id": revision_id}, app_bearer)
    assert not is_error, saved
    (_, kwargs), = services["save_draft"].calls
    assert kwargs["requested_by"] == "mcp:Claude" and kwargs["_seller"] == USER_ID
