"""Depop's captured sell page, rendered locally without Chrome or an account read."""
from __future__ import annotations

import json
from functools import lru_cache

from vendoo_studio.config import skills_dir
from vendoo_studio.services.seller_overrides import get_path, marketplace_text, price_for


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/depop-native-catalog.json").read_text())


@lru_cache(maxsize=1)
def fixed_options() -> dict:
    return json.loads((skills_dir() / "list-this/references/vendoo-dropdown-options.json").read_text())["forms"]["depop"]


def form(listing: dict) -> dict:
    """Keep native rows and order; resolve labels to posting IDs only at publication."""
    from vendoo_studio.services.native_editor import field

    categories = catalog()["categories"]
    ids = listing.get("direct_category_ids") or {}
    category_id = str(ids.get("depop") or (listing.get("marketplace_category_ids") or {}).get("depop") or "")
    category = next((row for row in categories if row["id"] == category_id), None)
    specifics = listing.get("depop_specifics") or {}
    fixed = fixed_options()

    def value(path, default=""):
        found = get_path(listing, path)
        return default if found is None else found

    def text(name):
        return marketplace_text(listing, "depop", name)

    def choice(path, label, options, section="Info", **extra):
        return field(path, label, value(path, [] if extra.get("multi") else ""), selection_only=True,
                     options=list(options), options_source="marketplace", section=section, **extra)

    rows = [
        field("marketplace_photos.depop", "", [], input_type="photos", section="Photos",
              help="Add up to 8 photos in JPEG or PNG format.", accept="image/jpeg,image/png,.jpg,.jpeg,.png",
              slot_labels=["Cover photo", "Front", "Back", "Side", "Label", "Detail", "Flaw"]),
        field("depop_specifics.description", "Description", text("description"), required=True, max_length=1000,
              section="Description", placeholder="eg. small grey Nike t-shirt, only worn a few times", counter=True, hashtag_max=5),
        field("direct_category_ids.depop", "Category", category_id, required=True, selection_only=True,
              options=[row["path"] for row in categories], option_values={row["path"]: row["id"] for row in categories},
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
            rows.append(field("depop_specifics.brand", label, text("brand"), section="Info"))
        elif key in fixed_keys:
            enhance = key in {"source", "age", "style"}
            extra = {"multi": True, "max_values": captured.get("max_values") or 1} if key != "condition" else {"required": True}
            row = choice("depop_specifics." + key, label, fixed[fixed_keys[key]],
                         section="Enhance your listing" if enhance else "Info", **extra)
            if key == "condition":
                row["option_values"] = dict(zip(fixed["condition"],
                    ["brand_new", "used_like_new", "used_excellent", "used_good", "used_fair"], strict=True))
            elif key == "colour":
                row["option_values"] = {label: label.lower() for label in fixed["primaryColor"]}
            if key == "colour":
                row["help"] = "Select up to two colors"
            elif key == "source":
                row["help"] = f"What kind of item is this? Add up to {row['max_values']}"
            elif key == "age":
                row["help"] = f"Do you know when this item was made? Add up to {row['max_values']}"
            elif key == "style":
                row["help"] = f"Pick relevant tags to describe this item's style. Add up to {row['max_values']}"
            rows.append(row)
        elif key == "variants":
            rows.append(choice("depop_specifics.size", label, captured["options"], required=True,
                               category_specific=True, width="quarter"))
        elif key == "quantity":
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
              max_value=9999999999.99, step=0.01, prefix="US$", section="Item price"),
        field("depop.note.fees", "No selling fees.", "", input_type="note", section="Item price"),
        field("depop.note.boost", "Learn more about boosting", "", input_type="note", section="Boost your listing"),
        field("depop_specifics.boost", "Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.",
              specifics.get("boost") is True, input_type="boolean", section="Boost your listing"),
        field("depop.note.carrier", "Depop Shipping (via USPS)", "", input_type="note", section="Shipping",
              help="You’ll receive a pre-paid shipping label and a QR code when you sell. Use either of these to ship at your nearest USPS Post Office."),
        field("depop_specifics.shipFrom", "Shipping from", specifics.get("shipFrom", ""), input_type="textarea",
              locked=True, section="Shipping", help="Your Depop account's shipping address is checked when you publish."),
        choice("depop_specifics.parcelSize", "Package size", fixed["parcelSize"], section="Shipping", required=True),
        field("depop_specifics.worldwideShipping.enabled", "Offer worldwide shipping",
              value("depop_specifics.worldwideShipping.enabled", False), input_type="boolean", section="Shipping"),
        field("depop_specifics.worldwideShipping.price", "Worldwide Shipping", value("depop_specifics.worldwideShipping.price"),
              input_type="number", required=value("depop_specifics.worldwideShipping.enabled", False) is True,
              min_value=0, max_value=99.99, step=0.01, prefix="US$", section="Shipping"),
    ])
    notes = []
    if category_id and not category:
        notes.append("This Depop category's fields are not in the captured catalog. Its form data is still needed.")
    return {"marketplace": "depop", "native": True, "known": not category_id or category is not None, "page_labels": True,
            "category_id": category_id if category else "", "fields": rows, "notes": notes}
