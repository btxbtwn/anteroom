"""MCP tools for orders, profit and loss, expenses and an inventory summary (#2012)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.business_expense import BusinessExpense
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.mcp_token import McpAccessToken, McpOAuthConnection
from anteroom.models.sourcing import SourceBox
from anteroom.services import mcp_finance_tools, mcp_http, sales_ledger

FINANCE_TOOLS = {"search_orders", "get_profit_and_loss", "search_expenses", "get_inventory_summary"}
TODAY = datetime.now(UTC).date()
NOW = datetime.now(UTC).replace(tzinfo=None)


def _call(client: TestClient, name: str, arguments: dict | None = None, extra_headers: dict | None = None):
    resp = client.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}},
    }, headers=extra_headers or {})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return {"error": text}, True
    return json.loads(text), False


def _tool_names(client: TestClient, extra_headers: dict | None = None) -> set[str]:
    resp = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=extra_headers or {})
    assert resp.status_code == 200, resp.text
    return {tool["name"] for tool in resp.json()["result"]["tools"]}


@pytest.fixture(autouse=True)
def _clean_tables():
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot

    def clean():
        with SessionLocal() as db:
            for model in (McpAccessToken, McpOAuthConnection, BackgroundTask, ListingCorrection, SaleSnapshot, Message,
                          Job, MarketplaceSale, MarketplaceListing, Photo, Listing, ListingRevision, Conversation,
                          SourceBox, BusinessExpense):
                db.query(model).delete()
            db.commit()

    clean()
    yield
    clean()


def _item(db: Session, conv_id: str, title: str, *, status: str = "active", price: float = 50.0,
          cost: float | None = 10.0, sku: str | None = None, listed_days_ago: int | None = None, user_id: str | None = None):
    owner = {"user_id": user_id} if user_id else {}
    db.add(Conversation(id=conv_id, title=title, status=status, **owner))
    db.flush()
    doc = {"title": title, "price": price, "sku": sku}
    if cost is not None:
        doc["cost"] = cost
    db.add(ListingRevision(id=f"rev-{conv_id}", conversation_id=conv_id, listing_json=doc, source="test", **owner))
    db.flush()
    db.add(Listing(conversation_id=conv_id, current_revision_id=f"rev-{conv_id}", **owner))
    if listed_days_ago is not None:
        db.add(MarketplaceListing(
            conversation_id=conv_id, marketplace="ebay", state="published", listing_id=f"eb-{conv_id}",
            revision_id=f"rev-{conv_id}", listed_price=price, listed_at=NOW - timedelta(days=listed_days_ago), **owner,
        ))


def _sale(db: Session, conv_id: str | None, marketplace: str, order_id: str, *, days_ago: int = 1,
          status: str = "paid", revenue: str = "40.00", fees: str = "5.00", shipping_cost: str = "6.00",
          item_cost: str = "10.00", user_id: str | None = None) -> MarketplaceSale:
    owner = {"user_id": user_id} if user_id else {}
    sold = NOW - timedelta(days=days_ago)
    sale = MarketplaceSale(
        conversation_id=conv_id, marketplace=marketplace, order_id=order_id, line_id="1", quantity=1,
        order_status=status, sold_at=sold, received_at=sold, state="dismissed", **owner,
    )
    sale.financials = {
        "currency": "USD", "item_revenue": revenue, "shipping_credit": "0.00", "refunds": "0.00", "fees": fees,
        "shipping_cost": shipping_cost, "item_cost": item_cost, "order_basis": sales_ledger.order_basis(sale),
        "source": "seller",
    }
    db.add(sale)
    return sale


def test_finance_tools_are_read_only_analytics_tools_with_titles_and_guidance():
    definitions = {tool["name"]: tool for tool in mcp_http.tool_definitions()}
    assert FINANCE_TOOLS <= set(definitions)
    for name in FINANCE_TOOLS:
        assert mcp_http.TOOL_PERMISSIONS[name] == ("analytics", "read")
        tool = definitions[name]
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["title"] == tool["title"]
        assert tool["inputSchema"]["additionalProperties"] is False
    # Each description points the agent at the right tool for neighbouring questions.
    assert "get_sales_analytics" in definitions["get_inventory_summary"]["description"]
    assert "get_profit_and_loss" in definitions["search_orders"]["description"]
    assert "search_expenses" in definitions["get_profit_and_loss"]["description"]
    assert set(mcp_finance_tools.HANDLERS) == set(mcp_finance_tools.PERMISSIONS) == FINANCE_TOOLS


def test_finance_tools_need_analytics_read():
    client = TestClient(app)
    reader = client.post("/api/settings/mcp-tokens", json={"name": "Reader"}).json()
    bearer = {"Authorization": f"Bearer {reader['token']}"}
    assert FINANCE_TOOLS <= _tool_names(client, bearer)

    blind = client.post("/api/settings/mcp-tokens", json={"name": "No analytics", "permissions": {"analytics": "none"}}).json()
    blind_bearer = {"Authorization": f"Bearer {blind['token']}"}
    assert not FINANCE_TOOLS & _tool_names(client, blind_bearer)
    for name in FINANCE_TOOLS:
        refused, is_error = _call(client, name, {}, extra_headers=blind_bearer)
        assert is_error
        assert f"not allowed to use {name}" in refused["error"]


def test_search_orders_filters_pages_and_leaves_out_buyer_details():
    client = TestClient(app)
    with SessionLocal() as db:
        _item(db, "conv-jacket", "Wool Field Jacket", status="sold", sku="JKT-01")
        _item(db, "conv-boots", "Leather Boots", status="sold", sku="BT-77")
        _item(db, "conv-scarf", "Silk Scarf", status="sold", sku="SC-5")
        _sale(db, "conv-jacket", "poshmark", "posh-1", days_ago=2, revenue="80.00")
        _sale(db, "conv-boots", "ebay", "ebay-9", days_ago=5, revenue="120.00")
        _sale(db, "conv-scarf", "ebay", "ebay-10", days_ago=3, status="refunded")
        _sale(db, "conv-scarf", "mercari", "old-1", days_ago=60)
        db.commit()

    found, err = _call(client, "search_orders", {})
    assert not err, found
    # Default window is the last 30 days, newest first.
    assert [row["order_id"] for row in found["orders"]] == ["posh-1", "ebay-10", "ebay-9"]
    assert found["total"] == 3
    jacket = found["orders"][0]
    assert jacket["marketplace"] == "poshmark"
    assert jacket["item"] == {"conversation_id": "conv-jacket", "title": "Wool Field Jacket", "sku": "JKT-01"}
    assert jacket["status"] == "paid"
    assert jacket["currency"] == "USD"
    assert jacket["sale_price"] == "80.00"
    assert jacket["fees"] == "5.00"
    assert jacket["shipping_cost"] == "6.00"
    assert jacket["cost"] == "10.00"
    assert jacket["net"] == "80.00"
    assert jacket["profit"] == "59.00"
    assert jacket["sold_at"].startswith((TODAY - timedelta(days=2)).isoformat())
    serialized = json.dumps(found).lower()
    for private in ("buyer", "address", "email", "message"):
        assert private not in serialized

    by_marketplace, err = _call(client, "search_orders", {"marketplace": "ebay"})
    assert not err and [row["order_id"] for row in by_marketplace["orders"]] == ["ebay-10", "ebay-9"]
    by_sku, err = _call(client, "search_orders", {"query": "bt-77"})
    assert not err and [row["order_id"] for row in by_sku["orders"]] == ["ebay-9"]
    by_title, err = _call(client, "search_orders", {"query": "field jacket"})
    assert not err and [row["order_id"] for row in by_title["orders"]] == ["posh-1"]
    refunded, err = _call(client, "search_orders", {"status": "refunded"})
    assert not err and [row["order_id"] for row in refunded["orders"]] == ["ebay-10"]
    older, err = _call(client, "search_orders", {"start": (TODAY - timedelta(days=90)).isoformat(), "sort": "oldest"})
    assert not err and older["orders"][0]["order_id"] == "old-1"

    page, err = _call(client, "search_orders", {"limit": 1, "offset": 1})
    assert not err
    assert [row["order_id"] for row in page["orders"]] == ["ebay-10"]
    assert page["total"] == 3 and page["has_more"] is True
    # Totals cover every match, not just the page.
    assert page["summary"]["currencies"][0]["net_revenue"] == "240.00"

    too_long, err = _call(client, "search_orders", {"start": "2020-01-01", "end": TODAY.isoformat()})
    assert err and "366 days" in too_long["error"]
    bad_status, err = _call(client, "search_orders", {"status": "shipped"})
    assert err and "status" in bad_status["error"]


def test_get_profit_and_loss_matches_the_profit_and_loss_screen(monkeypatch):
    client = TestClient(app)
    with SessionLocal() as db:
        _item(db, "conv-lamp", "Brass Lamp", status="sold")
        _sale(db, "conv-lamp", "ebay", "lamp-1", days_ago=1, revenue="100.00")
        db.add(BusinessExpense(incurred_date=TODAY, amount_cents=1250, currency="USD", category="shipping_supplies",
                               vendor="Uline", description="Mailers", notes="", accounting_type="operating_expense"))
        db.commit()
    period = {"start": (TODAY - timedelta(days=6)).isoformat(), "end": TODAY.isoformat()}
    for mode in ("accrual", "cash"):
        screen = client.get("/api/business-expenses/profit-and-loss", params={**period, "mode": mode})
        assert screen.status_code == 200, screen.text
        report, err = _call(client, "get_profit_and_loss", {**period, "mode": mode})
        assert not err, report
        assert report["mode"] == mode and report["start"] == period["start"] and report["end"] == period["end"]
        assert [
            {"currency": row["currency"], "incomplete_sales": row["incomplete_sales"],
             "lines": [{"label": line["label"], "amount_cents": line["amount_cents"]} for line in row["lines"]]}
            for row in report["currencies"]
        ] == screen.json()
    lines = {line["label"]: line for line in report["currencies"][0]["lines"]}
    assert lines["Net Profit"]["amount"] == f"{lines['Net Profit']['amount_cents'] / 100:.2f}"

    def fixed_now(tz):
        assert tz is UTC
        return datetime(2026, 11, 2, 0, 5, tzinfo=UTC)

    monkeypatch.setattr(mcp_finance_tools, "datetime", SimpleNamespace(now=fixed_now))
    default, err = _call(client, "get_profit_and_loss", {})
    assert not err, default
    assert (default["start"], default["end"]) == ("2026-11-01", "2026-11-02")
    backwards, err = _call(client, "get_profit_and_loss", {"start": TODAY.isoformat(), "end": (TODAY - timedelta(days=1)).isoformat()})
    assert err and "on or after" in backwards["error"]


def test_search_expenses_filters_by_category_and_text_with_totals():
    client = TestClient(app)
    with SessionLocal() as db:
        for days, cents, currency, category, vendor, description in (
            (1, 1250, "USD", "shipping_supplies", "Uline", "Poly mailers"),
            (3, 800, "USD", "shipping_supplies", "Amazon", "Tape"),
            (4, 2999, "USD", "software_subscriptions", "Canva", "Monthly plan"),
            (5, 1500, "CAD", "shipping_supplies", "Staples", "Boxes"),
            (90, 5000, "USD", "equipment", "B&H", "Light box"),
        ):
            db.add(BusinessExpense(
                incurred_date=TODAY - timedelta(days=days), amount_cents=cents, currency=currency, category=category,
                vendor=vendor, description=description, notes="", accounting_type="operating_expense",
            ))
        db.commit()

    found, err = _call(client, "search_expenses", {})
    assert not err, found
    assert [row["vendor"] for row in found["expenses"]] == ["Uline", "Amazon", "Canva", "Staples"]
    assert found["total"] == 4
    assert found["totals"] == [
        {"currency": "CAD", "count": 1, "amount_cents": 1500, "amount": "15.00",
         "by_category": [{"category": "shipping_supplies", "count": 1, "amount_cents": 1500, "amount": "15.00"}]},
        {"currency": "USD", "count": 3, "amount_cents": 5049, "amount": "50.49",
         "by_category": [{"category": "shipping_supplies", "count": 2, "amount_cents": 2050, "amount": "20.50"},
                         {"category": "software_subscriptions", "count": 1, "amount_cents": 2999, "amount": "29.99"}]},
    ]
    first = found["expenses"][0]
    assert first["amount"] == "12.50" and first["category"] == "shipping_supplies"
    assert first["date"] == (TODAY - timedelta(days=1)).isoformat()

    shipping, err = _call(client, "search_expenses", {"category": "shipping_supplies", "query": "mailers"})
    assert not err and [row["vendor"] for row in shipping["expenses"]] == ["Uline"]
    by_vendor, err = _call(client, "search_expenses", {"query": "CANVA"})
    assert not err and by_vendor["total"] == 1
    page, err = _call(client, "search_expenses", {"limit": 2, "offset": 2})
    assert not err and [row["vendor"] for row in page["expenses"]] == ["Canva", "Staples"] and page["has_more"] is False
    year, err = _call(client, "search_expenses", {"start": (TODAY - timedelta(days=120)).isoformat()})
    assert not err and year["total"] == 5
    bad, err = _call(client, "search_expenses", {"category": "groceries"})
    assert err and "category" in bad["error"]


def test_get_inventory_summary_counts_status_marketplaces_value_and_age():
    client = TestClient(app)
    with SessionLocal() as db:
        _item(db, "conv-new", "New Arrival", price=40.0, cost=8.0, listed_days_ago=3)
        _item(db, "conv-old", "Old Stock", price=100.0, cost=None, listed_days_ago=120)
        _item(db, "conv-draft", "Still a Draft", status="draft", price=25.0)
        _item(db, "conv-sold", "Sold Thing", status="sold", price=30.0)
        _sale(db, "conv-sold", "ebay", "sold-1")
        db.commit()

    summary, err = _call(client, "get_inventory_summary", {})
    assert not err, summary
    assert summary["items_by_status"]["active"] == 2
    assert summary["items_by_status"]["draft"] == 1
    assert summary["items_by_status"]["sold"] == 1
    assert summary["active"]["asking_value"] == 140.0
    assert summary["active"]["cost_basis"] == 8.0
    assert summary["active"]["items_with_cost"] == 1
    assert summary["active"]["listed_90_days_or_more"] == {"count": 1, "asking_value": 100.0}
    assert summary["marketplaces"] == [{"marketplace": "ebay", "live_items": 2, "asking_value": 140.0}]
    aging = {row["label"]: row for row in summary["aging"]}
    assert aging["Under 2 weeks"]["count"] == 1
    assert aging["Over 3 months"] == {"label": "Over 3 months", "count": 1, "asking_value": 100.0}
    assert summary["oldest"][0]["conversation_id"] == "conv-old"


def test_hosted_finance_tools_read_only_the_tokens_seller(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys, USER_ID)
    with Session(engine) as db:
        _item(db, "alice-item", "Alice Coat", status="sold", sku="AL-1", user_id=USER_ID)
        _item(db, "bob-item", "Bob Coat", status="sold", sku="BO-1", user_id=BOB)
        _sale(db, "alice-item", "ebay", "alice-order", user_id=USER_ID)
        _sale(db, "bob-item", "ebay", "bob-order", user_id=BOB)
        for owner, vendor in ((USER_ID, "Alice Supplies"), (BOB, "Bob Supplies")):
            db.add(BusinessExpense(user_id=owner, incurred_date=TODAY, amount_cents=500, currency="USD",
                                   category="shipping_supplies", vendor=vendor, description="", notes="",
                                   accounting_type="operating_expense"))
        db.commit()
    token = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice books"}).json()
    bearer = {"Authorization": f"Bearer {token['token']}"}

    orders, err = _call(client, "search_orders", {}, extra_headers=bearer)
    assert not err, orders
    assert [row["order_id"] for row in orders["orders"]] == ["alice-order"]
    expenses, err = _call(client, "search_expenses", {}, extra_headers=bearer)
    assert not err and [row["vendor"] for row in expenses["expenses"]] == ["Alice Supplies"]
    # The seeded sales are dated yesterday and the expenses today.
    report, err = _call(client, "get_profit_and_loss",
                        {"start": (TODAY - timedelta(days=2)).isoformat(), "end": TODAY.isoformat()}, extra_headers=bearer)
    assert not err, report
    lines = {line["label"]: line["amount_cents"] for line in report["currencies"][0]["lines"]}
    assert lines["Gross Sales"] == 4000 and lines["Operating Expenses"] == 500
    summary, err = _call(client, "get_inventory_summary", {}, extra_headers=bearer)
    assert not err and summary["items_by_status"]["sold"] == 1
