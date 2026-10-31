"""Poshmark's verified native controls and category catalog, served by Anteroom."""
from __future__ import annotations

import copy
import json
import math
import re
from functools import lru_cache

from vendoo_studio.config import skills_dir
from vendoo_studio.services.chrome_listing import poshmark_color_name
from vendoo_studio.services.seller_overrides import get_path, marketplace_text, price_for


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/poshmark-native-form.json").read_text())


def _multiple(listing: dict) -> bool:
    def many(value):
        try:
            return float(value) > 1
        except (TypeError, ValueError):
            return False

    rows = (listing.get("poshmark_specifics") or {}).get("inventoryRows")
    if isinstance(rows, list):
        return len(rows) > 1 or any(many(row.get("quantity")) for row in rows if isinstance(row, dict))
    return many(listing.get("quantity"))


def _initial_inventory(listing: dict, groups: list) -> list:
    details = listing.get("poshmark_specifics") or {}
    def key(value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    group_id = details.get("sizeSetId")
    fit = key(details.get("sizeType") or listing.get("sizeType") or "standard")
    if fit == "regular":
        fit = "standard"
    candidates = [group for group in groups if group["id"] == group_id] if group_id else [
        group for group in groups if key(group["label"]) == fit]
    system = str(details.get("sizeSystem") or "us").lower()
    native_id = details.get("nativeSizeId")
    wanted = marketplace_text(listing, "poshmark", "size")
    matches = []
    for group in candidates:
        for size in (group.get("systems") or {}).get(system, group.get("sizes") or []):
            matches_id = native_id is not None and str(size["id"]) == str(native_id)
            matches_label = native_id is None and key(wanted) and key(size["label"]) == key(wanted)
            if matches_id or matches_label:
                matches.append((group["id"], size["id"]))
    selected = matches[0] if len(matches) == 1 else ("", "")
    return [{"sizeSetId": selected[0], "sizeId": selected[1], "quantity": listing.get("quantity", 1), "sku": ""}]


def form(listing: dict) -> dict:
    data = catalog()
    rows = copy.deepcopy(data["fields"])
    category_row = next(row for row in rows if row["path"] == "direct_category_ids.poshmark")
    choices = category_row["option_values"]
    wanted = str((listing.get("direct_category_ids") or {}).get("poshmark") or "")
    category_id = wanted if wanted in choices.values() else ""
    if not category_id:
        matches = {value for value in choices.values() if wanted and value.split("/")[-1] == wanted}
        if len(matches) == 1:
            category_id = matches.pop()
    groups = copy.deepcopy(data["inventory_groups"].get("/".join(category_id.split("/")[:2]), []))
    specifics = listing.get("poshmark_specifics") or {}
    for row in rows:
        path = row["path"]
        value = get_path(listing, path)
        if path in {"poshmark_specifics.title", "poshmark_specifics.description", "poshmark_specifics.brand"}:
            value = marketplace_text(listing, "poshmark", path.rsplit(".", 1)[-1])
        elif path == "price":
            value = price_for(listing, "poshmark")
        elif path == "direct_category_ids.poshmark":
            value = category_id or wanted
        elif path == "poshmark_specifics.inventoryRows":
            row["inventory_groups"] = groups
            row["inventory_system"] = str(specifics.get("sizeSystem") or "us").lower()
            if value is None:
                value = _initial_inventory(listing, groups)
        elif path == "poshmark_specifics.multiItem" and value is None:
            value = "true" if _multiple(listing) else "false"
        elif path == "poshmark_specifics.colors" and value is None:
            value = list(dict.fromkeys(color for raw in (listing.get("primaryColor"), listing.get("secondaryColor"))
                                       if (color := poshmark_color_name(str(raw or "")))))
        elif path == "poshmark_specifics.poshmarkSku" and value is None:
            value = listing.get("sku", "")
        elif path == "poshmark_specifics.shippingDiscountId":
            # Account choices belong to this seller's saved definition, never to
            # the public catalog. No account request is made to open this form.
            saved = (listing.get("_native_forms") or {}).get("poshmark") or {}
            account_row = next((item for item in saved.get("fields") or [] if item.get("path") == path), None)
            if account_row:
                for key in ("options", "option_values", "option_amounts"):
                    row[key] = copy.deepcopy(account_row.get(key) or ([] if key == "options" else {}))
            if not row.get("options"):
                row["help"] = "Shipping discounts are account-specific. No saved discount choices are available. Leaving this blank uses no discount."
        if value is not None:
            row["value"] = copy.deepcopy(value)
    by_path = {row["path"]: row for row in rows}
    smart = get_path(listing, "poshmark_specifics.smartSell.enabled")
    if smart is None:
        try:
            smart = float(get_path(listing, "poshmark_specifics.smartSell.minimumPrice") or 0) > 0
        except (TypeError, ValueError):
            smart = False
    by_path["poshmark_specifics.smartSell.enabled"]["value"] = smart
    by_path["poshmark_specifics.smartSell.minimumPrice"]["required"] = smart is True
    by_path["poshmark_specifics.makeAvailableAt"]["required"] = by_path["poshmark_specifics.availability"]["value"] == "coming_soon"
    by_path["poshmark_specifics.earnings"]["value"] = ""
    # The earnings card follows this form's price and the chosen shipping discount.
    try:
        raw_price = price_for(listing, "poshmark")
        if raw_price is None:
            raise ValueError("No listing price")
        price = float(raw_price)
        discount = by_path["poshmark_specifics.shippingDiscountId"]
        discount_id = specifics.get("shippingDiscountId", "")
        amounts = discount.get("option_amounts") or {}
        known_discount = not discount_id or str(discount_id) in amounts
        if math.isfinite(price) and price > 0 and known_discount:
            cents = math.floor(price * 100 + 0.5)
            fee = 295 if price < 15 else math.floor(price * 20 + 0.5)
            shipping = math.floor(float(amounts.get(str(discount_id), 0)) * 100 + 0.5)
            by_path["poshmark_specifics.earnings"]["value"] = f"${max(0, cents - fee - shipping) / 100:.2f}"
    except (TypeError, ValueError):
        pass
    return {"marketplace": "poshmark", "native": True, "known": True, "category_id": category_id, "fields": rows}
