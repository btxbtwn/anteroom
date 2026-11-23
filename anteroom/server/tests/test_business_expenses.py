from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.models.business_expense import BusinessExpense
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.sourcing import SourceBox
from anteroom.routes.business_expenses import router
from anteroom.services.account_deletion import seller_tables
from anteroom.services.account_import import imported_tables
from anteroom.services.business_expenses import ExpenseInput, MileageInput, mileage
from anteroom.services.sales_ledger import order_basis

ROOT = "/api/business-expenses"
PERIOD = {"start": "2026-01-01", "end": "2026-01-31"}
EXPENSE = {"incurred_date": "2026-01-10", "amount_cents": 1200, "category": "shipping_supplies"}


@pytest.fixture
def expenses(tmp_path):
    from anteroom.services.schema_migrations import alembic_config

    url = f"sqlite:///{tmp_path / 'expenses.sqlite3'}"
    config = alembic_config(url)
    command.upgrade(config, "8580524be7de")
    engine = create_engine(url)
    assert "business_expenses" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "business_expenses" in inspect(engine).get_table_names()
    app = FastAPI()
    app.include_router(router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_crud_and_ownership_registry(expenses):
    client, _ = expenses
    created = client.post(ROOT, json=EXPENSE)
    assert created.status_code == 201, created.text
    row = created.json()
    assert "user_id" not in row
    assert row["import_source"] == "manual"
    assert row["currency"] == "USD"
    assert client.get(ROOT, params={**PERIOD, "category": "other"}).json() == []
    route = f"{ROOT}/{row['id']}"
    change = {**EXPENSE, "category": "inventory_purchases", "expected_revision": row["revision"]}
    saved = client.put(route, json=change).json()
    assert saved["accounting_type"] == "inventory_purchase"
    assert saved["revision"] == row["revision"] + 1
    assert client.put(route, json=change).status_code == 409
    assert client.delete(route, params={"expected_revision": row["revision"]}).status_code == 409
    delete_params = {"expected_revision": saved["revision"]}
    assert client.delete(route, params=delete_params).status_code == 204
    assert client.delete(route, params=delete_params).status_code == 404
    assert client.get(ROOT, params=PERIOD).json() == []
    assert "business_expenses" in {table.name for table in seller_tables()}
    assert "business_expenses" in {table.name for table in imported_tables()}


@pytest.mark.parametrize(("day", "country", "unit", "distance", "prior", "cents"), [
    (date(2026, 6, 30), "US", "mi", 10, 0, 725),
    (date(2026, 7, 1), "US", "mi", 10, 0, 760),
    (date(2026, 1, 1), "CA", "km", 10, 4995, 700),
])
def test_mileage(day, country, unit, distance, prior, cents):
    result = mileage(MileageInput.model_validate({
        "incurred_date": day, "country": country, "distance_unit": unit, "distance": distance,
        "prior_business_km": prior,
    }))
    assert result.amount_cents == cents


def test_csv_preview_errors_and_retry(expenses):
    client, _ = expenses
    assert client.get(f"{ROOT}/template.csv").text.startswith("\ufeffincurred_date,amount_cents,")
    content = "incurred_date,amount_cents,category,vendor\n2026-01-01,300,other,=1+1\n"
    invalid = client.post(f"{ROOT}/import", json={"content": content + "2026-01-02,oops,other,Shop\n"})
    assert invalid.json()["issues"][0]["row"] == 3
    assert invalid.json()["imported"] == 0
    preview = client.post(f"{ROOT}/import/preview", json={"content": content}).json()
    assert preview["valid"] == 1
    assert [(row["incurred_date"], row["amount_cents"]) for row in preview["preview"]] == [("2026-01-01", 300)]
    assert client.get(ROOT, params=PERIOD).json() == []
    assert client.post(f"{ROOT}/import", json={"content": content}).json()["imported"] == 1
    assert client.post(f"{ROOT}/import", json={"content": content}).json()["duplicates"] == 1
    assert "'=1+1" in client.get(f"{ROOT}/expenses.csv", params=PERIOD).text


@pytest.mark.parametrize("bulk", [False, True])
def test_context_refreshed_after_outer_commit(expenses, bulk):
    from anteroom.services.business_context import cached

    client, engine = expenses
    expense_id = client.post(ROOT, json=EXPENSE).json()["id"]
    with Session(engine) as writer, Session(engine) as reader:
        if bulk:
            writer.query(BusinessExpense).filter_by(id=expense_id).update({"amount_cents": 2000})
        else:
            writer.get_one(BusinessExpense, expense_id).amount_cents = 2000
            writer.flush()
        with writer.begin_nested():
            pass

        def read_amount():
            return reader.get_one(BusinessExpense, expense_id).amount_cents

        day = date(2026, 1, 10)
        assert cached(reader, day, read_amount) == 1200
        reader.commit()
        writer.commit()
        assert cached(reader, day, read_amount) == 2000


def test_bulk_import_inserts_across_batches_and_paginates(expenses):
    client, _ = expenses
    content = "incurred_date,amount_cents,category,description\n" + "".join(
        f"2026-01-01,300,other,Expense {index}\n" for index in range(205)
    )
    preview = client.post(f"{ROOT}/import/preview", json={"content": content}).json()
    assert preview["valid"] == 205
    assert len(preview["preview"]) == 5
    assert client.post(f"{ROOT}/import", json={"content": content}).json()["imported"] == 205
    first = client.get(ROOT, params=PERIOD).json()
    second = client.get(ROOT, params={**PERIOD, "page": 2}).json()
    assert len(first) == len(second) == 50
    assert {row["id"] for row in first}.isdisjoint(row["id"] for row in second)
    assert len(client.get(ROOT, params={**PERIOD, "page": 5}).json()) == 5


def test_import_deduplicates_equal_mileage_decimals(expenses):
    client, _ = expenses
    header = "incurred_date,amount_cents,category,distance,distance_unit,mileage_rate_cents\n"
    for distance, rate, imported in [("10", "70", 1), ("10.000", "70.000000", 0)]:
        content = header + f"2026-01-01,700,mileage,{distance},mi,{rate}\n"
        result = client.post(f"{ROOT}/import", json={"content": content}).json()
        assert result["imported"] == imported
        assert result["duplicates"] == 1 - imported


@pytest.mark.parametrize("edited", [False, True])
def test_import_recognizes_legacy_mileage_key(expenses, edited):
    client, engine = expenses
    original = ExpenseInput(
        incurred_date=date(2026, 1, 1), amount_cents=700, category="mileage",
        distance=Decimal("10"), distance_unit="mi", mileage_rate_cents=Decimal("70"),
    )
    # Recreate the serialized values from before the validator quantized them.
    legacy = original.model_copy(update={"distance": Decimal("10"), "mileage_rate_cents": Decimal("70")})
    with Session(engine) as db:
        row = BusinessExpense(**original.model_dump(), accounting_type="operating_expense",
                              import_source="csv", import_key=sha256(legacy.model_dump_json().encode()).hexdigest())
        db.add(row)
        db.commit()
        if edited:
            row.notes = "Added after import"
            db.commit()

    header = "incurred_date,amount_cents,category,distance,distance_unit,mileage_rate_cents\n"
    spellings = ["10,mi,70"] if edited else ["10,mi,70", "10.000,mi,70.000000"]
    for spelling in spellings:
        content = header + f"2026-01-01,700,mileage,{spelling}\n"
        for endpoint in ("import/preview", "import"):
            result = client.post(f"{ROOT}/{endpoint}", json={"content": content}).json()
            assert (result["valid"], result["imported"], result["duplicates"]) == (0, 0, 1)
    assert len(client.get(ROOT, params=PERIOD).json()) == 1


def test_cash_expenses_and_landed_box_costs(expenses):
    client, engine = expenses
    assert client.post(ROOT, json=EXPENSE).status_code == 201
    assert client.post(ROOT, json={**EXPENSE, "amount_cents": 700, "category": "inventory_purchases"}).status_code == 201
    assert client.post(ROOT, json={**EXPENSE, "amount_cents": 900, "currency": "CAD"}).status_code == 201
    with Session(engine) as db:
        db.add(SourceBox(store="Store", title="Box", price=16, shipping=4, bought_at=datetime(2026, 1, 12)))
        db.commit()

    def profit(mode):
        response = client.get(f"{ROOT}/profit-and-loss", params={**PERIOD, "mode": mode})
        assert response.status_code == 200, response.text
        return {row["currency"]: row["lines"][-1]["amount_cents"] for row in response.json()}

    assert profit("accrual") == {"CAD": -900, "USD": -1200}
    assert profit("cash") == {"CAD": -900, "USD": -3900}
    assert "cash,USD,Net Profit,-39.00" in client.get(f"{ROOT}/profit-and-loss.csv", params={**PERIOD, "mode": "cash"}).text


def test_report_uses_sale_proceeds_refunds_and_accrual_cogs(expenses):
    client, engine = expenses
    assert client.post(ROOT, json=EXPENSE).status_code == 201
    with Session(engine) as db:
        sold = datetime(2026, 1, 10)
        sale = MarketplaceSale(marketplace="ebay", order_id="purchased", line_id="line", order_status="partially_refunded",
                               quantity=2, sold_at=sold, received_at=sold, state="new")
        sale.financials = {
            "order_basis": order_basis(sale), "currency": "USD", "item_revenue": "100.00", "shipping_credit": "5.00",
            "refunds": "10.00", "fees": "10.00", "shipping_cost": "6.00", "item_cost": "25.00",
        }
        db.add(sale)
        db.commit()
    reports = {}
    for mode in ("cash", "accrual"):
        response = client.get(f"{ROOT}/profit-and-loss", params={**PERIOD, "mode": mode})
        assert response.status_code == 200, response.text
        result = response.json()[0]
        assert result["incomplete_sales"] == 0
        reports[mode] = {line["label"]: line["amount_cents"] for line in result["lines"]}
    assert reports["accrual"]["Net Revenue"] == 9500
    assert reports["accrual"]["COGS"] == 2500
    assert reports["accrual"]["Net Profit"] == 4200
    assert reports["cash"]["Net Profit"] == 6700
    report = client.get(f"{ROOT}/profit-and-loss", params={
        "start": "2026-01-10", "end": "2026-01-11", "after": "2026-01-10T12:00:00Z", "before": "2026-01-11T12:00:00Z",
    })
    assert report.status_code == 200, report.text
    assert report.json() == []
    previous = client.get(f"{ROOT}/profit-and-loss", params={
        "start": "2026-01-09", "end": "2026-01-10", "after": "2026-01-09T12:00:00Z", "before": "2026-01-10T12:00:00Z",
    })
    assert previous.status_code == 200, previous.text
    assert previous.json()[0]["lines"][-1]["amount_cents"] == 4200


def test_negative_refund_expenses_allowed_while_zero_and_negative_mileage_rejected(expenses):
    # L7: refunds/credits can be recorded as negative expenses; zero and negative mileage remain invalid.
    client, _engine = expenses
    refund = client.post(ROOT, json={
        "incurred_date": "2026-01-12",
        "category": "shipping_supplies",
        "vendor": "Pirate Ship Refund",
        "amount_cents": -1250,
    })
    assert refund.status_code == 201, refund.text
    assert refund.json()["amount_cents"] == -1250

    zero = client.post(ROOT, json={
        "incurred_date": "2026-01-12",
        "category": "shipping_supplies",
        "vendor": "Zero",
        "amount_cents": 0,
    })
    assert zero.status_code == 422

    neg_miles = client.post(ROOT, json={
        "incurred_date": "2026-01-12",
        "category": "mileage",
        "vendor": "Bad trip",
        "miles": -5,
    })
    assert neg_miles.status_code == 422
