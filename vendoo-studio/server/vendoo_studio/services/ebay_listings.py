"""Create a live eBay listing from the same fields Vendoo would write.

One seller-confirmed List uploads the photos, writes the inventory item and
offer, and publishes it. Nothing is queued or retried. A lost reply is checked
by reading the offer, not by creating another one.
"""
from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from vendoo_studio.config import PHOTOS_DIR
from vendoo_studio.models.ebay_fields import EBAY_CONDITION_DESCRIPTION
from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, segment
from vendoo_studio.services.marketplace_accounts import SignInRequired
from vendoo_studio.services.native_form import vendoo_form
from vendoo_studio.services.user_settings import get_package_dimensions
from vendoo_studio.services.vendoo_specifics import is_not_applicable

_SKIP = {
    "categoryid", "categorypath", "category_specifics", "categoryspecifics",
    "pricingformat", "pricingformatdetails", "conditiondescription",
    "shippingpolicyid", "paymentpolicyid", "returnspolicyid", "returnpolicyid",
    "shippingservice", "paymentmethod", "paypalemail", "acceptreturns",
    "returnpayedby", "returnwithin", "returnrefundmethod", "statusitem", "shipping",
}
_CONDITION = (
    (r"poor|major flaw", "USED_ACCEPTABLE"),
    (r"fair|acceptable", "USED_ACCEPTABLE"),
    (r"very good", "USED_VERY_GOOD"),
    (r"excellent|like new|nwot|new without", "USED_EXCELLENT"),
    (r"good|pre-owned|preowned|used", "USED_GOOD"),
    (r"new with tags|nwt|brand new|^new\b", "NEW"),
)


def ebay_condition(listing: dict) -> str:
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    text = str(specifics.get("condition") or listing.get("condition") or "").lower()
    for pattern, code in _CONDITION:
        if re.search(pattern, text) and not ("without" in text and code == "NEW"):
            return code
    return "USED_GOOD"


def ebay_sku(listing: dict, binding: dict) -> str:
    saved = str(binding.get("ebay_sku") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", saved):
        return saved
    raw = re.sub(r"[^A-Za-z0-9_-]", "", str(listing.get("sku") or ""))[:40]
    if raw:
        return raw
    digest = hashlib.sha256(f"{listing.get('title')}\n{listing.get('description')}".encode()).hexdigest()[:20]
    return "ar" + digest


def _aspect_name(key: str) -> str:
    if " " in key or (key[:1].isupper() and not re.search(r"[A-Z]", key[1:])):
        return key
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    return spaced[:1].upper() + spaced[1:]


def _aspect_values(value: Any) -> list[str]:
    if is_not_applicable(value):
        return ["Does Not Apply"]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def ebay_aspects(listing: dict, form: dict) -> dict[str, list[str]]:
    """Item specifics in eBay's aspect shape, including Does Not Apply."""
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    aspects: dict[str, list[str]] = {}

    def add(name: str, value: Any) -> None:
        values = _aspect_values(value)
        if name and values and name not in aspects:
            aspects[name] = values

    for key, value in specifics.items():
        if key.lower() in _SKIP or key == "category_specifics":
            continue
        add(_aspect_name(str(key)), value)
    nested = specifics.get("category_specifics")
    if isinstance(nested, dict):
        for key, value in nested.items():
            add(str(key), value)
    brand = (form.get("overrides") or {}).get("brand") or listing.get("brand")
    if brand and "Brand" not in aspects:
        add("Brand", brand)
    return aspects


def ebay_category_id(listing: dict) -> str:
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    category = (listing.get("marketplace_category_ids") or {}).get("ebay") or specifics.get("categoryId")
    text = str(category or "")
    if not re.fullmatch(r"\d{1,15}", text):
        raise ValueError("Choose an eBay category before listing.")
    return text


def package_ounces(listing: dict, form: dict) -> str:
    def total(source: dict) -> float:
        try:
            return float(source.get("weight_lb") or source.get("pounds") or 0) * 16 + float(source.get("weight_oz") or source.get("ounces") or 0)
        except (TypeError, ValueError):
            return 0

    ounces = total(listing)
    if ounces <= 0:
        ounces = total((form.get("overrides") or {}).get("weight") or {})
    if ounces <= 0:
        ounces = 16
    if ounces > 2400:
        raise ValueError("Enter a packaged weight of at most 2400 ounces.")
    return str(int(ounces)) if ounces == int(ounces) else f"{ounces:.2f}".rstrip("0").rstrip(".")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, (EbayFailure, ValueError, SignInRequired)):
        return str(exc)
    return "eBay did not confirm the listing. Check eBay before trying again."


async def _only(client: EbayClient, kind: str) -> str:
    body = await client.request("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": "EBAY_US"})
    policies = [policy for policy in body.get(f"{kind}Policies", []) if policy.get("marketplaceId") in (None, "EBAY_US")]
    if len(policies) != 1:
        names = ", ".join(str(policy.get("name") or "untitled") for policy in policies[:5]) or "none"
        label = {"fulfillment": "shipping", "payment": "payment", "return": "return"}[kind]
        raise ValueError(f"eBay needs exactly one {label} policy on this account. Found {len(policies)}: {names}.")
    return str(policies[0][f"{kind}PolicyId"])


async def _location(client: EbayClient) -> str:
    body = await client.request("GET", "/sell/inventory/v1/location", params={"limit": "20"})
    locations = [item for item in body.get("locations", []) if item.get("merchantLocationStatus") == "ENABLED"]
    if len(locations) != 1:
        names = ", ".join(str(item.get("name") or item.get("merchantLocationKey") or "untitled") for item in locations[:5]) or "none"
        raise ValueError(f"eBay needs exactly one enabled inventory location. Found {len(locations)}: {names}.")
    return str(locations[0]["merchantLocationKey"])


async def _complete_aspects(client: EbayClient, category_id: str, aspects: dict[str, list[str]]) -> dict[str, list[str]]:
    catalog = await client.request(
        "GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
        params={"category_id": category_id}, app=True,
    )
    completed = dict(aspects)
    for aspect in catalog.get("aspects", []):
        name = aspect.get("localizedAspectName") or ""
        allowed = [item.get("localizedValue") for item in aspect.get("aspectValues") or [] if item.get("localizedValue")]
        constraint = aspect.get("aspectConstraint") or {}
        values = completed.get(name) or []
        if constraint.get("aspectRequired") and not values and "Does Not Apply" in allowed:
            values = ["Does Not Apply"]
            completed[name] = values
        if constraint.get("aspectRequired") and not values:
            raise ValueError(f"eBay needs {name}.")
        if constraint.get("aspectMode") == "SELECTION_ONLY" and any(value not in allowed for value in values):
            raise ValueError(f"Choose a valid eBay value for {name}.")
    return completed


def _photo_upload(photo) -> dict:
    path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
    root = Path(PHOTOS_DIR).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("An eBay photo is missing from Anteroom. Restore it before listing.")
    if photo.mime_type not in {"image/jpeg", "image/png"}:
        raise ValueError("eBay photos need to be JPEG or PNG.")
    return {"field": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mime": photo.mime_type}


async def _image_url(client: EbayClient, photo) -> str:
    uploaded = await client.request("POST", "/commerce/media/v1_beta/image/create_image_from_file", upload=_photo_upload(photo))
    url = str(uploaded.get("imageUrl") or "")
    image_id = str(uploaded.get("imageId") or uploaded.get("fileId") or "")
    if not url and image_id:
        url = str((await client.request("GET", f"/commerce/media/v1_beta/image/{segment(image_id)}")).get("imageUrl") or "")
    parsed_ok = url.startswith("https://") and " " not in url and len(url) <= 2000
    if not parsed_ok:
        raise ValueError("eBay did not confirm a photo address. Check the listing before trying again.")
    return url


def _listing_url(listing_id: str) -> str:
    if not re.fullmatch(r"\d{6,20}", listing_id):
        raise ValueError("eBay did not confirm the listing ID. Check eBay before listing again.")
    return f"https://www.ebay.com/itm/{listing_id}"


def _listing_fields(snapshot: dict, form: dict) -> dict:
    title = str(snapshot.get("title") or "").strip()[:80]
    description = str(snapshot.get("description") or "").strip()[:4000]
    if not title or not description:
        raise ValueError("Add an eBay title and description before listing.")
    quantity = int(snapshot.get("quantity") or 1)
    if not 1 <= quantity <= 10000:
        raise ValueError("Enter an eBay quantity from 1 to 10000.")
    return {"title": title, "description": description, "quantity": quantity,
            "price": dollars(money_cents(str(snapshot.get("price")))), "category_id": ebay_category_id(snapshot),
            "condition": ebay_condition(snapshot),
            "condition_description": str((form.get("specifics") or {}).get("conditionDescription") or EBAY_CONDITION_DESCRIPTION)}


async def _revise_by_item_id(client: EbayClient, listing_id: str, fields: dict) -> dict:
    """A pasted eBay link has an item ID and no inventory offer. Revise that listing in place."""
    from xml.etree import ElementTree as ET

    from vendoo_studio.services.ebay_actions import owned_listing
    from vendoo_studio.services.ebay_client import NS

    before = await owned_listing(client, listing_id)
    if before["type"] != "FixedPriceItem":
        raise ValueError("Only a fixed-price eBay listing can be updated.")
    if fields["quantity"] < int(before.get("soldQuantity") or 0):
        raise ValueError("Quantity cannot be lower than the quantity already sold.")
    root = ET.Element("ReviseFixedPriceItemRequest", xmlns=NS["e"])
    item = ET.SubElement(root, "Item")
    for name, value in {"ItemID": listing_id, "Title": fields["title"], "Description": fields["description"],
                        "StartPrice": fields["price"], "Quantity": str(fields["quantity"])}.items():
        ET.SubElement(item, name).text = value
    await client.request("POST", "/ws/api.dll", xml=ET.tostring(root, encoding="unicode"), call="ReviseFixedPriceItem")
    after = await owned_listing(client, listing_id)
    if after["title"] != fields["title"] or str(after.get("price")) != fields["price"]:
        raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
    return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}


async def _update_live(snapshot: dict, photos: list, binding: dict, record: Callable[[dict], None],
                       active: Callable[[], bool]) -> dict:
    """Replace fields on the listing that is already live. Never publishes a second one."""
    attempted = False
    try:
        if not active():
            raise ValueError("The eBay update stopped. Check its result before trying again.")
        if not 1 <= len(photos) <= 12:
            raise ValueError("Choose between one and twelve eBay photos.")
        client = await EbayClient.connect()
        form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
        fields = _listing_fields(snapshot, form)
        listing_id = str(binding.get("listing_id") or binding.get("ebay_listing_id") or "")
        offer_id = str(binding.get("ebay_offer_id") or "")
        if not re.fullmatch(r"\d{6,20}", listing_id):
            raise ValueError("eBay has not confirmed which live listing to update.")
        if not offer_id:
            return await _revise_by_item_id(client, listing_id, fields)
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        current_id = str((offer.get("listing") or {}).get("listingId") or "")
        if offer.get("status") != "PUBLISHED" or current_id != listing_id:
            raise ValueError("This eBay offer is not the live listing. Check eBay before updating it.")
        aspects = await _complete_aspects(client, fields["category_id"], ebay_aspects(snapshot, form))
        sku = str(offer.get("sku") or ebay_sku(snapshot, binding))
        record({"ebay_sku": sku, "ebay_offer_id": offer_id, "ebay_listing_id": listing_id})
        image_urls = list(binding.get("ebay_image_urls") or [])
        if len(image_urls) != len(photos):
            image_urls = []
            for photo in photos:
                if not active():
                    raise ValueError("The eBay update stopped. Check its result before trying again.")
                attempted = True
                image_urls.append(await _image_url(client, photo))
                record({"ebay_image_urls": image_urls})
        weight = Decimal(package_ounces(snapshot, form))
        package = get_package_dimensions()
        attempted = True
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}", body={
            "condition": fields["condition"],
            "conditionDescription": fields["condition_description"],
            "availability": {"shipToLocationAvailability": {"quantity": fields["quantity"]}},
            "product": {"title": fields["title"], "description": fields["description"], "imageUrls": image_urls, "aspects": aspects},
            "packageWeightAndSize": {
                "weight": {"value": format(weight, "f"), "unit": "OUNCE"},
                "dimensions": {**package, "unit": "INCH"},
            },
        })
        policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        await client.request("PUT", f"/sell/inventory/v1/offer/{segment(offer_id)}", body={
            "sku": sku, "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
            "categoryId": fields["category_id"], "availableQuantity": fields["quantity"],
            "pricingSummary": {"price": {"value": fields["price"], "currency": "USD"}},
            "listingDescription": fields["description"],
            "merchantLocationKey": offer.get("merchantLocationKey"),
            "listingPolicies": policies,
        })
        confirmed = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        confirmed_id = str((confirmed.get("listing") or {}).get("listingId") or "")
        if confirmed.get("status") != "PUBLISHED" or confirmed_id != listing_id:
            raise ValueError("eBay has not confirmed the update on the same listing. Check eBay before trying again.")
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    except Exception as exc:
        client_rejected = isinstance(exc, EbayFailure) and exc.status in {400, 401, 403, 422}
        safe = not attempted or client_rejected or isinstance(exc, (ValueError, SignInRequired))
        return {"ok": False, "safe_to_retry": safe and not attempted, "error": _error_text(exc)}


async def execute(action: str, snapshot: dict, photos: list, binding: dict,
                  record: Callable[[dict], None], active: Callable[[], bool], *, confirm: bool = False) -> dict:
    if action == "update":
        if confirm is not True:
            return {"ok": False, "safe_to_retry": True, "error": "Confirm updating the live eBay listing."}
        return await _update_live(snapshot, photos, binding, record, active)
    attempted = False
    try:
        if action != "list" or confirm is not True:
            raise ValueError("Confirm listing on eBay.")
        if not active():
            raise ValueError("The eBay listing stopped. Check its result before trying again.")
        if not 1 <= len(photos) <= 12:
            raise ValueError("Choose between one and twelve eBay photos.")
        client = await EbayClient.connect()
        form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
        fields = _listing_fields(snapshot, form)
        title, description = fields["title"], fields["description"]
        category_id, price, quantity = fields["category_id"], fields["price"], fields["quantity"]
        aspects = await _complete_aspects(client, category_id, ebay_aspects(snapshot, form))
        fulfillment, payment, returns, location = await _only(client, "fulfillment"), await _only(client, "payment"), await _only(client, "return"), await _location(client)
        sku = ebay_sku(snapshot, binding)
        record({"ebay_sku": sku})
        image_urls = list(binding.get("ebay_image_urls") or [])
        if len(image_urls) != len(photos):
            image_urls = []
            for photo in photos:
                if not active():
                    raise ValueError("The eBay listing stopped. Check its result before trying again.")
                attempted = True
                image_urls.append(await _image_url(client, photo))
                record({"ebay_image_urls": image_urls})
        weight = Decimal(package_ounces(snapshot, form))
        condition = fields["condition"]
        condition_description = fields["condition_description"]
        package = get_package_dimensions()
        attempted = True
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}", body={
            "condition": condition,
            "conditionDescription": condition_description,
            "availability": {"shipToLocationAvailability": {"quantity": quantity}},
            "product": {"title": title, "description": description, "imageUrls": image_urls, "aspects": aspects},
            "packageWeightAndSize": {
                "weight": {"value": format(weight, "f"), "unit": "OUNCE"},
                "dimensions": {**package, "unit": "INCH"},
            },
        })
        record({"ebay_inventory_written": True})
        offer_id = str(binding.get("ebay_offer_id") or "")
        if not offer_id:
            offer = await client.request("POST", "/sell/inventory/v1/offer", body={
                "sku": sku, "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
                "includeCatalogProductDetails": False, "categoryId": category_id,
                "availableQuantity": quantity,
                "pricingSummary": {"price": {"value": price, "currency": "USD"}},
                "listingDescription": description,
                "merchantLocationKey": location,
                "listingPolicies": {
                    "fulfillmentPolicyId": fulfillment, "paymentPolicyId": payment, "returnPolicyId": returns,
                    "bestOfferTerms": {"bestOfferEnabled": True},
                },
            })
            offer_id = str(offer.get("offerId") or "")
            if not offer_id:
                raise ValueError("eBay did not confirm the offer. Check eBay before listing again.")
            record({"ebay_offer_id": offer_id})
        published = await client.request("POST", f"/sell/inventory/v1/offer/{segment(offer_id)}/publish", body={})
        listing_id = str(published.get("listingId") or "")
        url = _listing_url(listing_id)
        record({"ebay_listing_id": listing_id})
        return {"ok": True, "state": "published", "listing_url": url}
    except Exception as exc:
        written = bool(binding.get("ebay_inventory_written") or binding.get("ebay_offer_id"))
        client_rejected = isinstance(exc, EbayFailure) and exc.status in {400, 401, 403, 422}
        # Once the inventory item or offer exists, another List would duplicate it.
        safe = not written and (not attempted or client_rejected or isinstance(exc, (ValueError, SignInRequired)))
        return {"ok": False, "safe_to_retry": safe, "error": _error_text(exc)}


async def check(binding: dict, snapshot: dict | None = None) -> dict:
    try:
        offer_id = str(binding.get("ebay_offer_id") or "")
        if not offer_id:
            raise ValueError("eBay has not confirmed an offer for this listing. Check eBay before listing again.")
        client = await EbayClient.connect()
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        listing_id = str((offer.get("listing") or {}).get("listingId") or binding.get("ebay_listing_id") or "")
        if binding.get("action") == "update" and snapshot:
            sku = str(offer.get("sku") or binding.get("ebay_sku") or "")
            item = await client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(sku)}") if sku else {}
            product = item.get("product") if isinstance(item.get("product"), dict) else {}
            price = str((offer.get("pricingSummary") or {}).get("price", {}).get("value") or "")
            expected = dollars(money_cents(str(snapshot.get("price"))))
            title = str(snapshot.get("title") or "").strip()[:80]
            if product.get("title") != title or price != expected:
                raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
        if offer.get("status") == "PUBLISHED" and listing_id:
            return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
        raise ValueError("eBay has not confirmed this listing is live. Check eBay before listing again.")
    except Exception as exc:
        return {"ok": False, "safe_to_retry": False, "error": _error_text(exc)}
