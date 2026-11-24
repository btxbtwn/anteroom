from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_mcp_http import _call_tool, _clean_mcp_tables  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.automation import MarketplaceRelistLog, MarketplaceRelistRule
from anteroom.models.job import Job
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.routes import mcp
from anteroom.services import auto_relist, bridge_approvals, cloud_marketplace, cloud_sessions, hosted_browser_relay


def _new_draft(client, fields=None, auth=None):
    result, error = _call_tool(client, "create_draft_listing", {"title": "Vintage tee", "price": 35}, extra_headers=auth)
    assert not error, result
    if fields:
        edited, error = _call_tool(client, "edit_listing", {"conversation_id": result["conversation_id"], "fields": fields},
                                   extra_headers=auth)
        assert not error, edited
        result["revision_id"] = edited["revision_id"]
    return result


def _refresh_args(draft, marketplace):
    return {"conversation_id": draft["conversation_id"], "marketplace": marketplace, "refresh": True,
            "expected_revision_id": draft["revision_id"]}


def test_ebay_mcp_refresh_reads_live_category_and_account_choices(monkeypatch):
    from anteroom.services import category_fields, marketplace_schemas
    from anteroom.services.ebay_client import EbayClient

    category_fields.save_rows(marketplace_schemas.EBAY_ASPECTS, "11483", [{"name": "Old aspect"}])
    calls = []

    async def request(method, path, **_kwargs):
        assert method == "GET"
        name = path.rsplit("/", 1)[-1]
        calls.append(name)
        if name == "get_item_aspects_for_category":
            return {"aspects": [{"localizedAspectName": "Fresh aspect", "aspectValues": [{"localizedValue": "Fresh choice"}]}]}
        if name == "payment_policy":
            return {"paymentPolicies": [{"paymentPolicyId": "1", "name": "Fresh policy"}]}
        return {}

    async def connect():
        return SimpleNamespace(request=request)

    monkeypatch.setattr(EbayClient, "connect", connect)
    client = TestClient(app)
    draft = _new_draft(client, {"direct_category_ids": {"ebay": "11483"}})
    result, error = _call_tool(client, "get_marketplace_form", _refresh_args(draft, "ebay"))
    assert not error and result["fetched_via"] == "ebay_api", result
    assert {"get_item_aspects_for_category", "payment_policy", "location"} <= set(calls)
    fields = {row["path"]: row for row in result["fields"]}
    assert fields["ebay_specifics.Fresh aspect"]["options"] == ["Fresh choice"]
    assert fields["ebay_specifics.paymentPolicyId"]["options"] == [{"label": "Fresh policy", "value": "1"}]
    assert "ebay_specifics.Old aspect" not in fields


def test_etsy_mcp_refresh_reads_category_properties_and_shop_profiles(monkeypatch):
    from anteroom.services import etsy_form, etsy_listings, marketplace_accounts

    category = next(iter(etsy_form.category_choices()["option_values"].values()))

    async def etsy_api(method, path):
        assert (method, path) == ("GET", f"seller-taxonomy/nodes/{category}/properties")
        return {"results": [{"property_id": 200, "display_name": "Color", "possible_values": [{"value_id": 2, "name": "Blue"}]}]}

    async def profiles(*, fresh=False):
        assert fresh
        return {"shipping": [{"id": "1", "title": "Current shipping"}]}

    monkeypatch.setattr(marketplace_accounts, "etsy_api", etsy_api)
    monkeypatch.setattr(etsy_listings, "listing_profiles", profiles)
    client = TestClient(app)
    draft = _new_draft(client, {"direct_category_ids": {"etsy": str(category)}})
    result, error = _call_tool(client, "get_marketplace_form", _refresh_args(draft, "etsy"))
    assert not error and result["fetched_via"] == "etsy_api", result
    assert result["shop_profiles"]["shipping"] == {"1": "Current shipping"}
    assert next(row for row in result["fields"] if row["label"] == "Color")["options"] == ["Blue"]


def test_poshmark_mcp_refresh_uses_the_connected_cloud_reader(monkeypatch):
    monkeypatch.setattr(cloud_sessions, "has_active_session", lambda *_: True)

    async def execute(market, command, **_kwargs):
        assert market == "poshmark" and command["type"] == "marketplace.form"
        assert command["payload"]["refresh"] is True
        return {"ok": True, "refreshed": True, "form": {"marketplace": market, "fields": [
            {"path": "poshmark_specifics.shippingDiscountId", "options": ["Current shipping discounts"]},
        ]}}

    monkeypatch.setattr(cloud_marketplace, "execute", execute)
    client = TestClient(app)
    result, error = _call_tool(client, "get_marketplace_form", _refresh_args(_new_draft(client), "poshmark"))
    assert not error and result["fetched_via"] == "poshmark_cloud", result
    assert result["fields"][0]["options"] == ["Current shipping discounts"]


def test_form_refresh_timeout_is_local_to_its_tool_result(monkeypatch):
    from anteroom.services import mercari_api

    async def stalled(*_args):
        await asyncio.Event().wait()

    monkeypatch.setattr(mercari_api, "shipping_quotes", stalled)
    client = TestClient(app)
    draft = _new_draft(client, {
        "direct_category_ids": {"mercari": "275"}, "mercari_specifics": {"package": {"pounds": 1, "fitsShoebox": "yes"}},
    })
    monkeypatch.setattr(mcp, "_MCP_RPC_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(mcp, "_MCP_REPLY_MARGIN_SECONDS", 0.2)
    form, error = _call_tool(client, "get_marketplace_form", _refresh_args(draft, "mercari"))
    assert not error and form["available"] is False and form["reason"] == "timeout", form
    assert "Mercari" in form["message"]


def test_relist_status_uses_cap_backoff_and_pending_without_reconciliation(monkeypatch):
    moment = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr(auto_relist, "_now", lambda: moment)
    client = TestClient(app)
    drafts = [_new_draft(client) for _ in range(4)]
    with SessionLocal() as db:
        row = MarketplaceRelistRule(marketplace="poshmark", enabled=True, daily_cap=3,
                                    last_run_at=moment - timedelta(days=1), price_adjust_mode="amount", price_adjust_value=5)
        db.add(row)
        for index, draft in enumerate(drafts):
            listing_id = f"{index + 1:024x}"
            db.add(MarketplaceListing(conversation_id=draft["conversation_id"], marketplace="poshmark",
                                      state="published", listing_id=listing_id, listed_price=40,
                                      listing_url=f"https://poshmark.com/listing/{listing_id}",
                                      listed_at=moment - timedelta(days=80 - index)))
        db.add_all([
            MarketplaceRelistLog(conversation_id=drafts[1]["conversation_id"], marketplace="poshmark",
                                 status="failed", created_at=moment - timedelta(hours=1)),
            MarketplaceRelistLog(conversation_id=drafts[2]["conversation_id"], marketplace="poshmark",
                                 status="relist_pending", created_at=moment - timedelta(minutes=10)),
        ])
        db.commit()
        rule_id = row.id

    def refuse(*_args, **_kwargs):
        pytest.fail("A read-only preview must not enter reconciliation")

    monkeypatch.setattr(auto_relist, "_reconcile_pending_relists", refuse)
    result, error = _call_tool(client, "get_auto_relist_status")
    assert not error, result
    rules = {item["marketplace"]: item for item in result["rules"]}
    assert rules["ebay"]["enabled"] is False
    poshmark = rules["poshmark"]
    assert poshmark["remaining_today"] == 1
    assert [item["status"] for item in poshmark["candidates"]] == ["within_cap", "retry_backoff", "pending", "over_cap"]
    assert poshmark["candidates"][0]["proposed_price"] == 35
    assert "listing_gaps" in poshmark["candidates"][0]
    with SessionLocal() as db:
        assert db.get(MarketplaceRelistRule, rule_id).last_run_at == moment - timedelta(days=1)
        assert {log.status for log in db.query(MarketplaceRelistLog)} == {"failed", "relist_pending"}
        assert db.query(Job).count() == 0


def test_hosted_mcp_form_uses_the_sellers_relay_and_rejects_results_for_old_revisions(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    key = signing_keys[0].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv(bridge_approvals.KEY_ENV, key.decode())
    monkeypatch.setattr(mcp, "_MCP_RPC_TIMEOUT_SECONDS", 8.0)
    alice, bob = headers(signing_keys, USER_ID), headers(signing_keys, BOB)
    write = {"permissions": {"listings": "write"}}
    alice_token = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice", **write}).json()
    bob_token = client.post("/api/settings/mcp-tokens", headers=bob, json={"name": "Bob"}).json()
    alice_mcp = {"Authorization": f"Bearer {alice_token['token']}"}
    bob_mcp = {"Authorization": f"Bearer {bob_token['token']}"}
    draft = _new_draft(client, {"direct_category_ids": {"depop": "menswear_tshirts"}}, auth=alice_mcp)
    args = _refresh_args(draft, "depop")
    stolen, error = _call_tool(client, "get_marketplace_form", args, extra_headers=bob_mcp)
    assert error and "not found" in stolen["error"].lower()

    with Session(engine) as db:
        db.add(MarketplaceRelistRule(user_id=USER_ID, marketplace="poshmark", enabled=False, daily_cap=5))
        db.commit()
    for auth, cap in ((alice_mcp, 5), (bob_mcp, auto_relist.DEFAULT_DAILY_CAP)):
        status, error = _call_tool(client, "get_auto_relist_status", {"marketplace": "poshmark"}, auth)
        assert not error and status["rules"][0]["daily_cap"] == cap, status

    caps = ["marketplace.actions", hosted_browser_relay.MARKETPLACE_FORM_READ, bridge_approvals.SIGNED_COMMANDS_CAPABILITY]
    assert client.post("/api/browser-relay/heartbeat", headers=alice, json={"capabilities": caps}).status_code == 200
    form = {"marketplace": "depop", "fields": [
        {"path": "depop_specifics.brand", "label": "Brand", "options": [f"Style {i}" for i in range(75)] + ["Vintage label"]},
        {"path": "depop_specifics.shipFrom", "value": "A private address"},
    ]}

    def next_command():
        response = client.get("/api/browser-relay/next", headers=alice, params={"capabilities": ",".join(caps)})
        assert response.status_code == 200, response.text
        command = response.json()
        assert command["type"] == "marketplace.form" and command["payload"]["marketplace"] == "depop"
        assert command["job_id"] is None
        assert command["payload"]["approval"]
        return command

    def reply(command):
        assert client.post("/api/browser-relay/replies", headers=alice, json={
            "request_id": command["message_id"], "payload": {"ok": True, "refreshed": True, "form": form},
        }).status_code == 200

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(_call_tool, client, "get_marketplace_form", {**args, "field": "Brand", "query": "vintage"}, alice_mcp)
        reply(next_command())
        result, error = pending.result(timeout=5)
        assert not error and result["fetched_via"] == "chrome_bridge", result
        assert result["fields"][0]["options"] == ["Vintage label"]
        assert "private address" not in str(result)

        pending = worker.submit(_call_tool, client, "get_marketplace_form", args, alice_mcp)
        command = next_command()
        assert client.post("/api/drafts", headers=bob, json={"title": "Independent draft"}).status_code == 201
        edited, error = _call_tool(client, "edit_listing", {
            "conversation_id": draft["conversation_id"], "fields": {"price": 42},
        }, extra_headers=alice_mcp)
        assert not error, edited
        reply(command)
        result, error = pending.result(timeout=5)
    assert not error and result["reason"] == "listing_changed", result
