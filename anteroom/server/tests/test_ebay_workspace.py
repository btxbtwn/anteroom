import asyncio
import base64
import io
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from ebay_workspace_fake import ITEM_ID, WorkspaceEbay, order
from anteroom.services import listing_persistence
from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.models.conversation import Conversation
from anteroom.models.ebay_action import EbayAction
from anteroom.services import ebay_actions, ebay_client, ebay_workspace, keychain
from anteroom.services.vendoo_import import merge_notes


@pytest.mark.parametrize("description", [
    '<![CDATA[<div>Cat tee.<br /><br />Length: 22.5&quot;</div>',
    '<![CDATA[<div>Cat tee.<br /><br />Length: 22.5&quot;</div>]]>',
])
def test_plain_keeps_description_inside_literal_cdata(description):
    assert ebay_workspace.plain(description) == 'Cat tee.\n\nLength: 22.5"'


def example_image():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(stream, format="JPEG")
    return {"data": base64.b64encode(stream.getvalue()).decode(), "mime": "image/jpeg"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    state = SimpleNamespace(reads=[], writes=[], inventory={}, overrides={}, read_errors={}, write_error=0, write_results={})
    server = ThreadingHTTPServer(("127.0.0.1", 0), WorkspaceEbay)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(ebay_client, "API", origin)
    monkeypatch.setattr(ebay_client, "APIZ", origin)
    monkeypatch.setattr(ebay_client, "APIM", origin)
    accounts = {"ebay": {"accountId": "seller-id", "label": "seller", "accessToken": "test-token", "connectedAt": 1}}
    monkeypatch.setattr(ebay_client, "get_marketplace_accounts", lambda: accounts)
    monkeypatch.setattr(keychain, "get_marketplace_accounts", lambda: accounts)
    monkeypatch.setattr(ebay_client, "access_token", AsyncMock(return_value="test-token"))
    async def research(_method, _path, *, json_body):
        paths = {"categories": "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions",
                 "aspects": "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
                 "search": "/buy/browse/v1/item_summary/search", "image": "/buy/browse/v1/item_summary/search_by_image",
                 "ad_metadata": "/sell/marketing/v1/ad_report_metadata/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT"}
        async with httpx.AsyncClient() as http:
            return await http.request("POST" if json_body["operation"] == "image" else "GET", origin + paths[json_body["operation"]],
                                      params=json_body["params"], json={"image": json_body.get("image")} if json_body["operation"] == "image" else None)
    monkeypatch.setattr(ebay_client, "relay_request", research)
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    load_models()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    db.add(Conversation(id="coat", title="Test wool coat", status="active", notes=merge_notes(None, {
        "vendooListingUrls": {"ebay": f"https://www.ebay.com/itm/{ITEM_ID}"},
    })))
    db.commit()
    listing_persistence.save_revision(db, "coat", {"title": "Test wool coat", "price": 50, "cost": 5}, "user")

    def test_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    # eBay workspace handlers open their own short sessions on worker threads.
    monkeypatch.setattr("anteroom.database.SessionLocal", sessions)
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, db=db, sessions=sessions, remote=state, accounts=accounts)
    finally:
        client.close()
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def preview(workspace, kind, payload):
    response = workspace.client.post("/api/ebay/actions/preview", json={"kind": kind, "payload": payload})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(workspace, review, kind, payload, **extra):
    return workspace.client.post("/api/ebay/actions/confirm", json={"id": review["id"], "kind": kind, "payload": payload, "confirm": True, **extra})


def action_cases():
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    return {
        "message": {"conversation_id": "chat-1", "text": "Private reply to the buyer"},
        "mark_read": {"conversation_id": "chat-1"},
        "feedback": {"listing_id": ITEM_ID, "line_item_id": "line-1", "transaction_id": "tx-1", "text": "Thanks!"},
        "shipment": {"order_id": "order-1", "carrier": "USPS", "tracking": "TESTTRACKING"},
        "refund": {"order_id": "order-1", "amount": "12.50", "reason": "BUYER_RETURN"},
        "contest": {"dispute_id": "dispute-1", "note": "Evidence attached"},
        "evidence": {"dispute_id": "dispute-1", "file_id": "file-1", "evidence_type": "PROOF_OF_DELIVERY"},
        "markdown": {"name": "Coat sale", "listing_ids": [ITEM_ID], "percent": 10, "start": future, "end": end, "image_url": "https://i.ebayimg.com/image.jpg"},
        "quantity_discount": {"name": "Two coats", "listing_ids": [ITEM_ID], "percent": 10, "quantity": 2, "start": future, "end": end, "image_url": "https://i.ebayimg.com/image.jpg"},
        "campaign": {"name": "Coat campaign", "bid_percent": "2", "start": future},
        "ad": {"campaign_id": "campaign-1", "listing_ids": [ITEM_ID], "bid_percent": "2"},
        "campaign_control": {"resource_id": "campaign-1", "action": "pause"},
        "promotion_control": {"resource_id": "promo-1", "action": "pause"},
        "email": {"subject": "Coat sale", "message": "See our coats", "audience_codes": ["FOLLOWERS"], "listing_ids": [ITEM_ID], "schedule": future},
        "revise": {"listing_id": ITEM_ID, "title": "New title", "description": "Updated description", "price": "40", "quantity": 2},
        "inventory_revision": {"offer_id": "offer-1", "price": "40", "quantity": 2},
        "ad_report": {"campaign_id": "campaign-1", "start": "2026-09-01", "end": "2026-09-30"},
        "upload_evidence": {**example_image(), "dispute_id": "dispute-1"},
    }


@pytest.mark.parametrize("kind", list(action_cases()))
def test_each_write_requires_review_then_confirmation_and_never_repeats(workspace, kind, monkeypatch):
    if kind in ("contest", "evidence", "upload_evidence"):
        monkeypatch.setattr(ebay_client, "API", "http://127.0.0.1:1")
    payload = action_cases()[kind]
    review = preview(workspace, kind, payload)
    assert workspace.remote.writes == []
    rejected = confirm(workspace, review, kind, payload, confirm=False)
    assert rejected.status_code == 422
    assert workspace.remote.writes == []
    response = confirm(workspace, review, kind, payload)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "sent", response.text
    count = len(workspace.remote.writes)
    assert count == 1
    assert confirm(workspace, review, kind, payload).status_code == 409
    assert len(workspace.remote.writes) == count
    assert not any(path.endswith("/publish") for _, path, _ in workspace.remote.writes)


@pytest.mark.parametrize("section,target", [("traffic", ""), ("finances", ""), ("payouts", ""), ("inbox", ""), ("messages", "chat-1"),
    ("feedback", ""), ("orders", ""), ("order", "order-1"), ("disputes", ""), ("dispute", "dispute-1"), ("campaigns", ""),
    ("promotions", ""), ("promotion-reports", ""), ("emails", ""), ("audiences", ""), ("inventory", ""), ("offers", "existing"),
    ("policies", ""), ("locations", ""), ("categories", ""), ("aspects", "123"), ("research", ""), ("ad-reports", ""), ("ad-report", "task-1")])
def test_read_views_use_real_http_and_return_displayable_rows(workspace, section, target):
    response = workspace.client.get(f"/api/ebay/read/{section}", params={"target": target, "q": "coat"})
    assert response.status_code == 200, response.text
    assert response.json()["rows"], section
    assert workspace.remote.writes == []
    assert "accessToken" not in response.text and "test-token" not in response.text


@pytest.mark.parametrize("section,target,unused_host", [("orders", "", "APIZ"), ("payouts", "", "API"),
    ("disputes", "", "API"), ("dispute", "dispute-1", "API")])
def test_fulfillment_and_finances_use_their_actual_production_hosts(workspace, monkeypatch, section, target, unused_host):
    monkeypatch.setattr(ebay_client, unused_host, "http://127.0.0.1:1")
    response = workspace.client.get(f"/api/ebay/read/{section}", params={"target": target})
    assert response.status_code == 200, response.text


def test_earnings_match_live_order_quantities_to_local_costs(workspace):
    row = workspace.client.get("/api/ebay/read/finances").json()["rows"][0]
    assert row["amount"] == "40.00"
    assert row["fields"]["Item cost"] == "10.00"
    assert row["fields"]["Profit"] == "30.00"
    assert row["fields"]["Proceeds"] == "Recorded order credits minus debits"
    assert not any("order_earnings" in path for path in workspace.remote.reads)
    assert any("filter=creationdate" in path for path in workspace.remote.reads)


def test_finance_end_date_never_extends_into_the_future(monkeypatch):
    now = datetime(2026, 10, 24, 12, 34, 56, tzinfo=UTC)
    monkeypatch.setattr(ebay_workspace, "datetime", SimpleNamespace(now=lambda _zone: now))
    assert ebay_workspace.dates(date(2026, 10, 1), date(2026, 10, 24)) == (
        "2026-10-01T00:00:00.000Z", "2026-10-24T12:34:56.000Z")
    assert ebay_workspace.dates(date(2026, 10, 1), date(2026, 10, 23))[1] == "2026-10-23T23:59:59.999Z"


def test_missing_costs_remain_unknown(workspace):
    listing_persistence.save_revision(workspace.db, "coat", {"title": "Coat", "price": 50}, "user")
    row = workspace.client.get("/api/ebay/read/finances").json()["rows"][0]
    assert row["fields"]["Profit"] is None


def test_reviews_are_bound_to_input_account_and_expiration(workspace):
    payload = action_cases()["message"]
    review = preview(workspace, "message", payload)
    assert confirm(workspace, review, "message", {**payload, "text": "Different"}).status_code == 409
    workspace.accounts["ebay"]["connectedAt"] = 2
    assert confirm(workspace, review, "message", payload).status_code == 409
    workspace.accounts["ebay"]["connectedAt"] = 1
    action = workspace.db.get(EbayAction, review["id"])
    action.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    workspace.db.commit()
    assert confirm(workspace, review, "message", payload).status_code == 409
    assert workspace.remote.writes == []


def test_updated_refund_state_invalidates_review(workspace):
    payload = action_cases()["refund"]
    review = preview(workspace, "refund", payload)
    changed = order()
    changed["paymentSummary"]["refunds"] = [{"amount": {"value": "1.00", "currency": "USD"}}]
    workspace.remote.overrides["/sell/fulfillment/v1/order/order-1"] = changed
    assert confirm(workspace, review, "refund", payload).status_code == 409
    assert workspace.remote.writes == []


def test_unknown_outcomes_are_journaled_without_private_content_and_not_retried(workspace):
    payload = action_cases()["message"]
    review = preview(workspace, "message", payload)
    workspace.remote.write_error = 500
    response = confirm(workspace, review, "message", payload)
    assert response.json()["state"] == "unknown"
    assert len(workspace.remote.writes) == 1
    response = workspace.client.post("/api/ebay/actions/preview", json={"kind": "message", "payload": payload})
    assert response.status_code == 409
    rows = workspace.db.execute(text("SELECT * FROM ebay_actions")).fetchall()
    assert "Private reply" not in str(rows) and "test-token" not in str(rows)
    response = workspace.client.post(f"/api/ebay/actions/{review['id']}/checked", json={"confirm": True})
    assert response.status_code == 200
    assert len(workspace.remote.writes) == 1


def test_two_previews_have_one_concurrent_send(workspace):
    payload = action_cases()["message"]
    first, second = preview(workspace, "message", payload), preview(workspace, "message", payload)

    async def scenario():
        async def attempt(review):
            try:
                return await ebay_actions.execute(review["id"], "message", payload)
            except ebay_client.EbayFailure:
                return {"state": "blocked"}
        return await asyncio.gather(attempt(first), attempt(second))

    results = asyncio.run(scenario())
    assert sorted(r["state"] for r in results) == ["blocked", "sent"]
    assert len(workspace.remote.writes) == 1


def test_private_provider_errors_are_not_exposed(workspace):
    workspace.remote.read_errors["/commerce/message/v1/conversation"] = 403
    response = workspace.client.get("/api/ebay/read/inbox")
    assert response.status_code == 403
    assert "PRIVATE ERROR ECHO" not in response.text


@pytest.mark.parametrize("amount", ["50.01", "NaN", "Infinity", "0", "-1", "1.001"])
def test_invalid_refunds_never_become_writes(workspace, amount):
    payload = {**action_cases()["refund"], "amount": amount}
    response = workspace.client.post("/api/ebay/actions/preview", json={"kind": "refund", "payload": payload})
    assert response.status_code in (409, 422)
    assert workspace.remote.writes == []


def test_image_search_is_a_read_not_a_listing_write(workspace):
    response = workspace.client.post("/api/ebay/research/image", json=example_image())
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["title"] == "Image match"
    assert workspace.remote.writes == []


def test_interrupted_writes_recover_as_unknown_without_replay(workspace):
    review = preview(workspace, "message", action_cases()["message"])
    action = workspace.db.get(EbayAction, review["id"])
    action.state = "sending"
    workspace.db.commit()
    ebay_actions.recover(workspace.db)
    assert action.state == "unknown"
    assert workspace.remote.writes == []


@pytest.mark.parametrize("kind,path,changed", [
    ("campaign_control", "/sell/marketing/v1/ad_campaign/campaign-1", {
        "campaignName": "Coat campaign", "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": "15"}}),
    ("promotion_control", "/sell/marketing/v1/item_price_markdown/promo-1%40EBAY_US", {
        "name": "Coat sale", "selectedInventoryDiscounts": [{"discountBenefit": {"percentageOffItem": "50"}}]}),
])
def test_controls_recheck_material_live_settings(workspace, kind, path, changed):
    payload = {**action_cases()[kind], "action": "resume"}
    review = preview(workspace, kind, payload)
    assert any("2" in line or "10" in line for line in review["summary"])
    workspace.remote.overrides[path] = changed
    assert confirm(workspace, review, kind, payload).status_code == 409
    assert workspace.remote.writes == []


@pytest.mark.parametrize("kind", ["campaign_control", "promotion_control", "mark_read"])
def test_fresh_control_reviews_can_repeat_after_success(workspace, kind):
    payload = action_cases()[kind]
    for _ in range(3):
        review = preview(workspace, kind, payload)
        assert confirm(workspace, review, kind, payload).json()["state"] == "sent"
        assert confirm(workspace, review, kind, payload).status_code == 409
    assert len(workspace.remote.writes) == 3


@pytest.mark.parametrize("kind", ["shipment", "refund"])
def test_partially_refunded_orders_allow_remaining_operations(workspace, kind):
    updated = order()
    updated["orderPaymentStatus"] = "PARTIALLY_REFUNDED"
    updated["paymentSummary"]["refunds"] = [{"refundStatus": "SUCCEEDED", "amount": {"currency": "USD", "value": "10"}}]
    workspace.remote.overrides["/sell/fulfillment/v1/order/order-1"] = updated
    payload = action_cases()[kind]
    review = preview(workspace, kind, payload)
    assert confirm(workspace, review, kind, payload).json()["state"] == "sent"


def test_failed_refunds_do_not_consume_balance_or_report_success(workspace):
    updated = order()
    updated["paymentSummary"]["refunds"] = [{"refundStatus": "FAILED", "amount": {"currency": "USD", "value": "50"}}]
    workspace.remote.overrides["/sell/fulfillment/v1/order/order-1"] = updated
    payload = action_cases()["refund"]
    review = preview(workspace, "refund", payload)
    workspace.remote.write_results["/sell/fulfillment/v1/order/order-1/issue_refund"] = {"refundStatus": "FAILED"}
    assert confirm(workspace, review, "refund", payload).status_code == 422
    workspace.db.expire_all()
    assert workspace.db.get(EbayAction, review["id"]).state == "failed"


def test_pending_refund_is_shown_as_pending_and_reserves_balance(workspace):
    payload = action_cases()["refund"]
    review = preview(workspace, "refund", payload)
    workspace.remote.write_results["/sell/fulfillment/v1/order/order-1/issue_refund"] = {"refundStatus": "PENDING"}
    result = confirm(workspace, review, "refund", payload).json()
    assert "pending" in result["message"]
    updated = order()
    updated["paymentSummary"]["refunds"] = [{"refundStatus": "PENDING", "amount": {"currency": "USD", "value": "50"}}]
    workspace.remote.overrides["/sell/fulfillment/v1/order/order-1"] = updated
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "refund", "payload": payload}).status_code == 409


def test_email_filters_and_audience_pagination(workspace, monkeypatch):
    calls = []
    original = ebay_client.EbayClient.request

    async def request(self, method, path, **kwargs):
        if path.endswith("/email_campaign/audience"):
            params = kwargs["params"]
            assert params["emailCampaignType"] == "ITEM_SHOWCASE"
            calls.append(params["offset"])
            return {"total": 201, "audiences": [{"code": "FOLLOWERS" if params["offset"] else "FIRST"}]}
        return await original(self, method, path, **kwargs)

    monkeypatch.setattr(ebay_client.EbayClient, "request", request)
    preview(workspace, "email", action_cases()["email"])
    assert calls == [0, 200]
    response = workspace.client.get("/api/ebay/read/emails")
    assert response.status_code == 200
    assert any("q=campaignType%3AITEM_SHOWCASE" in path for path in workspace.remote.reads)


@pytest.mark.parametrize("kind", ["markdown", "quantity_discount"])
def test_discounts_bind_required_promotion_image(workspace, kind):
    payload = action_cases()[kind]
    review = preview(workspace, kind, payload)
    assert review["images"] == [payload["image_url"]]
    assert confirm(workspace, review, kind, payload).status_code == 200
    assert workspace.remote.writes[0][2]["promotionImageUrl"] == payload["image_url"]


def test_reconnect_after_accepted_refund_preserves_unknown_guard(workspace, monkeypatch):
    payload = action_cases()["refund"]
    review = preview(workspace, "refund", payload)
    original = httpx.AsyncClient.request

    async def request(self, method, url, **kwargs):
        response = await original(self, method, url, **kwargs)
        if str(url).endswith("/issue_refund"):
            workspace.accounts["ebay"]["connectedAt"] = 2
        return response

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    result = confirm(workspace, review, "refund", payload)
    assert result.json()["state"] == "unknown"
    assert len(workspace.remote.writes) == 1
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "refund", "payload": payload}).status_code == 409


def test_resume_binds_all_ads_and_actual_override_rates(workspace, monkeypatch):
    original = ebay_client.EbayClient.request
    rate, offsets = ["2"], []

    async def request(self, method, path, **kwargs):
        if path.endswith("/campaign-1/ad") and method == "GET":
            offset = kwargs["params"]["offset"]
            offsets.append(offset)
            return {"total": 201, "ads": [{"adId": str(offset), "listingId": str(offset), "bidPercentage": rate[0]}]}
        return await original(self, method, path, **kwargs)

    monkeypatch.setattr(ebay_client.EbayClient, "request", request)
    payload = {**action_cases()["campaign_control"], "action": "resume"}
    review = preview(workspace, "campaign_control", payload)
    assert offsets == [0, 200]
    assert any("effective rates" in line for line in review["summary"])
    rate[0] = "80"
    assert confirm(workspace, review, "campaign_control", payload).status_code == 409
    assert workspace.remote.writes == []


def test_snad_contest_requires_return_address_and_binds_it(workspace):
    workspace.remote.overrides["/sell/fulfillment/v1/payment_dispute/dispute-1"] = {
        "revision": 1, "paymentDisputeStatus": "OPEN", "reason": "SIGNIFICANTLY_NOT_AS_DESCRIBED"}
    payload = action_cases()["contest"]
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "contest", "payload": payload}).status_code == 422
    payload["return_address"] = {"fullName": "Test Seller", "addressLine1": "123 Test Street", "city": "Austin", "stateOrProvince": "TX", "postalCode": "78701", "phone": "+1 555 555 1234"}
    review = preview(workspace, "contest", payload)
    assert any("123 Test Street" in line for line in review["summary"])
    assert confirm(workspace, review, "contest", payload).status_code == 200
    assert workspace.remote.writes[0][2]["returnAddress"]["primaryPhone"] == {"number": "15555551234", "countryCode": "US"}
    rows = workspace.db.execute(text("SELECT * FROM ebay_actions")).fetchall()
    assert "123 Test Street" not in str(rows)


@pytest.mark.parametrize("kind,changes", [("markdown", {"percent": 90}), ("quantity_discount", {"percent": 90}),
    ("campaign", {"bid_percent": "2.25"}), ("ad", {"bid_percent": "2.25"}), ("email", {"subject": "x" * 71}),
    ("campaign", {"bid_percent": "bad"}), ("ad", {"bid_percent": "2.00"}),
    ("markdown", {"end": (datetime.now(UTC) + timedelta(days=70)).isoformat()}),
    ("markdown", {"end": (datetime.now(UTC) + timedelta(days=1, hours=1)).isoformat(), "start": (datetime.now(UTC) + timedelta(days=1)).isoformat()}),
])
def test_contract_limits_reject_invalid_marketing_approvals(workspace, kind, changes):
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": kind, "payload": {**action_cases()[kind], **changes}}).status_code == 422
    assert workspace.remote.writes == []


def test_line_item_refunds_reduce_remaining_balance_and_invalidate_reviews(workspace):
    payload = {**action_cases()["refund"], "amount": "25"}
    review = preview(workspace, "refund", payload)
    updated = order()
    updated["orderPaymentStatus"] = "PARTIALLY_REFUNDED"
    updated["lineItems"][0]["refunds"] = [{"refundId": "line-refund", "amount": {"currency": "USD", "value": "40"}}]
    workspace.remote.overrides["/sell/fulfillment/v1/order/order-1"] = updated
    assert confirm(workspace, review, "refund", payload).status_code == 409
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "refund", "payload": payload}).status_code == 409
    assert workspace.remote.writes == []


def test_refund_allocations_are_summed_across_items_and_deduplicated_at_order_level():
    refund = {"refundId": "refund-1", "amount": {"currency": "USD", "value": "15"}}
    updated = order()
    updated["lineItems"] = [{"refunds": [refund, refund]}, {"refunds": [refund]}]
    updated["paymentSummary"]["refunds"] = [{**refund, "amount": {"currency": "USD", "value": "30"}},
                                            {"refundId": "other", "amount": {"currency": "USD", "value": "5"}}]
    assert ebay_actions.refunded_cents(updated) == 3500


def test_campaign_resume_discloses_continuing_future_inventory_selection(workspace):
    workspace.remote.overrides["/sell/marketing/v1/ad_campaign/campaign-1"] = {
        "campaignName": "Automatic coats", "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": "2"},
        "campaignCriterion": {"autoSelectFutureInventory": True, "selectionRules": [{"categoryIds": ["123"]}]}}
    payload = {**action_cases()["campaign_control"], "action": "resume"}
    review = preview(workspace, "campaign_control", payload)
    assert any("Future matching listings" in line and "incur fees" in line for line in review["summary"])
    assert any("123" in line and "selectionRules" in line for line in review["summary"])
    workspace.remote.overrides["/sell/marketing/v1/ad_campaign/campaign-1"]["campaignCriterion"]["selectionRules"][0]["categoryIds"] = ["999"]
    assert confirm(workspace, review, "campaign_control", payload).status_code == 409
    assert workspace.remote.writes == []


def test_action_migration_preserves_previous_release_database(tmp_path):
    from alembic import command
    from anteroom.services.schema_migrations import alembic_config
    path = tmp_path / "before.db"
    config = alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "7a64a26d3dc2")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO conversations(id,title) VALUES ('keep','Keep this item')"))
    command.upgrade(config, "head")
    assert "ebay_actions" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM conversations WHERE id='keep'")).scalar() == "Keep this item"
    engine.dispose()


@pytest.mark.parametrize("confirm_with_bulk_shape", [False, True])
def test_installed_iphone_ad_payload_uses_the_same_review_and_confirmation(workspace, confirm_with_bulk_shape):
    legacy = {"campaign_id": "campaign-1", "listing_id": ITEM_ID, "bid_percent": "2"}
    bulk = {"campaign_id": "campaign-1", "listing_ids": [ITEM_ID], "bid_percent": "2"}
    review = preview(workspace, "ad", legacy)
    payload = bulk if confirm_with_bulk_shape else legacy
    assert confirm(workspace, review, "ad", payload, confirm=False).status_code == 422
    assert not workspace.remote.writes
    response = confirm(workspace, review, "ad", payload)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "sent"
    assert workspace.remote.writes == [(
        "POST", "/sell/marketing/v1/ad_campaign/campaign-1/bulk_create_ads_by_listing_id",
        {"requests": [{"listingId": ITEM_ID, "bidPercentage": "2"}]},
    )]


@pytest.mark.parametrize("fields", [
    {"listing_id": ITEM_ID, "listing_ids": [ITEM_ID]},
    {"listing_id": "invalid"},
    {"listing_id": ITEM_ID, "unexpected": True},
])
def test_invalid_legacy_ad_payloads_are_rejected(workspace, fields):
    payload = {"campaign_id": "campaign-1", "bid_percent": "2", **fields}
    response = workspace.client.post("/api/ebay/actions/preview", json={"kind": "ad", "payload": payload})
    assert response.status_code == 422, response.text
    assert not workspace.remote.writes


def test_bulk_ads_use_active_ebay_inventory_and_explicit_confirmation(workspace):
    second_id = "123456789013"
    path = "/sell/marketing/v1/ad_campaign/campaign-1/bulk_create_ads_by_listing_id"
    workspace.remote.write_results = {path: {"responses": [
        {"listingId": ITEM_ID, "adId": "ad-1", "statusCode": 200},
        {"listingId": second_id, "adId": "ad-2", "statusCode": 200},
    ]}}
    response = workspace.client.get("/api/ebay/read/advertising-listings")
    assert response.status_code == 200
    assert {row["id"] for row in response.json()["rows"]} == {ITEM_ID, second_id}

    payload = {"campaign_id": "campaign-1", "listing_ids": [second_id, ITEM_ID, ITEM_ID], "bid_percent": "3.5"}
    review = preview(workspace, "ad", payload)
    assert any("3.5%" in line for line in review["summary"])
    assert confirm(workspace, review, "ad", payload, confirm=False).status_code == 422
    assert not workspace.remote.writes
    result = confirm(workspace, review, "ad", payload)
    assert result.json()["state"] == "sent"
    assert workspace.remote.writes == [("POST", path, {"requests": [
        {"listingId": ITEM_ID, "bidPercentage": "3.5"}, {"listingId": second_id, "bidPercentage": "3.5"},
    ]})]
    assert confirm(workspace, review, "ad", payload).status_code == 409


def test_bulk_ads_cover_multiple_inventory_pages_and_ebay_write_batches(workspace):
    workspace.remote.active_items = [(str(123456789000 + index), f"Listing {index}") for index in range(631)]
    ids = [identifier for identifier, _ in workspace.remote.active_items]
    response = workspace.client.get("/api/ebay/read/advertising-listings")
    assert [row["id"] for row in response.json()["rows"]] == ids
    payload = {"campaign_id": "campaign-1", "listing_ids": ids, "bid_percent": "4.5"}
    review = preview(workspace, "ad", payload)
    assert not workspace.remote.writes
    result = confirm(workspace, review, "ad", payload).json()
    assert result["message"] == "eBay added 631 of 631 listings to the campaign."
    batches = [body["requests"] for _, _, body in workspace.remote.writes]
    assert [len(batch) for batch in batches] == [500, 131]
    assert [ad["listingId"] for batch in batches for ad in batch] == ids


def test_bulk_ads_reject_stale_inventory_and_report_declined_ids(workspace):
    second_id = "123456789013"
    payload = {"campaign_id": "campaign-1", "listing_ids": [ITEM_ID, second_id], "bid_percent": "3.5"}
    review = preview(workspace, "ad", payload)
    workspace.remote.active_items = [(ITEM_ID, "Test wool coat")]
    assert confirm(workspace, review, "ad", payload).status_code == 409
    assert not workspace.remote.writes

    workspace.remote.active_items.append((second_id, "Test jeans"))
    path = "/sell/marketing/v1/ad_campaign/campaign-1/bulk_create_ads_by_listing_id"
    workspace.remote.write_results = {path: {"responses": [
        {"listingId": ITEM_ID, "statusCode": 200},
        {"listingId": second_id, "statusCode": 409, "errors": [{"message": "PRIVATE ERROR ECHO"}]},
    ]}}
    result = confirm(workspace, review, "ad", payload).json()
    assert result["message"] == f"eBay added 1 of 2 listings to the campaign. eBay declined these listing IDs: {second_id}."
    assert confirm(workspace, review, "ad", payload).status_code == 409


def test_wholly_declined_ads_can_be_reviewed_again(workspace):
    payload = action_cases()["ad"]
    review = preview(workspace, "ad", payload)
    path = "/sell/marketing/v1/ad_campaign/campaign-1/bulk_create_ads_by_listing_id"
    workspace.remote.write_results = {path: {"responses": [{"listingId": ITEM_ID, "statusCode": 409}]}}
    result = confirm(workspace, review, "ad", payload).json()
    assert result["state"] == "failed"
    assert ITEM_ID in result["message"]
    assert confirm(workspace, review, "ad", payload).status_code == 409
    workspace.remote.write_results.clear()
    retry = preview(workspace, "ad", payload)
    assert confirm(workspace, retry, "ad", payload).json()["state"] == "sent"
    assert len(workspace.remote.writes) == 2


def test_later_ad_batch_failure_reports_completed_results(workspace):
    workspace.remote.active_items = [(str(123456789000 + i), f"Listing {i}") for i in range(501)]
    ids = [identifier for identifier, _ in workspace.remote.active_items]
    payload = {**action_cases()["ad"], "listing_ids": ids}
    path = "/sell/marketing/v1/ad_campaign/campaign-1/bulk_create_ads_by_listing_id"
    workspace.remote.write_results = {path: {"responses": [
        {"listingId": identifier, "statusCode": 409 if i == 0 else 200}
        for i, identifier in enumerate(ids[:500])
    ]}}
    workspace.remote.write_errors = {2: 500}
    review = preview(workspace, "ad", payload)
    result = confirm(workspace, review, "ad", payload).json()
    assert result["state"] == "unknown"
    assert "eBay added 499 of 501 listings" in result["message"]
    assert f"declined these listing IDs: {ids[0]}." in result["message"]
    assert f"did not confirm these listing IDs: {ids[-1]}." in result["message"]
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "ad", "payload": payload}).status_code == 409
    assert len(workspace.remote.writes) == 2


def test_legacy_unknown_ad_blocks_review_and_confirmation_for_that_listing(workspace):
    payload = action_cases()["ad"]
    review = preview(workspace, "ad", payload)
    legacy = EbayAction(account_id="seller-id", kind="ad", resource_id=ITEM_ID,
                        fingerprint="legacy-ad-plan", state="unknown", expires_at=ebay_actions.clock())
    workspace.db.add(legacy)
    workspace.db.commit()
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "ad", "payload": payload}).status_code == 409
    assert confirm(workspace, review, "ad", payload).status_code == 409
    preview(workspace, "ad", {**payload, "listing_ids": ["123456789013"]})
    assert not workspace.remote.writes
    checked = workspace.client.post(f"/api/ebay/actions/{legacy.id}/checked", json={"confirm": True})
    assert checked.status_code == 200
    assert confirm(workspace, review, "ad", payload).json()["state"] == "sent"


@pytest.mark.parametrize("during_confirmation", [False, True])
def test_capped_ad_inventory_is_never_offered_or_used_for_confirmation(workspace, during_confirmation):
    payload = action_cases()["ad"]
    if during_confirmation:
        review = preview(workspace, "ad", payload)
    workspace.remote.active_total = 25_000
    read = workspace.client.get("/api/ebay/read/advertising-listings")
    assert read.status_code == 422
    assert "Seller Hub" in read.json()["detail"]
    if during_confirmation:
        response = confirm(workspace, review, "ad", payload)
    else:
        response = workspace.client.post("/api/ebay/actions/preview", json={"kind": "ad", "payload": payload})
    assert response.status_code == 422
    assert not workspace.remote.writes


def test_first_ad_batch_http_conflict_requires_reconciliation(workspace):
    payload = action_cases()["ad"]
    review = preview(workspace, "ad", payload)
    workspace.remote.write_error = 409
    response = confirm(workspace, review, "ad", payload)
    assert response.json()["state"] == "unknown"
    assert workspace.client.post("/api/ebay/actions/preview", json={"kind": "ad", "payload": payload}).status_code == 409
    assert len(workspace.remote.writes) == 1
