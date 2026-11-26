"""What a direct List to Poshmark, Mercari, or Depop needs before anything is written.

Imported ``marketplace_category_ids`` remain historical values.
These checks read the same snapshot and only decide whether direct List can start.
These checks show unsupported colors, missing categories and shipping details,
and other field gaps beside List. A fractional Poshmark Listing Price also
stops the marketplace write before it starts.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP

from anteroom.services.direct_preflight import Context, gap, requirement
from anteroom.services.mercari_shipping_label import PATH as LABEL_PATH
from anteroom.services.mercari_shipping_label import label_answer

# Poshmark's catalog colors (catalog_v2, read 2026-10-25). Grey and Beige are its Gray and Tan.
POSHMARK_COLORS = (
    "Black", "Blue", "Brown", "Cream", "Gold", "Gray", "Green", "Orange", "Pink", "Purple", "Red",
    "Silver", "Yellow", "Tan", "White",
)
_POSHMARK_TRANSLATE = {"grey": "Gray", "beige": "Tan"}


def _money(value) -> Decimal | None:
    try:
        amount = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - a blank or odd price is handled by the caller
        return None
    return amount if amount.is_finite() else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _whole_dollars(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def poshmark_fractional_price_error(price: int | float | None) -> str | None:
    if price is not None and price % 1 != 0:
        return "Enter a whole-dollar price for Poshmark."
    return None


def poshmark_fee_cents(sale_cents: int) -> int:
    """Poshmark's cut of a sale: $2.95 under $15, 20% from $15."""
    return 295 if sale_cents < 1500 else math.floor(sale_cents / 5 + 0.5)


def poshmark_lowest_smart_floor(shipping_discount_cents: int = 0) -> int:
    """The lowest whole-dollar Smart Sell price that covers Poshmark's fee and the
    seller's shipping discount. Poshmark refuses the listing below it."""
    floor = 3  # Poshmark's lowest listing price
    while floor * 100 - poshmark_fee_cents(floor * 100) - shipping_discount_cents <= 0:
        floor += 1
    return floor


def poshmark_smart_minimum(snapshot: dict) -> int:
    """The Smart Sell lowest price a direct List sends, in whole dollars, or 0 for off.

    Mirrors the extension's ``poshmarkSmartMinimum``: the Poshmark tab's own
    setting decides when it has one, then the prepared form's.
    """
    specifics = _dict(snapshot.get("poshmark_specifics"))
    if specifics.get("availability") == "not_for_sale":
        return 0
    direct = _dict(specifics.get("smartSell"))
    form_smart = _dict(snapshot.get("form")).get("specifics")
    form_smart = form_smart.get("smartSell") if isinstance(form_smart, dict) else None
    if isinstance(direct.get("enabled"), bool):
        raw = direct.get("minimumPrice") if direct["enabled"] else None
    elif isinstance(form_smart, dict):
        raw = form_smart.get("minPrice") if form_smart.get("enabled") else None
    else:
        raw = specifics.get("smartSellMinimumPrice") or direct.get("minimumPrice")
    amount = _money(raw)
    return _whole_dollars(amount) if amount is not None and amount > 0 else 0


def poshmark_shipping_discount_cents(snapshot: dict) -> int:
    """The shipping discount the seller chose for this listing, in cents, from
    the amounts their saved Poshmark form read from the account. 0 when none
    is chosen or its amount is not known."""
    chosen = str(_dict(snapshot.get("poshmark_specifics")).get("shippingDiscountId") or "")
    if not chosen:
        return 0
    saved = _dict(_dict(snapshot.get("_native_forms")).get("poshmark"))
    for row in saved.get("fields") or []:
        if isinstance(row, dict) and row.get("path") == "poshmark_specifics.shippingDiscountId":
            amount = _money(_dict(row.get("option_amounts")).get(chosen))
            return max(0, int(amount * 100 + Decimal("0.5"))) if amount is not None else 0
    return 0


def poshmark_color_name(value: str) -> str | None:
    """Poshmark's spelling of a color, or None when that color is left off the listing."""
    text = str(value or "").strip()
    if not text:
        return None
    translated = _POSHMARK_TRANSLATE.get(text.casefold(), text)
    return next((name for name in POSHMARK_COLORS if name.casefold() == translated.casefold()), None)


@requirement("poshmark")
def poshmark_requirements(snapshot: dict, context: Context) -> list[dict]:
    del context
    found: list[dict] = []
    from anteroom.services.seller_overrides import marketplace_text

    title = marketplace_text(snapshot, "poshmark", "title").strip()
    description = marketplace_text(snapshot, "poshmark", "description").strip()
    if not title:
        found.append(gap("title", "Poshmark needs a title."))
    elif len(title) > 80:
        found.append(gap("title", "Poshmark titles are at most 80 characters."))
    if not description:
        found.append(gap("description", "Poshmark needs a description."))
    elif len(description) > 1500:
        found.append(gap("description", "Poshmark descriptions are at most 1,500 characters."))
    from anteroom.services.seller_overrides import price_for

    price = price_for(snapshot, "poshmark")
    if error := poshmark_fractional_price_error(price):
        found.append(gap("price", error))
    elif price is None or price < 3:
        found.append(gap("price", "Poshmark needs a price of $3 or more."))
    smart = poshmark_smart_minimum(snapshot)
    shipping = poshmark_shipping_discount_cents(snapshot)
    lowest = poshmark_lowest_smart_floor(shipping)
    if 0 < smart < lowest:
        covers = "Poshmark's fee and your shipping discount" if shipping else "Poshmark's fee"
        found.append(gap("poshmark_specifics.smartSell.minimumPrice",
                         f"Poshmark won't turn on Smart Sell below ${lowest}: the Lowest Price has to cover "
                         f"{covers}. Raise the Lowest Price to ${lowest} or more, or turn Smart Sell off."))
    dropped = []
    for value in (snapshot.get("primaryColor") or snapshot.get("color"), snapshot.get("secondaryColor")):
        text = str(value or "").strip()
        if text and poshmark_color_name(text) is None and text not in dropped:
            dropped.append(text)
    if dropped:
        listed = " and ".join(dropped)
        found.append(gap("primaryColor", f"Poshmark does not list {listed}, so that color is left off.", blocking=False))
    if not _category_id(snapshot, "poshmark"):
        found.append(gap("direct_category_ids.poshmark", "Poshmark needs a category from its own list."))
    return found


def _category_id(snapshot: dict, marketplace: str) -> str:
    """The marketplace tab's own category. Historical imported category ids are not native taxonomy ids."""
    direct = snapshot.get("direct_category_ids")
    if not isinstance(direct, dict):
        direct = {}
    return str(direct.get(marketplace) or "").strip()


def _mercari_subcategories(chosen: str) -> list[str]:
    """The names under a Mercari category in its own catalog. A leaf has none."""
    from anteroom.services.mercari_form import catalog

    return [str(row["name"]) for row in catalog()["itemCategories"] if str(row["parentId"]) == chosen]


@requirement("mercari")
def mercari_requirements(snapshot: dict, context: Context) -> list[dict]:
    found: list[dict] = []
    from anteroom.services.seller_overrides import marketplace_text

    description = marketplace_text(snapshot, "mercari", "description").strip()
    if not description:
        found.append(gap("description", "Mercari needs a description."))
    elif len(description) > 1000:
        found.append(gap("description", "Mercari descriptions are at most 1,000 characters."))
    form = snapshot.get("form")
    if not isinstance(form, dict):
        form = {}
    chosen = _category_id(snapshot, "mercari")
    chosen = chosen if re.fullmatch(r"\d{1,6}", chosen) else ""
    children = _mercari_subcategories(chosen) if chosen else []
    if children:
        found.append(gap("direct_category_ids.mercari",
                         f"That Mercari category has subcategories ({', '.join(children)}). Choose the most specific one."))
    elif not chosen:
        found.append(gap("direct_category_ids.mercari", "Mercari needs a category."))
    specifics = snapshot.get("mercari_specifics")
    if not isinstance(specifics, dict):
        specifics = {}
    package = specifics.get("package")
    if not isinstance(package, dict):
        package = {}
    overrides = form.get("overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    # Mercari's form asks these on the page. Answering them here keeps List
    # Everywhere from starting a job Chrome would refuse before its first write.
    if not _mercari_ships_from(snapshot, specifics, form):
        found.append(gap("mercari_specifics.shipsFrom", "Mercari needs the ZIP code the item ships from."))
    if str(specifics.get("shippingMethod") or "MERCARI").upper() != "SOYO":
        if str(package.get("fitsShoebox") or "").strip().lower() not in {"yes", "no"}:
            found.append(gap("mercari_specifics.package.fitsShoebox",
                             "Mercari needs an answer to “Will your item fit in a shoebox?”"))
        if not _mercari_ounces(snapshot, package, overrides):
            found.append(gap("mercari_specifics.package.pounds", "Mercari needs a package weight for its prepaid label."))
        if label_answer(snapshot).source == "none":
            found.append(gap(LABEL_PATH, "Mercari needs a prepaid shipping label."))
    if smart_gap := _mercari_smart_pricing_gap(snapshot, specifics):
        found.append(smart_gap)
    # Chrome checks the selected leaf and size against Mercari's own master
    # catalog before uploading. Vendoo's mirrored size table is not consulted.
    del context
    return found


def mercari_whole_dollars(value: object) -> int | None:
    """Round a Mercari dollar amount to whole dollars the way Mercari's form does (``Math.round``)."""
    if value is None:
        return None
    amount = _money(value)
    return _whole_dollars(amount) if amount is not None else None


def _mercari_smart_pricing_amounts(snapshot: dict, specifics: dict) -> tuple[int, int | None] | None:
    """Whole-dollar ``(price, minimum)`` when Mercari Smart Pricing is on, else None.

    Mercari receives both values rounded to whole dollars (``Math.round``).
    """
    smart = specifics.get("smartPricing")
    if not isinstance(smart, dict) or smart.get("enabled") is not True:
        return None
    from anteroom.services.seller_overrides import price_for

    raw_price = price_for(snapshot, "mercari")
    rounded_price = mercari_whole_dollars(raw_price)
    price_dollars = rounded_price if rounded_price is not None and rounded_price > 0 else 0
    min_dollars = mercari_whole_dollars(smart.get("minimumPrice"))
    return price_dollars, min_dollars


def mercari_smart_pricing_meets_price(snapshot: dict) -> bool:
    """True when Mercari Smart Pricing is on and the listing price reaches or crosses its floor."""
    amounts = _mercari_smart_pricing_amounts(snapshot, _dict(snapshot.get("mercari_specifics")))
    if amounts is None:
        return False
    price_dollars, min_dollars = amounts
    if price_dollars <= 0:
        return False
    return price_dollars == 1 or (min_dollars is not None and min_dollars >= price_dollars)


def _mercari_smart_pricing_gap(snapshot: dict, specifics: dict) -> dict | None:
    """Mercari refuses drafts when Smart Pricing is on and its minimum meets or exceeds price."""
    amounts = _mercari_smart_pricing_amounts(snapshot, specifics)
    if amounts is None:
        return None
    price_dollars, min_dollars = amounts
    if price_dollars == 1:
        return gap(
            "mercari_specifics.smartPricing.minimumPrice",
            "Mercari Smart Pricing needs a listing price of $2 or more. Raise the price or turn Smart Pricing off.",
        )
    if min_dollars is None or min_dollars < 1:
        return gap(
            "mercari_specifics.smartPricing.minimumPrice",
            "Mercari Smart Pricing needs a minimum price of $1 or more, or turn Smart Pricing off.",
        )
    if price_dollars >= 2 and min_dollars >= price_dollars:
        highest = price_dollars - 1
        return gap(
            "mercari_specifics.smartPricing.minimumPrice",
            f"Mercari Smart Pricing minimum (${min_dollars}) must be below the listing price (${price_dollars}). "
            f"Lower the minimum price to ${highest} or less, or turn Smart Pricing off.",
        )
    return None


def _mercari_ships_from(snapshot: dict, specifics: dict, form: dict) -> str:
    """The five-digit ZIP Mercari's form requires, from the listing or Item Details."""
    form_specifics = form.get("specifics")
    if not isinstance(form_specifics, dict):
        form_specifics = {}
    shipping = form_specifics.get("shipping")
    if not isinstance(shipping, dict):
        shipping = {}
    for value in (specifics.get("shipsFrom"), form.get("ships_from_zip"), snapshot.get("zipCode"),
                  shipping.get("location")):
        text = str(value or "").strip()
        if re.fullmatch(r"\d{5}", text):
            return text
    return ""


def _mercari_ounces(snapshot: dict, package: dict, overrides: dict) -> int:
    """The package weight in whole ounces, in the order Chrome reads it."""
    def present(value) -> bool:
        return str(value if value is not None else "").strip() != ""

    if present(package.get("pounds")) or present(package.get("ounces")):
        sources = [(package.get("pounds"), package.get("ounces"))]
    else:
        weight = overrides.get("weight")
        if not isinstance(weight, dict):
            weight = {}
        sources = [(snapshot.get("weight_lb"), snapshot.get("weight_oz")),
                   (weight.get("pounds"), weight.get("ounces"))]
    for pounds, ounces in sources:
        try:
            total = math.ceil(float(pounds or 0) * 16 + float(ounces or 0))
        except (TypeError, ValueError):
            continue
        if total > 0:
            return total
    return 0


@requirement("depop")
def depop_requirements(snapshot: dict, context: Context) -> list[dict]:
    found: list[dict] = []
    specifics = snapshot.get("depop_specifics")
    if not isinstance(specifics, dict):
        specifics = {}
    # An explicit clear is not a request to use General. Name the editable Size
    # row before sending rather than letting a later shipping refusal hide it.
    size_key = "size" if "size" in specifics else "nativeSizeId"
    if size_key in specifics and not str(specifics[size_key] or "").strip():
        from anteroom.services.depop_form import catalog

        category = next((row for row in catalog()["categories"] if row["id"] == _category_id(snapshot, "depop")), {})
        if any(row["id"] == "variants" for row in category.get("fields") or []):
            fields = ((snapshot.get("_native_forms") or {}).get("depop") or {}).get("fields") or []
            path = next((row["path"] for row in fields if row.get("path") in {
                "depop_specifics.size", "depop_specifics.nativeSizeId"}), "depop_specifics.size")
            found.append(gap(path, "Choose a clothing size for this Depop category."))
    worldwide = specifics.get("worldwideShipping") or {}
    if worldwide.get("enabled") is True:
        amount = _money(worldwide.get("price"))
        if amount is None or not 0 <= amount < 100 or amount != amount.quantize(Decimal("0.01")):
            found.append(gap("depop_specifics.worldwideShipping.price", "Depop needs a worldwide shipping price from $0 to $99.99, in dollars and cents."))
    if len(str(specifics.get("sku", snapshot.get("sku")) or "")) > 50:
        found.append(gap("depop_specifics.sku", "Depop SKUs are at most 50 characters."))
    label = str(specifics.get("parcelSize") or specifics.get("parcel_size") or "").strip()
    if not label:
        found.append(gap("depop_specifics.parcelSize", "Depop needs a parcel size."))
    else:
        from anteroom.services.chrome_catalog import KNOWN_PARCELS, parcel_key

        if parcel_key(label) not in KNOWN_PARCELS:
            found.append(gap("depop_specifics.parcelSize", f'Depop does not offer "{label}" as a parcel size.'))
    if not _category_id(snapshot, "depop"):
        found.append(gap("direct_category_ids.depop", "Depop needs a category from its own list."))
    del context
    return found
