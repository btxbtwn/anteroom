"""Create a live eBay listing from the same fields Vendoo would write.

One seller-confirmed List uploads the photos, writes the inventory item and
offer, and publishes it. Nothing is retried automatically. The SKU is the
item's key on eBay: every List of the same item writes the same inventory item
(an idempotent PUT) and reuses its one offer, found by SKU when a reply was
lost, so a retry finishes the listing instead of making a second one. Check
only reads.
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
import httpx

from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, segment
from vendoo_studio.services.listing_result import ListingProblem, failure, missing
from vendoo_studio.services.marketplace_accounts import SignInRequired
from vendoo_studio.services.relay_client import RelayError
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


def item_key(conversation_id: str) -> str:
    """The SKU Anteroom gives an item that has none of its own: unique to the item, stable across retries."""
    return "ar-" + re.sub(r"[^A-Za-z0-9]", "", conversation_id)[:40]


def ebay_sku(listing: dict, binding: dict, conversation_id: str) -> str:
    """The seller's SKU when they gave one, else the item's own key. Once written, it never changes."""
    saved = str(binding.get("ebay_sku") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", saved):
        return saved
    raw = re.sub(r"[^A-Za-z0-9_-]", "", str(listing.get("sku") or ""))[:40]
    return raw or item_key(conversation_id)


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


# eBay aspects the listing's general fields already answer, when its eBay
# specifics leave them empty (the JSON contract's top-level keys).
_GENERAL_ASPECTS = {
    "Brand": ("brand",), "Color": ("primaryColor", "color"), "Size": ("size", "size_us"), "Size Type": ("sizeType",),
    "Department": ("department", "gender"), "Material": ("material",), "Style": ("style",), "Pattern": ("pattern",),
    "Type": ("type",),
}


def _aspect_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def ebay_aspects(listing: dict, form: dict) -> dict[str, list[str]]:
    """Item specifics in eBay's aspect shape, including Does Not Apply."""
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    aspects: dict[str, list[str]] = {}

    def add(name: str, value: Any) -> None:
        values = _aspect_values(value)
        if name and values and _aspect_key(name) not in {_aspect_key(known) for known in aspects}:
            aspects[name] = values

    for key, value in specifics.items():
        if key.lower() in _SKIP or key == "category_specifics":
            continue
        add(_aspect_name(str(key)), value)
    nested = specifics.get("category_specifics")
    if isinstance(nested, dict):
        for key, value in nested.items():
            add(str(key), value)
    add("Brand", (form.get("overrides") or {}).get("brand"))
    for name, keys in _GENERAL_ASPECTS.items():
        add(name, next((listing.get(key) for key in keys if str(listing.get(key) or "").strip()), None))
    return aspects


def ebay_category_id(listing: dict) -> str:
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    category = (listing.get("marketplace_category_ids") or {}).get("ebay") or specifics.get("categoryId")
    text = str(category or "")
    if not re.fullmatch(r"\d{1,15}", text):
        raise missing("Choose an eBay category before listing.", "marketplace_category_ids.ebay")
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
    if isinstance(exc, (EbayFailure, ValueError, SignInRequired, RelayError)):
        return str(exc)
    return "eBay did not confirm the listing. Check eBay before trying again."


# Writes that a lost reply leaves uncertain. Image uploads and the PUTs of the
# inventory item and offer are safe to repeat, so they are not listed.
_UNREPEATABLE = frozenset({"offer", "publish"})


def _failure(exc: Exception, pending: str | None) -> dict:
    """The contract result for an exception, given the write in flight when it happened."""
    said = getattr(exc, "marketplace_message", None)
    if isinstance(exc, ListingProblem):
        return failure(str(exc), exc.code, safe=True, fields=exc.fields, marketplace_message=said)
    if isinstance(exc, SignInRequired):
        return failure("Reconnect eBay in Settings → Connections, then List again.", "signed_out", safe=True)
    if isinstance(exc, RelayError):
        return failure(str(exc), "network", safe=True)
    if isinstance(exc, httpx.ConnectError):
        # The request never reached eBay.
        return failure("Anteroom could not reach eBay. Check the connection and List again.", "network", safe=True)
    if isinstance(exc, EbayFailure) and exc.code:
        # A definite refusal: eBay answered and did not run the call.
        return failure(str(exc), exc.code, safe=True, retry_after_s=exc.retry_after_s, marketplace_message=said)
    if isinstance(exc, ValueError) and pending is None:
        return failure(str(exc), "rejected", safe=True)
    if pending not in _UNREPEATABLE:
        # Nothing unrepeatable was in flight: a retry rewrites the same SKU and offer.
        code = "network" if isinstance(exc, httpx.TransportError) else "rejected"
        return failure(_error_text(exc), code, safe=True, marketplace_message=said)
    return failure(_error_text(exc), "uncertain", safe=False, marketplace_message=said)


async def _not_found(call) -> dict | None:
    try:
        return await call
    except EbayFailure as exc:
        # 25710/25713: eBay has no such inventory item or offer.
        if exc.status == 404 or re.search(r"\b2571[03]\b", str(exc)):
            return None
        raise


async def _offer_for(client: EbayClient, sku: str, binding: dict) -> dict | None:
    """This item's one EBAY_US offer: the saved one, or the one eBay has for its SKU."""
    offer_id = str(binding.get("ebay_offer_id") or "")
    if offer_id:
        return await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
    if not binding.get("ebay_inventory_written"):
        # An offer needs the inventory item, which this item has never written.
        return None
    found = await _not_found(client.request("GET", "/sell/inventory/v1/offer",
                                            params={"sku": sku, "marketplace_id": "EBAY_US", "format": "FIXED_PRICE"}))
    offers = [offer for offer in (found or {}).get("offers") or []
              if offer.get("marketplaceId") in (None, "EBAY_US") and offer.get("offerId")]
    if len(offers) > 1:
        raise ValueError(f"eBay has {len(offers)} offers for SKU {sku}. Remove the extra ones in eBay, then List again.")
    return offers[0] if offers else None


def resumable(error: str, **extra) -> dict:
    """Nothing went wrong, but the listing is not live yet: the next List finishes it."""
    return {"ok": False, "safe_to_retry": True, "error": error, **extra}


def _published(offer: dict, record: Callable[[dict], None]) -> dict | None:
    listing_id = str((offer.get("listing") or {}).get("listingId") or "")
    if offer.get("status") == "PUBLISHED" and listing_id:
        record({"ebay_listing_id": listing_id})
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    return None


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
    """The listing's aspects under eBay's own names and values for this category.

    Names and choices match eBay's case-insensitively. Every required aspect
    still missing is reported at once, as fields the seller (or chat) can fill.
    """
    catalog = await client.request(
        "GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
        params={"category_id": category_id}, app=True,
    )
    completed = dict(aspects)
    problems: list[tuple[str, str]] = []
    for aspect in catalog.get("aspects", []):
        name = aspect.get("localizedAspectName") or ""
        if not name:
            continue
        allowed = [item.get("localizedValue") for item in aspect.get("aspectValues") or [] if item.get("localizedValue")]
        constraint = aspect.get("aspectConstraint") or {}
        required = bool(constraint.get("aspectRequired"))
        same = next((key for key in completed if key != name and _aspect_key(key) == _aspect_key(name)), None)
        if same and not completed.get(name):
            completed[name] = completed.pop(same)
        values = completed.get(name) or []
        if constraint.get("aspectMode") == "SELECTION_ONLY" and values:
            choices = {str(choice).casefold(): choice for choice in allowed}
            matched = [choices.get(str(value).casefold()) for value in values]
            if all(matched):
                values = matched
            elif required:
                problems.append((name, f"Choose a valid eBay value for {name}."))
                continue
            else:
                # eBay would refuse the whole listing over an optional value it does not offer.
                values = [value for value in matched if value]
            if values:
                completed[name] = values
            else:
                completed.pop(name, None)
        if required and not values and "Does Not Apply" in allowed:
            values = completed[name] = ["Does Not Apply"]
        if required and not values:
            problems.append((name, f"eBay needs {name}."))
    if problems:
        raise ListingProblem(" ".join(message for _, message in problems), "missing_fields",
                             fields=[{"field": f"ebay_specifics.category_specifics.{name}", "message": message}
                                     for name, message in problems])
    return completed


def _photo_upload(photo) -> dict:
    path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
    root = Path(PHOTOS_DIR).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise missing("An eBay photo is missing from Anteroom. Restore it before listing.", "photos")
    if photo.mime_type not in {"image/jpeg", "image/png"}:
        raise missing("eBay photos need to be JPEG or PNG.", "photos")
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


def _photo_checksum(photo) -> str:
    path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
    if not path.is_relative_to(Path(PHOTOS_DIR).resolve()) or not path.is_file():
        raise missing("An eBay photo is missing from Anteroom. Restore it before listing.", "photos")
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _image_urls(client: EbayClient, photos: list, binding: dict, record: Callable[[dict], None],
                      active: Callable[[], bool]) -> list[str]:
    """eBay picture URLs in listing order. A photo already on eBay is reused only when it is the same file."""
    uploaded = {(entry.get("photo_id"), entry.get("checksum")): entry.get("url")
                for entry in binding.get("ebay_images") or [] if isinstance(entry, dict) and entry.get("url")}
    images: list[dict] = []
    for photo in photos:
        checksum = _photo_checksum(photo)
        url = uploaded.get((photo.id, checksum))
        if not url:
            if not active():
                raise ValueError("The eBay listing stopped before it finished. List again to continue.")
            url = await _image_url(client, photo)
        images.append({"photo_id": photo.id, "checksum": checksum, "url": url})
        # Keep earlier uploads too, so a retry after a failure does not upload them again.
        known = [entry for entry in binding.get("ebay_images") or [] if entry.get("photo_id") not in {i["photo_id"] for i in images}]
        record({"ebay_images": images + known})
    record({"ebay_images": images})
    return [image["url"] for image in images]


def _listing_url(listing_id: str) -> str:
    if not re.fullmatch(r"\d{6,20}", listing_id):
        raise ValueError("eBay did not confirm the listing ID. Check eBay before listing again.")
    return f"https://www.ebay.com/itm/{listing_id}"


def _listing_fields(snapshot: dict, form: dict) -> dict:
    title = str(snapshot.get("title") or "").strip()[:80]
    description = str(snapshot.get("description") or "").strip()[:4000]
    if not title or not description:
        raise missing("Add an eBay title and description before listing.", *[name for name, value in (("title", title), ("description", description)) if not value])
    quantity = int(snapshot.get("quantity") or 1)
    if not 1 <= quantity <= 10000:
        raise missing("Enter an eBay quantity from 1 to 10000.", "quantity")
    return {"title": title, "description": description, "quantity": quantity,
            "price": dollars(money_cents(str(snapshot.get("price")))), "category_id": ebay_category_id(snapshot),
            "condition": ebay_condition(snapshot),
            "condition_description": str((form.get("specifics") or {}).get("conditionDescription") or EBAY_CONDITION_DESCRIPTION)}


async def _revise_by_item_id(client: EbayClient, listing_id: str, fields: dict) -> dict:
    """A pasted eBay link has an item ID and no inventory offer. Revise that listing in place."""
    from xml.etree import ElementTree as ET

    from vendoo_studio.services.ebay_actions import owned_listing
    from vendoo_studio.services.ebay_client import NS, trading_xml

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
    await client.request("POST", "/ws/api.dll", xml=trading_xml(root), call="ReviseFixedPriceItem")
    after = await owned_listing(client, listing_id)
    if after["title"] != fields["title"] or str(after.get("price")) != fields["price"]:
        raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
    return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}


def _inventory_body(fields: dict, aspects: dict, image_urls: list[str], snapshot: dict, form: dict) -> dict:
    weight = Decimal(package_ounces(snapshot, form))
    return {
        "condition": fields["condition"],
        "conditionDescription": fields["condition_description"],
        "availability": {"shipToLocationAvailability": {"quantity": fields["quantity"]}},
        "product": {"title": fields["title"], "description": fields["description"], "imageUrls": image_urls, "aspects": aspects},
        "packageWeightAndSize": {
            "weight": {"value": format(weight, "f"), "unit": "OUNCE"},
            "dimensions": {**get_package_dimensions(), "unit": "INCH"},
        },
    }


def _offer_body(sku: str, fields: dict, location: str | None, policies: dict) -> dict:
    return {
        "sku": sku, "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
        "includeCatalogProductDetails": False, "categoryId": fields["category_id"],
        "availableQuantity": fields["quantity"],
        "pricingSummary": {"price": {"value": fields["price"], "currency": "USD"}},
        "listingDescription": fields["description"],
        "merchantLocationKey": location,
        "listingPolicies": policies,
    }


async def _update_live(snapshot: dict, photos: list, binding: dict, record: Callable[[dict], None],
                       active: Callable[[], bool]) -> dict:
    """Replace fields on the listing that is already live. Never publishes a second one.

    Every write here is a replace, so a lost reply is safe to send again.
    """
    try:
        if not active():
            raise ValueError("The eBay update stopped. Check its result before trying again.")
        if not 1 <= len(photos) <= 12:
            raise missing("Choose between one and twelve eBay photos.", "photos")
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
        sku = str(offer.get("sku") or binding.get("ebay_sku") or "")
        if not sku:
            raise ValueError("eBay did not return this listing's SKU. Check eBay before updating it.")
        record({"ebay_sku": sku, "ebay_offer_id": offer_id, "ebay_listing_id": listing_id})
        image_urls = await _image_urls(client, photos, binding, record, active)
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}",
                             body=_inventory_body(fields, aspects, image_urls, snapshot, form))
        policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        body = _offer_body(sku, fields, offer.get("merchantLocationKey"), policies)
        body.pop("includeCatalogProductDetails")
        await client.request("PUT", f"/sell/inventory/v1/offer/{segment(offer_id)}", body=body)
        confirmed = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        confirmed_id = str((confirmed.get("listing") or {}).get("listingId") or "")
        if confirmed.get("status") != "PUBLISHED" or confirmed_id != listing_id:
            raise ValueError("eBay has not confirmed the update on the same listing. Check eBay before trying again.")
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    except Exception as exc:
        return _failure(exc, None)


async def execute(action: str, snapshot: dict, photos: list, binding: dict,
                  record: Callable[[dict], None], active: Callable[[], bool], *, confirm: bool = False,
                  conversation_id: str = "") -> dict:
    if action == "update":
        if confirm is not True:
            return failure("Confirm updating the live eBay listing.", "rejected", safe=True)
        return await _update_live(snapshot, photos, binding, record, active)
    pending: str | None = None
    try:
        if action != "list" or confirm is not True:
            raise ValueError("Confirm listing on eBay.")
        if not active():
            raise ValueError("The eBay listing stopped before it started.")
        if not 1 <= len(photos) <= 12:
            raise missing("Choose between one and twelve eBay photos.", "photos")
        client = await EbayClient.connect()
        form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
        fields = _listing_fields(snapshot, form)
        aspects = await _complete_aspects(client, fields["category_id"], ebay_aspects(snapshot, form))
        fulfillment, payment, returns, location = await _only(client, "fulfillment"), await _only(client, "payment"), await _only(client, "return"), await _location(client)
        sku = ebay_sku(snapshot, binding, conversation_id)
        if not binding.get("ebay_inventory_written") and sku != item_key(conversation_id):
            # The seller's own SKU may already name another item on eBay. Never overwrite it.
            if await _not_found(client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(sku)}")) is not None:
                raise missing(f"eBay already has an item with SKU {sku}. Give this item its own SKU, then List again.", "sku")
        offer = await _offer_for(client, sku, binding)
        if offer:
            record({"ebay_sku": sku, "ebay_offer_id": str(offer.get("offerId") or binding["ebay_offer_id"])})
            live = _published(offer, record)
            if live:
                return live
        image_urls = await _image_urls(client, photos, binding, record, active)
        # From here eBay may hold an inventory item under this SKU, and it is this item's.
        record({"ebay_sku": sku, "ebay_inventory_written": True})
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}",
                             body=_inventory_body(fields, aspects, image_urls, snapshot, form))
        body = _offer_body(sku, fields, location, {
            "fulfillmentPolicyId": fulfillment, "paymentPolicyId": payment, "returnPolicyId": returns,
            "bestOfferTerms": {"bestOfferEnabled": True},
        })
        offer_id = str(binding.get("ebay_offer_id") or "")
        if offer_id:
            # The offer from an earlier List: bring it up to date, then publish it.
            await client.request("PUT", f"/sell/inventory/v1/offer/{segment(offer_id)}", body=body)
        else:
            pending = "offer"
            created = await client.request("POST", "/sell/inventory/v1/offer", body=body)
            offer_id = str(created.get("offerId") or "")
            if not offer_id:
                raise EbayFailure("eBay did not confirm the offer. Check eBay before listing again.")
            record({"ebay_offer_id": offer_id})
            pending = None
        if not active():
            raise ValueError("The eBay listing stopped before it was published. List again to publish it.")
        pending = "publish"
        published = await client.request("POST", f"/sell/inventory/v1/offer/{segment(offer_id)}/publish", body={})
        listing_id = str(published.get("listingId") or "")
        url = _listing_url(listing_id)
        record({"ebay_listing_id": listing_id})
        return {"ok": True, "state": "published", "listing_url": url}
    except Exception as exc:
        return _failure(exc, pending)


async def check(binding: dict, snapshot: dict | None = None, record: Callable[[dict], None] | None = None) -> dict:
    """Read what eBay has for this item. Never writes."""
    record = record or (lambda values: None)
    try:
        sku = str(binding.get("ebay_sku") or "")
        if not sku:
            # The SKU is saved before anything is written.
            return resumable("Nothing reached eBay. List again.")
        client = await EbayClient.connect()
        if binding.get("action") == "update" and snapshot:
            return await _check_update(client, binding, snapshot)
        offer = await _offer_for(client, sku, binding)
        if offer:
            record({"ebay_offer_id": str(offer.get("offerId") or binding.get("ebay_offer_id"))})
            live = _published(offer, record)
            if live:
                return live
            return resumable("eBay has this listing saved but not live. List again to publish it.")
        return resumable("eBay has no listing for this item. List again.")
    except Exception as exc:
        result = _failure(exc, "check")
        return {**result, "safe_to_retry": False, "error_code": "uncertain" if result["error_code"] != "signed_out" else "signed_out"}


async def _check_update(client: EbayClient, binding: dict, snapshot: dict) -> dict:
    title = str(snapshot.get("title") or "").strip()[:80]
    expected = dollars(money_cents(str(snapshot.get("price"))))
    offer_id = str(binding.get("ebay_offer_id") or "")
    listing_id = str(binding.get("listing_id") or binding.get("ebay_listing_id") or "")
    if not offer_id:
        from vendoo_studio.services.ebay_actions import owned_listing

        live = await owned_listing(client, listing_id)
        if live["title"] != title or str(live.get("price")) != expected:
            raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
    sku = str(offer.get("sku") or binding.get("ebay_sku") or "")
    item = await client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(sku)}") if sku else {}
    product = item.get("product") if isinstance(item.get("product"), dict) else {}
    price = str((offer.get("pricingSummary") or {}).get("price", {}).get("value") or "")
    if product.get("title") != title or price != expected:
        raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
    listing_id = str((offer.get("listing") or {}).get("listingId") or listing_id)
    if offer.get("status") == "PUBLISHED" and listing_id:
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    raise ValueError("eBay has not confirmed this listing is live. Check eBay before listing again.")
