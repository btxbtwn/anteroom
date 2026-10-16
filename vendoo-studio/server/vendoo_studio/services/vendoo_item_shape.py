"""The shape of a Vendoo item, and Vendoo's own defaults for one.

Keys, the marketplaces an item carries a section for, and what Vendoo's
``getInitGeneralDetails`` / ``getInit*Form`` seed before anything is filled
in, plus the small value helpers every other piece of the item is built with.
See ``docs/vendoo-listing-architecture.md``.
"""

from __future__ import annotations

import re
from typing import Any

from vendoo_studio.models.ebay_fields import EBAY_CONDITION_DESCRIPTION
from vendoo_studio.models.mercari_shipping import (
    DEFAULT_SHIPPING_LABEL,
    ground_advantage,
)

API_BASE = "https://api.web.vendoo.co"
MSVC_BASE = "https://us.vendoo.co"
FUNCTIONS_BASE = "https://us-central1-vendoo-prod-7948f.cloudfunctions.net"
FIREBASE_PROJECT = "vendoo-prod-7948f"

GET_ITEM = "/api/item/{item_id}"
CATEGORY_SEARCH = "/api/category/search"
IMAGE_UPLOAD_URL = "/inventory/v1/images/url"
ITEMS_FUNCTION = "/items"
# Bulk importer path; takes items that came *from* a marketplace. Kept for
# reference — the form path above is what a net-new item uses.
IMPORT_NORMALIZED = "/api/rest/v1/import/items_normalized"

# Deliberately not wired up: these publish to marketplaces, and AGENTS.md says
# automation stops at saved drafts.
_LIST_ITEM = "/api/item/{item_id}/list"
_DELIST_ITEM = "/api/item/{item_id}/delist"

GENERAL_KEY = "generalDetails"
LISTINGS_KEY = "listings"
DEFAULT_ORIGIN = "vendoo"
CURRENT_ITEM_VERSION = 10

# Every marketplace Vendoo's new-item factory seeds a listings section for.
ALL_MARKETPLACES = (
    "ebay", "etsy", "poshmark", "mercari", "grailed", "depop", "tradesy", "kidizen",
    "facebook", "shopify", "sellhound", "vestiaire", "vinted", "whatnot", "sellwild", "vestiaireApi",
)

# generalDetails fields Vendoo stores as a coded object with a display label.
CODED_FIELDS = ("condition", "primaryColor", "secondaryColor")

_LABEL_KEYS = ("displayName", "label", "name")
_VALUE_KEYS = ("value", "id", "key", "code")

# USPS Ground Advantage, the prepaid label this seller ships every item with.
# The tier follows the package weight — see ``models.mercari_shipping``.
MERCARI_GROUND_ADVANTAGE_LABEL = DEFAULT_SHIPPING_LABEL

# Grailed ships per region; US is the one Vendoo turns on by default.
GRAILED_SHIPPING_REGIONS = ("ca", "uk", "eu", "au", "asia", "other")

# Poshmark Smart Sell floor used on this seller's working drafts.
POSHMARK_SMART_SELL_MIN = "5"

SPECIFICS_SOURCES = {
    mp: f"{mp}_specifics"
    for mp in ("ebay", "poshmark", "mercari", "depop", "etsy", "grailed", "vinted", "facebook", "sellwild")
}

# Studio names its listing fields in camelCase; Vendoo names the same fields
# per category ("Sleeve Length", "Country of Origin"). Matching is done on the
# words rather than a hand-kept table, so a category Studio has never seen
# still lines up. See ``vendoo_specifics`` for where the field list comes from.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# --------------------------------------------------------------------------
# Vendoo's defaults (mirrors getInitGeneralDetails / getInit*Form)
# --------------------------------------------------------------------------


def _weight_dims_overrides() -> dict[str, Any]:
    return {
        "quantity": "1",
        "weight": {"pounds": "0", "ounces": "0"},
        "dimensions": {"length": "0", "width": "0", "height": "0"},
    }


def default_general_details() -> dict[str, Any]:
    return {
        "images": [],
        "videos": [],
        "title": "",
        "description": "",
        "notes": "",
        "brand": "",
        "condition": "",
        "primaryColor": "",
        "secondaryColor": "",
        "sku": "",
        "category": "",
        "weight": {"pounds": "0", "ounces": "0"},
        "dimensions": {"length": "0", "width": "0", "height": "0"},
        "price": "",
        "cost": "",
        "tags": [],
        "quantity": "1",
        "size": {"option": {"label": "", "value": ""}, "scale": {"label": "", "value": ""}},
    }


def _marketplace_specific_defaults(marketplace: str) -> dict[str, Any]:
    if marketplace == "ebay":
        return {
            "shippingPolicyId": "", "paymentPolicyId": "", "returnsPolicyId": "",
            "returnPayedBy": "", "returnWithin": "", "returnRefundMethod": "",
            "shippingService": "", "statusItem": "Active",
            "shipping": {"cost": "", "method": "Standard", "location": "", "handling": "", "type": ""},
            "paymentMethod": "PayPal", "conditionDescription": EBAY_CONDITION_DESCRIPTION,
            "paypalEmail": "",
            "acceptReturns": "", "pricingFormat": "FixedPriceItem",
            "pricingFormatDetails": {
                "auction": {"duration": "Days_7", "startingPrice": "", "buyItNowPrice": "",
                            "allowBestOffer": "", "acceptOffersOfAtLeast": "", "declineOffersLowerThan": ""},
                # Fixed Price + Best Offer with Auto-accept — matches this seller's drafts.
                "fixedPrice": {"duration": "GTC", "buyItNowPrice": "", "allowBestOffer": True,
                               "acceptOffersOfAtLeast": "", "declineOffersLowerThan": ""},
            },
        }
    if marketplace == "etsy":
        return {
            "listingState": None, "renewalOption": None, "quantity": 1,
            "shippingTemplateID": "", "processingProfileID": "", "tags": [],
            # Taken from an item this seller actually listed. "i_did" is wrong
            # for resale — Etsy asks who made it, and it was not the seller —
            # and Vendoo stores whatIsIt coded rather than as a word.
            "whoMade": "someone_else", "isSupply": True, "whenMade": "2020_2026", "whatIsIt": "0",
            "returnPolicyID": None, "materials": [], "listingType": "",
        }
    if marketplace == "poshmark":
        return {
            "originalPrice": "",
            "smartSell": {"enabled": True, "minPrice": POSHMARK_SMART_SELL_MIN},
        }
    if marketplace == "mercari":
        return {
            "tags": [], "smartPricing": False, "floorPrice": "",
            # Mercari's prepaid Ground Advantage label; the tier is rewritten
            # from the package weight once the listing's weight is known.
            "shippingLabel": MERCARI_GROUND_ADVANTAGE_LABEL,
            "shipping": {
                "deliveryMethod": "mercari_shipping", "location": "",
                "payerId": 1, "carrierId": ground_advantage(None)[0],
            },
            "mercariLocalInformation": "",
        }
    if marketplace == "depop":
        return {
            "priceCurrency": "USD", "shippingMethods": [], "shippingMethod": "", "nationalShippingCost": "",
            "age": [], "source": [], "style": [],
            "location": {"geoLat": 0, "geoLng": 0, "address": "", "countryCode": "", "id": "", "zipCode": ""},
        }
    if marketplace == "grailed":
        # Vendoo's form ships US at $30 and every other region off; the seller
        # sets the real rate on the form.
        shipping = {region: {"amount": 0, "enabled": False} for region in GRAILED_SHIPPING_REGIONS}
        shipping["us"] = {"amount": 30, "enabled": True}
        return {
            "makeoffer": False, "paypal_id": "", "buynow": True, "currency": "USD",
            "minimum_price": "", "smart_price": False, "measurementType": "cm", "measurements": {},
            "shipping": shipping,
            "shippingForm": {"label": "", "method": "", "address": "", "freeShipping": False},
        }
    return {}


def default_listing_section(marketplace: str) -> dict[str, Any]:
    """One ``listings.<marketplace>`` entry as Vendoo's factory seeds it."""
    section: dict[str, Any] = {
        "marketplaceID": marketplace,
        "dateCreated": "",
        "dateLastModified": "",
        "type": "listing",
        "status": {"notListed": True},
        "overrides": _weight_dims_overrides() if marketplace in ("ebay", "etsy", "poshmark", "mercari", "sellwild") else (
            {"quantity": "1"} if marketplace in ("depop", "grailed", "vinted", "facebook") else {}
        ),
        "categorySpecifics": {},
        "listingAttemptMessages": [],
        "marketplaceSpecifics": _marketplace_specific_defaults(marketplace),
        "sales": [],
    }
    if marketplace in ("shopify", "sellhound", "sellwild", "whatnot", "vinted", "facebook"):
        section["listedID"] = ""
        section["listingURL"] = ""
    return section


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _label_of(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in _LABEL_KEYS:
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _code_of(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    for key in _VALUE_KEYS:
        if value.get(key) not in (None, ""):
            return value[key]
    return None


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part or "").strip()]
    return [str(value).strip()]


def _num_str(value: Any) -> str:
    """Vendoo's form stores numbers as strings ("48", "0")."""
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _bucket(section: dict[str, Any], name: str) -> dict[str, Any]:
    bucket = section.get(name)
    if not isinstance(bucket, dict):
        bucket = {}
        section[name] = bucket
    return bucket
