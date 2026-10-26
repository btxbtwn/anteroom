import csv
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import marketplace_accounts


@pytest.fixture
def ledger(monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Conversation(id="coat", title="Wool coat", status="active", notes='{"private":"never-export"}'))
    db.commit()
    ListingRepo(db).save_revision("coat", {"title": "Wool coat", "price": 45.5, "quantity": 3, "cost": 5, "sku": "COAT-1", "description": "Wool coat"}, "user")
    sale = MarketplaceSale(marketplace="ebay", order_id="order", line_id="line", listing_id="123456789012", quantity=2, conversation_id="coat")
    db.add(sale)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    remote = AsyncMock(side_effect=AssertionError("Local ledger must never contact a marketplace"))
    monkeypatch.setattr(marketplace_accounts, "etsy_api", remote)
    monkeypatch.setattr(marketplace_accounts, "autodelist_for_sales", remote)
    client = TestClient(app)
    yield SimpleNamespace(client=client, db=db, sale=sale, remote=remote)
    remote.assert_not_called()
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def filters():
    today = datetime.now(UTC).date()
    return {"start": (today - timedelta(days=29)).isoformat(), "end": today.isoformat()}


def record(ledger):
    response = ledger.client.get(f"/api/sales-ledger/{ledger.sale.id}")
    assert response.status_code == 200, response.text
    return response.json()


def save(ledger, **changes):
    current = record(ledger)
    body = {"currency": "USD", "item_revenue": "45.50", "shipping_credit": "5.00", "refunds": "2.00", "fees": "6.25",
            "shipping_cost": "4.10", "item_cost": "10.00", "expected_revision": current["financial_revision"],
            "expected_order_basis": current["order_basis"], **changes}
    return ledger.client.put(f"/api/sales-ledger/{ledger.sale.id}/financials", json=body)


def report(ledger, **params):
    response = ledger.client.get("/api/sales-ledger", params={**filters(), **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_financials_are_saved_on_the_existing_line_and_use_exact_decimal_totals(ledger):
    response = save(ledger)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["net_revenue"] == "48.50" and row["profit"] == "28.15"
    assert row["financial_revision"] == 1 and row["financials_stale"] is False
    assert ledger.db.query(MarketplaceSale).count() == 1
    assert ListingRepo(ledger.db).get_revisions("coat")[0].listing_json["quantity"] == 3
    totals = report(ledger)["summary"]
    assert totals["currencies"][0]["profit"] == "28.15"
    assert totals["currencies"][0]["profit_known"] == 1


def test_unknown_amounts_remain_unknown_and_explicit_zero_is_known(ledger):
    assert record(ledger)["profit"] is None
    assert save(ledger, fees=None).json()["profit"] is None
    assert report(ledger)["summary"]["currencies"][0]["profit"] is None
    row = save(ledger, fees="0", refunds="0", shipping_cost="0", item_cost="0", shipping_credit="0").json()
    assert row["profit"] == "45.50"


def test_net_fee_credits_are_supported_without_floating_point_roundoff(ledger):
    row = save(ledger, item_revenue="0.1", shipping_credit="0.2", refunds="0", fees="-0.01", shipping_cost="0", item_cost="0").json()
    assert row["net_revenue"] == "0.3" and row["profit"] == "0.31"


def test_currencies_are_never_combined(ledger):
    save(ledger)
    other = MarketplaceSale(marketplace="etsy", order_id="other", line_id="other", listing_id="555")
    ledger.db.add(other)
    ledger.db.commit()
    row = ledger.client.get(f"/api/sales-ledger/{other.id}").json()
    body = {"currency": "EUR", "item_revenue": "10", "shipping_credit": "0", "refunds": "0", "fees": "1", "shipping_cost": "0", "item_cost": "2",
            "expected_revision": 0, "expected_order_basis": row["order_basis"]}
    assert ledger.client.put(f"/api/sales-ledger/{other.id}/financials", json=body).status_code == 200
    totals = report(ledger)["summary"]
    assert [(group["currency"], group["profit"]) for group in totals["currencies"]] == [("EUR", "7"), ("USD", "28.15")]
    assert totals["unmatched"] == 1


def test_stale_financial_editor_cannot_overwrite_another_edit(ledger):
    before = record(ledger)
    assert save(ledger).status_code == 200
    result = save(ledger, expected_revision=before["financial_revision"], item_revenue="99")
    assert result.status_code == 409
    assert record(ledger)["financials"]["item_revenue"] == "45.50"


@pytest.mark.parametrize("change", [{"quantity": 3}, {"order_status": "refunded"}, {"conversation_id": None}])
def test_order_changes_require_review_and_prevent_an_old_editor_saving(ledger, change):
    assert save(ledger).status_code == 200
    before = record(ledger)
    for key, value in change.items():
        setattr(ledger.sale, key, value)
    ledger.db.commit()
    result = save(ledger, expected_order_basis=before["order_basis"])
    assert result.status_code == 409
    current = record(ledger)
    assert current["financials_stale"] and current["profit"] is None
    assert report(ledger)["summary"]["needs_review"] == 1
    assert save(ledger).json()["financials_stale"] is False


@pytest.mark.parametrize("change", [{"fees": "NaN"}, {"item_revenue": "Infinity"}, {"item_cost": "-1"}, {"refunds": "1e3"},
    {"item_revenue": 45.5}, {"fees": True}, {"currency": "usd"}, {"currency": "US"}, {"private": "never-export"}])
def test_invalid_financial_data_is_rejected_without_changing_any_record(ledger, change):
    assert save(ledger, **change).status_code == 422
    assert record(ledger)["financial_revision"] == 0


def test_dismissed_and_refunded_orders_remain_visible_without_reopening_alerts(ledger):
    ledger.sale.state = "dismissed"
    ledger.sale.order_status = "refunded"
    ledger.db.commit()
    assert save(ledger, refunds="45.50").status_code == 200
    row = report(ledger)["rows"][0]
    assert row["alert_state"] == "dismissed" and row["order_status"] == "refunded"
    assert ledger.db.query(MarketplaceSale).one().state == "dismissed"


def test_pagination_and_export_cover_the_same_full_filtered_dataset(ledger):
    for index in range(54):
        ledger.db.add(MarketplaceSale(marketplace="etsy", order_id=f"order-{index}", line_id="line", listing_id="555"))
    ledger.db.commit()
    first, second = report(ledger), report(ledger, page=2)
    assert len(first["rows"]) == 50 and len(second["rows"]) == 5
    assert first["summary"]["lines"] == 55 and first["has_more"] and not second["has_more"]
    response = ledger.client.get("/api/sales-ledger/export/sales.csv", params=filters())
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 55
    filtered = report(ledger, marketplace="ebay")
    assert filtered["summary"]["lines"] == 1
    assert ledger.client.get("/api/sales-ledger", params={**filters(), "page": 0}).status_code == 422


def test_csv_preserves_unknowns_and_numeric_fee_credits_while_neutralizing_text_formulas(ledger):
    ledger.db.get(Conversation, "coat").title = '=HYPERLINK("https://attacker.test")'
    ledger.db.commit()
    save(ledger, fees="-1.25", item_cost=None)
    response = ledger.client.get("/api/sales-ledger/export/sales.csv", params=filters())
    row = next(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert row["title"].startswith("'=")
    assert row["fees"] == "-1.25" and row["item_cost"] == row["profit"] == ""
    assert "never-export" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_inventory_export_uses_current_local_values_and_only_validated_clean_links(ledger):
    MarketplaceListingRepo(ledger.db).save("coat", "ebay", {"state": "published", "listing_url": "https://www.ebay.com/itm/123456789012?private=never-export"})
    response = ledger.client.get("/api/sales-ledger/export/inventory.csv")
    row = next(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert row["local_quantity"] == "3" and row["unit_cost"] == "5.0"
    assert row["saved_ebay_url"] == "https://www.ebay.com/itm/123456789012"
    assert "never-export" not in response.text and "notes" not in row
    assert set(row) >= {"conversation_id", "title", "description", "local_status", "sku", "price"}


def test_date_filters_are_utc_and_export_errors_remain_json(ledger):
    assert ledger.client.get("/api/sales-ledger", params={"start": "2000-02-02", "end": "2000-01-01"}).status_code == 422
    assert ledger.client.get("/api/sales-ledger/export/sales.csv", params={**filters(), "marketplace": "vendoo"}).status_code == 422
    assert ledger.client.get("/api/sales-ledger/missing").status_code == 404
