"""Recover order-line evidence through direct read APIs. Never notify or delist."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
import re
import xml.etree.ElementTree as ET

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.ebay_client import EbayClient, NS, assert_identity, digest, trading_xml
from vendoo_studio.services.etsy_workspace import EtsyClient
from vendoo_studio.services.relay_sales import find_listing, reconcile_unmatched

PAGE_SIZE = 25
MAX_PAGES = 4000


class RecoveryError(ValueError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class OrderLine:
    order_id: str
    line_id: str
    listing_id: str
    quantity: int | None
    status: str
    sold_at: datetime | None
    modified_at: datetime | None


def _time(value) -> datetime | None:
    try:
        if type(value) in (int, float):
            result = datetime.fromtimestamp(value, UTC)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(UTC).replace(tzinfo=None) if result.tzinfo else result
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _id(value) -> str:
    result = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", result):
        raise RecoveryError("The marketplace returned an unreadable order identity. This page was not saved.")
    return result


def _listing_id(marketplace: str, value) -> str:
    result = str(value or "")
    pattern = r"[1-9][0-9]{0,19}" if marketplace == "etsy" else r"[0-9]{9,15}"
    if not re.fullmatch(pattern, result):
        raise RecoveryError("The marketplace returned an invalid listing identity. This page was not saved.")
    return result


def _quantity(value) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,8}", value):
        return int(value)
    return None


def _etsy_lines(receipt: dict) -> list[OrderLine]:
    status = receipt.get("status")
    states = {"canceled": "cancelled", "fully refunded": "refunded", "partially refunded": "partially_refunded"}
    if status in states:
        state = states[status]
    elif status in {"paid", "completed"} and receipt.get("is_paid") is True:
        state = "paid"
    else:
        return []
    transactions = receipt.get("transactions")
    if not isinstance(transactions, list) or any(not isinstance(row, dict) for row in transactions):
        raise RecoveryError("Etsy returned unreadable order lines. This page was not saved.")
    order_id = _id(receipt.get("receipt_id"))
    modified = _time(receipt.get("updated_timestamp") or receipt.get("update_timestamp"))
    sold = _time(receipt.get("create_timestamp"))
    return [OrderLine(order_id, _id(row.get("transaction_id")), _listing_id("etsy", row.get("listing_id")),
                      _quantity(row.get("quantity")), state, sold, modified) for row in transactions]


def _ebay_lines(order: ET.Element) -> list[OrderLine]:
    def text(path):
        return order.findtext(path, namespaces=NS)

    status = text("e:OrderStatus")
    if status == "Cancelled":
        state = "cancelled"
    elif status == "CancelPending":
        state = "cancellation_pending"
    elif (status in {"Active", "Completed", "InProcess"} and text("e:CheckoutStatus/e:Status") == "Complete"
          and text("e:CheckoutStatus/e:eBayPaymentStatus") == "NoPaymentFailure" and _time(text("e:PaidTime"))):
        state = "paid"
    else:
        return []
    order_id = _id(text("e:OrderID"))
    modified = _time(text("e:CheckoutStatus/e:LastModifiedTime"))
    sold = _time(text("e:PaidTime"))
    # Trading OrderLineItemID matches ORDER_CONFIRMATION notifications.
    # Fulfillment REST lineItemId identifies a different namespace.
    return [OrderLine(order_id, _id(row.findtext("e:OrderLineItemID", namespaces=NS)),
                      _listing_id("ebay", row.findtext("e:Item/e:ItemID", namespaces=NS)),
                      _quantity(row.findtext("e:QuantityPurchased", namespaces=NS)), state, sold, modified)
            for row in order.findall("e:TransactionArray/e:Transaction", NS)]


async def _ebay_payment(client: EbayClient, lines: list[OrderLine]) -> list[OrderLine]:
    if not lines or lines[0].status != "paid":
        return lines
    # Fulfillment supplies payment/refund status. Its lineItemId is never used
    # as a ledger key: the Trading IDs above remain the notification identity.
    detail = await client.request("GET", f"/sell/fulfillment/v1/order/{lines[0].order_id}")
    if not isinstance(detail, dict) or detail.get("orderId") != lines[0].order_id:
        raise RecoveryError("eBay did not verify this order's payment identity. This page was not saved.")
    status = {"PAID": "paid", "FULLY_REFUNDED": "refunded", "PARTIALLY_REFUNDED": "partially_refunded"}.get(detail.get("orderPaymentStatus") or "")
    if status is None:
        return []
    modified = _time(detail.get("lastModifiedDate"))
    return [replace(line, status=status, modified_at=max(filter(None, (line.modified_at, modified)), default=None)) for line in lines]


def _connection(identity, expected: str | None, connected_at: str) -> str:
    if identity[1] != connected_at:
        raise RecoveryError("The marketplace connection changed. Restart recovery for this account.", 409)
    connection = digest(identity)
    if expected is not None and expected != connection:
        raise RecoveryError("The marketplace connection changed. Restart recovery for this account.", 409)
    return connection


async def _read(marketplace: str, start: datetime, end: datetime, page: int, expected_connection: str | None, connected_at: str):
    if marketplace == "etsy":
        client = EtsyClient()
        connection = _connection(client.identity, expected_connection, connected_at)
        payload = await client.read("/receipts", {
            "limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE, "sort_on": "updated", "sort_order": "desc",
            "min_last_modified": int(start.replace(tzinfo=UTC).timestamp()),
            "max_last_modified": int(end.replace(tzinfo=UTC).timestamp()),
        })
        rows, count = payload.get("results"), payload.get("count")
        if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or len(rows) > PAGE_SIZE
                or type(count) is not int or count < 0 or (not rows and (page - 1) * PAGE_SIZE < count)):
            raise RecoveryError("Etsy returned an unreadable order page. Nothing on this page was saved.")
        return [_etsy_lines(row) for row in rows], page * PAGE_SIZE < count, client.assert_current, connection
    client = await EbayClient.connect()
    connection = _connection(client.seller, expected_connection, connected_at)
    root = ET.Element("GetOrdersRequest", xmlns=NS["e"])
    fields = {"OrderRole": "Seller", "OrderStatus": "All", "DetailLevel": "ReturnAll", "SortingOrder": "Descending",
              "ModTimeFrom": start.isoformat() + "Z", "ModTimeTo": end.isoformat() + "Z"}
    for key, value in fields.items():
        ET.SubElement(root, key).text = value
    pagination = ET.SubElement(root, "Pagination")
    ET.SubElement(pagination, "EntriesPerPage").text = str(PAGE_SIZE)
    ET.SubElement(pagination, "PageNumber").text = str(page)
    response = (await client.request("POST", "/ws/api.dll", xml=trading_xml(root), call="GetOrders"))["xml"]
    orders = response.find("e:OrderArray", NS)
    more = response.findtext("e:HasMoreOrders", namespaces=NS)
    if orders is None or more not in {"true", "false"}:
        raise RecoveryError("eBay returned an unreadable order page. Nothing on this page was saved.")
    rows = orders.findall("e:Order", NS)
    if len(rows) > PAGE_SIZE or (not rows and more == "true"):
        raise RecoveryError("eBay returned an incomplete order page. Retry this page.")
    limit = asyncio.Semaphore(4)

    async def payment(row):
        async with limit:
            return await _ebay_payment(client, _ebay_lines(row))

    results = await asyncio.gather(*(payment(row) for row in rows), return_exceptions=True)
    verified_orders: list[list[OrderLine]] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        verified_orders.append(result)
    return verified_orders, more == "true", lambda: assert_identity(client.seller), connection


def _save(db: Session, marketplace: str, lines: list[OrderLine], observed: datetime) -> dict:
    counts = {"recorded": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    unique = {}
    for line in lines:
        key = (line.order_id, line.line_id)
        if key in unique and unique[key] != line:
            raise RecoveryError("The marketplace returned conflicting duplicate order lines. This page was not saved.")
        unique[key] = line
    for line in unique.values():
        sale = db.query(MarketplaceSale).filter_by(marketplace=marketplace, order_id=line.order_id, line_id=line.line_id).first()
        if sale is None:
            # Older ID-only notices could use a listing/order ID when their
            # line ID was absent. Do not silently turn that into a second sale.
            possible = db.query(MarketplaceSale.id).filter(
                MarketplaceSale.marketplace == marketplace, MarketplaceSale.order_id == line.order_id,
                MarketplaceSale.line_id.in_([line.listing_id, line.order_id]),
            ).first()
            if possible:
                counts["conflicts"] += 1
                continue
            owner = find_listing(db, marketplace, line.listing_id)
            db.add(MarketplaceSale(marketplace=marketplace, order_id=line.order_id, line_id=line.line_id,
                                  listing_id=line.listing_id, quantity=line.quantity, order_status=line.status,
                                  order_updated_at=line.modified_at, order_observed_at=observed, sold_at=line.sold_at,
                                  conversation_id=owner.id if owner else None))
            db.flush()
            counts["recorded"] += 1
            continue
        if sale.listing_id and sale.listing_id != line.listing_id:
            counts["conflicts"] += 1
            continue
        if ((sale.order_observed_at and sale.order_observed_at > observed)
                or (sale.order_updated_at and line.modified_at and line.modified_at < sale.order_updated_at)
                or (sale.order_status in {"cancelled", "refunded"} and line.status not in {"cancelled", "refunded"}
                    and (not sale.order_updated_at or not line.modified_at or line.modified_at <= sale.order_updated_at))):
            counts["unchanged"] += 1
            continue
        changed = (sale.order_status != line.status or (line.quantity is not None and sale.quantity != line.quantity) or sale.listing_id is None)
        sale.listing_id = line.listing_id
        if line.quantity is not None:
            sale.quantity = line.quantity
        if line.sold_at is not None and sale.sold_at is None:
            sale.sold_at = line.sold_at
        sale.order_status = line.status
        if line.modified_at is not None:
            sale.order_updated_at = line.modified_at
        if changed or sale.order_observed_at is None:
            sale.order_observed_at = observed
        counts["updated" if changed else "unchanged"] += 1
    db.commit()
    return counts


async def recover(db: Session, marketplace: str, start: date, end: date, page: int, connected_at: str, connection: str | None = None, through: datetime | None = None) -> dict:
    today = datetime.now(UTC).date()
    if marketplace not in {"ebay", "etsy"} or not 1 <= page <= MAX_PAGES:
        raise RecoveryError("Choose eBay or Etsy and a valid recovery page.", 422)
    if start < today - timedelta(days=89) or end > today or start > end or (end - start).days > 29:
        raise RecoveryError("Choose up to 30 days within the last 90 days, through today (UTC).", 422)
    if page > 1 and (connection is None or through is None):
        raise RecoveryError("Start recovery at page one to verify the connection and time boundary.", 422)
    observed = datetime.now(UTC).replace(tzinfo=None)
    start_at = datetime.combine(start, time.min)
    end_at = min(datetime.combine(end + timedelta(days=1), time.min) - timedelta(seconds=1), observed)
    if through is not None:
        if through.tzinfo is None:
            raise RecoveryError("Use a UTC recovery boundary returned by the first page.", 422)
        boundary = through.astimezone(UTC).replace(tzinfo=None)
        if boundary < start_at or boundary > end_at:
            raise RecoveryError("The recovery boundary is outside the selected dates.", 422)
        end_at = boundary
    orders, more, verify, connection = await _read(marketplace, start_at, end_at, page, connection, connected_at)
    lines = [line for order in orders for line in order]
    counts: dict[str, int] = {}
    for attempt in range(2):
        try:
            with start_gate():
                verify()
                counts = _save(db, marketplace, lines, observed)
            break
        except IntegrityError:
            db.rollback()
            if attempt:
                raise RecoveryError("Orders changed during recovery. Retry this page.", 409) from None
        except BaseException:
            db.rollback()
            raise
    matched = reconcile_unmatched(db, marketplace=marketplace)
    return {"marketplace": marketplace, "connection": connection, "through": end_at.replace(tzinfo=UTC).isoformat(), "page": page, "nextPage": page + 1 if more and page < MAX_PAGES else None,
            "ordersScanned": len(orders), "skippedOrders": sum(not order for order in orders), "matched": matched,
            "truncated": more and page == MAX_PAGES, **counts}
