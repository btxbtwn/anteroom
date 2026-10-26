"""Seller-owned financial records on the direct order-line ledger."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, utcnow
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services.api_job_lock import start_gate

AMOUNTS = ("item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost")
MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted")
PAGE_SIZE = 50
BASIS_FIELDS = ("marketplace", "order_id", "line_id", "listing_id", "conversation_id", "quantity", "order_status", "order_updated_at", "order_observed_at")


class LedgerError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class FinancialEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    item_revenue: str | None = None
    shipping_credit: str | None = None
    refunds: str | None = None
    fees: str | None = None
    shipping_cost: str | None = None
    item_cost: str | None = None

    @field_validator(*AMOUNTS, mode="before")
    @classmethod
    def money(cls, value, info):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("Enter money as a decimal string.")
        value = value.strip()
        pattern = r"-?[0-9]{1,12}(?:\.[0-9]{1,4})?" if info.field_name == "fees" else r"[0-9]{1,12}(?:\.[0-9]{1,4})?"
        if not re.fullmatch(pattern, value):
            raise ValueError("Use a decimal amount with up to four decimal places. Only net fees may be negative.")
        return format(Decimal(value), "f")


class FinancialSave(FinancialEntry):
    expected_revision: int = Field(ge=0, strict=True)
    expected_order_basis: str = Field(pattern=r"^[a-f0-9]{64}$")


def _iso(value):
    return value.replace(tzinfo=UTC).isoformat() if isinstance(value, datetime) else value


def order_basis(sale: MarketplaceSale) -> str:
    values = [_iso(getattr(sale, field)) for field in BASIS_FIELDS]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def financial_values(sale: MarketplaceSale) -> tuple[dict | None, bool]:
    raw = sale.financials
    if raw is None:
        return None, False
    if not isinstance(raw, dict):
        return None, True
    try:
        data = FinancialEntry.model_validate({field: raw.get(field) for field in ("currency", *AMOUNTS)}).model_dump()
    except ValidationError:
        return None, True
    return data, raw.get("order_basis") != order_basis(sale)


def totals(data: dict | None, stale: bool = False) -> tuple[str | None, str | None]:
    if data is None or stale:
        return None, None
    values = {key: Decimal(data[key]) if data.get(key) is not None else None for key in AMOUNTS}
    net = (values["item_revenue"] + values["shipping_credit"] - values["refunds"]
           if all(values[key] is not None for key in ("item_revenue", "shipping_credit", "refunds")) else None)
    profit = net - values["fees"] - values["shipping_cost"] - values["item_cost"] if net is not None and all(value is not None for value in values.values()) else None
    return format(net, "f") if net is not None else None, format(profit, "f") if profit is not None else None


def sale_out(sale: MarketplaceSale, title: str | None) -> dict:
    financials, stale = financial_values(sale)
    net, profit = totals(financials, stale)
    raw = sale.financials if isinstance(sale.financials, dict) else {}
    return {"id": sale.id, "marketplace": sale.marketplace, "order_id": sale.order_id, "line_id": sale.line_id,
            "listing_id": sale.listing_id, "quantity": sale.quantity, "order_status": sale.order_status,
            "alert_state": sale.state, "conversation_id": sale.conversation_id, "title": title,
            "sold_at": _iso(sale.sold_at), "received_at": _iso(sale.received_at),
            "financials": financials, "financials_stale": stale, "financial_revision": sale.financial_revision,
            "financials_updated_at": raw.get("updated_at"), "order_basis": order_basis(sale),
            "net_revenue": net, "profit": profit}


def get_sale(db: Session, sale_id: str) -> dict:
    row = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id).filter(MarketplaceSale.id == sale_id).first()
    if row is None:
        raise LedgerError("Sale record not found.", 404)
    return sale_out(*row)


def save_financials(db: Session, sale_id: str, body: FinancialSave) -> dict:
    with start_gate():
        sale = db.get(MarketplaceSale, sale_id, populate_existing=True)
        if sale is None:
            raise LedgerError("Sale record not found.", 404)
        if order_basis(sale) != body.expected_order_basis:
            raise LedgerError("The order or its item link changed. Reload the record before saving.", 409)
        data = body.model_dump(exclude={"expected_revision", "expected_order_basis"})
        data.update(order_basis=order_basis(sale), updated_at=_iso(utcnow()), source="seller")
        conditions = [MarketplaceSale.id == sale_id, MarketplaceSale.financial_revision == body.expected_revision]
        conditions.extend(getattr(MarketplaceSale, key) == getattr(sale, key) for key in BASIS_FIELDS)
        try:
            result = db.execute(update(MarketplaceSale).where(*conditions).values(
                financials=data, financial_revision=MarketplaceSale.financial_revision + 1,
            ).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                db.rollback()
                raise LedgerError("The financial record changed. Reload it before saving.", 409)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    db.expire_all()
    return get_sale(db, sale_id)


def sale_query(db: Session, start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None):
    if start > end or start < date(1970, 1, 1) or end > datetime.now(UTC).date():
        raise LedgerError("Choose a valid UTC date range from 1970 through today.")
    if marketplace is not None and marketplace not in MARKETPLACES:
        raise LedgerError("Choose a supported marketplace.")
    moment = func.coalesce(MarketplaceSale.sold_at, MarketplaceSale.received_at)
    query = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id).filter(
        moment >= datetime.combine(start, time.min), moment < datetime.combine(end + timedelta(days=1), time.min),
    )
    if marketplace:
        query = query.filter(MarketplaceSale.marketplace == marketplace)
    if conversation_id:
        query = query.filter(MarketplaceSale.conversation_id == conversation_id)
    return query.order_by(moment.desc(), MarketplaceSale.id)


def summary(query) -> dict:
    groups = {}
    count, unmatched, unrecorded, stale_count = 0, 0, 0, 0
    for sale, _title in query.yield_per(500):
        count += 1
        unmatched += sale.conversation_id is None
        data, stale = financial_values(sale)
        stale_count += stale
        if data is None or not any(data[key] is not None for key in AMOUNTS):
            unrecorded += 1
            continue
        group = groups.setdefault(data["currency"], {"currency": data["currency"], "lines": 0, "net_known": 0, "profit_known": 0,
                                                   "net_revenue": Decimal(0), "profit": Decimal(0)})
        group["lines"] += 1
        net, profit = totals(data, stale)
        if net is not None:
            group["net_known"] += 1
            group["net_revenue"] += Decimal(net)
        if profit is not None:
            group["profit_known"] += 1
            group["profit"] += Decimal(profit)
    for group in groups.values():
        group["net_revenue"] = format(group["net_revenue"], "f") if group["net_known"] else None
        group["profit"] = format(group["profit"], "f") if group["profit_known"] else None
    return {"lines": count, "unmatched": unmatched, "unrecorded": unrecorded, "needs_review": stale_count,
            "currencies": [groups[key] for key in sorted(groups)]}


def list_sales(db: Session, start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None, page: int = 1) -> dict:
    query = sale_query(db, start, end, marketplace, conversation_id)
    totals = summary(query)
    rows = [sale_out(*row) for row in query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()]
    return {"rows": rows, "summary": totals, "page": page, "has_more": page * PAGE_SIZE < totals["lines"]}
