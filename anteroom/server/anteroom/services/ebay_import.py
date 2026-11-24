"""Read a seller's existing eBay listings into local inventory. Never write to eBay."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import xml.etree.ElementTree as ET

import httpx

from sqlalchemy.orm import Session

from anteroom.database import in_worker_session
from anteroom.models.schema import ListingSchema
from anteroom.services import ebay_actions, inventory_import
from anteroom.services.ebay_client import EbayClient, EbayFailure, NS, assert_identity
from anteroom.services.ebay_workspace import active_listing_page

PAGE_SIZE = 25


def _number(value: str | None) -> int | None:
    try:
        number = int(value) if value is not None else None
        return number if number is not None and number >= 0 else None
    except (ValueError, TypeError):
        return None


async def active_listings(client: EbayClient, page_number: int) -> dict:
    active = await active_listing_page(client, page_number, PAGE_SIZE)
    if active is None:
        return {"rows": [], "page": page_number, "total": 0, "has_more": False}
    # eBay has answered: match its rows against inventory with a short session of their own.
    return await in_worker_session(_candidate_page, active, page_number)


def _candidate_page(db: Session, active: ET.Element, page_number: int) -> dict:
    from anteroom.services.chrome_import import match_candidates, suggested_match

    items = match_candidates(db)
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
        title = item.findtext("e:Title", namespaces=NS) or identifier
        usd = price.text if price is not None and price.get("currencyID") == "USD" else None
        # An existing item can be linked to even a listing this import cannot copy.
        match = None if existing or "more than one" in (reason or "") else suggested_match(db, "ebay", identifier, title, _float(usd), items)
        rows.append({"id": identifier, "title": title, "price": usd,
                     "quantity": max(0, total - sold) if total is not None and sold is not None else None,
                     "reason": reason, "conversation_id": existing.id if existing else None, "match": match})
    pages = _number(active.findtext("e:PaginationResult/e:TotalNumberOfPages", namespaces=NS))
    total = _number(active.findtext("e:PaginationResult/e:TotalNumberOfEntries", namespaces=NS))
    return {"rows": rows, "page": page_number, "total": total,
            "has_more": page_number < pages if pages is not None else len(rows) == PAGE_SIZE}


def _float(value: str | None) -> float | None:
    try:
        number = float(value) if value else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def _seller_sku(value) -> str | None:
    """A SKU the seller typed on eBay. Anteroom's inventory key is not one."""
    from anteroom.services.ebay_listings import generated_inventory_key

    text = str(value or "").strip()
    if not text or generated_inventory_key(text):
        return None
    return text


def _decimal_text(value) -> str:
    """A positive number as eBay wrote it, without trailing zeros ("10.50" -> "10.5"), else ""."""
    try:
        number = Decimal(str(value or "").strip())
    except InvalidOperation:
        return ""
    if not number.is_finite() or number <= 0:
        return ""
    return format(number.normalize(), "f")


def _package(package: dict) -> dict:
    """Package weight (lbs. and oz.) and dimensions (in.), as Item Details keeps them."""
    out: dict = {}
    pounds, ounces = _number(package.get("WeightMajor")), _number(package.get("WeightMinor"))
    if (pounds or 0) + (ounces or 0) > 0:
        out.update(weight_lb=pounds or 0, weight_oz=ounces or 0)
    sides = [_decimal_text(package.get(name)) for name in ("PackageLength", "PackageWidth", "PackageDepth")]
    if all(sides):
        out["package_dimensions_in"] = "x".join(sides)
    return out


def _best_offer(offer: dict) -> dict | None:
    """Best Offer exactly as the listing has it: on with its own prices (blank when it has none), or off."""
    enabled = str(offer.get("enabled") or "").strip().casefold()
    if enabled not in {"true", "false"}:
        return None
    if enabled == "false":
        return {"enabled": False}
    return {"enabled": True, "autoAcceptPrice": str(offer.get("autoAcceptPrice") or "").strip(),
            "minimumOfferPrice": str(offer.get("minimumOfferPrice") or "").strip()}


def _listing_settings(info: dict) -> dict:
    """The rest of eBay's listing form, under the ``ebay_specifics`` keys the eBay tab and List/Update read."""
    from anteroom.services.marketplace_schemas import _ebay_listing_type, ebay_duration

    out: dict = {key: value for key, value in (info.get("policies") or {}).items() if str(value or "").strip()}
    if info.get("merchantLocationKey"):
        out["merchantLocationKey"] = info["merchantLocationKey"]
    best_offer = _best_offer(info.get("bestOffer") or {})
    if best_offer is not None:
        out["bestOffer"] = best_offer
    if kind := _ebay_listing_type(info.get("type")):
        out["listingFormat"] = kind
    if duration := ebay_duration(info.get("duration")):
        out["listingDuration"] = duration
    for key in ("subtitle", "upc", "charityId", "donationPercent"):
        if str(info.get(key) or "").strip():
            out[key] = str(info[key]).strip()
    if (lot := _number(info.get("lotSize"))) and lot > 1:
        out["lotSize"] = str(lot)
    if str(info.get("privateListing") or "").casefold() == "true":
        out["privateListing"] = True
    if str((info.get("package") or {}).get("ShippingIrregular") or "").casefold() == "true":
        out["irregularPackage"] = True
    if second := str(info.get("secondaryCategoryId") or "").strip():
        out["secondaryCategoryId"] = second
        if name := str(info.get("secondaryCategoryName") or "").strip():
            out["secondaryCategoryPath"] = name.replace(":", " > ")
    if info.get("conditionDescription"):
        out["conditionDescription"] = info["conditionDescription"]
    # ``videoId`` names a local ListingVideo. eBay's video stays where it is: an unset choice leaves it on Update.
    return out


def listing_snapshot(info: dict) -> dict:
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
    category_id = str(info.get("categoryId") or "").strip()
    # A category eBay did not name is left unset for the seller to choose, never stored as "".
    categories: dict = {"category_path": category} if category else {}
    if category_id:
        categories.update(marketplace_category_ids={"ebay": category_id},
                          direct_category_ids={"ebay": category_id, **({"ebay_path": category} if category else {})})
    if category:
        categories["marketplace_categories"] = {"ebay": category}
    general = {key: value for key, value in (("primaryColor", first("color")), ("department", first("department")),
                                              ("sizeType", first("size type"))) if value}
    return {"title": info["title"], "description": info["description"], "price": float(price), "quantity": total - sold,
            "brand": first("brand"), "size": first("size"), "sku": _seller_sku(info.get("sku")),
            "condition": ListingSchema.validate_condition(info.get("condition") or None),
            **general, **categories, **_package(info.get("package") or {}),
            "ebay_specifics": {**specifics, **_listing_settings(info)}}


async def _read_inventory(client: EbayClient, path: str, params: dict) -> dict:
    """A read the import can do without: an Inventory API answer, or {} when eBay has none for this listing."""
    try:
        body = await client.request("GET", path, params=params)
    except (EbayFailure, httpx.HTTPError):
        return {}
    return body if isinstance(body, dict) else {}


async def _inventory_details(client: EbayClient, info: dict) -> dict:
    """Category and item location GetItem leaves out. Read only.

    A listing made through eBay's Inventory API has an offer that names both. Otherwise the
    item location is the seller's one inventory location at the listing's ZIP code.
    """
    found: dict = {}
    sku = str(info.get("sku") or "").strip()
    if sku:
        body = await _read_inventory(client, "/sell/inventory/v1/offer", {"sku": sku, "marketplace_id": "EBAY_US"})
        offer = next((offer for offer in body.get("offers") or [] if isinstance(offer, dict)
                      and str((offer.get("listing") or {}).get("listingId") or "") == info["id"]), {})
        found = {key: str(offer[key]) for key in ("categoryId", "merchantLocationKey") if str(offer.get(key) or "").strip()}
    postal = str(info.get("postalCode") or "").strip()
    if not found.get("merchantLocationKey") and postal:
        body = await _read_inventory(client, "/sell/inventory/v1/location", {"limit": "100"})
        keys = [str(location["merchantLocationKey"]) for location in body.get("locations") or []
                if isinstance(location, dict) and location.get("merchantLocationKey")
                and str(location.get("merchantLocationStatus") or "ENABLED").upper() == "ENABLED"
                and str(((location.get("location") or {}).get("address") or {}).get("postalCode") or "").strip() == postal]
        if len(keys) == 1:
            found["merchantLocationKey"] = keys[0]
    return found


NO_CATEGORY = "eBay did not say which category this listing is in. Choose it on the eBay tab before updating the listing."


async def import_listing(client: EbayClient, listing_id: str, *, unless_matched: bool = False) -> dict:
    info = await ebay_actions.owned_listing(client, listing_id)
    if info.get("id") != listing_id:
        raise EbayFailure("eBay returned a different listing. Nothing was imported.", 409)
    listing_snapshot(info)  # Refuse an unsupported listing before reading anything else.
    found = await _inventory_details(client, info)
    if not str(info.get("categoryId") or "").strip() and found.get("categoryId"):
        info = {**info, "categoryId": found["categoryId"]}
    if found.get("merchantLocationKey"):
        info = {**info, "merchantLocationKey": found["merchantLocationKey"]}
    snapshot = listing_snapshot(info)
    try:
        result = await inventory_import.save_owned_listing(
            "ebay", listing_id, snapshot, info.get("images", []), lambda: assert_identity(client.seller),
            unless_matched=unless_matched,
        )
    except inventory_import.ImportConflict as exc:
        raise EbayFailure(str(exc), 409) from exc
    if "ebay" not in snapshot.get("direct_category_ids", {}) and result.get("conversation_id") \
            and not result.get("reused") and isinstance(result.get("warnings"), list):
        result["warnings"].append(NO_CATEGORY)
    return result


async def link_listing(client: EbayClient, listing_id: str, conversation_id: str) -> dict:
    """Link an active eBay listing to an item the seller chose. Reads eBay only; the item is kept as it is."""
    from anteroom.services.keychain import get_marketplace_accounts

    async def verify() -> None:
        info = await ebay_actions.listing(client, listing_id)
        account = get_marketplace_accounts().get("ebay") or {}
        if info.get("id") != listing_id or not account.get("label") or str(info.get("seller") or "").casefold() != str(account["label"]).casefold():
            raise EbayFailure("This listing does not belong to the connected seller.", 409)
        if info.get("status") != "Active":
            raise EbayFailure("This eBay listing is not active.", 409)
        assert_identity(client.seller)

    try:
        return await inventory_import.link_owned_listing("ebay", listing_id, conversation_id, verify)
    except inventory_import.LinkError as exc:
        raise EbayFailure(str(exc), exc.status_code) from exc
