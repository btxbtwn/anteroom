"""Poshmark's verified native controls and category catalog, served by Anteroom."""
from __future__ import annotations

import copy
import json
import math
import re
from functools import lru_cache

from anteroom.config import skills_dir
from anteroom.models.sizes import size_choice
from anteroom.services.chrome_listing import poshmark_color_name
from anteroom.services.seller_overrides import get_path, marketplace_text, price_for
from anteroom.services.vendoo_encodings import marketplace_condition

CONDITIONS = frozenset({"nwt", "uln", "ug", "uf"})


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/poshmark-native-form.json").read_text())


CATEGORY_PATH = "direct_category_ids.poshmark"


@lru_cache(maxsize=1)
def category_choices() -> dict:
    """Poshmark's category picker, the same for every listing."""
    row = next(row for row in catalog()["fields"] if row["path"] == CATEGORY_PATH)
    return {key: row[key] for key in ("options", "option_values", "cascade") if key in row}


@lru_cache(maxsize=1)
def _category_index() -> tuple[frozenset[str], dict[str, str]]:
    values = frozenset(category_choices()["option_values"].values())
    by_leaf: dict[str, set[str]] = {}
    for value in values:
        by_leaf.setdefault(value.split("/")[-1], set()).add(value)
    unique_leaves = {leaf: next(iter(matches)) for leaf, matches in by_leaf.items() if len(matches) == 1}
    return values, unique_leaves


AVAILABILITY_PATH = "poshmark_specifics.availability"


def use_availability_id(listing: dict) -> None:
    """Store Availability as Poshmark's id. A draft may hold the label the form
    shows (``For Sale``); Poshmark rejects that as an invalid inventory status."""
    specifics = listing.get("poshmark_specifics")
    if not isinstance(specifics, dict) or not isinstance(specifics.get("availability"), str):
        return
    row = next(row for row in catalog()["fields"] if row["path"] == AVAILABILITY_PATH)
    wanted = specifics["availability"].strip().casefold()
    for label, native_id in row["option_values"].items():
        if wanted in (label.casefold(), native_id.casefold()):
            specifics["availability"] = native_id
            return


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


def _kids_gender(listing: dict) -> str:
    """boys or girls from the listing's department or category, never guessed."""
    paths = listing.get("marketplace_categories")
    ebay = listing.get("ebay_specifics")
    for source in (listing.get("department"), ebay.get("department") if isinstance(ebay, dict) else None,
                   listing.get("category_path"), *(paths.values() if isinstance(paths, dict) else ())):
        text = str(source or "").lower()
        boys, girls = bool(re.search(r"\bboy'?s?'?\b", text)), bool(re.search(r"\bgirl'?s?'?\b", text))
        if boys != girls:
            return "boys" if boys else "girls"
    return ""


def _size_pools(listing: dict, groups: list, key) -> list[list]:
    """The size groups List looks in, in order: the named group or fit, else the
    standard group then Plus and Big & Tall, and a kids' category's gender then Baby."""
    details = listing.get("poshmark_specifics") or {}
    group_id = details.get("sizeSetId")
    if group_id:
        return [[group for group in groups if group["id"] == group_id]]
    fit = key(details.get("sizeType") or listing.get("sizeType") or "standard")
    if fit == "regular":
        fit = "standard"
    labelled = lambda *labels: [group for group in groups if key(group["label"]) in labels]  # noqa: E731
    if fit != "standard":
        return [labelled(fit)]
    if labelled("standard"):
        # Past the standard range Poshmark keeps larger sizes in Plus or Big & Tall.
        return [labelled("standard"), labelled("plus", "bigtall")]
    if labelled("boys", "girls"):
        gender = _kids_gender(listing)
        return [labelled(gender), labelled("baby")] if gender else []
    return [[group for group in groups if group["id"] != "custom"]]


def _condition_from_text(value) -> str:
    """Poshmark's code for a condition in words, as List reads it; empty when none fits."""
    text = str(value or "").lower()
    if re.search(r"new with tags|\bnwt\b", text) and "without" not in text:
        return "nwt"
    for pattern, code in ((r"new without tags|\bnwot\b|like new|excellent", "uln"), (r"fair|poor", "uf"),
                          (r"good|imperfection|defect|pre-owned|used", "ug"), (r"^new\b|brand new", "nwt")):
        if re.search(pattern, text):
            return code
    return ""


def general_condition(listing: dict) -> str:
    """The condition List sends when this form has none of its own, or empty when General has none."""
    chosen = _condition_from_text((listing.get("poshmark_specifics") or {}).get("poshmarkCondition"))
    if chosen:
        return chosen
    form = listing.get("form")
    prepared = str(((form.get("overrides") if isinstance(form, dict) else None) or {}).get("condition") or "").lower()
    if prepared in CONDITIONS:
        return prepared
    code = str(marketplace_condition(None, "poshmark", listing.get("condition")) or "")
    return code if code in CONDITIONS else _condition_from_text(listing.get("condition"))


def _initial_inventory(listing: dict, groups: list) -> list:
    details = listing.get("poshmark_specifics") or {}
    def key(value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    system = str(details.get("sizeSystem") or "us").lower()
    native_id = details.get("nativeSizeId")
    wanted = marketplace_text(listing, "poshmark", "size")
    matches = []
    for pool in _size_pools(listing, groups, key):
        for group in pool:
            sizes = (group.get("systems") or {}).get(system, group.get("sizes") or [])
            # The tag's size as this group spells it: L for Large (12/14), Waist 32 for 32.
            label = size_choice(wanted, [size["label"] for size in sizes]) if native_id is None else None
            for size in sizes:
                if (native_id is not None and str(size["id"]) == str(native_id)) or (label and size["label"] == label):
                    matches.append((group["id"], size["id"]))
        if matches:
            break
    selected = matches[0] if len(matches) == 1 else ("", "")
    return [{"sizeSetId": selected[0], "sizeId": selected[1], "quantity": listing.get("quantity", 1), "sku": ""}]


def _key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def imported_inventory(category_id: str, sizes: list[dict]) -> list[dict]:
    """The Size rows for sizes read from a Poshmark post.

    A post names each size's group by its tags (``standard``, ``petite``...);
    the Size picker stores the group's id. A size no group holds, on a row with
    no tags, is the seller's custom size. A size that cannot be placed keeps its
    id with no group, for the seller to choose.
    """
    groups = catalog()["inventory_groups"].get("/".join(str(category_id).split("/")[:2]), [])
    rows = []
    for size in sizes:
        size_id, system = str(size.get("size_id") or ""), str(size.get("size_system") or "us").lower()
        tags = {_key(tag) for tag in size.get("size_set_tags") or []}
        holding = [group for group in groups if any(
            str(entry["id"]) == size_id for entry in (group.get("systems") or {}).get(system, group.get("sizes") or []))]
        named = [group for group in holding if _key(group["label"]) in tags]
        found = named or (holding if len(holding) == 1 and not tags else [])
        group_id = found[0]["id"] if found else "custom" if size_id and not tags and any(
            group["id"] == "custom" for group in groups) else ""
        rows.append({"sizeSetId": group_id, "sizeId": size_id, "quantity": size.get("quantity", 1), "sku": size.get("sku") or ""})
    return rows


def follow_general_quantity(before: dict, listing: dict) -> dict:
    """A lone Size row that offered General's quantity keeps offering it when General's changes.

    A one-size listing kept as a row (it has its own SKU) would otherwise send
    Poshmark its old quantity after a sale elsewhere lowered General's.
    """
    rows = (listing.get("poshmark_specifics") or {}).get("inventoryRows")
    old, new = before.get("quantity"), listing.get("quantity")
    if not (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict)) or old is None or new is None:
        return listing
    row_quantity = rows[0].get("quantity")
    if row_quantity is None:
        return listing
    try:
        follows = float(row_quantity) == float(old) and float(new) != float(old)
    except (TypeError, ValueError):
        return listing
    if not follows:
        return listing
    listing = {**listing, "poshmark_specifics": {**listing["poshmark_specifics"], "inventoryRows": [{**rows[0], "quantity": new}]}}
    return listing


def form(listing: dict) -> dict:
    data = catalog()
    rows = [dict(row) for row in data["fields"]]
    category_ids, unique_leaves = _category_index()
    wanted = str((listing.get("direct_category_ids") or {}).get("poshmark") or "")
    category_id = wanted if wanted in category_ids else unique_leaves.get(wanted, "")
    groups = data["inventory_groups"].get("/".join(category_id.split("/")[:2]), [])
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
        elif path == "poshmark_specifics.condition" and value is None:
            # Follows General until the seller picks one here, as List does.
            value = general_condition(listing)
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
