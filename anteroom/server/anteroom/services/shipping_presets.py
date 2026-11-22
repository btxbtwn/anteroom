"""Reusable shipping presets: a package size and weight, plus each marketplace's shipping choices.

The seller names a preset and says in plain English when it fits ("Use for
shoes"). Generation shows the model every preset with its condition and
applies the one it picks; the editor and Bulk Edit apply one on a click. A
preset only fills local fields. Nothing is sent to a marketplace.
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any

SHIPPING_PRESETS_KEY = "shipping_presets"
MAX_PRESETS = 50
MAX_NAME = 60
MAX_CONDITION = 300
MAX_DIMENSION = 108
MAX_POUNDS = 150

# The marketplace shipping fields a preset can set, and the values each accepts.
# eBay and Etsy policies are account-specific IDs; the others are fixed choices.
MARKETPLACE_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "ebay": {"fulfillmentPolicyId": {"label": "eBay shipping policy ID", "pattern": r"^\d{1,20}$"}},
    "etsy": {"shipping_profile_id": {"label": "Etsy shipping profile ID", "pattern": r"^[1-9]\d{0,19}$"}},
    "mercari": {
        "shippingMethod": {"label": "Mercari delivery", "choices": {"MERCARI": "Standard shipping", "SOYO": "Ship on your own"}},
        "shippingPayerId": {"label": "Mercari shipping payer", "choices": {"1": "Buyer pays", "2": "I'll pay"}},
    },
    "depop": {
        "parcelSize": {"label": "Depop parcel size", "choices": {
            size: size for size in ("Extra extra small", "Extra small", "Small", "Medium", "Large", "Extra large")}},
    },
}


def _text(value: Any, limit: int, name: str, *, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"Enter a {name}.")
    if len(text) > limit:
        raise ValueError(f"The {name} may have at most {limit} characters.")
    return text


def _whole(value: Any, low: int, high: int, name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"Enter a whole number for the {name}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a whole number for the {name}.") from None
    if number != int(number) or number < low or number > high:
        raise ValueError(f"The {name} must be a whole number from {low} to {high}.")
    return int(number)


def _marketplace_values(raw: Any) -> dict[str, dict[str, str]]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Marketplace shipping choices must be grouped by marketplace.")
    cleaned: dict[str, dict[str, str]] = {}
    for market, values in raw.items():
        fields = MARKETPLACE_FIELDS.get(market)
        if fields is None or not isinstance(values, dict):
            raise ValueError("A preset can set shipping on eBay, Etsy, Mercari or Depop.")
        kept: dict[str, str] = {}
        for key, value in values.items():
            spec = fields.get(key)
            if spec is None:
                raise ValueError(f"{market.title()} has no preset field named {key}.")
            text = str(value if value is not None else "").strip()
            if not text:
                continue
            if "choices" in spec and text not in spec["choices"]:
                raise ValueError(f"Choose a {spec['label']} from the list.")
            if "pattern" in spec and not re.fullmatch(spec["pattern"], text):
                raise ValueError(f"Enter the {spec['label']} as digits.")
            kept[key] = text
        if kept:
            cleaned[market] = kept
    return cleaned


def clean_preset(raw: Any, *, preset_id: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("A shipping preset needs a name.")
    length = _whole(raw.get("length"), 1, MAX_DIMENSION, "length")
    width = _whole(raw.get("width"), 1, MAX_DIMENSION, "width")
    height = _whole(raw.get("height"), 1, MAX_DIMENSION, "height")
    dimensions = [length, width, height]
    if any(value is not None for value in dimensions) and None in dimensions:
        raise ValueError("Enter the length, width and height, or leave all three empty.")
    pounds = _whole(raw.get("weight_lb"), 0, MAX_POUNDS, "pounds")
    ounces = _whole(raw.get("weight_oz"), 0, 15, "ounces")
    preset = {
        "id": preset_id or str(raw.get("id") or "") or uuid.uuid4().hex,
        "name": _text(raw.get("name"), MAX_NAME, "preset name", required=True),
        "usage_condition": _text(raw.get("usage_condition"), MAX_CONDITION, "usage condition"),
        "length": length,
        "width": width,
        "height": height,
        "weight_lb": pounds,
        "weight_oz": ounces,
        "marketplaces": _marketplace_values(raw.get("marketplaces")),
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", preset["id"]):
        raise ValueError("That shipping preset could not be found.")
    if all(preset[key] is None for key in ("length", "weight_lb", "weight_oz")) and not preset["marketplaces"]:
        raise ValueError("Give the preset a package size, a weight or a marketplace shipping choice.")
    return preset


def _stored(payload: dict) -> list[dict]:
    raw = payload.get(SHIPPING_PRESETS_KEY)
    presets = []
    for item in raw if isinstance(raw, list) else []:
        try:
            presets.append(clean_preset(item))
        except ValueError:
            continue
    return presets


def get_shipping_presets() -> list[dict]:
    from anteroom.services.user_settings import read_settings

    return _stored(read_settings())


def save_shipping_preset(raw: Any, preset_id: str | None = None) -> list[dict]:
    """Add a preset, or replace the one with ``preset_id``."""
    from anteroom.services.user_settings import update_settings

    preset = clean_preset(raw, preset_id=preset_id)

    def mutate(payload: dict) -> None:
        presets = _stored(payload)
        if preset_id is not None and not any(item["id"] == preset_id for item in presets):
            raise ValueError("That shipping preset could not be found.")
        if any(item["name"].casefold() == preset["name"].casefold() and item["id"] != preset["id"] for item in presets):
            raise ValueError("Another shipping preset already has that name.")
        if preset_id is None and len(presets) >= MAX_PRESETS:
            raise ValueError(f"Keep at most {MAX_PRESETS} shipping presets.")
        replaced = [preset if item["id"] == preset["id"] else item for item in presets]
        payload[SHIPPING_PRESETS_KEY] = replaced if preset_id is not None else [*presets, preset]

    return _stored(update_settings(mutate))


def delete_shipping_preset(preset_id: str) -> list[dict]:
    from anteroom.services.user_settings import update_settings

    def mutate(payload: dict) -> None:
        presets = _stored(payload)
        if not any(item["id"] == preset_id for item in presets):
            raise ValueError("That shipping preset could not be found.")
        payload[SHIPPING_PRESETS_KEY] = [item for item in presets if item["id"] != preset_id]

    return _stored(update_settings(mutate))


def find_preset(key: str | None, presets: list[dict] | None = None) -> dict | None:
    """A preset by id, or by name ignoring case and spacing (how the model names one)."""
    wanted = " ".join(str(key or "").split()).casefold()
    if not wanted:
        return None
    for preset in presets if presets is not None else get_shipping_presets():
        if preset["id"] == key or preset["name"].casefold() == wanted:
            return preset
    return None


def package_fields(preset: dict) -> dict[str, Any]:
    """The General package fields a preset sets."""
    fields: dict[str, Any] = {}
    if preset.get("length") is not None:
        fields["package_dimensions_in"] = f"{preset['length']}x{preset['width']}x{preset['height']}"
    if preset.get("weight_lb") is not None or preset.get("weight_oz") is not None:
        fields["weight_lb"] = preset.get("weight_lb") or 0
        fields["weight_oz"] = preset.get("weight_oz") or 0
    return fields


def field_updates(preset: dict) -> list[dict]:
    """Every path the preset sets, as ``apply_field_updates`` takes them."""
    updates = [{"path": path, "value": value} for path, value in package_fields(preset).items()]
    for market, values in (preset.get("marketplaces") or {}).items():
        for key, value in values.items():
            stored: Any = int(value) if key == "shippingPayerId" else value
            updates.append({"path": f"{market}_specifics.{key}", "value": stored})
    return updates


def apply_package(listing: dict, preset: dict, *, respect_locks: bool = True) -> list[str]:
    """Set the preset's package size and weight on the listing."""
    from anteroom.services.seller_overrides import is_locked

    applied = []
    for path, value in package_fields(preset).items():
        if respect_locks and is_locked(listing, path):
            continue
        listing[path] = value
        applied.append(path)
    return applied


def apply_marketplace_shipping(listing: dict, preset: dict, *, respect_locks: bool = True) -> list[str]:
    """Set each marketplace's shipping choices from the preset."""
    from anteroom.services.seller_overrides import is_locked, set_path

    applied = []
    for update in field_updates(preset):
        path = update["path"]
        if "_specifics." not in path or (respect_locks and is_locked(listing, path)):
            continue
        set_path(listing, path, update["value"])
        applied.append(path)
    return applied


def applied_listing(listing: dict, preset: dict) -> tuple[dict, list[str]]:
    """A copy of the listing with the preset applied, the seller's own choice.

    Used by the editor's and Bulk Edit's Apply preset: every path it sets is the
    seller's now, so later generation leaves it alone.
    """
    from anteroom.services.seller_overrides import apply_field_updates

    updated = copy.deepcopy(listing) if isinstance(listing, dict) else {}
    paths = apply_field_updates(updated, field_updates(preset))
    return updated, paths


def generation_prompt(presets: list[dict] | None = None) -> str:
    """The presets, for the model to choose one by its plain-English condition."""
    presets = presets if presets is not None else get_shipping_presets()
    if not presets:
        return ""
    lines = []
    for preset in presets:
        parts = []
        package = package_fields(preset)
        if "package_dimensions_in" in package:
            parts.append(f"box {package['package_dimensions_in']} in")
        if "weight_lb" in package:
            parts.append(f"{package['weight_lb']} lb {package['weight_oz']} oz")
        condition = preset.get("usage_condition") or "no condition given"
        lines.append(f'- "{preset["name"]}": {condition}' + (f" ({', '.join(parts)})" if parts else ""))
    return (
        "\n\n--- Shipping presets ---\n\n"
        "The seller saved these shipping presets. Each says when to use it:\n"
        + "\n".join(lines)
        + '\nAdd "shipping_preset" to the listing JSON with the exact name of the one preset whose '
        "condition fits this item best, or \"\" when none fits. Anteroom fills the package size, weight "
        "and marketplace shipping from the preset you name."
    )


def payload() -> dict:
    return {
        "presets": get_shipping_presets(),
        "fields": {market: {key: {"label": spec["label"], **({"choices": spec["choices"]} if "choices" in spec else {})}
                            for key, spec in fields.items()}
                   for market, fields in MARKETPLACE_FIELDS.items()},
    }
