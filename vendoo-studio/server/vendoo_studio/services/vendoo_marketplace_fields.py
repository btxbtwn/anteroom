"""The marketplace-specific pieces of a Vendoo item.

Form defaults this seller expects on every listing (eBay Best Offer, Etsy's
codes and tags, Poshmark Smart Sell, Mercari's Ground Advantage label,
Facebook's shipping and offers, Vinted's package), the brand each form reads,
and Depop's option codes.
"""

from __future__ import annotations

import math
import re
from typing import Any

from vendoo_studio.models.etsy_fields import sanitize_etsy_tags
from vendoo_studio.models.facebook_shipping import package_weight_label
from vendoo_studio.models.mercari_shipping import (
    DEFAULT_PACKAGE_OUNCES,
    ground_advantage,
    package_ounces,
)
from vendoo_studio.models.schema import DEPOP_OPTION_CODES
from vendoo_studio.services.vendoo_item_shape import (
    SPECIFICS_SOURCES,
    _bucket,
    _clean,
    _norm,
    _num_str,
    _string_list,
)
from vendoo_studio.services.vendoo_specifics import specifics_key

# Etsy form codes (display labels overwrite these and leave the dropdown blank).
ETSY_WHO_CODES = {
    "another company or person": "someone_else",
    "someone else": "someone_else",
    "a member of my shop": "collective",
    "i did": "i_did",
}
ETSY_WHAT_CODES = {
    "a finished product": "0",
    "a supply or tool to make things": "1",
}
# Vendoo's listingStateOptions: draft = Draft Listing, active = Live Listing, edit = Inactive.
ETSY_LIVE_LISTING = "active"

# Marketplace form brand fields live on overrides, not categorySpecifics. The
# Chrome filler writes `#listings.<mp>.overrides.brand`; create must too.
# Grailed calls it the designer; its form reads the same override.
_BRAND_OVERRIDE_MARKETPLACES = frozenset({"ebay", "etsy", "poshmark", "mercari", "depop", "grailed"})

# Values that mean "there is no brand" on Mercari — check No Brand/Not sure.
_MERCARI_NO_BRAND_TOKENS = frozenset({
    "", "unbranded", "no brand", "not sure", "no brand not sure", "no brand/not sure",
    "none", "n a", "na", "unknown",
})


def _listing_brand(listing: dict[str, Any], marketplace: str) -> str:
    """Brand for one marketplace form: specifics override, else general brand."""
    raw = listing.get(SPECIFICS_SOURCES.get(marketplace, f"{marketplace}_specifics"))
    if isinstance(raw, dict):
        specific = _clean(raw.get("brand"))
        if specific:
            return specific
    return _clean(listing.get("brand")) or ""


# Offer floors from the list-this PRICING formula (skills/list-this/SKILL.md):
# auto-accept offers at list - $2, turn down anything under list - $4. Offers in
# between wait for the seller.
AUTO_ACCEPT_BELOW_LIST = 2
MINIMUM_BELOW_LIST = 4


def offer_floors(price: Any) -> tuple[str, str] | None:
    """(auto-accept, minimum) offer in whole dollars, or None without a usable price.

    Below $5 the minimum would reach $0, so there is no floor and every offer
    waits for the seller.
    """
    try:
        amount = float(str(price or "").replace("$", "").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return None
    whole = int(round(amount))
    if whole - MINIMUM_BELOW_LIST < 1:
        return None
    return str(whole - AUTO_ACCEPT_BELOW_LIST), str(whole - MINIMUM_BELOW_LIST)


def _listing_price(listing: dict[str, Any], general: dict[str, Any]) -> str:
    return _num_str(listing.get("price") or general.get("price"))


def _apply_ebay_pricing(
    known: dict[str, Any],
    general: dict[str, Any],
    listing: dict[str, Any],
) -> None:
    """Force Fixed Price with Best Offer Auto-accept + minimum offer."""
    known["pricingFormat"] = "FixedPriceItem"
    details = known.get("pricingFormatDetails")
    if not isinstance(details, dict):
        details = {}
        known["pricingFormatDetails"] = details
    fixed = details.get("fixedPrice")
    if not isinstance(fixed, dict):
        fixed = {}
        details["fixedPrice"] = fixed
    price = _listing_price(listing, general)
    if price:
        fixed["buyItNowPrice"] = price
    fixed["duration"] = _clean(fixed.get("duration")) or "GTC"
    fixed["allowBestOffer"] = True
    accept, minimum = offer_floors(price or fixed.get("buyItNowPrice")) or ("", "")
    fixed["acceptOffersOfAtLeast"] = accept
    fixed["declineOffersLowerThan"] = minimum


def _etsy_who_code(raw: Any) -> str:
    text = _clean(raw) or ""
    if not text:
        return "someone_else"
    if text in {"someone_else", "collective", "i_did"}:
        return text
    return ETSY_WHO_CODES.get(_norm(text), "someone_else")


def _etsy_what_code(raw: Any) -> str:
    text = _clean(raw) or ""
    if not text:
        return "0"
    if text in {"0", "1"}:
        return text
    return ETSY_WHAT_CODES.get(_norm(text), "0")


# Etsy's when_made codes, which Vendoo's form stores, by Studio's dropdown label.
_ETSY_WHEN_CODES = {
    "Made To Order (Not Yet Made)": "made_to_order",
    "2020 - 2026 (Recently)": "2020_2026",
    "2010 - 2019 (Recently)": "2010_2019",
    "2007 - 2009 (Recently)": "2007_2009",
    "Before 2007 (Vintage)": "before_2007",
    "2000 - 2006 (Vintage)": "2000_2006",
    "1990s (Vintage)": "1990s",
    "1980s (Vintage)": "1980s",
    "1970s (Vintage)": "1970s",
    "1960s (Vintage)": "1960s",
    "1950s (Vintage)": "1950s",
    "1940s (Vintage)": "1940s",
    "1930s (Vintage)": "1930s",
    "1920s (Vintage)": "1920s",
    "1910s (Vintage)": "1910s",
    "1900 - 1909 (Vintage)": "1900s",
    "1800s (Vintage)": "1800s",
    "1700s (Vintage)": "1700s",
    "Before 1700 (Vintage)": "before_1700",
}
_ETSY_WHEN_CODE_VALUES = frozenset(_ETSY_WHEN_CODES.values())


def _etsy_when_code(raw: Any) -> str:
    """The form's code for a when-made label; a vintage decade stays that decade.

    Made To Order is not selected. An already-made item gets a year range.
    Anything unreadable falls to the recent bucket.
    """
    from vendoo_studio.models.etsy_fields import etsy_when_options, etsy_when_to_store

    text = _clean(raw) or ""
    if not text:
        return "2020_2026"
    if text in _ETSY_WHEN_CODE_VALUES and text != "made_to_order":
        return text
    return _ETSY_WHEN_CODES.get(etsy_when_to_store(text, options=etsy_when_options()), "2020_2026")


def _ebay_brand_label(brand: str) -> str:
    """eBay's Brand dropdown matches ``Unbranded``, not a lowercased free-text."""
    text = _clean(brand) or ""
    if _norm(text) == "unbranded":
        return "Unbranded"
    return text


def _apply_etsy_listing_codes(known: dict[str, Any], listing: dict[str, Any]) -> None:
    """Map Studio's Etsy display labels onto the codes the form stores."""
    raw = listing.get("etsy_specifics")
    raw = raw if isinstance(raw, dict) else {}
    who = known.get("whoMade") or raw.get("who_made") or raw.get("whoMade")
    what = known.get("whatIsIt") or raw.get("what_is") or raw.get("whatIsIt")
    when = known.get("whenMade") or raw.get("when_made") or raw.get("whenMade")
    known["whoMade"] = _etsy_who_code(who)
    known["whatIsIt"] = _etsy_what_code(what)
    known["whenMade"] = _etsy_when_code(when)
    # Finished-product resale is a supply of someone else's work.
    known["isSupply"] = _etsy_what_code(what) == "0" or bool(known.get("isSupply", True))
    # Always "Live Listing"; the form stores the code, not the label.
    known["listingState"] = ETSY_LIVE_LISTING


def _apply_etsy_tags(known: dict[str, Any], general: dict[str, Any]) -> None:
    """Etsy Tags come from the general form's Tags.

    Vendoo's form save copies them over; an API write has to do it itself or
    the Etsy form shows no tags. Strip characters Etsy rejects (#, /, &, …).
    """
    tags = sanitize_etsy_tags(_string_list(general.get("tags")))
    if tags:
        known["tags"] = tags


def _apply_poshmark_original_price(known: dict[str, Any]) -> None:
    """Poshmark original price is always 0. No compare-at price."""
    known["originalPrice"] = "0"


def _apply_poshmark_smart_sell(
    known: dict[str, Any],
    general: dict[str, Any],
    listing: dict[str, Any],
) -> None:
    """Smart Sell accepts offers down to the same minimum as eBay, or stays off."""
    floors = offer_floors(_listing_price(listing, general))
    known["smartSell"] = {"enabled": floors is not None, "minPrice": floors[1] if floors else ""}


def _apply_mercari_smart_pricing(known: dict[str, Any]) -> None:
    """Leave Mercari Smart Pricing off.

    The seller lists at the price they set. Vendoo turns Smart Pricing on by
    default, and that demands a floor price the form then blocks on
    ("Missing Floor Price"). A listing that asks for it, or a draft that
    already has it, is turned back off on every save.
    """
    known["smartPricing"] = False
    known["floorPrice"] = ""


def _apply_mercari_shipping(
    known: dict[str, Any],
    weight: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> None:
    """Always select the prepaid USPS Ground Advantage label for this weight.

    The form lists only the tiers that carry the package, so the id has to
    match the listing's weight — a fixed one sits outside the list and shows
    as an empty Shipping Label. A weightless listing is the same story: the
    form offers no tier at all, so it is given the default half-pound package
    the tier is picked from. Non-GA labels from a previous draft are
    overwritten: after Regenerate + API save the seller expects Ground
    Advantage every time.
    """
    ounces = package_ounces((weight or {}).get("pounds"), (weight or {}).get("ounces"))
    if ounces <= 0:
        ounces = DEFAULT_PACKAGE_OUNCES
        if isinstance(overrides, dict):
            overrides["weight"] = {"pounds": "0", "ounces": str(DEFAULT_PACKAGE_OUNCES)}
    carrier_id, tier_label = ground_advantage(ounces)
    known["shippingLabel"] = tier_label
    shipping = known.get("shipping")
    if not isinstance(shipping, dict):
        shipping = {}
        known["shipping"] = shipping
    shipping["deliveryMethod"] = "mercari_shipping"
    shipping["payerId"] = 1
    shipping["carrierId"] = carrier_id


def _learned(schema: dict[str, Any] | None, marketplace: str, name: str) -> dict[str, Any]:
    table = (((schema or {}).get("marketplaces") or {}).get(marketplace) or {}).get(name)
    return table if isinstance(table, dict) else {}


def _apply_facebook_shipping(
    known: dict[str, Any],
    weight: dict[str, Any] | None,
    schema: dict[str, Any] | None,
    unresolved: list[dict[str, str]] | None,
) -> None:
    """Pick Facebook's Package weight from the weight, and the carrier the seller uses.

    Delivery method, shipping option and free shipping are the account's
    defaults and are left alone. The carrier is whatever the seller chose on
    their own Facebook forms for this tier — Facebook decides which carriers a
    tier offers — or their only carrier when they have only ever used one.
    """
    label = package_weight_label(weight)
    if not label:
        return
    known["packageWeightLabel"] = label
    carriers = _learned(schema, "facebook", "shippingCarrier")
    carrier = carriers.get(label)
    if not carrier and len(set(carriers.values())) == 1:
        carrier = next(iter(carriers.values()))
    if carrier:
        known["carrier"] = carrier
    elif unresolved is not None:
        unresolved.append({"field": "facebook:Shipping carrier", "value": ""})


def _apply_facebook_offers(known: dict[str, Any], price: Any) -> None:
    """Turn Allow Offers on, with the lowest Minimum price Facebook accepts.

    Facebook refuses a minimum under half the price, so the floor is half,
    rounded up to a whole dollar.
    """
    known["allowOffers"] = True
    try:
        amount = float(_num_str(price))
    except ValueError:
        return
    if amount > 0:
        known["minimumPrice"] = str(math.ceil(amount / 2))


# Vinted's Shoulder Width is the garment's width across, measured pit to pit.
_PIT_TO_PIT_RE = re.compile(r"(?i)\bpit[\s-]*to[\s-]*pit\s*:\s*(\d+(?:\.\d+)?)")
_LENGTH_RE = re.compile(r"(?i)\blength\s*:\s*(\d+(?:\.\d+)?)")
VINTED_MEASUREMENT_KEYS = ("width", "length")


def vinted_measurements(listing: dict[str, Any]) -> dict[str, str]:
    """Shoulder Width and Length, in inches, from the description's Measurements line.

    That line holds the seller's own tape-measure numbers, written there
    verbatim. Nothing else is trusted for them, so a model's guess at a
    measurement never reaches the form.
    """
    from vendoo_studio.services.listing_carryover import description_block

    block = description_block(listing.get("description"), "measurements?")
    out: dict[str, str] = {}
    for key, pattern in (("width", _PIT_TO_PIT_RE), ("length", _LENGTH_RE)):
        match = pattern.search(block)
        if match:
            out[key] = match.group(1)
    return out


def _apply_vinted_package(
    section: dict[str, Any],
    leaf_id: str,
    listing: dict[str, Any],
    weight: dict[str, Any] | None,
    schema: dict[str, Any] | None,
    unresolved: list[dict[str, str]] | None,
) -> None:
    """Vinted's measurements and Package Size, stored as ``{leaf}_<field>``.

    Package Size is a Vinted id offered per category, so it is learned from the
    seller's own Vinted forms against each package's weight: an item gets the
    size the seller chose for the lightest package that weighed at least as
    much. A heavier item than any seen is left for the seller to pick.
    """
    specifics = _bucket(section, "categorySpecifics")
    measured = vinted_measurements(listing)
    for key in VINTED_MEASUREMENT_KEYS:
        if key in measured:
            specifics[specifics_key(leaf_id, key)] = measured[key]
        else:
            specifics.pop(specifics_key(leaf_id, key), None)

    weight = weight if isinstance(weight, dict) else {}
    ounces = package_ounces(weight.get("pounds"), weight.get("ounces")) or DEFAULT_PACKAGE_OUNCES
    sizes = _learned(schema, "vinted", "packageSize")
    heavier = sorted(
        (int(seen), size) for seen, size in sizes.items()
        if str(seen).isdigit() and int(seen) >= ounces and size not in (None, "")
    )
    if heavier:
        specifics[specifics_key(leaf_id, "packageSizeId")] = str(heavier[0][1])
    elif unresolved is not None:
        unresolved.append({"field": "vinted:Package Size", "value": ""})


def _mercari_wants_no_brand(brand: str) -> bool:
    return _norm(brand) in _MERCARI_NO_BRAND_TOKENS


def _apply_marketplace_brand(
    section: dict[str, Any],
    marketplace: str,
    listing: dict[str, Any],
) -> None:
    """Put brand where Vendoo's forms actually read it — ``overrides.brand``.

    Mercari has no catch-all brand option: an empty or unlisted brand is the
    No Brand/Not sure checkbox (``overrides.noBrand``), not a typed value.
    Depop's catch-all is the ``Other`` option.
    """
    if marketplace not in _BRAND_OVERRIDE_MARKETPLACES:
        return
    brand = _listing_brand(listing, marketplace)
    overrides = section.setdefault("overrides", {})
    if marketplace == "mercari":
        if _mercari_wants_no_brand(brand):
            overrides["noBrand"] = True
            overrides.pop("brand", None)
        elif brand:
            overrides["brand"] = brand
            overrides["noBrand"] = False
        return
    if marketplace == "depop" and _mercari_wants_no_brand(brand):
        overrides["brand"] = "Other"
        return
    if not brand:
        return
    if marketplace == "ebay":
        brand = _ebay_brand_label(brand)
    overrides["brand"] = brand


def depop_option_codes(field: str, value: Any) -> tuple[list[str], list[str]]:
    """Encode Depop style/age/source labels (or codes) as Vendoo's codes.

    Returns ``(codes, unknown)``; Vendoo's Depop mapper would drop the unknown
    ones anyway, so they are reported rather than sent.
    """
    lookup: dict[str, str] = {}
    for label, code in DEPOP_OPTION_CODES[field].items():
        lookup[_norm(label)] = code
        lookup[_norm(code)] = code
    codes: list[str] = []
    unknown: list[str] = []
    for item in _string_list(value):
        code = lookup.get(_norm(item))
        if code is None:
            unknown.append(item)
        elif code not in codes:
            codes.append(code)
    return codes, unknown


def _apply_depop_option_codes(
    known: dict[str, Any], unresolved: list[dict[str, str]] | None
) -> None:
    for field in DEPOP_OPTION_CODES:
        codes, unknown = depop_option_codes(field, known.get(field))
        known[field] = codes
        if unresolved is not None:
            for item in unknown:
                unresolved.append({"field": f"depop:{field}", "value": item})
