"""eBay and Etsy sales record their amounts from the marketplace's own order records."""
import asyncio
import copy
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal

import pytest
from test_order_recovery import dates, ebay_recovery, receipt, recovery  # noqa: F401 - fixtures
from test_order_recovery import ebay_imported, etsy_imported  # noqa: F401 - fixtures

from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import marketplace_accounts, order_amounts
from vendoo_studio.services.sales_ledger import order_basis
from vendoo_studio.services.sales_records import missing_fields, records

ORDER = "12-12345-12345"


@pytest.fixture(autouse=True)
def fresh_checks():
    order_amounts._checked.clear()
    yield
    order_amounts._checked.clear()


def usd(value):
    return {"value": value, "currency": "USD"}


def etsy_money(cents):
    return {"amount": cents, "divisor": 100, "currency_code": "USD"}


@pytest.fixture
def ebay_money_order(ebay_recovery):  # noqa: F811
    fixture = ebay_recovery
    fixture.order_detail.update({
        "pricingSummary": {"priceSubtotal": usd("40.00"), "priceDiscount": usd("-4.00"), "deliveryCost": usd("6.50")},
        "totalMarketplaceFee": usd("5.10"),
        "paymentSummary": {"refunds": [{"amount": usd("2.00"), "refundStatus": "REFUNDED"},
                                       {"amount": usd("9.00"), "refundStatus": "PENDING"}]},
        "lineItems": [{"legacyItemId": "123456789012", "lineItemCost": usd("40.00"), "discountedLineItemCost": usd("36.00")}],
    })
    fixture.finance_calls = []
    fixture.labels = [{"transactionId": "sale", "transactionType": "SALE", "orderId": ORDER, "bookingEntry": "CREDIT",
                       "amount": usd("33.40"), "totalFeeAmount": usd("5.10")},
                      {"transactionId": "label", "transactionType": "SHIPPING_LABEL", "orderId": ORDER,
                       "bookingEntry": "DEBIT", "amount": usd("4.85")}]
    original = fixture.remote.request

    async def request(method, path, *, xml=None, call=None, params=None):
        if path == "/sell/finances/v1/transaction":
            assert method == "GET" and params["filter"] == f"orderId:{{{ORDER}}}"
            fixture.finance_calls.append(params)
            return {"transactions": copy.deepcopy(fixture.labels), "total": len(fixture.labels)}
        return await original(method, path, xml=xml, call=call)

    fixture.remote.request = request
    return fixture


def recover_ebay(fixture):
    result = fixture.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 200, result.text
    return result.json()


def test_ebay_sale_records_price_shipping_fees_refunds_and_label(ebay_money_order):
    fixture = ebay_money_order
    recover_ebay(fixture)
    sale = fixture.db.query(MarketplaceSale).one()
    money = sale.financials
    assert (money["item_revenue"], money["shipping_credit"], money["fees"], money["refunds"], money["shipping_cost"]) == \
        ("36.00", "6.50", "5.10", "2.00", "4.85")
    assert money["source"] == "marketplace" and money["currency"] == "USD" and money["order_basis"] == order_basis(sale)
    record = next(row for row in records(fixture.db) if row["id"] == sale.id)
    assert not record["financials_stale"]
    assert set(missing_fields(record)) <= {"cost of goods"}


def test_complete_amounts_are_not_read_again_each_catch_up(ebay_money_order):
    fixture = ebay_money_order
    recover_ebay(fixture)
    revision = fixture.db.query(MarketplaceSale).one().financial_revision
    order_amounts._checked.clear()
    recover_ebay(fixture)
    assert len(fixture.finance_calls) == 1
    assert fixture.db.query(MarketplaceSale).one().financial_revision == revision


def test_amounts_the_seller_entered_are_never_overwritten(ebay_money_order):
    fixture = ebay_money_order
    recover_ebay(fixture)
    sale = fixture.db.query(MarketplaceSale).one()
    entered = {"source": "seller", "currency": "USD", "item_revenue": "50", "fees": "7", "item_cost": "5"}
    sale.financials, sale.financial_revision = entered, 9
    fixture.db.commit()
    order_amounts._checked.clear()
    fixture.order_detail["lastModifiedDate"] = "2026-10-26T12:30:00Z"
    recover_ebay(fixture)
    fixture.db.refresh(sale)
    assert sale.financials == entered and sale.financial_revision == 9


def test_stale_kept_amounts_are_refreshed_keeping_what_the_marketplace_does_not_report(ebay_money_order):
    fixture = ebay_money_order
    fixture.labels = fixture.labels[:1]  # The label was bought elsewhere.
    recover_ebay(fixture)
    sale = fixture.db.query(MarketplaceSale).one()
    sale.financials = {"source": "vendoo", "currency": "USD", "item_revenue": "30", "fees": "3", "shipping_cost": "4.10",
                       "item_cost": "6", "order_basis": "0" * 64}
    fixture.db.commit()
    order_amounts._checked.clear()
    recover_ebay(fixture)
    fixture.db.refresh(sale)
    money = sale.financials
    assert (money["item_revenue"], money["fees"], money["shipping_cost"], money["item_cost"]) == ("36.00", "5.10", "4.10", "6")
    assert money["source"] == "marketplace" and money["order_basis"] == order_basis(sale)


def test_unreadable_fulfillment_amounts_leave_the_sale_recorded_without_them(ebay_recovery):  # noqa: F811
    recover_ebay(ebay_recovery)
    sale = ebay_recovery.db.query(MarketplaceSale).one()
    assert sale.order_status == "paid" and sale.financials is None


def money_receipt(**changes):
    row = receipt()
    row.update({"subtotal": etsy_money(2500), "total_shipping_cost": etsy_money(599), "discount_amt": etsy_money(0),
                "refunds": [{"amount": etsy_money(300)}]})
    row["transactions"][0]["price"] = etsy_money(2500)
    row.update(changes)
    return row


def test_etsy_sale_records_price_buyer_shipping_and_refunds(recovery):  # noqa: F811
    recovery.remote.receipts = [money_receipt()]
    result = recovery.client.post("/api/marketplace-sales/recover", json={"marketplace": "etsy", "connected_at": "1", **dates()})
    assert result.status_code == 200, result.text
    sale = recovery.db.query(MarketplaceSale).one()
    money = sale.financials
    assert (money["item_revenue"], money["shipping_credit"], money["refunds"]) == ("25", "5.99", "3")
    # Etsy does not report its fees or label purchases per order.
    assert money["fees"] is None and money["shipping_cost"] is None and money["source"] == "marketplace"


def test_older_etsy_sales_are_filled_newest_first_and_not_reread(recovery, monkeypatch):  # noqa: F811
    db = recovery.db
    for order_id, day in (("100", 1), ("101", 3)):
        db.add(MarketplaceSale(marketplace="etsy", order_id=order_id, line_id="200", listing_id="555", quantity=1,
                               order_status="paid", sold_at=datetime(2026, 1, day)))
    db.commit()
    reads = []
    original = marketplace_accounts.etsy_api

    async def read(method, path, *, params=None):
        if path.startswith("shops/42/receipts/"):
            assert method == "GET"
            receipt_id = path.rsplit("/", 1)[1]
            reads.append(receipt_id)
            return money_receipt(receipt_id=int(receipt_id))
        return await original(method, path, params=params)

    monkeypatch.setattr(marketplace_accounts, "etsy_api", read)
    assert asyncio.run(order_amounts.backfill("etsy", lambda: nullcontext(db), limit=1)) == 1
    assert reads == ["101"]
    assert asyncio.run(order_amounts.backfill("etsy", lambda: nullcontext(db))) == 1
    assert reads == ["101", "100"]
    assert all(row.financials["item_revenue"] == "25" for row in db.query(MarketplaceSale).all())
    assert asyncio.run(order_amounts.backfill("etsy", lambda: nullcontext(db))) == 0
    assert reads == ["101", "100"]


def test_listing_only_reports_and_cancelled_orders_are_not_filled(recovery):  # noqa: F811
    db = recovery.db
    db.add(MarketplaceSale(marketplace="etsy", order_id="555", line_id="555", listing_id="555", order_status="paid"))
    db.add(MarketplaceSale(marketplace="etsy", order_id="102", line_id="200", listing_id="555", order_status="cancelled"))
    db.commit()
    assert order_amounts._backlog(db, "etsy", 10) == []


def test_ebay_lines_of_one_item_share_the_order_by_price():
    detail = {"pricingSummary": {"priceSubtotal": usd("30.00"), "deliveryCost": usd("5.00"), "deliveryDiscount": usd("-5.00")},
              "totalMarketplaceFee": usd("4.00"), "paymentSummary": {"refunds": []},
              "lineItems": [{"legacyItemId": "1", "lineItemCost": usd("10.00")}, {"legacyItemId": "1", "lineItemCost": usd("20.00")}]}
    money = order_amounts.ebay_money(detail, [("1-a", "1"), ("1-b", "1")], None)
    assert money is not None
    assert money.totals == {"item_revenue": Decimal("30.00"), "shipping_credit": Decimal("0.00"), "refunds": Decimal(0),
                            "fees": Decimal("4.00"), "shipping_cost": None}
    # The two lines cannot be told apart by item ID, so the order is shared evenly.
    assert money.line_revenue == {"1-a": None, "1-b": None}


@pytest.mark.parametrize("detail", [{}, {"pricingSummary": {"priceSubtotal": {"value": "10", "currency": "EUR"}}},
                                    {"pricingSummary": {"priceSubtotal": usd("bad")}}])
def test_unreadable_or_foreign_currency_orders_record_no_amounts(detail):
    assert order_amounts.ebay_money(detail, [("1-a", "1")], None) is None
    assert order_amounts.etsy_money({"subtotal": {"amount": 100, "divisor": 100, "currency_code": "EUR"}}, [("1", "1")]) is None


def test_checked_orders_wait_before_being_read_again(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(order_amounts.time, "monotonic", lambda: clock[0])
    assert order_amounts._due("etsy", "1") is True
    assert order_amounts._due("etsy", "1") is False
    clock[0] += order_amounts.RECHECK_S
    assert order_amounts._due("etsy", "1") is True

