import copy
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services import keychain, marketplace_accounts


def money(amount=1250, divisor=100, currency="USD"):
    return {"amount": amount, "divisor": divisor, "currency_code": currency}


def order(receipt_id=123, **extra):
    return {
        "receipt_id": receipt_id, "status": "paid", "is_paid": True, "is_shipped": False,
        "create_timestamp": 1790812800, "grandtotal": money(),
        "name": "Private buyer", "buyer_email": "private@example.test", "formatted_address": "Private address",
        "message_from_buyer": "Private message", "transactions": [
            {"transaction_id": receipt_id + 100, "title": "Shirt &amp; jacket", "quantity": 2,
             "sku": "SHIRT-1", "is_digital": False, "buyer_user_id": 999},
        ], **extra,
    }


@pytest.fixture
def workspace(monkeypatch):
    state = SimpleNamespace(calls=[], status=200, after_read=None, payloads={
        "shops/42": {"shop_id": 42, "shop_name": "MyShop", "title": "Vintage &amp; handmade", "listing_active_count": 12,
                     "transaction_sold_count": 400, "num_favorers": 80, "review_count": 35, "review_average": 4.9,
                     "is_vacation": False, "accessToken": "never-display", "policy_seller_info": "Private seller info"},
        "shops/42/receipts": {"count": 30, "results": [order()]},
        "shops/42/listings": {"count": 1, "results": [
            {"listing_id": 555, "title": "Vintage coat", "state": "active", "price": money(5000),
             "quantity": 1, "num_favorers": 4, "creation_timestamp": 1790812800,
             "url": "https://untrusted.example/", "User": {"email": "private@example.test"}},
        ]},
    }, accounts={"etsy": {"accountId": "42", "label": "MyShop", "connectedAt": 1,
                           "accessToken": "private-token", "expiresAt": time.time() + 3600}})
    monkeypatch.setattr(keychain, "get_marketplace_accounts", lambda: state.accounts)
    monkeypatch.setattr(marketplace_accounts, "get_marketplace_accounts", lambda: state.accounts)

    async def relay(method, path, *, params, headers):
        state.calls.append((method, path, params, headers))
        assert method == "GET"
        body = copy.deepcopy(state.payloads[path.removeprefix("/studio/etsy/v3/application/")])
        if state.after_read:
            state.after_read()
        return httpx.Response(state.status, json=body if state.status == 200 else {"error": "private-token Private buyer"},
                              request=httpx.Request(method, "https://relay.example" + path, headers=headers))

    monkeypatch.setattr(marketplace_accounts, "relay_request", relay)
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, remote=state)
    finally:
        client.close()


def test_shop_snapshot_uses_the_connected_shop_and_whitelists_fields(workspace):
    response = workspace.client.get("/api/etsy/shop")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["name"], body["activeListings"], body["rating"]) == ("MyShop", 12, 4.9)
    assert body["title"] == "Vintage & handmade"
    assert body["url"] == "https://www.etsy.com/shop/MyShop"
    assert "never-display" not in response.text
    assert "Private seller" not in response.text
    assert workspace.remote.calls[0][:2] == ("GET", "/studio/etsy/v3/application/shops/42")
    assert workspace.remote.calls[0][3] == {"X-Etsy-Authorization": "Bearer private-token"}


def test_orders_use_inclusive_utc_dates_filters_and_pagination(workspace):
    response = workspace.client.get("/api/etsy/orders?start=2026-10-01&end=2026-10-02&offset=25&status=unshipped")
    assert response.status_code == 200
    method, path, params, _ = workspace.remote.calls[-1]
    assert (method, path) == ("GET", "/studio/etsy/v3/application/shops/42/receipts")
    assert params == {
        "limit": 25, "offset": 25, "sort_on": "created", "sort_order": "desc",
        "min_created": int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()),
        "max_created": int(datetime(2026, 10, 3, tzinfo=UTC).timestamp()) - 1,
        "was_paid": "true", "was_shipped": "false", "was_canceled": "false",
    }
    assert response.json()["nextOffset"] == 26
    assert response.json()["rows"][0]["items"][0]["title"] == "Shirt & jacket"
    for private in ["Private buyer", "private@example.test", "Private address", "Private message", "buyer_user_id", "private-token"]:
        assert private not in response.text


def test_page_totals_keep_currencies_and_unknown_amounts_separate(workspace):
    payload = workspace.remote.payloads["shops/42/receipts"]
    payload.update(count=5, results=[order(1), order(2, grandtotal=money(2000)),
                                    order(3, grandtotal=money(2500, 1000, "KWD")),
                                    order(4, grandtotal=money(divisor=0)), order(5, is_paid=False)])
    body = workspace.client.get("/api/etsy/orders").json()
    assert body["nextOffset"] is None
    assert body["summary"] == {
        "paidOrders": 4, "needsShipping": 4, "missingTotals": 1,
        "paidTotals": [{"currency": "KWD", "amount": "2.5"}, {"currency": "USD", "amount": "32.5"}],
    }
    assert body["rows"][3]["total"] is None


@pytest.mark.parametrize("extra", [
    {"status": "canceled"}, {"status": "fully refunded"}, {"is_paid": False}, {"is_shipped": True},
    {"transactions": [{"transaction_id": 20, "is_digital": True, "title": "Download"}]}, {"transactions": []},
])
def test_shipping_counts_exclude_cancelled_refunded_digital_and_unknown_orders(workspace, extra):
    workspace.remote.payloads["shops/42/receipts"] = {"count": 1, "results": [order(**extra)]}
    assert workspace.client.get("/api/etsy/orders").json()["summary"]["needsShipping"] == 0


def test_lists_drafts_without_mutating_or_linking_them_as_public(workspace):
    raw = workspace.remote.payloads["shops/42/listings"]["results"][0]
    raw["state"] = "draft"
    response = workspace.client.get("/api/etsy/listings?state=draft&offset=25")
    assert response.status_code == 200
    assert response.json()["rows"][0]["url"] is None
    assert response.json()["rows"][0]["price"] == {"amount": "50", "currency": "USD"}
    assert "private@example.test" not in response.text
    assert "untrusted.example" not in response.text
    assert workspace.remote.calls[-1][2]["state"] == "draft"
    assert all(call[0] == "GET" for call in workspace.remote.calls)
    assert workspace.client.post("/api/etsy/listings").status_code == 405


@pytest.mark.parametrize("path", [
    "/api/etsy/orders?start=2026-10-05&end=2026-10-01", "/api/etsy/orders?start=1999-12-31",
    "/api/etsy/orders?end=2099-01-01", "/api/etsy/orders?start=2000-01-01", "/api/etsy/orders?offset=-1",
    "/api/etsy/orders?status=publish", "/api/etsy/listings?state=publish",
])
def test_invalid_filters_never_reach_etsy(workspace, path):
    assert workspace.client.get(path).status_code == 422
    assert workspace.remote.calls == []


@pytest.mark.parametrize("status, expected", [(401, 403), (403, 403), (429, 429), (500, 502)])
def test_upstream_failures_do_not_expose_private_bodies_or_tokens(workspace, status, expected):
    workspace.remote.status = status
    response = workspace.client.get("/api/etsy/orders")
    assert response.status_code == expected
    assert "private-token" not in response.text
    assert "Private buyer" not in response.text


def test_disconnected_shop_never_calls_the_relay(workspace):
    workspace.remote.accounts.clear()
    assert workspace.client.get("/api/etsy/shop").status_code == 403
    assert workspace.remote.calls == []


def test_switching_accounts_during_a_read_discards_the_response(workspace):
    workspace.remote.after_read = lambda: workspace.remote.accounts["etsy"].update(connectedAt=2)
    response = workspace.client.get("/api/etsy/shop")
    assert response.status_code == 409
    assert "MyShop" not in response.text
