"""Reads of linked eBay and Etsy listings, taken after a sale. Never write to a marketplace."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import logging
import math

from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.services import ebay_import, ebay_workspace, etsy_import, keychain
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, assert_identity
from vendoo_studio.services.etsy_workspace import EtsyClient, EtsyFailure


log = logging.getLogger("vendoo_studio.marketplace_inventory")


class InventoryCheckError(ValueError):
    pass


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(Decimal(str(value)))
    except (ValueError, InvalidOperation, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


async def _ebay(listing_id: str) -> dict:
    client = await EbayClient.connect()
    info = await ebay_workspace.listing(client, listing_id)
    assert_identity(client.seller)
    account = keychain.get_marketplace_accounts().get("ebay") or {}
    if (info.get("id") != listing_id or not account.get("label")
            or str(info.get("seller", "")).casefold() != str(account["label"]).casefold()):
        raise InventoryCheckError("This listing does not belong to the connected eBay seller.")
    states = {"Active": "active", "Ended": "ended", "Completed": "completed"}
    if info.get("status") not in states:
        raise InventoryCheckError("eBay did not return a reliable listing status.")
    result = {"state": states[info["status"]], "price": None, "quantity": None, "warning": None}
    if info["status"] != "Active":
        return result
    if info.get("site") != "US":
        result["warning"] = "Price and quantity checks support US listings."
        return result
    if info.get("variations"):
        return _ebay_variations(info, result)
    try:
        snapshot = ebay_import.listing_snapshot(info)
    except EbayFailure:
        result["warning"] = "The listing status was checked, but its fixed price and quantity could not be verified in USD."
    else:
        result.update(price=snapshot["price"], quantity=snapshot["quantity"])
    return result


def _ebay_variations(info: dict, result: dict) -> dict:
    """Stock left on each variation (eBay's quantity minus sold). The listing is sold out only when every one is."""
    rows = []
    for row in info.get("variation_rows") or []:
        total, sold = ebay_import._number(row.get("quantity")), ebay_import._number(row.get("soldQuantity"))
        if total is None or sold is None or sold > total:
            result["warning"] = "eBay did not return a reliable quantity for every variation."
            return result
        rows.append({"sku": row.get("sku") or "", "values": row.get("values") or {}, "quantity": total - sold,
                     "price": _number(row.get("price"))})
    if not rows:
        result["warning"] = "eBay did not return this listing's variations."
        return result
    prices = {row["price"] for row in rows}
    result.update(quantity=sum(row["quantity"] for row in rows), variations=rows,
                  price=prices.pop() if len(prices) == 1 else None)
    return result


async def _etsy(listing_id: str) -> dict:
    client = await EtsyClient.connect()
    item = await client.listing(listing_id)
    if str(item.get("listing_id")) != listing_id or str(item.get("shop_id")) != client.shop_id:
        raise InventoryCheckError("This listing does not belong to the connected Etsy shop.")
    if item.get("state") not in {"active", "inactive", "sold_out", "draft", "expired", "removed", "edit"}:
        raise InventoryCheckError("Etsy did not return a reliable listing status.")
    result = {"state": item["state"], "price": None, "quantity": None, "warning": None}
    if item["state"] != "active":
        return result
    if item.get("has_variations") is not False or item.get("listing_type") != "physical":
        result["warning"] = "Price and quantity checks support physical listings without variations."
        return result
    try:
        _, _, quantity, price = etsy_import.single_offering(await client.inventory(listing_id), item)
    except EtsyFailure as exc:
        # Connection changes must fail the observation, rather than return a
        # successful status from the shop that was just disconnected.
        client.assert_current()
        if exc.status != 409:
            raise
        result["warning"] = "The listing status was checked, but its single-offering price and quantity could not be verified in USD."
    else:
        result.update(price=price, quantity=quantity)
    client.assert_current()
    return result


def _record(session_factory, conv_id: str, marketplace: str, listing_id: str, started: str, result: dict) -> list[dict]:
    """Compare a verified remote read with the latest local revision and save the observation (a worker-thread step)."""
    from vendoo_studio.services.ebay_variations import total_quantity
    from vendoo_studio.services.marketplace_delist import listings

    with session_factory() as db:
        # The seller may have replaced the link while the remote read was in flight.
        db.expire_all()
        current = listings(db, conv_id).get(marketplace) or {}
        if current.get("listing_id") != listing_id or current.get("state") != "live":
            raise InventoryCheckError("This item's marketplace link or status changed during the read.")
        listing = ListingRepo(db).get_current(conv_id)
        revision = db.get(ListingRevision, listing.current_revision_id) if listing and listing.current_revision_id else None
        snapshot = revision.listing_json if revision else {}
        differences = []
        varied = total_quantity(snapshot) if marketplace == "ebay" else None
        for field in ("price", "quantity"):
            remote, local = result[field], _number(snapshot.get(field))
            if field == "quantity" and varied is not None:
                local = float(varied)
            if field == "price" and result.get("variations"):
                continue
            if remote is not None and remote != local:
                differences.append({"field": field, "local": local, "remote": remote})
        observation = {"listing_id": listing_id, "checked_at": started, "state": result["state"],
                       "quantity": result["quantity"], "price": result["price"]}
        if result.get("variations"):
            # Per-SKU stock, so a sale of one size is not read as the whole item selling out.
            observation["variations"] = [{"sku": row["sku"], "quantity": row["quantity"]} for row in result["variations"]]
        values: dict = {"inventory_observation": observation}
        if result["price"] is not None and not result.get("variations"):
            # A verified read of the live listing: the price the marketplace has now.
            values["listed_price"] = result["price"]
        MarketplaceListingRepo(db).save(conv_id, marketplace, values)
        return differences


async def observe(conv_id: str, marketplace: str, listing_id: str, *, session_factory) -> dict:
    """Compare verified public values with the latest local revision, keeping both intact.

    The remote read holds no database session; the comparison and save run on a worker thread.
    """
    started = datetime.now(UTC).isoformat()
    result = await {"ebay": _ebay, "etsy": _etsy}[marketplace](listing_id)
    differences = await asyncio.to_thread(_record, session_factory, conv_id, marketplace, listing_id, started, result)
    return {"listing_id": listing_id, **result, "differences": differences}


def _unknown_stock(session_factory, sale_ids: list[str]) -> list[tuple[str, str, str]]:
    """The paid eBay and Etsy sales whose remaining stock Anteroom cannot tell (a worker-thread step)."""
    from vendoo_studio.models.marketplace_sale import MarketplaceSale, PAID_STATUSES
    from vendoo_studio.services.sale_inventory import stock_after_sale

    with session_factory() as db:
        sales = db.query(MarketplaceSale).filter(MarketplaceSale.id.in_(sale_ids)).all()
        targets = {(sale.conversation_id, sale.marketplace, sale.listing_id) for sale in sales
                   if sale.marketplace in {"ebay", "etsy"} and sale.conversation_id and sale.listing_id
                   and sale.order_status in PAID_STATUSES}
        return [target for target in sorted(targets) if stock_after_sale(db, *target)["status"] == "unknown"]


async def read_after_sales(sale_ids: list[str], *, session_factory) -> None:
    """Read eBay or Etsy stock once after a sale, when Anteroom cannot tell what is left.

    A one-of-a-kind item is sold out without a read. An item with more than one
    unit needs the marketplace's own stock, read after the sale, before anything
    is delisted elsewhere. A failed read leaves the stock unknown.
    """
    # Each database step runs on a worker thread with a session of its own, so
    # no query waits on the event loop and no connection or transaction is held
    # while the marketplace answers.
    for conv_id, marketplace, listing_id in await asyncio.to_thread(_unknown_stock, session_factory, sale_ids):
        try:
            await observe(conv_id, marketplace, listing_id, session_factory=session_factory)
        except Exception as exc:  # noqa: BLE001 - the sale is recorded either way
            # The exception text can carry marketplace responses; log its type only.
            log.info("Could not read %s stock for %s: %s", marketplace, conv_id, type(exc).__name__)
