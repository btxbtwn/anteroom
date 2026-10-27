"""Seller-requested observations of linked listings. Never write to a marketplace."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import math

from sqlalchemy.orm import Session

from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.services import ebay_import, ebay_workspace, etsy_import, keychain
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, assert_identity
from vendoo_studio.services.etsy_workspace import EtsyClient, EtsyFailure


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
    client = EtsyClient()
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


async def observe(db: Session, conv_id: str, marketplace: str, listing_id: str) -> dict:
    """Compare verified public values with the latest local revision, keeping both intact."""
    from vendoo_studio.services.marketplace_delist import listings

    started = datetime.now(UTC).isoformat()
    result = await {"ebay": _ebay, "etsy": _etsy}[marketplace](listing_id)
    # The seller may have replaced the link while the remote read was in flight.
    db.expire_all()
    current = listings(db, conv_id).get(marketplace) or {}
    if current.get("listing_id") != listing_id or current.get("state") != "live":
        raise InventoryCheckError("This item's marketplace link or status changed. Sync again.")
    listing = ListingRepo(db).get_current(conv_id)
    revision = db.get(ListingRevision, listing.current_revision_id) if listing and listing.current_revision_id else None
    snapshot = revision.listing_json if revision else {}
    from vendoo_studio.services.ebay_variations import total_quantity

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
    MarketplaceListingRepo(db).save(conv_id, marketplace, {"inventory_observation": observation})
    return {"listing_id": listing_id, **result, "differences": differences}
