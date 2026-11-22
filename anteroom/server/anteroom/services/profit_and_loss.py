"""Currency-separated profit and loss based on recorded figures."""
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.sourcing import SourceBox
from anteroom.services import sales_ledger
from anteroom.services.business_expenses import entries, round_cents

AccountingMode = Literal["accrual", "cash"]


class ReportLine(BaseModel):
    label: str
    amount_cents: int


class CurrencyReport(BaseModel):
    currency: str
    incomplete_sales: int
    lines: list[ReportLine]


SALES_FIELDS = {
    "Gross Sales": "item_revenue",
    "Collected Shipping": "shipping_credit",
    "Refunds": "refunds",
    "Marketplace Fees": "fees",
    "Shipping Costs": "shipping_cost",
    "COGS": "item_cost",
}


def report(db: Session, start: date, end: date, mode: AccountingMode,
           sales: list[dict[str, Any]] | None = None, *,
           after: datetime | None = None, before: datetime | None = None) -> list[CurrencyReport]:
    totals: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    incomplete: defaultdict[str, int] = defaultdict(int)
    fields = SALES_FIELDS if mode == "accrual" else {k: v for k, v in SALES_FIELDS.items() if k != "COGS"}
    sales = sales_ledger.sale_records(db, start, end) if sales is None else sales
    lower = after or datetime.combine(start, time.min, UTC)
    upper = before or datetime.combine(end, time.max, UTC) + timedelta(microseconds=1)
    for sale in sales:
        if sale["order_status"] in {"cancelled", "cancellation_pending"}:
            continue
        if after is not None and not sale["sold_at"]:
            continue
        if after is not None or before is not None:
            moment = datetime.fromisoformat(sale["sold_at"] or sale["received_at"])
            moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
            if not lower <= moment < upper:
                continue
        financials = sale["financials"]
        currency = (financials or {}).get("currency") or "Unassigned"
        amounts = totals[currency]
        if sale["financials_stale"] or not financials:
            incomplete[currency] += 1
            continue
        if not sale["sold_at"] or any(financials.get(key) is None for key in fields.values() if key != "refunds"):
            incomplete[currency] += 1
        for label, key in fields.items():
            amounts[label] += round_cents(Decimal(financials.get(key) or 0) * 100)
    inventory_label = "COGS" if mode == "accrual" else "Inventory Purchases"
    operating: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Date-only expenses enter the books at the start of their recorded UTC day.
    expense_start = lower.date() + timedelta(days=lower.time() != time.min)
    expense_end = (upper - timedelta(microseconds=1)).date()
    for expense in db.scalars(entries(expense_start, expense_end)):
        if expense.accounting_type == "inventory_purchase":
            if mode == "cash":
                totals[expense.currency][inventory_label] += expense.amount_cents
        else:
            totals[expense.currency]
            operating[expense.currency][expense.category] += expense.amount_cents
    if mode == "cash":
        boxes = db.scalars(select(SourceBox).where(
            SourceBox.bought_at >= lower.replace(tzinfo=None),
            SourceBox.bought_at < upper.replace(tzinfo=None),
        ))
        for box in boxes:
            totals["USD"][inventory_label] += round_cents((Decimal(str(box.price)) + Decimal(str(box.shipping))) * 100)
    result: list[CurrencyReport] = []
    for currency, amounts in sorted(totals.items()):
        net = amounts["Gross Sales"] + amounts["Collected Shipping"] - amounts["Refunds"]
        categories = operating[currency]
        operating_total = sum(categories.values())
        profit = net - amounts[inventory_label] - amounts["Marketplace Fees"] - amounts["Shipping Costs"] - operating_total
        lines = {
            "Gross Sales": amounts["Gross Sales"],
            "Collected Shipping": amounts["Collected Shipping"],
            "Refunds": amounts["Refunds"],
            "Net Revenue": net,
            inventory_label: amounts[inventory_label],
            "Marketplace Fees": amounts["Marketplace Fees"],
            "Shipping Costs": amounts["Shipping Costs"],
            **{category.replace("_", " ").capitalize(): value for category, value in sorted(categories.items())},
            "Operating Expenses": operating_total,
            "Net Profit": profit,
        }
        result.append(CurrencyReport(currency=currency, incomplete_sales=incomplete[currency], lines=[
            ReportLine(label=label, amount_cents=value) for label, value in lines.items()
        ]))
    return result
