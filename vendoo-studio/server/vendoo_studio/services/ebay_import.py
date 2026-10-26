"""Read a seller's existing eBay listings into local inventory. Never write to eBay."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import xml.etree.ElementTree as ET

from sqlalchemy.orm import Session

from vendoo_studio.models.schema import ListingSchema
from vendoo_studio.services import ebay_actions, inventory_import
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, NS, assert_identity, trading_xml

PAGE_SIZE = 25


def _number(value: str | None) -> int | None:
    try:
        number = int(value) if value is not None else None
        return number if number is not None and number >= 0 else None
    except (ValueError, TypeError):
        return None


async def active_listings(client: EbayClient, db: Session, page_number: int) -> dict:
    root = ET.Element("GetMyeBaySellingRequest", xmlns=NS["e"])
    active = ET.SubElement(root, "ActiveList")
    ET.SubElement(active, "Include").text = "true"
    pagination = ET.SubElement(active, "Pagination")
    ET.SubElement(pagination, "EntriesPerPage").text = str(PAGE_SIZE)
    ET.SubElement(pagination, "PageNumber").text = str(page_number)
    for name in ("SoldList", "UnsoldList", "ScheduledList", "SellingSummary"):
        ET.SubElement(ET.SubElement(root, name), "Include").text = "false"
    result = await client.request("POST", "/ws/api.dll", xml=trading_xml(root), call="GetMyeBaySelling")
    active = result["xml"].find("e:ActiveList", NS)
    if active is None:
        return {"rows": [], "page": page_number, "total": 0, "has_more": False}
    rows = []
    for item in active.findall("e:ItemArray/e:Item", NS):
        identifier = item.findtext("e:ItemID", namespaces=NS) or ""
        if not identifier.isdigit():
            continue
        price = item.find("e:SellingStatus/e:CurrentPrice", NS)
        total = _number(item.findtext("e:Quantity", namespaces=NS))
        sold = _number(item.findtext("e:SellingStatus/e:QuantitySold", namespaces=NS))
        reason = None
        if item.find("e:Variations", NS) is not None:
            reason = "Variation listings are not supported by this import yet."
        elif item.findtext("e:ListingType", namespaces=NS) not in {"FixedPriceItem", "StoresFixedPrice"}:
            reason = "This import supports fixed-price listings."
        elif price is None or price.get("currencyID") != "USD":
            reason = "This import supports listings priced in USD."
        try:
            existing = inventory_import.owner(db, "ebay", identifier)
        except inventory_import.ImportConflict as exc:
            existing, reason = None, str(exc)
        rows.append({"id": identifier, "title": item.findtext("e:Title", namespaces=NS) or identifier,
                     "price": price.text if price is not None and price.get("currencyID") == "USD" else None,
                     "quantity": max(0, total - sold) if total is not None and sold is not None else None,
                     "reason": reason, "conversation_id": existing.id if existing else None})
    pages = _number(active.findtext("e:PaginationResult/e:TotalNumberOfPages", namespaces=NS))
    total = _number(active.findtext("e:PaginationResult/e:TotalNumberOfEntries", namespaces=NS))
    return {"rows": rows, "page": page_number, "total": total,
            "has_more": page_number < pages if pages is not None else len(rows) == PAGE_SIZE}


def _snapshot(info: dict) -> dict:
    total, sold = _number(info.get("quantity")), _number(info.get("soldQuantity"))
    if total is None or sold is None or sold > total:
        raise EbayFailure("eBay did not return a reliable available quantity for this listing.", 409)
    try:
        price = Decimal(info["price"])
    except (InvalidOperation, TypeError):
        raise EbayFailure("eBay did not return a valid USD price.", 409) from None
    if not price.is_finite() or price <= 0 or not math.isfinite(float(price)):
        raise EbayFailure("eBay did not return a valid USD price.", 409)
    if info.get("type") not in {"FixedPriceItem", "StoresFixedPrice"}:
        raise EbayFailure("This import supports active fixed-price listings without variations.", 409)
    specifics = info.get("specifics") or {}
    by_name = {name.casefold(): values for name, values in specifics.items()}

    def first(name):
        return next(iter(by_name.get(name) or []), None)

    category = str(info.get("categoryName") or "").replace(":", " > ")
    return {"title": info["title"], "description": info["description"], "price": float(price), "quantity": total - sold,
            "brand": first("brand"), "size": first("size"), "sku": info.get("sku") or None,
            "condition": ListingSchema.validate_condition(info.get("condition") or None),
            "category_path": category, "marketplace_categories": {"ebay": category},
            "marketplace_category_ids": {"ebay": info.get("categoryId")},
            "ebay_specifics": {**specifics, **({"conditionDescription": info["conditionDescription"]} if info.get("conditionDescription") else {})}}


async def import_listing(client: EbayClient, db: Session, listing_id: str) -> dict:
    info = await ebay_actions.owned_listing(client, listing_id)
    if info.get("id") != listing_id:
        raise EbayFailure("eBay returned a different listing. Nothing was imported.", 409)
    snapshot = _snapshot(info)
    try:
        return await inventory_import.save_owned_listing(
            db, "ebay", listing_id, snapshot, info.get("images", []), lambda: assert_identity(client.seller),
        )
    except inventory_import.ImportConflict as exc:
        raise EbayFailure(str(exc), 409) from exc
