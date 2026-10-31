"""Read active Etsy listings into Anteroom without writing to Etsy."""
from __future__ import annotations

from decimal import Decimal
import html
import math

from sqlalchemy.orm import Session

from vendoo_studio.services import etsy_workspace, inventory_import
from vendoo_studio.services.etsy_workspace import EtsyClient, EtsyFailure

PAGE_SIZE = 25


def _money(raw) -> float:
    if (not isinstance(raw, dict) or raw.get("currency_code") != "USD" or type(raw.get("amount")) is not int
            or raw["amount"] <= 0 or type(raw.get("divisor")) is not int or raw["divisor"] <= 0):
        raise EtsyFailure("This import requires a valid price in USD.", 409)
    price = float(Decimal(raw["amount"]) / Decimal(raw["divisor"]))
    if not math.isfinite(price) or price <= 0:
        raise EtsyFailure("Etsy did not return a valid USD price.", 409)
    return price


def _count(value) -> int:
    if type(value) is not int or value < 0:
        raise EtsyFailure("Etsy did not return a reliable available quantity.", 409)
    return value


async def active_listings(client: EtsyClient, db: Session, page_number: int) -> dict:
    page = await etsy_workspace.listings(client, "active", (page_number - 1) * PAGE_SIZE)
    rows = []
    for item in page["rows"]:
        price = item.get("price")
        reason = None if price and price["currency"] == "USD" else "This import supports listings priced in USD."
        try:
            existing = inventory_import.owner(db, "etsy", item["id"])
        except inventory_import.ImportConflict as exc:
            existing, reason = None, str(exc)
        rows.append({"id": item["id"], "title": item["title"], "price": price["amount"] if price and price["currency"] == "USD" else None,
                     "quantity": item["quantity"], "reason": reason, "conversation_id": existing.id if existing else None})
    return {"rows": rows, "page": page_number, "total": page["total"], "has_more": page["nextOffset"] is not None}


def single_offering(raw: dict, item: dict) -> tuple[str | None, dict, int, float]:
    """Return the one SKU and offering, refusing to flatten variation inventory."""
    results = raw.get("results")
    if (not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict)
            or str(results[0].get("listing_id")) != str(item["listing_id"]) or "inventory" not in results[0]):
        raise EtsyFailure("Etsy did not return inventory for this listing. Nothing was imported.", 409)
    inventory = results[0].get("inventory")
    if inventory is None:
        # Etsy explicitly returns null for listings never edited with its inventory
        # tools. The listing's own quantity/price are authoritative in that case.
        skus = item.get("skus") or []
        if not isinstance(skus, list) or len(skus) > 1 or any(not isinstance(sku, str) for sku in skus):
            raise EtsyFailure("This import supports one SKU without variations.", 409)
        return next(iter(skus), None), {}, _count(item.get("quantity")), _money(item.get("price"))
    if not isinstance(inventory, dict):
        raise EtsyFailure("Etsy returned unreadable inventory.", 409)
    products = inventory.get("products")
    if not isinstance(products, list) or len(products) != 1 or not isinstance(products[0], dict):
        raise EtsyFailure("This import supports one SKU without variations.", 409)
    product = products[0]
    offerings = product.get("offerings")
    if (product.get("is_deleted") is True or not isinstance(offerings, list) or len(offerings) != 1
            or not isinstance(offerings[0], dict) or offerings[0].get("is_enabled") is not True
            or offerings[0].get("is_deleted") is True):
        raise EtsyFailure("This import requires one active inventory offering without variations.", 409)
    offering = offerings[0]
    sku = product.get("sku")
    if sku is not None and not isinstance(sku, str):
        raise EtsyFailure("Etsy returned an unreadable SKU.", 409)
    return sku or None, offering, _count(offering.get("quantity")), _money(offering.get("price"))


def _snapshot(item: dict, sku: str | None, quantity: int, price: float, properties: dict) -> dict:
    def words(key):
        value = item.get(key) or []
        if not isinstance(value, list) or any(not isinstance(word, str) for word in value):
            raise EtsyFailure("Etsy returned unreadable listing details.", 409)
        return [html.unescape(word) for word in value]

    materials, tags, styles = words("materials"), words("tags"), words("style")
    details = {key: item[key] for key in ("who_made", "when_made", "materials", "shipping_profile_id", "return_policy_id", "readiness_state_id") if item.get(key) is not None}
    details["materials"] = materials
    if styles:
        details["styles"] = styles
    if type(item.get("is_supply")) is bool:
        details["what_is"] = "1" if item["is_supply"] else "0"
    details["listing_type"] = "physical"
    attributes = {}
    rows = properties.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EtsyFailure("Etsy returned unreadable item properties.", 409)
    for prop in rows:
        name, values = prop.get("property_name"), prop.get("values")
        if isinstance(name, str) and isinstance(values, list) and all(isinstance(value, str) for value in values):
            attributes[name] = [html.unescape(value) for value in values]
    details["category_specifics"] = attributes
    by_name = {name.casefold(): values for name, values in attributes.items()}
    return {"title": html.unescape(item["title"]), "description": html.unescape(item.get("description") or ""),
            "price": price, "quantity": quantity, "sku": sku,
            "tags": tags, "material": ", ".join(materials),
            "brand": next(iter(by_name.get("brand") or []), None), "size": next(iter(by_name.get("size") or []), None),
            "marketplace_category_ids": {"etsy": str(item["taxonomy_id"])},
            "direct_category_ids": {"etsy": str(item["taxonomy_id"])}, "etsy_specifics": details}


async def import_listing(client: EtsyClient, db: Session, listing_id: str) -> dict:
    item = await client.listing(listing_id)
    if str(item.get("listing_id")) != listing_id or str(item.get("shop_id")) != client.shop_id:
        raise EtsyFailure("This listing does not belong to the connected Etsy shop.", 409)
    if item.get("state") != "active" or item.get("listing_type") != "physical" or item.get("has_variations") is not False:
        raise EtsyFailure("This import supports active physical Etsy listings without variations.", 409)
    if not isinstance(item.get("title"), str) or not item["title"].strip() or type(item.get("taxonomy_id")) is not int or item["taxonomy_id"] <= 0:
        raise EtsyFailure("Etsy did not return a title and category for this listing.", 409)
    sku, offering, quantity, price = single_offering(await client.inventory(listing_id), item)
    properties = await client.read(f"/listings/{listing_id}/properties")
    snapshot = _snapshot(item, sku, quantity, price, properties)
    if offering.get("readiness_state_id") is not None:
        snapshot["etsy_specifics"]["readiness_state_id"] = offering["readiness_state_id"]
    images = item.get("images")
    if not isinstance(images, list) or any(not isinstance(image, dict) for image in images):
        raise EtsyFailure("Etsy did not return the listing's photo records.", 409)
    ordered = sorted(enumerate(images), key=lambda pair: pair[1]["rank"] if type(pair[1].get("rank")) is int else pair[0])
    urls = [image["url_fullxfull"] for _, image in ordered if isinstance(image.get("url_fullxfull"), str)]
    try:
        return await inventory_import.save_owned_listing(
            db, "etsy", listing_id, snapshot, urls, client.assert_current, binding={"etsy_shop_id": client.shop_id},
        )
    except inventory_import.ImportConflict as exc:
        raise EtsyFailure(str(exc), 409) from exc
