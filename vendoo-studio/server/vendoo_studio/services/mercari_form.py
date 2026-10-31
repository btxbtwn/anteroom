"""Mercari's native sell form, using local taxonomy and anonymous shipping quotes."""
from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from functools import lru_cache

from vendoo_studio.config import skills_dir
from vendoo_studio.services.seller_overrides import get_path, marketplace_text, price_for

NO_BRAND = "No Brand/Not sure"


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/mercari-native-catalog.json").read_text())


@lru_cache(maxsize=1)
def categories() -> tuple[dict, ...]:
    rows = catalog()["itemCategories"]
    by_id = {row["id"]: row for row in rows}
    children = {}
    for row in rows:
        children.setdefault(row["parentId"], []).append(row)
    result = []

    def visit(row, ancestors):
        path = [*ancestors, row["name"]]
        below = children.get(row["id"], [])
        for child in sorted(below, key=lambda row: row["displayOrder"]):
            visit(child, path)
        if not below or len(path) > 1:
            result.append({"id": str(row["id"]), "path": " > ".join(path)})

    for row in rows:
        if row["parentId"] not in by_id:
            visit(row, [])
    return tuple(result)


def _number(value) -> float:
    try:
        result = float(value or 0)
        return result if math.isfinite(result) else 0
    except (TypeError, ValueError):
        return 0


def package(listing: dict) -> tuple[int, tuple[float, float, float] | None, dict]:
    own = (listing.get("mercari_specifics") or {}).get("package") or {}
    detail = re.split(r"\s*[x×*]\s*", str(listing.get("package_dimensions_in") or ""))
    sides = [_number(value) for value in detail] if len(detail) == 3 else []
    values = {"pounds": own.get("pounds", listing.get("weight_lb", "")),
              "ounces": own.get("ounces", listing.get("weight_oz", "")),
              "fitsShoebox": own.get("fitsShoebox", ""), "skipDimensions": own.get("skipDimensions", False)}
    for index, key in enumerate(("length", "width", "height")):
        values[key] = own[key] if own.get(key) not in (None, "") else sides[index] if sides else ""
    # Once either weight input is set, both come from that native question.
    if own.get("pounds") not in (None, "") or own.get("ounces") not in (None, ""):
        weight = math.ceil(_number(own.get("pounds")) * 16 + _number(own.get("ounces")))
    else:
        weight = math.ceil(_number(listing.get("weight_lb")) * 16 + _number(listing.get("weight_oz")))
    dimensions = None
    if values["fitsShoebox"] == "yes":
        dimensions = (14, 10, 5)
    elif values["fitsShoebox"] == "no":
        if values["skipDimensions"] in (True, "true"):
            dimensions = (0, 0, 0)
        elif all(_number(values[key]) > 0 for key in ("length", "width", "height")):
            dimensions = tuple(_number(values[key]) for key in ("length", "width", "height"))
    return max(0, weight), dimensions, values


def _condition(value):
    text = str(value or "").strip().lower()
    if text in {"1", "2", "3", "4", "5"}:
        return text
    for pattern, code in ((r"poor|major flaw", "5"), (r"fair", "4"),
                          (r"new without tags|nwot|like new", "2"),
                          (r"new with tags|nwt", "1"), (r"good|excellent|imperfection|pre-owned|used", "3"),
                          (r"^new\b", "1")):
        if re.search(pattern, text):
            return code
    return ""


def _sizes(category: dict | None) -> list[dict]:
    if not category or not category["itemSizeGroupId"]:
        return []
    data = catalog()
    group = next(row for row in data["itemSizeGroups"] if row["id"] == category["itemSizeGroupId"])
    sizes = {row["id"]: row for row in data["itemSizes"]}
    subgroups = {row["id"]: row["title"] for row in data["itemSizeSubGroups"]}
    rows = [sizes[size_id] for size_id in group["sizeIds"]]
    labels = [" ".join(part for part in (row["title"], row["subtitle"]) if part) for row in rows]
    counts = Counter(labels)
    return [{"id": str(row["id"]), "name": label, "group": subgroups.get(row["itemSizeSubGroupId"], ""),
             "label": label + (" · " + (subgroups.get(row["itemSizeSubGroupId"]) or str(row["id"]))
                               if counts[label] > 1 else "")}
            for row, label in zip(rows, labels, strict=True)]


def form(listing: dict, shipping_classes: list[dict] | None = None, *, notes: list[str] | None = None) -> dict:
    from vendoo_studio.services.native_editor import field

    specifics = listing.get("mercari_specifics") or {}
    category_id = str((listing.get("direct_category_ids") or {}).get("mercari") or "")
    category = next((row for row in catalog()["itemCategories"] if str(row["id"]) == category_id), None)
    rows = copy.deepcopy(catalog()["fields"])
    fields = {row["path"]: row for row in rows}
    for path, row in fields.items():
        row["value"] = get_path(listing, path) if get_path(listing, path) is not None else ""
    for name in ("title", "description"):
        fields[f"mercari_specifics.{name}"]["value"] = marketplace_text(listing, "mercari", name)
    fields["marketplace_photos.mercari"]["value"] = []
    price = price_for(listing, "mercari")
    fields["price"]["value"] = price if price is not None else ""
    choices = categories()
    fields["direct_category_ids.mercari"].update(value=category_id, options=[row["path"] for row in choices],
                                               option_values={row["path"]: row["id"] for row in choices})
    brands = [row["name"] for row in catalog()["itemBrands"]]
    brand = marketplace_text(listing, "mercari", "brand")
    blank = re.sub(r"[^a-z0-9]+", " ", brand.casefold()).strip() in {
        "", "unbranded", "no brand", "not sure", "no brand not sure", "none", "n a", "na", "unknown"}
    brand = NO_BRAND if blank or specifics.get("noBrand") is True else next(
        (name for name in brands if name.casefold() == brand.casefold()), brand)
    fields["mercari_specifics.brand"].update(value=brand, options=[*brands, "No brand / Not sure"],
                                            option_values={**{name: name for name in brands}, "No brand / Not sure": NO_BRAND})
    fields["mercari_specifics.condition"]["value"] = _condition(specifics.get("condition") or listing.get("condition"))
    sizes = _sizes(category)
    if sizes:
        value = specifics.get("nativeSizeId", "")
        if "nativeSizeId" not in specifics:
            wanted = str(specifics.get("size") or listing.get("size") or "").strip().casefold()
            matches = [row for row in sizes if wanted in (row["label"].casefold(), row["name"].casefold())]
            fit = str(specifics.get("sizeType") or listing.get("sizeType") or "").strip().casefold()
            if fit:
                matches = [row for row in matches if fit in row["group"].casefold()]
            elif len(matches) > 1:
                matches = [row for row in matches if row["group"].casefold() in {"standard", "regular"}]
            if len(matches) == 1:
                value = matches[0]["id"]
        row = field("mercari_specifics.nativeSizeId", "Size", value, required=True, selection_only=True,
                    options=[row["label"] for row in sizes], option_values={row["label"]: row["id"] for row in sizes},
                    option_groups={row["label"]: row["group"] for row in sizes if row["group"]},
                    category_specific=True, placeholder="Select size", section="Product info", options_source="marketplace")
        rows.insert(rows.index(fields["mercari_specifics.condition"]) + 1, row)
    _, _, package_values = package(listing)
    for key, value in package_values.items():
        fields[f"mercari_specifics.package.{key}"]["value"] = value
    own_shipping = specifics.get("shippingMethod") == "SOYO"
    fields["mercari_specifics.shippingMethod"]["value"] = specifics.get("shippingMethod") or "MERCARI"
    fields["mercari_specifics.shippingPayerId"]["value"] = 2 if own_shipping else specifics.get("shippingPayerId", 1)
    fields["mercari_specifics.package.fitsShoebox"]["required"] = not own_shipping
    classes = shipping_classes or []
    selected = str(specifics.get("shippingClassId") or "")
    saved_label = (listing.get("form") or {}).get("mercari_shipping_label") or {}
    default = next((row for row in classes if row["carrierDisplayName"] == saved_label.get("label")), None)
    default = default or next((row for row in classes if row["carrierDisplayName"] == "USPS Ground Advantage"), None)
    if not selected and default:
        selected = str(default["id"])

    def label_help(row):
        before = max(row.get("standardFee", 0), row.get("nonPromotionalFee", 0))
        amount = f"${row['fee'] / 100:.2f}"
        if before > row["fee"]:
            amount += f" ${before / 100:.2f}"
        return " | ".join(part for part in (amount, row.get("etaForSeller", "")) if part)

    selected_class = next((row for row in classes if str(row["id"]) == selected), None)
    summary = ""
    if selected_class:
        ounces = selected_class["weightRange"]["max"]
        weight = f"{ounces} oz" if ounces < 16 else f"{ounces / 16:g} lb"
        payer = "You pay" if fields["mercari_specifics.shippingPayerId"]["value"] in (2, "2") else "Buyer pays"
        summary = "Recommended: " + " | ".join(part for part in (
            f"Up to {weight}", selected_class.get("etaForSeller", ""), f"{payer} ${selected_class['fee'] / 100:.2f}") if part)
    fields["mercari_specifics.shippingClassId"].update(
        value=selected, required=not own_shipping, options=[row["carrierDisplayName"] for row in classes],
        option_values={row["carrierDisplayName"]: str(row["id"]) for row in classes},
        option_help={row["carrierDisplayName"]: label_help(row) for row in classes}, help=summary)
    fields["mercari_specifics.shipsFrom"]["value"] = next((str(value).strip() for value in (
        specifics.get("shipsFrom"), get_path(listing, "form.ships_from_zip"), listing.get("zipCode"),
        get_path(listing, "form.specifics.shipping.location")) if re.fullmatch(r"\d{5}", str(value or "").strip())), "")
    smart = specifics.get("smartPricing")
    enabled = smart.get("enabled") is True if isinstance(smart, dict) else _number(price) >= 1
    fields["mercari_specifics.smartPricing.enabled"]["value"] = enabled
    minimum = (smart or {}).get("minimumPrice")
    fields["mercari_specifics.smartPricing.minimumPrice"].update(
        value=minimum if minimum not in (None, "") else math.floor(_number(price) * 0.85 + 0.5) if enabled else "",
        required=enabled)
    notes = list(notes or [])
    if category_id and not category:
        notes.append("This Mercari category is not in the native catalog. Reload the catalog before choosing its fields.")
    return {"marketplace": "mercari", "native": True, "known": not category_id or category is not None,
            "page_labels": True, "category_id": category_id if category else "", "fields": rows, "notes": notes}


async def editor_form(listing: dict) -> dict:
    from vendoo_studio.services.mercari_api import shipping_quotes

    ounces, dimensions, _ = package(listing)
    category_id = str((listing.get("direct_category_ids") or {}).get("mercari") or "")
    classes, notes = [], []
    if (listing.get("mercari_specifics") or {}).get("shippingMethod") != "SOYO" and category_id.isdigit() and ounces and dimensions is not None:
        try:
            quotes = await shipping_quotes(int(category_id), ounces, dimensions)
            labels = {row["id"]: row for row in catalog()["shippingLabels"]}
            for quote in quotes:
                if quote["id"] not in labels:
                    raise ValueError("Unknown Mercari shipping label")
                if quote["weightRange"]["max"] >= ounces:
                    classes.append({**labels[quote["id"]], **quote})
        except Exception:  # noqa: BLE001 - all product fields remain available without a shipping quote
            classes = []
            notes.append("Mercari shipping labels could not be loaded. Reload choices to try again.")
    return form(listing, classes, notes=notes)
