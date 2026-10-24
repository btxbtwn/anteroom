"""Read the connected Etsy shop through the existing relay; keep only display fields.

Endpoint and Money contracts: https://www.etsy.com/openapi/generated/oas/3.0.0.json
No marketplace writes or local persistence take place here.
"""
from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote

from vendoo_studio.services import keychain, marketplace_accounts
from vendoo_studio.services.marketplace_accounts import SignInRequired

PAGE_SIZE = 25


class EtsyFailure(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _id(value) -> str:
    result = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", result):
        raise EtsyFailure("Etsy returned an invalid identifier.")
    return result


def _identity() -> tuple[str, str]:
    account = keychain.get_marketplace_accounts().get("etsy") or {}
    if not account.get("accountId") or not account.get("accessToken"):
        raise SignInRequired("Connect Etsy to view your shop dashboard.")
    return _id(account["accountId"]), str(account.get("connectedAt", ""))


class EtsyClient:
    def __init__(self):
        self.identity = _identity()
        self.shop_id = self.identity[0]

    async def read(self, suffix: str = "", params: dict | None = None) -> dict:
        if _identity() != self.identity:
            raise EtsyFailure("The Etsy connection changed. Refresh this view.", 409)
        result = await marketplace_accounts.etsy_api("GET", f"shops/{self.shop_id}{suffix}", params=params)
        if _identity() != self.identity:
            raise EtsyFailure("The Etsy connection changed. Refresh this view.", 409)
        if not isinstance(result, dict):
            raise EtsyFailure("Etsy returned an unreadable response. Try again.")
        return result


def _text(value) -> str:
    return html.unescape(value) if isinstance(value, str) else ""


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _timestamp(value) -> str | None:
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _money(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    amount, divisor, currency = value.get("amount"), value.get("divisor"), value.get("currency_code")
    if (type(amount) is not int or amount < 0 or type(divisor) is not int or divisor <= 0
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        return None
    return {"amount": format(Decimal(amount) / Decimal(divisor), "f"), "currency": currency}


def _page(payload: dict, offset: int, transform) -> dict:
    results, total = payload.get("results"), _count(payload.get("count"))
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results) or total is None:
        raise EtsyFailure("Etsy returned an unreadable results page. Try again.")
    next_offset = offset + len(results)
    return {"rows": [transform(row) for row in results], "total": total,
            "nextOffset": next_offset if results and next_offset < total else None}


async def shop(client: EtsyClient) -> dict:
    raw = await client.read()
    if _id(raw.get("shop_id")) != client.shop_id:
        raise EtsyFailure("Etsy returned a different shop. Reconnect your account.", 409)
    name = _text(raw.get("shop_name"))
    rating = raw.get("review_average")
    return {
        "id": client.shop_id, "name": name, "title": _text(raw.get("title")),
        "url": f"https://www.etsy.com/shop/{quote(name, safe='')}" if name else None,
        "activeListings": _count(raw.get("listing_active_count")),
        "soldItems": _count(raw.get("transaction_sold_count")), "favorites": _count(raw.get("num_favorers")),
        "reviewCount": _count(raw.get("review_count")),
        "rating": rating if type(rating) in (int, float) and 0 <= rating <= 5 else None,
        "onVacation": raw.get("is_vacation") is True,
    }


def _order(raw: dict) -> dict:
    transactions = raw.get("transactions") or []
    if not isinstance(transactions, list) or any(not isinstance(row, dict) for row in transactions):
        raise EtsyFailure("Etsy returned unreadable order items. Try again.")
    status = _text(raw.get("status")) or "unknown"
    paid, shipped = raw.get("is_paid") is True, raw.get("is_shipped") is True
    return {
        "id": _id(raw.get("receipt_id")), "status": status,
        "paid": paid, "shipped": shipped, "createdAt": _timestamp(raw.get("create_timestamp")),
        "total": _money(raw.get("grandtotal")),
        "needsShipping": (paid and raw.get("is_shipped") is False
                          and status not in {"canceled", "fully refunded"}
                          and any(row.get("is_digital") is False for row in transactions)),
        "items": [{"id": _id(row.get("transaction_id")), "title": _text(row.get("title")),
                   "quantity": _count(row.get("quantity")), "sku": _text(row.get("sku"))}
                  for row in transactions],
    }


async def orders(client: EtsyClient, start: date, end: date, offset: int, status: str) -> dict:
    today = datetime.now(UTC).date()
    if start < date(2000, 1, 1) or end > today or start > end or (end - start).days > 366:
        raise EtsyFailure("Choose dates from 2000 through today, up to one year at a time.", 422)
    params = {
        "limit": PAGE_SIZE, "offset": offset, "sort_on": "created", "sort_order": "desc",
        "min_created": int(datetime.combine(start, time.min, UTC).timestamp()),
        "max_created": int(datetime.combine(end + timedelta(days=1), time.min, UTC).timestamp()) - 1,
    }
    if status in {"paid", "unshipped"}:
        params["was_paid"] = "true"
    if status == "unshipped":
        params.update(was_shipped="false", was_canceled="false")
    page = _page(await client.read("/receipts", params), offset, _order)
    totals: dict[str, Decimal] = {}
    missing = 0
    for row in page["rows"]:
        if not row["paid"]:
            continue
        money = row["total"]
        if money is None:
            missing += 1
        else:
            currency = money["currency"]
            totals[currency] = totals.get(currency, Decimal(0)) + Decimal(money["amount"])
    page["summary"] = {
        "paidOrders": sum(row["paid"] for row in page["rows"]),
        "needsShipping": sum(row["needsShipping"] for row in page["rows"]),
        "paidTotals": [{"currency": currency, "amount": format(amount, "f")} for currency, amount in sorted(totals.items())],
        "missingTotals": missing,
    }
    return page


def _listing(raw: dict) -> dict:
    listing_id = _id(raw.get("listing_id"))
    state = _text(raw.get("state")) or "unknown"
    return {
        "id": listing_id, "title": _text(raw.get("title")), "state": state,
        "url": f"https://www.etsy.com/listing/{listing_id}" if state == "active" else None,
        "price": _money(raw.get("price")), "quantity": _count(raw.get("quantity")),
        "favorites": _count(raw.get("num_favorers")), "createdAt": _timestamp(raw.get("creation_timestamp")),
    }


async def listings(client: EtsyClient, state: str, offset: int) -> dict:
    return _page(await client.read("/listings", {
        "state": state, "limit": PAGE_SIZE, "offset": offset,
        "sort_on": "created", "sort_order": "desc",
    }), offset, _listing)
