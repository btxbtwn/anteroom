"""Seller-owned financial records on the direct order-line ledger."""
from __future__ import annotations
from typing import cast as type_cast
from sqlalchemy.engine import CursorResult

from datetime import UTC, date, datetime, time
from decimal import Decimal
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, utcnow
from vendoo_studio.models.marketplace_sale import OTHER_MARKETPLACE, MarketplaceSale
from vendoo_studio.services.api_job_lock import start_gate

AMOUNTS = ("item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost")
MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop")
# Where a sale can be recorded: the marketplaces, plus anywhere else (in person, Instagram).
SALE_MARKETPLACES = (*MARKETPLACES, OTHER_MARKETPLACE)
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
    sold_on: date | None = None
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
    revenue, shipping_credit = values["item_revenue"], values["shipping_credit"]
    # The forms never ask for refunds; none recorded means none happened.
    refunds = values["refunds"] if values["refunds"] is not None else Decimal(0)
    fees, shipping_cost, item_cost = (values[key] for key in ("fees", "shipping_cost", "item_cost"))
    net = revenue + shipping_credit - refunds if revenue is not None and shipping_credit is not None else None
    profit = (net - fees - shipping_cost - item_cost
              if net is not None and fees is not None and shipping_cost is not None and item_cost is not None else None)
    return format(net, "f") if net is not None else None, format(profit, "f") if profit is not None else None


def sale_out(sale: MarketplaceSale, title: str | None) -> dict:
    financials, stale = financial_values(sale)
    net, profit = totals(financials, stale)
    raw = sale.financials if isinstance(sale.financials, dict) else {}
    return {"id": sale.id, "marketplace": sale.marketplace, "order_id": sale.order_id, "line_id": sale.line_id,
            "listing_id": sale.listing_id, "quantity": sale.quantity, "order_status": sale.order_status, "source": sale.source,
            "alert_state": sale.state, "conversation_id": sale.conversation_id, "title": title,
            "sold_at": _iso(sale.sold_at), "received_at": _iso(sale.received_at),
            "financials": financials, "financials_stale": stale, "financial_revision": sale.financial_revision,
            "financials_updated_at": raw.get("updated_at"), "order_basis": order_basis(sale),
            "net_revenue": net, "profit": profit}


def get_sale(db: Session, sale_id: str) -> dict:
    from vendoo_studio.services.sales_records import records
    from vendoo_studio.services.vendoo_sales_archive import ensure_archived

    ensure_archived(db)
    row = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id).filter(MarketplaceSale.id == sale_id).first()
    if row is None:
        raise LedgerError("Sale record not found.", 404)
    found = next((record for record in records(db, row[0].conversation_id) if record["id"] == sale_id), None)
    if found is None:
        raise LedgerError("Sale record not found.", 404)
    return found


def save_financials(db: Session, sale_id: str, body: FinancialSave) -> dict:
    if body.sold_on is not None and not date(1970, 1, 1) <= body.sold_on <= datetime.now(UTC).date():
        raise LedgerError("Choose a sale date from 1970 through today.")
    with start_gate():
        from vendoo_studio.services.vendoo_sales_archive import ensure_archived

        ensure_archived(db)
        sale = db.get(MarketplaceSale, sale_id, populate_existing=True)
        if sale is None:
            raise LedgerError("Sale record not found.", 404)
        if order_basis(sale) != body.expected_order_basis:
            raise LedgerError("The order or its item link changed. Reload the record before saving.", 409)
        data = body.model_dump(exclude={"expected_revision", "expected_order_basis", "sold_on"})
        # The record form asks what Mark as sold asks. Amounts it leaves out
        # (a marketplace's refund, an imported item cost) stay as they were.
        kept = sale.financials if isinstance(sale.financials, dict) else {}
        for key in ("refunds", "item_cost"):
            if key not in body.model_fields_set:
                data[key] = kept.get(key)
        data.update(order_basis=order_basis(sale), updated_at=_iso(utcnow()), source="seller")
        values: dict = {"financials": data, "financial_revision": MarketplaceSale.financial_revision + 1}
        if "sold_on" in body.model_fields_set:
            data["sold_on"] = body.sold_on.isoformat() if body.sold_on else None
            if body.sold_on is None:
                values["sold_at"] = None
            elif sale.sold_at is None or sale.sold_at.date() != body.sold_on:
                values["sold_at"] = datetime.combine(body.sold_on, time(12))
        conditions = [MarketplaceSale.id == sale_id, MarketplaceSale.financial_revision == body.expected_revision]
        conditions.extend(getattr(MarketplaceSale, key) == getattr(sale, key) for key in BASIS_FIELDS)
        try:
            result = db.execute(update(MarketplaceSale).where(*conditions).values(**values).execution_options(synchronize_session=False))
            if type_cast(CursorResult, result).rowcount != 1:
                db.rollback()
                raise LedgerError("The financial record changed. Reload it before saving.", 409)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    db.expire_all()
    return get_sale(db, sale_id)


def sale_records(db: Session, start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None,
                 q: str = "", attention: str = "all", sort: str = "newest"):
    from vendoo_studio.services.sales_records import records

    if start > end or start < date(1970, 1, 1) or end > datetime.now(UTC).date():
        raise LedgerError("Choose a valid UTC date range from 1970 through today.")
    if marketplace is not None and marketplace not in SALE_MARKETPLACES:
        raise LedgerError("Choose a supported marketplace.")
    if attention not in {"all", "missing", "review", "loss"} or sort not in {"newest", "oldest", "title"}:
        raise LedgerError("Choose a supported sales filter and sort order.")
    search = q.strip().casefold()
    rows = [row for row in records(db, conversation_id)
            if start.isoformat() <= str(row["sold_at"] or row["received_at"])[:10] <= end.isoformat()
            and (marketplace is None or row["marketplace"] == marketplace)
            and (not search or search in " ".join(str(row.get(key) or "") for key in ("title", "order_id", "line_id", "marketplace")).casefold())
            and (attention == "all" or attention == "missing" and bool(row["missing_sale_data"])
                 or attention == "review" and row["financials_stale"]
                 or attention == "loss" and row["profit"] is not None and Decimal(row["profit"]) < 0)]
    if sort == "title":
        return sorted(rows, key=lambda row: ((row["title"] or "").casefold(), row["id"]))
    return sorted(rows, key=lambda row: (row["sold_at"] or row["received_at"], row["id"]), reverse=sort == "newest")


def summary(rows) -> dict:
    from vendoo_studio.services.sales_records import missing_counts

    groups = {}
    count, unmatched, unrecorded, stale_count = 0, 0, 0, 0
    for row in rows:
        count += 1
        unmatched += row["conversation_id"] is None
        data, stale = row["financials"], row["financials_stale"]
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
    return {"lines": count, "unmatched": unmatched, "needs_review": stale_count,
            # Sales missing any amount, and which amounts: what the Missing sale data filter shows.
            "missing_records": sum(1 for row in rows if row["missing_sale_data"]),
            "missing_counts": missing_counts([row["missing_sale_data"] for row in rows]),
            # Removal note: lines with no amount at all. Replaced by missing_records and
            # missing_counts; kept until 2027-02-01 for iPhone builds that still read it.
            "unrecorded": unrecorded,
            "currencies": [groups[key] for key in sorted(groups)]}


def list_sales(db: Session, start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None, page: int = 1,
               q: str = "", attention: str = "all", sort: str = "newest") -> dict:
    records = sale_records(db, start, end, marketplace, conversation_id, q, attention, sort)
    totals = summary(records)
    rows = records[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    return {"rows": rows, "summary": totals, "page": page, "has_more": page * PAGE_SIZE < totals["lines"]}


class SellerSale(FinancialEntry):
    """A sale the seller records with Mark as sold, on a marketplace or anywhere else."""
    marketplace: str
    sold_on: date
    quantity: int = Field(default=1, ge=1, le=10_000, strict=True)
    # One per form submission, so a double click records one sale.
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def record_seller_sale(db: Session, conv_id: str, body: SellerSale) -> dict:
    """Record a sale with its amounts. Never touches a marketplace.

    A sale on a named marketplace that leaves no units also marks that
    marketplace sold, so Delist never removes the listing that sold. The
    response lists the other live listings, for the seller to delist through
    the confirmed Delist if they choose; nothing is delisted here.
    """
    from vendoo_studio.services import marketplace_delist
    from vendoo_studio.services.item_status import status_for
    from vendoo_studio.services.vendoo_import import parse_notes

    if body.marketplace not in SALE_MARKETPLACES:
        raise LedgerError("Choose where the item sold.")
    today = datetime.now(UTC).date()
    if body.sold_on > today or body.sold_on < date(1970, 1, 1):
        raise LedgerError("Choose the day it sold, today or earlier.")
    order_id = f"seller-{body.request_id}"
    with start_gate():
        conv = db.get(Conversation, conv_id)
        if conv is None:
            raise LedgerError("Item not found.", 404)
        existing = db.query(MarketplaceSale).filter_by(marketplace=body.marketplace, order_id=order_id, line_id="1").first()
        if existing is not None:
            if existing.conversation_id != conv_id:
                raise LedgerError("This sale was recorded for another item.", 409)
        else:
            before = status_for(db, conv_id, parse_notes(conv.notes))
            left = before.quantity - before.units_sold
            if left < body.quantity:
                raise LedgerError(
                    "This item is already sold. Change the amounts on its sale in Sales records, or raise its quantity if you have more."
                    if left <= 0 else f"Only {left} of {before.quantity} left to sell.", 409)
            known = marketplace_delist.listings(db, conv_id).get(body.marketplace) or {} if body.marketplace in MARKETPLACES else {}
            sold_at = utcnow().replace(tzinfo=None) if body.sold_on == today else datetime.combine(body.sold_on, time(12))
            sale = MarketplaceSale(
                marketplace=body.marketplace, order_id=order_id, line_id="1", listing_id=known.get("listing_id"),
                quantity=body.quantity, order_status="paid", conversation_id=conv_id, source="seller",
                # The seller made the record, so there is no alert to review.
                state="dismissed", sold_at=sold_at, resolved_at=utcnow(),
            )
            amounts = body.model_dump(include={"currency", *AMOUNTS})
            if any(amounts[key] is not None for key in AMOUNTS):
                sale.financials = {**amounts, "order_basis": order_basis(sale), "updated_at": _iso(utcnow()), "source": "seller"}
                sale.financial_revision = 1
            db.add(sale)
            db.commit()
            existing = sale
        sale_id = existing.id
        status = status_for(db, conv_id, parse_notes(conv.notes))
        if status.status == "sold" and body.marketplace in MARKETPLACES:
            listing = marketplace_delist.listings(db, conv_id).get(body.marketplace) or {}
            if not listing.get("sold_here"):
                marketplace_delist._mark(db, conv_id, body.marketplace, "sold", None)
    listings = marketplace_delist.listings(db, conv_id)
    offer = [] if status.status != "sold" else [
        name for name, entry in listings.items()
        if name != body.marketplace and entry.get("listing_id") and not entry.get("sold_here")
        and entry.get("state") not in {"deleted", "deleting"}
    ]
    return {"sale": get_sale(db, sale_id), "status": status.status, "units_sold": status.units_sold,
            "quantity": status.quantity, "listings": listings, "delist_offer": offer}


def delete_seller_sale(db: Session, sale_id: str, expected_revision: int) -> dict:
    """Delete a sale the seller recorded. Never touches a marketplace.

    A sale a marketplace reported is refused: the next order check would bring
    it back. When the deleted sale was the one that marked its marketplace
    sold, that mark goes too, so the item no longer counts the sale.
    """
    from vendoo_studio.models.marketplace_sale import PAID_STATUSES
    from vendoo_studio.services import marketplace_delist
    from vendoo_studio.services.vendoo_import import parse_notes

    with start_gate():
        sale = db.get(MarketplaceSale, sale_id, populate_existing=True)
        if sale is None:
            raise LedgerError("Sale record not found.", 404)
        if sale.source != "seller":
            raise LedgerError("Only sales you recorded can be deleted. A sale the marketplace reported comes back the next time "
                              "Anteroom checks; cancel or refund it on the marketplace instead.", 409)
        conv_id, marketplace, recorded = sale.conversation_id, sale.marketplace, marketplace_delist._time(sale.received_at)
        try:
            result = db.execute(delete(MarketplaceSale).where(
                MarketplaceSale.id == sale_id, MarketplaceSale.financial_revision == expected_revision,
            ).execution_options(synchronize_session=False))
            if type_cast(CursorResult, result).rowcount != 1:
                db.rollback()
                raise LedgerError("This sale changed. Reload it before deleting.", 409)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        db.expire_all()
        conv = db.get(Conversation, conv_id) if conv_id else None
        if conv is not None and marketplace in MARKETPLACES:
            mark = marketplace_delist._notes_dict(parse_notes(conv.notes), "marketplaceMarks").get(marketplace)
            marked = marketplace_delist._time(mark.get("at")) if isinstance(mark, dict) and mark.get("state") == "sold" else None
            others = db.query(MarketplaceSale.id).filter(
                MarketplaceSale.conversation_id == conv_id, MarketplaceSale.marketplace == marketplace,
                MarketplaceSale.order_status.in_(PAID_STATUSES)).first()
            if marked and recorded and marked >= recorded and others is None:
                marketplace_delist._store(db, conv.id, "marketplaceMarks", marketplace, None)
    return {"deleted": sale_id}
