"""Depop's captured sell page, rendered locally without Chrome or an account read."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from anteroom.config import skills_dir
from anteroom.services.depop_measurements import measurement_inputs
from anteroom.services.seller_overrides import get_path, marketplace_text, price_for

DEPOP_CONDITIONS = frozenset({"brand_new", "used_like_new", "used_excellent", "used_good", "used_fair"})
DEPOP_COLOURS = frozenset({
    "black", "grey", "white", "brown", "tan", "cream", "yellow", "red", "burgundy",
    "pink", "purple", "blue", "navy", "green", "khaki", "orange", "silver", "gold", "multi",
})


def _condition_from_text(value: Any) -> str:
    """Depop's condition slug from a plain label, matching depop-native.js."""
    text = str(value or "").strip().lower()
    if text in DEPOP_CONDITIONS:
        return text
    for pattern, slug in (
        (r"defect|imperfection", "used_good"),
        (r"fair|poor", "used_fair"),
        (r"excellent", "used_excellent"),
        (r"like new|nwot|new without", "used_like_new"),
        (r"brand new|nwt|new with tags|^new\b", "brand_new"),
        (r"good|pre-owned|preowned|used", "used_good"),
    ):
        if re.search(pattern, text):
            return slug
    return ""


def general_condition(listing: dict) -> str:
    """The condition Depop sends when this form has none of its own, else empty."""
    form_data = listing.get("form")
    prepared = ((form_data.get("overrides") if isinstance(form_data, dict) else None) or {}).get("condition")
    return _condition_from_text(prepared) or _condition_from_text(listing.get("condition"))


def _colour_slug(value: Any) -> str:
    name = str(value or "").strip().lower().replace("gray", "grey").replace("beige", "tan")
    name = re.sub(r"^multi[- ]?colou?r(?:ed)?$", "multi", name)
    return name if name in DEPOP_COLOURS else ""


def general_colours(listing: dict) -> list[str]:
    """Up to two Depop colour slugs from General colors, matching depop-native.js."""
    colours = list(dict.fromkeys(
        slug for raw in (listing.get("primaryColor"), listing.get("secondaryColor"))
        if (slug := _colour_slug(raw))
    ))
    return colours[:2]


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/depop-native-catalog.json").read_text())


@lru_cache(maxsize=1)
def fixed_options() -> dict:
    shipping = json.loads((skills_dir() / "list-this/references/anteroom-dropdown-options.json").read_text())["forms"]["depop"]
    return {**catalog()["fixed_options"], "parcelSize": shipping["parcelSize"]}


@lru_cache(maxsize=1)
def category_choices() -> dict:
    """Depop's category picker, the same for every listing."""
    rows = catalog()["categories"]
    return {"options": [row["path"] for row in rows], "option_values": {row["path"]: row["id"] for row in rows}}


@lru_cache(maxsize=1)
def brand_choices() -> dict:
    """Depop's brand picker, the same for every listing."""
    return {"options": list(catalog()["brands"])}


@lru_cache(maxsize=1)
def _brands_by_fold() -> dict[str, str]:
    return {brand.strip().casefold(): brand for brand in catalog()["brands"]}


def listed_brand(name: str) -> str:
    """The Depop brand a brand name posts as, the way the extension picks it.

    A listed brand, or the one listed brand the name starts ("Columbia" is
    Columbia Sportswear). Unbranded, and anything Depop does not list, is Other.
    """
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return ""
    if wanted in {"unbranded", "no brand"}:
        return "Other"
    brands = _brands_by_fold()
    if wanted in brands:
        return brands[wanted]
    starts = [brand for fold, brand in brands.items() if fold.startswith(wanted + " ")]
    return starts[0] if len(starts) == 1 else "Other"


@lru_cache(maxsize=1)
def _by_id() -> dict:
    return {row["id"]: row for row in catalog()["categories"]}


def size_options(category_id: str) -> list[str]:
    """Depop's size labels for this category, or empty when the catalog has none."""
    if not category_id:
        return []
    category = _by_id().get(category_id) or {}
    return list(next((row["options"] for row in category.get("fields") or [] if row["id"] == "variants"), []) or [])


def size_label(category_id: str, size) -> str:
    """The size as this category's own list spells it, else the size as written.

    Depop lists a man's waist as 36", a top's as XL and a kid's as 16 years,
    while the tag says 36, XL or 16. The form offers the page's spelling, so a
    size is stored as the choice the page shows instead of a value its list
    never carries.
    """
    from anteroom.models.sizes import size_choice

    written = str(size or "").strip()
    if not written or not category_id:
        return written
    return size_choice(written, size_options(category_id)) or written


def form(listing: dict) -> dict:
    """Keep native rows and order; resolve labels to posting IDs only at publication."""
    from anteroom.services.native_editor import field
    from anteroom.services.seller_region import seller_region

    region = seller_region()
    currency = "US$" if region.country == "US" else region.currency
    carriers = {"US": "USPS", "GB": "Evri", "AU": "Australia Post"}
    carrier = carriers.get(region.country)
    specifics = listing.get("depop_specifics") or {}
    category_id = str((listing.get("direct_category_ids") or {}).get("depop") or "")
    category = _by_id().get(category_id)
    fixed = fixed_options()
    quote = catalog()["us_parcel_quotes"].get(category_id, {}) if region.country == "US" else {}

    def value(path: str, default: Any = "") -> Any:
        found = get_path(listing, path)
        return default if found is None else found

    def text(name):
        return marketplace_text(listing, "depop", name)

    def choice(path, label, options, section="Info", *, shared_options: bool = False, **extra):
        return field(path, label, value(path, [] if extra.get("multi") else ""), selection_only=True,
                     options=options if shared_options else list(options), options_source="marketplace",
                     section=section, **extra)

    category_picker = category_choices()
    rows = [
        field("marketplace_photos.depop", "", [], input_type="photos", section="Photos",
              help="Add up to 8 photos in JPEG or PNG format.", accept="image/jpeg,image/png,.jpg,.jpeg,.png",
              slot_labels=["Cover photo", "Front", "Back", "Side", "Label", "Detail", "Flaw"]),
        field("depop_specifics.description", "Description", text("description"), required=True, max_length=1000,
              section="Description", placeholder="eg. small grey Nike t-shirt, only worn a few times", counter=True, hashtag_max=5),
        field("direct_category_ids.depop", "Category", category_id, required=True, selection_only=True,
              options=category_picker["options"], option_values=category_picker["option_values"],
              options_source="marketplace", section="Info",
              clears=["depop_specifics.size", "depop_specifics.nativeSizeId", "depop_specifics.type", "depop_specifics.attributes"]),
    ]
    fixed_keys = {"source": "source", "age": "age", "style": "style", "colour": "primaryColor", "condition": "condition"}
    for captured in category["fields"] if category else catalog()["common_fields"]:
        key, label = captured["id"], captured["label"]
        if key.startswith("attributes."):
            path = "depop_specifics." + key
            maximum = captured["max_values"]
            rows.append(choice(path, label, captured["options"], multi=True, max_values=maximum,
                               category_specific=True, help=f"Select up to {maximum}"))
        elif key == "brand":
            row = choice("depop_specifics.brand", label, brand_choices()["options"], shared_options=True)
            row["value"] = listed_brand(text("brand"))
            rows.append(row)
        elif key in fixed_keys:
            enhance = key in {"source", "age", "style"}
            extra = {"multi": True, "max_values": captured.get("max_values") or 1} if key != "condition" else {"required": True}
            row = choice("depop_specifics." + key, label, fixed[fixed_keys[key]],
                         section="Enhance your listing" if enhance else "Info", **extra)
            row["option_values"] = dict(catalog()["fixed_option_values"][key])
            if key == "condition" and "condition" not in specifics:
                row["value"] = general_condition(listing)
            elif key == "colour":
                if "colour" not in specifics:
                    row["value"] = general_colours(listing)
                row["help"] = "Select up to two colors"
            elif key == "source":
                row["help"] = f"What kind of item is this? Add up to {row['max_values']}"
            elif key == "age":
                row["help"] = f"Do you know when this item was made? Add up to {row['max_values']}"
            elif key == "style":
                row["help"] = f"Pick relevant tags to describe this item's style. Add up to {row['max_values']}"
            rows.append(row)
        elif key == "variants":
            row = choice("depop_specifics.size", label, captured["options"], required=True,
                         category_specific=True, width="quarter")
            # The general size follows until this form sets its own, as brand and
            # description do, and it is the page's own spelling of that size.
            row["value"] = size_label(category_id, text("size"))
            rows.append(row)
        elif key == "quantity":
            inputs = measurement_inputs(specifics)
            for measurement in catalog()["product_measurements"].get(category_id.partition("_")[2], []):
                rows.append(field(f"depop_specifics.measurementInputs.{measurement}", catalog()["measurement_labels"][measurement],
                                  inputs.get(measurement), section="Info", placeholder="Inches or cm, e.g. 14 in"))
            rows.append(field("quantity", label, listing.get("quantity", 1), input_type="number", min_value=1,
                              step=1, section="Info", width="quarter"))
        elif key == "SKU":
            rows.extend([field("depop.note.sku", "Learn more about SKUs", "", input_type="note", section="Info"),
                         field("depop_specifics.sku", label, specifics.get("sku", listing.get("sku", "")),
                               max_length=50, section="Info", width="half")])
        elif key not in {"priceAmount", "shippingMethods"}:
            raise ValueError(f"Unmapped Depop field: {key}")
    # A category must be chosen before its dependent rows can be built.
    rows.extend([
        field("price", "Item price", price_for(listing, "depop"), required=True, input_type="number", min_value=1,
              max_value=9999999999.99, step=0.01, prefix=currency, section="Item price"),
        field("depop.note.fees", "No selling fees.", "", input_type="note", section="Item price"),
        field("depop.note.boost", "Learn more about boosting", "", input_type="note", section="Boost your listing"),
        field("depop_specifics.boost", "Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.",
              specifics.get("boost") is True, input_type="boolean", section="Boost your listing"),
        *([field("depop.note.carrier", f"Depop Shipping (via {carrier})", "", input_type="note", section="Shipping",
                 help="Use the shipping label Depop provides for your order.")] if carrier else []),
        field("depop_specifics.shipFrom", "Shipping from", specifics.get("shipFrom", ""), input_type="textarea",
              locked=True, section="Shipping", help="Your Depop account's shipping address is checked when you publish."),
        *([choice("depop_specifics.parcelSize", "Package size", quote.get("options", fixed["parcelSize"]),
                  section="Shipping", required=True, **({
                      "option_groups": {size: "Suggested" if size == quote["suggested"] else "Change size" for size in quote["options"]},
                      "help": f"Depop suggests {quote['suggested']} for this item.",
                  } if quote else {}))]
           if carrier else [field("depop_specifics.domesticShippingPrice", "Domestic shipping", value("depop_specifics.domesticShippingPrice"),
                                   input_type="number", required=True, min_value=0, step=0.01, prefix=currency, section="Shipping")]),
        field("depop_specifics.worldwideShipping.enabled", "Offer worldwide shipping",
              value("depop_specifics.worldwideShipping.enabled", False), input_type="boolean", section="Shipping"),
        field("depop_specifics.worldwideShipping.price", "Worldwide Shipping", value("depop_specifics.worldwideShipping.price"),
              input_type="number", required=value("depop_specifics.worldwideShipping.enabled", False) is True,
              min_value=0, max_value=99.99, step=0.01, prefix=currency, section="Shipping"),
    ])
    notes = []
    if category_id and not category:
        notes.append("This Depop category's fields are not in the captured catalog. Its form data is still needed.")
    return {"marketplace": "depop", "native": True, "known": not category_id or category is not None, "page_labels": True,
            "category_id": category_id if category else "", "fields": rows, "notes": notes}
