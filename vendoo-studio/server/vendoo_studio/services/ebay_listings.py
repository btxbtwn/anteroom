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
import html
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
from vendoo_studio.services import marketplace_schemas
from vendoo_studio.services.direct_preflight import Context, gap, requirement, seeded_leaf
from vendoo_studio.services.user_settings import get_ebay_listing_defaults, get_package_dimensions
from vendoo_studio.services.vendoo_specifics import is_not_applicable

_SKIP = {
    "categoryid", "categorypath", "category_specifics", "categoryspecifics",
    "pricingformat", "pricingformatdetails", "conditiondescription",
    "shippingpolicyid", "paymentpolicyid", "returnspolicyid", "returnpolicyid",
    "shippingservice", "paymentmethod", "paypalemail", "acceptreturns",
    "returnpayedby", "returnwithin", "returnrefundmethod", "statusitem", "shipping",
    # Not aspects: the condition has its own field, product identifiers go on the product.
    "condition", "upc", "ean", "isbn", "epid",
    # Offer and listing settings, sent (or not) on their own.
    "bestoffer", "primarystorecategory", "secondarystorecategory", "shippingpolicy",
    "delistingattemptmessages", "personalizationinstructions",
    "autoaccept", "autoacceptprice", "autodeclineprice", "minimumoffer", "minimumofferprice",
    "storecategoryname", "storecategory2name",
    "listingformat", "listingduration", "auctionstartprice", "auctionreserveprice", "auctionbuyitnow",
}
# Identifiers eBay asks branded items to answer, Does Not Apply included.
_IDENTIFIER_ASPECTS = {"mpn"}
# Aspects that name the item itself: matched to eBay's spelling only when it is the same value.
_IDENTITY_ASPECTS = {"brand", "model", "mpn"}
# Studio's keys whose eBay aspect name the camelCase spelling does not give.
_ASPECT_NAMES = {
    "countryoforigin": "Country/Region of Manufacture", "countryofmanufacture": "Country/Region of Manufacture",
    "countryregionofmanufacture": "Country/Region of Manufacture", "performanceactivity": "Performance/Activity",
    "mpn": "MPN", "manufacturerpartnumber": "MPN",
}
_PRODUCT_IDS = {"upc": "upc", "ean": "ean", "isbn": "isbn"}
TITLE_MAX = 80
PRODUCT_DESCRIPTION_MAX = 4000
LISTING_DESCRIPTION_MAX = 500000
MAX_PHOTOS = 24
MAX_ASPECT_VALUES = 30
# Studio's conditions as eBay's apparel grades, which is where eBay keeps the
# Pre-owned Excellent/Good/Fair ladder (condition IDs 2990/3000/3010).
_CONDITION = (
    (r"defect|imperfection", "NEW_WITH_DEFECTS"),
    (r"new without|new other|nwot|without (tags|box)", "NEW_OTHER"),
    (r"new with tags|nwt|brand new|^new\b", "NEW"),
    (r"excellent|like new", "PRE_OWNED_EXCELLENT"),
    (r"fair|poor|major flaw|acceptable", "PRE_OWNED_FAIR"),
    (r"good|pre-owned|preowned|used", "USED_EXCELLENT"),
)
CONDITION_IDS = {
    "NEW": "1000", "NEW_OTHER": "1500", "NEW_WITH_DEFECTS": "1750", "LIKE_NEW": "2750",
    "PRE_OWNED_EXCELLENT": "2990", "USED_EXCELLENT": "3000", "PRE_OWNED_FAIR": "3010",
    "USED_VERY_GOOD": "4000", "USED_GOOD": "5000", "USED_ACCEPTABLE": "6000", "FOR_PARTS_OR_NOT_WORKING": "7000",
}


def ebay_condition(listing: dict) -> str:
    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    text = str(specifics.get("condition") or listing.get("condition") or "").strip()
    native = next((code for code, condition_id in CONDITION_IDS.items() if condition_id == text), None)
    if native:
        return native
    if text.upper() in CONDITION_IDS:
        return text.upper()
    for pattern, code in _CONDITION:
        if re.search(pattern, text.lower()):
            return code
    return "USED_EXCELLENT"


async def _check_condition(client: EbayClient, category_id: str, condition: str) -> None:
    """Refuse a condition this category does not take, when eBay says which ones it does.

    Without that answer the listing goes ahead and eBay's own refusal explains it.
    """
    try:
        policy = await marketplace_schemas.ebay_conditions(client, category_id)
    except EbayFailure:
        return
    allowed = {item["id"]: item["label"] for item in policy["conditions"]}
    if allowed and CONDITION_IDS[condition] not in allowed:
        names = ", ".join(name for name in allowed.values() if name)
        message = f"eBay does not take this condition in this category. Choose one of: {names}."
        raise ListingProblem(message, "missing_fields", fields=[{"field": "condition", "message": message}])


def item_key(conversation_id: str) -> str:
    """Inventory item key for an item with no seller SKU. Stable across retries, and not the seller's SKU."""
    return "ar-" + re.sub(r"[^A-Za-z0-9]", "", conversation_id)[:40]


def generated_inventory_key(value: str) -> bool:
    """True for a key Anteroom minted. eBay's Inventory API requires one; it is not a seller SKU."""
    return bool(re.fullmatch(r"ar-[A-Za-z0-9]{1,40}", str(value or "")))


def seller_sku(listing: dict) -> str:
    """The SKU the seller typed. A generated inventory key stored on the listing is ignored."""
    raw = re.sub(r"[^A-Za-z0-9_-]", "", str((listing or {}).get("sku") or ""))[:40]
    return "" if generated_inventory_key(raw) else raw


def ebay_sku(listing: dict, binding: dict, conversation_id: str) -> str:
    """The inventory item key. The seller's SKU when they set one, otherwise the internal key.

    The Inventory API has no separate hidden key: this value is also the offer SKU, so eBay
    shows it as the custom label. Anteroom keeps it on the marketplace binding only.
    """
    saved = str(binding.get("ebay_sku") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", saved):
        return saved
    return seller_sku(listing) or item_key(conversation_id)


def _aspect_name(key: str) -> str:
    if _aspect_key(key) in _ASPECT_NAMES:
        return _ASPECT_NAMES[_aspect_key(key)]
    if " " in key or (key[:1].isupper() and not re.search(r"[A-Z]", key[1:])):
        return key
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    return spaced[:1].upper() + spaced[1:]


def _aspect_values(value: Any) -> list[str]:
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


def _specifics(listing: dict) -> dict:
    """The listing's eBay specifics, one copy per aspect (``flatten_ebay_specifics``)."""
    from vendoo_studio.models.ebay_fields import flatten_ebay_specifics

    specifics = listing.get("ebay_specifics")
    if not isinstance(specifics, dict):
        return {}
    view = {"ebay_specifics": specifics}
    flatten_ebay_specifics(view)
    return view["ebay_specifics"]


def ebay_aspects(listing: dict, form: dict) -> dict[str, list[str]]:
    """Item specifics in eBay's aspect shape.

    Does Not Apply is dropped, except on MPN: an optional aspect with no answer
    is left off, and ``_match_aspects`` writes Does Not Apply only where eBay
    requires the aspect and offers that value.
    """
    specifics = _specifics(listing)
    aspects: dict[str, list[str]] = {}

    def add(name: str, value: Any) -> None:
        if is_not_applicable(value) and _aspect_key(name) not in _IDENTIFIER_ASPECTS:
            return
        values = ["Does Not Apply"] if is_not_applicable(value) else _aspect_values(value)
        if name and values and _aspect_key(name) not in {_aspect_key(known) for known in aspects}:
            aspects[name] = values

    for key, value in specifics.items():
        if key.lower() in _SKIP or _aspect_key(str(key)) in _SKIP or isinstance(value, dict):
            continue
        add(_aspect_name(str(key)), value)
    explicit = {_aspect_key(_aspect_name(str(key))) for key in specifics}
    if "brand" not in explicit:
        add("Brand", (form.get("overrides") or {}).get("brand"))
    for name, keys in _GENERAL_ASPECTS.items():
        if _aspect_key(name) not in explicit:
            add(name, next((listing.get(key) for key in keys if str(listing.get(key) or "").strip()), None))
    return aspects


def product_ids(listing: dict) -> dict[str, list[str]]:
    """UPC, EAN and ISBN for eBay's product, where eBay reads them (not as aspects)."""
    out: dict[str, list[str]] = {}
    for source in (_specifics(listing), listing):
        for key, value in source.items():
            name = _PRODUCT_IDS.get(_aspect_key(str(key)))
            if name and name not in out and str(value or "").strip():
                # eBay's own words for an item with no identifier.
                out[name] = ["Does not apply"] if is_not_applicable(value) else _aspect_values(value)
    return out


def ebay_title(listing: dict) -> str:
    """eBay's 80 characters, cut at a whole word. The pre-flight says when it is cut."""
    text = " ".join(str(listing.get("title") or "").split())
    if len(text) <= TITLE_MAX:
        return text
    cut = text[:TITLE_MAX + 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else text[:TITLE_MAX]).rstrip(" -,|/")


def description_html(text: str) -> str:
    """The listing description as eBay renders it: HTML, so paragraphs and line breaks survive."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if part.strip()]
    return "".join("<p>" + "<br>".join(html.escape(line.strip()) for line in part.split("\n")) + "</p>"
                   for part in paragraphs)


def store_category_names(listing: dict) -> list[str]:
    """The seller's eBay store categories, as eBay's path ("/Jackets"). "Other" is eBay's default: none."""
    names = []
    for key in ("primaryStoreCategory", "secondaryStoreCategory"):
        name = str(_specifics(listing).get(key) or "").strip().strip("/")
        if name and name.casefold() != "other" and not is_not_applicable(name):
            names.append("/" + name)
    return list(dict.fromkeys(names))


FIXED_PRICE = "FIXED_PRICE"
AUCTION = "AUCTION"
FORMAT_LABELS = {FIXED_PRICE: "Fixed price", AUCTION: "Auction"}
DEFAULT_AUCTION_DURATION = "DAYS_7"


def duration_label(value: str) -> str:
    if value == "GTC":
        return "Good 'Til Cancelled"
    days = int(value.removeprefix("DAYS_"))
    return f"{days} day" if days == 1 else f"{days} days"


def _format_amount(details: dict, key: str, label: str, *, required: bool = False) -> str | None:
    text = str(details.get(key) or "").replace("$", "").replace(",", "").strip()
    if not text:
        if required:
            raise missing(f"Enter the eBay {label}.", f"ebay_specifics.{key}")
        return None
    try:
        return dollars(money_cents(text))
    except ValueError as exc:
        raise missing(f"Enter the eBay {label} as a positive amount.", f"ebay_specifics.{key}") from exc


def listing_format(snapshot: dict) -> dict:
    """The seller's eBay format, duration and auction prices, as eBay's Inventory API spells them.

    No choice is a fixed-price, Good 'Til Cancelled listing. eBay's own per-category
    rules are checked live at List (``_check_format``).
    """
    from vendoo_studio.services.marketplace_schemas import _ebay_listing_type, ebay_duration

    details = snapshot.get("ebay_specifics") if isinstance(snapshot.get("ebay_specifics"), dict) else {}
    raw = str(details.get("listingFormat") or "").strip()
    kind = _ebay_listing_type(raw) if raw else FIXED_PRICE
    if kind is None:
        raise missing("Choose Fixed price or Auction for eBay.", "ebay_specifics.listingFormat")
    chosen = str(details.get("listingDuration") or "").strip()
    duration = ebay_duration(chosen) if chosen else ("GTC" if kind == FIXED_PRICE else DEFAULT_AUCTION_DURATION)
    if duration is None:
        raise missing("Choose an eBay listing duration.", "ebay_specifics.listingDuration")
    if kind == FIXED_PRICE and duration != "GTC":
        raise missing("eBay fixed-price listings run Good 'Til Cancelled.", "ebay_specifics.listingDuration")
    if kind == AUCTION and duration == "GTC":
        raise missing("Choose how many days the eBay auction runs.", "ebay_specifics.listingDuration")
    out: dict = {"format": kind, "duration": duration}
    if kind == AUCTION:
        start = _format_amount(details, "auctionStartPrice", "starting bid", required=True)
        reserve = _format_amount(details, "auctionReservePrice", "reserve price")
        buy_now = _format_amount(details, "auctionBuyItNow", "Buy It Now price")
        if reserve and Decimal(reserve) <= Decimal(start):
            raise missing("The eBay reserve price must be higher than the starting bid.", "ebay_specifics.auctionReservePrice")
        if buy_now and Decimal(buy_now) <= Decimal(start):
            raise missing("The eBay Buy It Now price must be higher than the starting bid.", "ebay_specifics.auctionBuyItNow")
        out.update(start=start, reserve=reserve, buy_it_now=buy_now)
    return out


async def _check_format(client: EbayClient, category_id: str, chosen: dict) -> None:
    """Refuse a format or duration eBay does not offer in this category, before anything is written."""
    if chosen["format"] != AUCTION:
        return
    allowed = (await marketplace_schemas.ebay_listing_types(client, category_id)).get(AUCTION) or []
    if not allowed:
        raise missing("eBay does not offer auctions in this category. Choose Fixed price.", "ebay_specifics.listingFormat")
    if chosen["duration"] not in allowed:
        lengths = ", ".join(duration_label(value) for value in allowed)
        raise missing(f"eBay auctions in this category run {lengths}.", "ebay_specifics.listingDuration")


def _short_description(text: str) -> str:
    """The product's copy of the description (eBay keeps 4000 characters there; the listing keeps all of it)."""
    if len(text) <= PRODUCT_DESCRIPTION_MAX:
        return text
    cut = text[:PRODUCT_DESCRIPTION_MAX]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


# Trading-card leaves, where eBay grades a card with condition descriptors.
CARD_CATEGORIES = frozenset({"183050", "183454", "261328"})
_GRADERS = {"psa": "275010", "bccg": "275011", "bvg": "275012", "bgs": "275013", "csg": "275014", "cgc": "275015",
            "sgc": "275016", "ksa": "275017", "gma": "275018", "hga": "275019", "isa": "2750110", "pca": "2750111",
            "gsg": "2750112", "pgs": "2750113", "mnt": "2750114", "tag": "2750115", "rare": "2750116", "rcg": "2750117",
            "other": "2750123"}
# Grade 10 is 275020, 9.5 is 275021 … 1 is 2750218.
_GRADES = {format(10 - step / 2, "g"): f"27502{step}" for step in range(19)} | {"authentic": "2750219"}
_CARD_CONDITIONS = {"near mint or better": "400010", "near mint": "400010", "excellent": "400011", "very good": "400012",
                    "poor": "400013", "lightly played": "400015", "moderately played": "400016", "heavily played": "400017"}


def card_condition(listing: dict, category_id: str) -> tuple[str, list[dict]] | None:
    """eBay's condition and descriptors for a trading card, or None outside the card categories."""
    if category_id not in CARD_CATEGORIES:
        return None
    said = {_aspect_key(str(key)): str(value or "").strip() for key, value in _specifics(listing).items()
            if not isinstance(value, (dict, list))}
    grader = _GRADERS.get(_aspect_key(said.get("professionalgrader", "")))
    grade = _GRADES.get(said.get("grade", "").lower().replace("gem mint", "").strip())
    if grader and grade:
        descriptors = [{"name": "27501", "values": [grader]}, {"name": "27502", "values": [grade]}]
        if said.get("certificationnumber"):
            descriptors.append({"name": "27503", "additionalInfo": said["certificationnumber"][:30]})
        return "LIKE_NEW", descriptors
    ungraded = _CARD_CONDITIONS.get(said.get("cardcondition", "").lower())
    if ungraded:
        return "USED_VERY_GOOD", [{"name": "40001", "values": [ungraded]}]
    raise missing("eBay needs the card's condition (Near Mint or Better, Excellent, Very Good or Poor), "
                  "or its professional grader and grade.", "ebay_specifics.Card Condition")


def ebay_category_id(listing: dict) -> str:
    """The live eBay leaf when one was chosen, otherwise the Vendoo category id Send uses."""
    from vendoo_studio.services.direct_categories import direct_category_id

    specifics = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    category = direct_category_id(listing, "ebay") or (listing.get("marketplace_category_ids") or {}).get("ebay") or specifics.get("categoryId")
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
        raise missing("Enter the packed weight before listing on eBay.", "weight_oz")
    if ounces > 2400:
        raise missing("Enter a packaged weight of at most 2400 ounces.", "weight_oz")
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
    if isinstance(exc, EbayFailure) and exc.status >= 500 and pending not in _UNREPEATABLE and str(exc).startswith("eBay returned HTTP"):
        # A gateway 503 (and its cousins) is eBay being down, not a refusal of this listing.
        # Hand-raised failures also default to status 502, and those keep their own words.
        return failure("eBay is temporarily unavailable. Try again.", "network", safe=True, marketplace_message=said)
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


async def _offer_for(client: EbayClient, sku: str, binding: dict, kind: str = FIXED_PRICE) -> dict | None:
    """This item's EBAY_US offer in the chosen format: the saved one, or the one eBay has for its SKU.

    eBay keeps one offer per SKU and format. A live offer always wins; an
    unpublished offer in the other format is left as it is.
    """
    offer_id = str(binding.get("ebay_offer_id") or "")
    if offer_id:
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        if offer.get("status") == "PUBLISHED" or offer.get("format", kind) == kind:
            return offer
    if not binding.get("ebay_inventory_written"):
        # An offer needs the inventory item, which this item has never written.
        return None
    found = await _not_found(client.request("GET", "/sell/inventory/v1/offer",
                                            params={"sku": sku, "marketplace_id": "EBAY_US", "format": kind}))
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


POLICY_LABELS = {"fulfillment": "shipping", "payment": "payment", "return": "return"}


async def _policies(client: EbayClient, kind: str) -> list[dict]:
    body = await client.request("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": "EBAY_US"})
    return [{"id": str(policy[f"{kind}PolicyId"]), "title": str(policy.get("name") or "Untitled")[:200]}
            for policy in body.get(f"{kind}Policies", [])
            if policy.get("marketplaceId") in (None, "EBAY_US") and policy.get(f"{kind}PolicyId")]


def _location_enabled(item: dict) -> bool:
    """eBay enables a new location by default and may omit that status on the list."""
    if not item.get("merchantLocationKey"):
        return False
    status = str(item.get("merchantLocationStatus") or "ENABLED").strip().upper()
    return status == "ENABLED"


async def _locations(client: EbayClient) -> list[dict]:
    body = await client.request("GET", "/sell/inventory/v1/location", params={"limit": "100"})
    return [{"id": str(item["merchantLocationKey"]), "title": str(item.get("name") or item["merchantLocationKey"])[:200]}
            for item in body.get("locations", []) if isinstance(item, dict) and _location_enabled(item)]


def _ship_from_postal_code() -> str | None:
    """The saved inventory location when it is a US ZIP eBay can store."""
    from vendoo_studio.services.user_settings import get_ebay_inventory_location

    match = re.fullmatch(r"(\d{5})(?:-\d{4})?", get_ebay_inventory_location())
    return match.group(1) if match else None


async def _create_ship_from_location(client: EbayClient) -> dict:
    """Make the eBay inventory location from the ZIP in Settings → Connections → eBay."""
    postal = _ship_from_postal_code()
    if not postal:
        raise missing(
            "eBay has no inventory location. Enter a 5-digit ZIP under Settings → Connections → eBay.",
            "settings.ebay.merchant_location_key",
        )
    key = f"zip-{postal}"
    body = {
        "name": postal,
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
        "location": {"address": {"postalCode": postal, "country": "US"}},
    }
    path = f"/sell/inventory/v1/location/{segment(key)}"
    for attempt in range(2):
        try:
            await client.request("POST", path, body=body)
            return {"id": key, "title": postal}
        except EbayFailure as exc:
            # 409: this ZIP is already a location. 5xx: eBay often stores the
            # location and then fails the reply, which is what a bare 503 was.
            if exc.status == 409:
                return {"id": key, "title": postal}
            if exc.status >= 500 and any(item["id"] == key for item in await _locations(client)):
                return {"id": key, "title": postal}
            if exc.status < 500 or attempt:
                raise
    raise EbayFailure("eBay is temporarily unavailable. Try again.", 503)


async def listing_profiles(client: EbayClient | None = None) -> dict[str, list[dict]]:
    """The account's business policies and enabled locations, names and IDs only."""
    client = client or await EbayClient.connect()
    try:
        return {"fulfillment": await _policies(client, "fulfillment"), "payment": await _policies(client, "payment"),
                "return": await _policies(client, "return"), "locations": await _locations(client)}
    except EbayFailure as exc:
        if exc.status != 403:
            raise
        raise EbayFailure("eBay has not given Anteroom access to your shipping, payment, and return policies. "
                          "Reconnect eBay to grant it.", 403, code="needs_permission",
                          marketplace_message=exc.marketplace_message) from exc


def _pick(options: list[dict], chosen: str | None, kind: str, setting: str) -> str:
    """The seller's stored choice, else the account's only one. Several and no choice: ask."""
    ids = [option["id"] for option in options]
    if chosen in ids:
        return chosen
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise missing(f"Add an eBay {kind} in eBay before listing.", f"settings.ebay.{setting}")
    plural = kind.replace("policy", "policies").replace("location", "locations")
    raise missing(f"This eBay account has {len(ids)} {plural}. Choose the one new listings use in Settings → Connections.",
                  f"settings.ebay.{setting}")


async def listing_choices(client: EbayClient) -> dict[str, str]:
    profiles = await listing_profiles(client)
    defaults = get_ebay_listing_defaults()
    chosen = {
        "fulfillmentPolicyId": _pick(profiles["fulfillment"], defaults.get("fulfillment_policy_id"), "shipping policy", "fulfillment_policy_id"),
        "paymentPolicyId": _pick(profiles["payment"], defaults.get("payment_policy_id"), "payment policy", "payment_policy_id"),
        "returnPolicyId": _pick(profiles["return"], defaults.get("return_policy_id"), "return policy", "return_policy_id"),
    }
    locations = profiles["locations"]
    if not locations:
        locations = [await _create_ship_from_location(client)]
    chosen["merchantLocationKey"] = _pick(locations, defaults.get("merchant_location_key"),
                                          "inventory location", "merchant_location_key")
    return chosen


def best_offer_terms(form: dict, price: str, listing: dict | None = None) -> dict:
    """Best Offer for create and update. The seller's prices win; otherwise accept at list - $2 and decline under list - $4."""
    from vendoo_studio.services.vendoo_marketplace_fields import best_offer_enabled, offer_floors

    ebay = (listing or {}).get("ebay_specifics") if isinstance((listing or {}).get("ebay_specifics"), dict) else {}
    chosen = ebay.get("bestOffer") if isinstance(ebay.get("bestOffer"), dict) else {}
    fixed = ((form.get("specifics") or {}).get("pricingFormatDetails") or {}).get("fixedPrice") or {}
    if not best_offer_enabled(chosen) or fixed.get("allowBestOffer") is False and not chosen:
        return {"bestOfferEnabled": False}
    if chosen and not best_offer_enabled(chosen):
        return {"bestOfferEnabled": False}
    floors = offer_floors(price) or ("", "")

    def _amount(raw) -> Decimal | None:
        try:
            amount = Decimal(str(raw or "").replace("$", "").replace(",", "").strip())
        except ArithmeticError:
            return None
        return amount if amount > 0 else None

    def _seller_amount(*keys: str) -> Decimal | None:
        for key in keys:
            if key not in chosen:
                continue
            if str(chosen.get(key) or "").strip() == "":
                return None
            return _amount(chosen.get(key))
        return None

    seller_accept = _seller_amount("autoAcceptPrice")
    seller_minimum = _seller_amount("minimumOfferPrice", "autoDeclinePrice")
    accept = seller_accept if "autoAcceptPrice" in chosen else _amount(fixed.get("acceptOffersOfAtLeast")) or _amount(floors[0])
    minimum = seller_minimum if any(key in chosen for key in ("minimumOfferPrice", "autoDeclinePrice")) \
        else _amount(fixed.get("declineOffersLowerThan")) or _amount(floors[1])
    # A formula floor on the wrong side of a price the seller set is left off.
    if seller_accept is not None and seller_minimum is None and minimum is not None and not minimum < seller_accept:
        minimum = None
    if seller_minimum is not None and seller_accept is None and accept is not None and not seller_minimum < accept:
        accept = None
    if accept is not None and minimum is not None and not minimum < accept:
        accept = minimum = None
    price_amount = _amount(price)
    parsed: dict[str, Decimal] = {}
    if accept is not None and price_amount is not None and accept < price_amount:
        parsed["autoAcceptPrice"] = accept
    if minimum is not None and price_amount is not None and minimum < price_amount:
        parsed["autoDeclinePrice"] = minimum
    terms: dict = {"bestOfferEnabled": True}
    for name, amount in parsed.items():
        terms[name] = {"value": format(amount.quantize(Decimal("0.01")), "f"), "currency": "USD"}
    return terms


def _best_offer_xml(item, terms: dict) -> None:
    """Trading API Best Offer on a listing that has no inventory offer."""
    from xml.etree import ElementTree as ET

    details = ET.SubElement(item, "BestOfferDetails")
    ET.SubElement(details, "BestOfferEnabled").text = "true" if terms.get("bestOfferEnabled") else "false"
    if not terms.get("bestOfferEnabled"):
        return
    listing = None
    for name, tag in (("autoAcceptPrice", "BestOfferAutoAcceptPrice"), ("autoDeclinePrice", "MinimumBestOfferPrice")):
        value = (terms.get(name) or {}).get("value")
        if not value:
            continue
        if listing is None:
            listing = ET.SubElement(item, "ListingDetails")
        node = ET.SubElement(listing, tag)
        node.set("currencyID", "USD")
        node.text = value


async def _complete_aspects(client: EbayClient, category_id: str, aspects: dict[str, list[str]]) -> dict[str, list[str]]:
    """The listing's aspects, matched against eBay's own for this leaf (``marketplace_schemas``).

    Only eBay's aspects are sent, under its exact names: a value is matched
    loosely to eBay's spelling, a multi-value aspect is split into its values,
    and a single-value one keeps its first. Every required aspect still missing
    or invalid is reported at once, under eBay's name, as fields the seller (or
    chat) can fill.
    """
    try:
        schema = await marketplace_schemas.ebay_aspects(client, category_id)
    except EbayFailure as exc:
        if exc.code == "rejected" and exc.status in {400, 404}:
            raise missing("eBay does not list items in that category. Choose a current, most specific eBay category.",
                          "marketplace_category_ids.ebay") from exc
        raise
    return _match_aspects(schema, aspects)


def _match_aspects(schema: list[dict], aspects: dict[str, list[str]]) -> dict[str, list[str]]:
    """Match the listing's aspects to one category's schema. Required misses are refused together."""
    given = {_aspect_key(name): values for name, values in aspects.items()}
    completed: dict[str, list[str]] = {}
    problems: list[tuple[str, str]] = []
    for field in schema:
        name = field["name"]
        parent_answers = {row["name"]: given.get(_aspect_key(row["name"]), []) for row in schema}
        allowed = [value["label"] for value in marketplace_schemas.aspect_options(field, parent_answers)]
        values = list(given.get(_aspect_key(name)) or [])
        values = _split_values(values, allowed)[:MAX_ASPECT_VALUES] if field["multi"] else values[:1]
        constrained = {value["label"] for value in field["values"] if value.get("constraints")}
        if any(value in constrained and value not in allowed for value in values):
            problems.append((name, f"Choose {name} for the selected parent aspects."))
            continue
        if field["mode"] == "selection" and values:
            matched = [_choice(value, allowed) for value in values]
            if not all(matched) and field["required"]:
                problems.append((name, f"Choose one of eBay's values for {name}."))
                continue
            # eBay would refuse the whole listing over an optional value it does not offer.
            values = [value for value in matched if value]
        elif allowed and values:
            # Free text still reads best in eBay's own spelling, when one is the same value.
            near = _aspect_key(name) not in _IDENTITY_ASPECTS
            values = list(dict.fromkeys(_choice(value, allowed, near=near) or value for value in values))
        limit = field.get("max_length")
        if limit and any(len(value) > limit for value in values):
            if field["required"]:
                problems.append((name, f"eBay takes at most {limit} characters for {name}."))
                continue
            values = [value for value in values if len(value) <= limit]
        if field["required"] and not values:
            unknown = next((choice for choice in allowed if choice.casefold() == "does not apply"), None)
            if unknown:
                values = [unknown]
            else:
                problems.append((name, f"eBay needs {name}."))
                continue
        if values:
            completed[name] = values
    if problems:
        raise ListingProblem(" ".join(message for _, message in problems), "missing_fields",
                             fields=[{"field": f"ebay_specifics.{name}", "message": message}
                                     for name, message in problems])
    return completed


def _loose(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def _choice(value: str, allowed: list[str], *, near: bool = True) -> str | None:
    """eBay's own spelling of the value: same letters, then singular, then the one choice that contains it."""
    wanted = _loose(value)
    if not wanted:
        return None
    for same in (lambda choice: _loose(choice) == wanted, lambda choice: _loose(choice).rstrip("s") == wanted.rstrip("s")):
        hit = next((choice for choice in allowed if same(choice)), None)
        if hit:
            return hit
    if not near:
        return None
    hits = [choice for choice in allowed if wanted in _loose(choice) or (len(_loose(choice)) > 3 and _loose(choice) in wanted)]
    return hits[0] if len(hits) == 1 else None


def _split_values(values: list[str], allowed: list[str]) -> list[str]:
    """One value per item for a multi-value aspect: "Floral, Spring" is two themes unless eBay offers it whole."""
    out: list[str] = []
    for value in values:
        parts = [value] if (allowed and _choice(value, allowed)) else re.split(r"\s*(?:[,;/|]|\s&\s|\sand\s)\s*", value)
        for part in parts:
            if part.strip() and part.strip().casefold() not in {item.casefold() for item in out}:
                out.append(part.strip())
    return out


def _photo_upload(photo) -> dict:
    path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
    root = Path(PHOTOS_DIR).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise missing("An eBay photo is missing from Anteroom. Restore it before listing.", "photos")
    if photo.mime_type not in {"image/jpeg", "image/png"}:
        raise missing("eBay photos need to be JPEG or PNG.", "photos")
    return {"field": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mime": photo.mime_type}


def ebay_picture_url(url: str) -> str:
    """The picture address inventory will accept.

    The media API appends ``?set_id=...``. Sending that query on the inventory
    item makes eBay answer 25001, a core inventory error, and save nothing.
    """
    text = str(url or "").strip().split("?", 1)[0]
    if text.startswith("https://") and " " not in text and len(text) <= 2000:
        return text
    return ""


async def _image_url(client: EbayClient, photo) -> str:
    uploaded = await client.request("POST", "/commerce/media/v1_beta/image/create_image_from_file", upload=_photo_upload(photo))
    url = str(uploaded.get("imageUrl") or "")
    image_id = str(uploaded.get("imageId") or uploaded.get("fileId") or "")
    if not url and image_id:
        url = str((await client.request("GET", f"/commerce/media/v1_beta/image/{segment(image_id)}")).get("imageUrl") or "")
    picture = ebay_picture_url(url)
    if not picture:
        raise ValueError("eBay did not confirm a photo address. Check the listing before trying again.")
    return picture


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
        url = ebay_picture_url(uploaded.get((photo.id, checksum)) or "")
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


def condition_description(snapshot: dict, form: dict) -> str:
    details = snapshot.get("ebay_specifics") or {}
    if "conditionDescription" in details:
        return str(details["conditionDescription"] or "")
    return str((form.get("specifics") or {}).get("conditionDescription") or EBAY_CONDITION_DESCRIPTION)


def _listing_fields(snapshot: dict, form: dict) -> dict:
    title = ebay_title(snapshot)
    description = str(snapshot.get("description") or "").strip()
    if not title or not description:
        raise missing("Add an eBay title and description before listing.", *[name for name, value in (("title", title), ("description", description)) if not value])
    if len(description_html(description)) > LISTING_DESCRIPTION_MAX:
        raise missing("eBay descriptions are at most 500,000 characters. Shorten it.", "description")
    try:
        quantity = int(str(snapshot.get("quantity") or 1).strip())
    except ValueError:
        quantity = 0
    if not 1 <= quantity <= 10000:
        raise missing("Enter an eBay quantity from 1 to 10000.", "quantity")
    from vendoo_studio.services.seller_overrides import price_for
    from vendoo_studio.services.vendoo_marketplace_fields import best_offer_enabled

    chosen = listing_format(snapshot)
    if chosen["format"] == AUCTION:
        if quantity != 1:
            raise missing("An eBay auction sells one item. Set quantity to 1.", "quantity")
        offer = (snapshot.get("ebay_specifics") or {}).get("bestOffer")
        if isinstance(offer, dict) and offer and best_offer_enabled(offer):
            raise missing("Turn off eBay offers for an auction. Anteroom sends offers only on fixed-price listings.",
                          "ebay_specifics.bestOffer.enabled")
        # The price on an auction is its optional Buy It Now.
        price = chosen["buy_it_now"] or ""
    else:
        try:
            price = dollars(money_cents(str(price_for(snapshot, "ebay"))))
        except (ValueError, ArithmeticError) as exc:
            raise missing("Set a positive eBay price before listing.", "price") from exc
    category_id = ebay_category_id(snapshot)
    condition, descriptors = card_condition(snapshot, category_id) or (ebay_condition(snapshot), [])
    condition_note = condition_description(snapshot, form)
    if len(condition_note) > 1000:
        raise missing("eBay condition descriptions are at most 1,000 characters.", "ebay_specifics.conditionDescription")
    return {"title": title, "description": description, "quantity": quantity, "price": price, "category_id": category_id,
            "condition": condition, "condition_descriptors": descriptors, "store_categories": store_category_names(snapshot),
            "condition_description": condition_note, "format": chosen}


@requirement("ebay")
def requirements(snapshot: dict, context: Context) -> list[dict]:
    """What eBay would refuse, read from the listing alone. Aspects, policies and conditions are checked live at List."""
    form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
    found: list[dict] = []
    for check in (lambda: _listing_fields(snapshot, form), lambda: package_ounces(snapshot, form)):
        try:
            check()
        except ListingProblem as exc:
            found += [gap(item["field"], item["message"]) for item in exc.fields or []]
        except ValueError as exc:
            found.append(gap("listing", str(exc)))
    title = " ".join(str(snapshot.get("title") or "").split())
    if len(title) > TITLE_MAX:
        found.append(gap("title", f"eBay titles are at most {TITLE_MAX} characters, so eBay gets: {ebay_title(snapshot)}", blocking=False))
    category = str((snapshot.get("marketplace_category_ids") or {}).get("ebay") or "")
    if seeded_leaf(context.db, "ebay", category) is False:
        found.append(gap("marketplace_category_ids.ebay", "That eBay category has subcategories. Choose the most specific one."))
    found.extend(_cached_aspect_gaps(snapshot, form))
    return found


def _cached_aspect_gaps(snapshot: dict, form: dict) -> list[dict]:
    """Required eBay aspects from the cached live schema. No cache means List still checks them."""
    from vendoo_studio.services.category_fields import load_rows
    from vendoo_studio.services.direct_categories import live_category_id
    from vendoo_studio.services.marketplace_schemas import EBAY_ASPECTS

    category = live_category_id(snapshot, "ebay")
    if not re.fullmatch(r"[1-9]\d{0,14}", category):
        return []
    try:
        rows = load_rows(EBAY_ASPECTS, category)
    except Exception:  # noqa: BLE001 - a closed database only means the live check at List remains
        return []
    if not rows:
        return []
    try:
        _match_aspects(rows, ebay_aspects(snapshot, form))
    except ListingProblem as exc:
        return [gap(item["field"], item["message"]) for item in exc.fields or []]
    return []


async def _revise_by_item_id(client: EbayClient, listing_id: str, fields: dict, terms: dict | None = None) -> dict:
    """A pasted eBay link has an item ID and no inventory offer. Revise that listing in place."""
    from xml.etree import ElementTree as ET

    from vendoo_studio.services.ebay_actions import owned_listing
    from vendoo_studio.services.ebay_client import NS, trading_xml

    if fields["format"]["format"] != FIXED_PRICE:
        raise ValueError("eBay cannot change a live listing between fixed price and auction. End it on eBay and list it again.")
    before = await owned_listing(client, listing_id)
    if before["type"] != "FixedPriceItem":
        raise ValueError("Only a fixed-price eBay listing can be updated.")
    if fields["quantity"] < int(before.get("soldQuantity") or 0):
        raise ValueError("Quantity cannot be lower than the quantity already sold.")
    root = ET.Element("ReviseFixedPriceItemRequest", xmlns=NS["e"])
    if terms and terms.get("bestOfferEnabled"):
        for key, tag in (("autoAcceptPrice", "BestOfferAutoAcceptPrice"), ("autoDeclinePrice", "MinimumBestOfferPrice")):
            if not terms.get(key):
                ET.SubElement(root, "DeletedField").text = f"Item.ListingDetails.{tag}"
    condition_note_applies = fields["condition"] != "NEW" and not fields["condition_descriptors"]
    if condition_note_applies and not fields["condition_description"]:
        ET.SubElement(root, "DeletedField").text = "Item.ConditionDescription"
    item = ET.SubElement(root, "Item")
    for name, value in {"ItemID": listing_id, "Title": fields["title"], "Description": description_html(fields["description"]),
                        "StartPrice": fields["price"], "Quantity": str(fields["quantity"])}.items():
        ET.SubElement(item, name).text = value
    if condition_note_applies and fields["condition_description"]:
        ET.SubElement(item, "ConditionDescription").text = fields["condition_description"]
    if terms:
        _best_offer_xml(item, terms)
    await client.request("POST", "/ws/api.dll", xml=trading_xml(root), call="ReviseFixedPriceItem")
    after = await owned_listing(client, listing_id)
    if (after["title"] != fields["title"] or str(after.get("price")) != fields["price"]
            or (condition_note_applies and after.get("conditionDescription", "") != fields["condition_description"])):
        raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
    return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}


def _dimensions(snapshot: dict) -> dict:
    """The listing's own package (Item Details), else the box in Settings."""
    sides = re.findall(r"\d+(?:\.\d+)?", str(snapshot.get("package_dimensions_in") or ""))
    if len(sides) == 3 and min(float(side) for side in sides) > 0:
        return {"length": float(sides[0]), "width": float(sides[1]), "height": float(sides[2]), "unit": "INCH"}
    return {**get_package_dimensions(), "unit": "INCH"}


def _inventory_body(fields: dict, aspects: dict, image_urls: list[str], snapshot: dict, form: dict) -> dict:
    ounces = Decimal(package_ounces(snapshot, form))
    # A quoted weight is what made the inventory service answer 25001 instead of saving the item.
    weight = int(ounces) if ounces == int(ounces) else float(ounces)
    body = {
        "condition": fields["condition"],
        "conditionDescription": fields["condition_description"],
        "availability": {"shipToLocationAvailability": {"quantity": fields["quantity"]}},
        "product": {"title": fields["title"], "description": _short_description(fields["description"]),
                    "imageUrls": [picture for picture in (ebay_picture_url(url) for url in image_urls) if picture],
                    "aspects": aspects, **product_ids(snapshot)},
        "packageWeightAndSize": {
            "weight": {"value": weight, "unit": "OUNCE"},
            "dimensions": _dimensions(snapshot),
        },
    }
    if fields.get("condition_descriptors"):
        body["conditionDescriptors"] = fields["condition_descriptors"]
    return body


def _usd(value: str) -> dict:
    return {"value": value, "currency": "USD"}


def offer_terms(form: dict, fields: dict, snapshot: dict) -> dict | None:
    """Best Offer terms for a fixed-price offer. An auction carries none."""
    return None if fields["format"]["format"] == AUCTION else best_offer_terms(form, fields["price"], snapshot)


def _offer_body(sku: str, fields: dict, location: str | None, policies: dict) -> dict:
    chosen = fields["format"]
    pricing = {"price": _usd(fields["price"])} if fields["price"] else {}
    if chosen["format"] == AUCTION:
        pricing["auctionStartPrice"] = _usd(chosen["start"])
        if chosen.get("reserve"):
            pricing["auctionReservePrice"] = _usd(chosen["reserve"])
    policies = {key: value for key, value in policies.items() if value is not None}
    body = {
        "sku": sku, "marketplaceId": "EBAY_US", "format": chosen["format"], "listingDuration": chosen["duration"],
        "includeCatalogProductDetails": False, "categoryId": fields["category_id"],
        "pricingSummary": pricing,
        "listingDescription": description_html(fields["description"]),
        "merchantLocationKey": location,
        "listingPolicies": policies,
    }
    if chosen["format"] != AUCTION:
        # eBay: an auction offer does not carry availableQuantity.
        body["availableQuantity"] = fields["quantity"]
    if fields.get("store_categories"):
        body["storeCategoryNames"] = fields["store_categories"]
    return body


async def _update_live(snapshot: dict, photos: list, binding: dict, record: Callable[[dict], None],
                       active: Callable[[], bool]) -> dict:
    """Replace fields on the listing that is already live. Never publishes a second one.

    Every write here is a replace, so a lost reply is safe to send again.
    """
    try:
        if not active():
            raise ValueError("The eBay update stopped. Check its result before trying again.")
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise missing(f"Choose between one and {MAX_PHOTOS} eBay photos.", "photos")
        client = await EbayClient.connect()
        form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
        fields = _listing_fields(snapshot, form)
        from vendoo_studio.services.vendoo_marketplace_fields import best_offer_error

        problem = best_offer_error(snapshot)
        if problem:
            raise ValueError(problem)
        listing_id = str(binding.get("listing_id") or binding.get("ebay_listing_id") or "")
        offer_id = str(binding.get("ebay_offer_id") or "")
        if not re.fullmatch(r"\d{6,20}", listing_id):
            raise ValueError("eBay has not confirmed which live listing to update.")
        if not offer_id:
            return await _revise_by_item_id(client, listing_id, fields, best_offer_terms(form, fields["price"], snapshot))
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        current_id = str((offer.get("listing") or {}).get("listingId") or "")
        if offer.get("status") != "PUBLISHED" or current_id != listing_id:
            raise ValueError("This eBay offer is not the live listing. Check eBay before updating it.")
        if offer.get("format", FIXED_PRICE) != fields["format"]["format"]:
            raise ValueError("eBay cannot change a live listing between fixed price and auction. End it on eBay and list it again.")
        aspects = await _complete_aspects(client, fields["category_id"], ebay_aspects(snapshot, form))
        await _check_condition(client, fields["category_id"], fields["condition"])
        sku = str(offer.get("sku") or binding.get("ebay_sku") or "")
        if not sku:
            raise ValueError("eBay did not return this listing's SKU. Check eBay before updating it.")
        record({"ebay_sku": sku, "ebay_offer_id": offer_id, "ebay_listing_id": listing_id})
        image_urls = await _image_urls(client, photos, binding, record, active)
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}",
                             body=_inventory_body(fields, aspects, image_urls, snapshot, form))
        policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        policies = {**policies, "bestOfferTerms": offer_terms(form, fields, snapshot)}
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
        if action not in {"list", "save_draft"} or (action == "list" and confirm is not True):
            raise ValueError("Confirm listing on eBay.")
        if not active():
            raise ValueError("The eBay listing stopped before it started.")
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise missing(f"Choose between one and {MAX_PHOTOS} eBay photos.", "photos")
        client = await EbayClient.connect()
        form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
        fields = _listing_fields(snapshot, form)
        from vendoo_studio.services.vendoo_marketplace_fields import best_offer_error

        problem = best_offer_error(snapshot)
        if problem:
            raise ValueError(problem)
        aspects = await _complete_aspects(client, fields["category_id"], ebay_aspects(snapshot, form))
        await _check_condition(client, fields["category_id"], fields["condition"])
        await _check_format(client, fields["category_id"], fields["format"])
        choices = await listing_choices(client)
        sku = ebay_sku(snapshot, binding, conversation_id)
        if not binding.get("ebay_inventory_written") and sku != item_key(conversation_id):
            # The seller's own SKU may already name another item on eBay. Never overwrite it.
            if await _not_found(client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(sku)}")) is not None:
                raise missing(f"eBay already has an item with SKU {sku}. Give this item its own SKU, then List again.", "sku")
        kind = fields["format"]["format"]
        offer = await _offer_for(client, sku, binding, kind)
        if offer:
            record({"ebay_sku": sku, "ebay_offer_id": str(offer.get("offerId") or binding["ebay_offer_id"]), "ebay_format": kind})
            live = _published(offer, record)
            if live:
                return live
        image_urls = await _image_urls(client, photos, binding, record, active)
        # From here eBay may hold an inventory item under this SKU, and it is this item's.
        record({"ebay_sku": sku, "ebay_inventory_written": True})
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(sku)}",
                             body=_inventory_body(fields, aspects, image_urls, snapshot, form))
        body = _offer_body(sku, fields, choices["merchantLocationKey"], {
            "fulfillmentPolicyId": choices["fulfillmentPolicyId"], "paymentPolicyId": choices["paymentPolicyId"],
            "returnPolicyId": choices["returnPolicyId"], "bestOfferTerms": offer_terms(form, fields, snapshot),
        })
        offer_id = str((offer or {}).get("offerId") or "")
        if offer_id:
            # The offer from an earlier List: bring it up to date, then publish it.
            await client.request("PUT", f"/sell/inventory/v1/offer/{segment(offer_id)}", body=body)
        else:
            pending = "offer"
            created = await client.request("POST", "/sell/inventory/v1/offer", body=body)
            offer_id = str(created.get("offerId") or "")
            if not offer_id:
                raise EbayFailure("eBay did not confirm the offer. Check eBay before listing again.")
            record({"ebay_offer_id": offer_id, "ebay_format": kind})
            pending = None
        if action == "save_draft":
            # Saved on eBay, not listed: an unpublished offer buyers cannot see. Read it back.
            saved = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
            differences = offer_differences(saved, sku, fields)
            if differences:
                raise ValueError(f"eBay saved different values for: {', '.join(differences)}. Check the item before listing.")
            return {"ok": True, "state": "draft"}
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
        offer = await _offer_for(client, sku, binding, str(binding.get("ebay_format") or FIXED_PRICE))
        if offer:
            record({"ebay_offer_id": str(offer.get("offerId") or binding.get("ebay_offer_id"))})
            live = _published(offer, record)
            if live:
                return live
            if binding.get("action") == "save_draft" and snapshot:
                form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "ebay")
                differences = offer_differences(offer, sku, _listing_fields(snapshot, form))
                if differences:
                    raise ValueError(f"eBay saved different values for: {', '.join(differences)}. Save the draft again.")
                return {"ok": True, "state": "draft"}
            return resumable("eBay has this listing saved but not live. List again to publish it.")
        return resumable("eBay has no listing for this item. List again.")
    except Exception as exc:
        result = _failure(exc, "check")
        return {**result, "safe_to_retry": False, "error_code": "uncertain" if result["error_code"] != "signed_out" else "signed_out"}


def _same_amount(saved: object, expected: str | None) -> bool:
    value = (saved or {}).get("value") if isinstance(saved, dict) else None
    if not expected:
        return value in (None, "")
    try:
        return Decimal(str(value)) == Decimal(expected)
    except ArithmeticError:
        return False


def offer_differences(offer: dict, sku: str, fields: dict) -> list[str]:
    """What eBay's saved offer holds differently from the revision sent. Empty when it matches."""
    chosen = fields["format"]
    pricing = offer.get("pricingSummary") if isinstance(offer.get("pricingSummary"), dict) else {}
    checks = {
        "SKU": offer.get("sku") == sku,
        "format": offer.get("format") == chosen["format"],
        "duration": offer.get("listingDuration") == chosen["duration"],
        "category": str(offer.get("categoryId") or "") == fields["category_id"],
        "price" if chosen["format"] == FIXED_PRICE else "Buy It Now price": _same_amount(pricing.get("price"), fields["price"] or None),
    }
    if chosen["format"] == AUCTION:
        checks["starting bid"] = _same_amount(pricing.get("auctionStartPrice"), chosen["start"])
        checks["reserve price"] = _same_amount(pricing.get("auctionReservePrice"), chosen.get("reserve"))
    else:
        checks["quantity"] = str(offer.get("availableQuantity") or "") == str(fields["quantity"])
    return [name for name, same in checks.items() if not same]


async def _check_update(client: EbayClient, binding: dict, snapshot: dict) -> dict:
    title = ebay_title(snapshot)
    expected = dollars(money_cents(str(snapshot.get("price")))) if listing_format(snapshot)["format"] == FIXED_PRICE else ""
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
    chosen = listing_format(snapshot)
    if chosen["format"] == AUCTION:
        same_price = _same_amount((offer.get("pricingSummary") or {}).get("auctionStartPrice"), chosen["start"])
    else:
        same_price = str((offer.get("pricingSummary") or {}).get("price", {}).get("value") or "") == expected
    if product.get("title") != title or not same_price:
        raise ValueError("eBay has not confirmed the update. Check the live listing before trying again.")
    listing_id = str((offer.get("listing") or {}).get("listingId") or listing_id)
    if offer.get("status") == "PUBLISHED" and listing_id:
        return {"ok": True, "state": "published", "listing_url": _listing_url(listing_id)}
    raise ValueError("eBay has not confirmed this listing is live. Check eBay before listing again.")
