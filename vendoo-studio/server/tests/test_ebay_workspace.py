import asyncio
import base64
import io
import threading
from datetime import UTC, datetime, timedelta
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
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.ebay_action import EbayAction
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import ebay_actions, ebay_client, keychain
from vendoo_studio.services.vendoo_import import merge_notes


def example_image():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(stream, format="JPEG")
    return {"data": base64.b64encode(stream.getvalue()).decode(), "mime": "image/jpeg", "dispute_id": ""}


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
    ListingRepo(db).save_revision("coat", {"title": "Test wool coat", "price": 50, "cost": 5}, "user")

    def test_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
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
        "ad": {"campaign_id": "campaign-1", "listing_id": ITEM_ID, "bid_percent": "2"},
        "campaign_control": {"resource_id": "campaign-1", "action": "pause"},
        "promotion_control": {"resource_id": "promo-1", "action": "pause"},
        "email": {"subject": "Coat sale", "message": "See our coats", "audience_codes": ["FOLLOWERS"], "listing_ids": [ITEM_ID], "schedule": future},
        "draft": {"sku": "new-sku", "title": "Wool coat", "description": "A nice wool coat", "category_id": "123", "price": "45.00", "quantity": 1,
                  "condition": "USED_EXCELLENT", "image_urls": ["https://i.ebayimg.com/images/g/test/s-l1600.jpg"], "aspects": {"Brand": ["Acme"]},
                  "fulfillment_policy_id": "ship", "payment_policy_id": "pay", "return_policy_id": "return", "merchant_location_key": "warehouse"},
        "revise": {"listing_id": ITEM_ID, "title": "New title", "description": "Updated description", "price": "40", "quantity": 2},
        "publish_offer": {"offer_id": "offer-1", "marketplace": "ebay"},
        "inventory_revision": {"offer_id": "offer-1", "price": "40", "quantity": 2},
        "ad_report": {"campaign_id": "campaign-1", "start": "2026-09-01", "end": "2026-09-30"},
        "upload_image": example_image(),
        "upload_evidence": {**example_image(), "dispute_id": "dispute-1"},
    }


@pytest.mark.parametrize("kind", list(action_cases()))
def test_each_write_requires_review_then_confirmation_and_never_repeats(workspace, kind):
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
    assert count == (2 if kind == "draft" else 1)
    assert confirm(workspace, review, kind, payload).status_code == 409
    assert len(workspace.remote.writes) == count
    if kind != "publish_offer":
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


@pytest.mark.parametrize("section,unused_host", [("orders", "APIZ"), ("payouts", "API")])
def test_fulfillment_and_finances_use_their_actual_production_hosts(workspace, monkeypatch, section, unused_host):
    monkeypatch.setattr(ebay_client, unused_host, "http://127.0.0.1:1")
    response = workspace.client.get(f"/api/ebay/read/{section}")
    assert response.status_code == 200, response.text


def test_earnings_match_live_order_quantities_to_local_costs(workspace):
    row = workspace.client.get("/api/ebay/read/finances").json()["rows"][0]
    assert row["amount"] == "40.00"
    assert row["fields"]["Item cost"] == "10.00"
    assert row["fields"]["Profit"] == "30.00"
    assert row["fields"]["Expenses"] == "10.00"


def test_missing_costs_remain_unknown(workspace):
    ListingRepo(workspace.db).save_revision("coat", {"title": "Coat", "price": 50}, "user")
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
            with workspace.sessions() as db:
                try:
                    return await ebay_actions.execute(db, review["id"], "message", payload)
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
    ("campaign", {"bid_percent": "bad"}), ("ad", {"bid_percent": "2.00"}), ("draft", {"weight_oz": "bad"}),
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
    from vendoo_studio.services.schema_migrations import alembic_config
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
