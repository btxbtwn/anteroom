"""Mercari's native sell form, using local taxonomy and anonymous shipping quotes."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import Counter
from functools import lru_cache

from anteroom.config import skills_dir
from anteroom.services.seller_overrides import get_path, marketplace_text, price_for

_NO_BRAND_TOKENS = frozenset({
    "", "unbranded", "no brand", "not sure", "no brand not sure",
    "none", "n a", "na", "unknown",
})
_QUOTE_TTL_S = 15 * 60
_QUOTE_MAX_ENTRIES = 512
_QuoteKey = tuple[int, int, tuple[float, float, float]]
_quote_cache: dict[_QuoteKey, tuple[float, list[dict]]] = {}
_quote_inflight: dict[_QuoteKey, asyncio.Task[list[dict]]] = {}
_quote_generation: dict[_QuoteKey, int] = {}
_quote_seq = 0


def is_no_brand(value: object) -> bool:
    """True when a brand value means no brand, unbranded, or not sure."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip() in _NO_BRAND_TOKENS


def resolve_brand(listing: dict) -> tuple[str, bool]:
    """Resolve Mercari's brand picker value and ``noBrand`` checkbox state.

    When the brand is absent, unbranded, or not in Mercari's brand list,
    Mercari selects ``No brand / Not sure`` (``noBrand = True``) and leaves
    Brand empty unless the seller explicitly unchecked ``noBrand``."""
    specifics = listing.get("mercari_specifics") or {}
    if specifics.get("noBrand") is True:
        return "", True
    raw = marketplace_text(listing, "mercari", "brand")
    if is_no_brand(raw):
        return "", specifics.get("noBrand") is not False
    known = _brands_by_fold().get(raw.casefold())
    if known is not None:
        return known, False
    return "", specifics.get("noBrand") is not False


def clear_quote_cache() -> None:
    """Drop cached public Mercari shipping quotes (for tests)."""
    _quote_cache.clear()
    _quote_inflight.clear()
    _quote_generation.clear()


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


@lru_cache(maxsize=1)
def category_choices() -> dict:
    """Mercari's category picker, the same for every listing."""
    choices = categories()
    return {"options": [row["path"] for row in choices], "option_values": {row["path"]: row["id"] for row in choices}}


@lru_cache(maxsize=1)
def brand_choices() -> dict:
    """Mercari's brand picker, the same for every listing."""
    brands = [row["name"] for row in catalog()["itemBrands"]]
    return {"options": brands, "option_values": {name: name for name in brands}}


@lru_cache(maxsize=1)
def _categories_by_id() -> dict[str, dict]:
    return {str(row["id"]): row for row in catalog()["itemCategories"]}


@lru_cache(maxsize=1)
def _brands_by_fold() -> dict[str, str]:
    # Reversed so the first catalog entry wins on case-insensitive collisions.
    return {row["name"].casefold(): row["name"] for row in reversed(catalog()["itemBrands"])}


@lru_cache(maxsize=1)
def _shipping_labels_by_id() -> dict[int, dict]:
    return {row["id"]: row for row in catalog()["shippingLabels"]}


@lru_cache(maxsize=1)
def _size_tables() -> tuple[dict[int, dict], dict[int, dict], dict[int, str]]:
    data = catalog()
    groups = {row["id"]: row for row in data["itemSizeGroups"]}
    sizes = {row["id"]: row for row in data["itemSizes"]}
    subgroups = {row["id"]: row["title"] for row in data["itemSizeSubGroups"]}
    return groups, sizes, subgroups


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
              # A saved radio label ("Yes") is the same answer as its value ("yes").
              "fitsShoebox": str(own.get("fitsShoebox") or "").strip().casefold(),
              "skipDimensions": own.get("skipDimensions", False)}
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
            dimensions = (_number(values["length"]), _number(values["width"]), _number(values["height"]))
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


def general_condition(listing: dict) -> str:
    """The Mercari condition code from General when Mercari has not set or cleared one."""
    specifics = listing.get("mercari_specifics") or {}
    if "condition" in specifics:
        return _condition(specifics.get("condition"))
    return _condition(listing.get("condition"))


def _sizes(category: dict | None) -> list[dict]:
    if not category or not category["itemSizeGroupId"]:
        return []
    groups, sizes, subgroups = _size_tables()
    group = groups[category["itemSizeGroupId"]]
    rows = [sizes[size_id] for size_id in group["sizeIds"]]
    labels = [" ".join(part for part in (row["title"], row["subtitle"]) if part) for row in rows]
    counts = Counter(labels)
    return [{"id": str(row["id"]), "name": label, "group": subgroups.get(row["itemSizeSubGroupId"], ""),
             "label": label + (" · " + (subgroups.get(row["itemSizeSubGroupId"]) or str(row["id"]))
                               if counts[label] > 1 else "")}
            for row, label in zip(rows, labels, strict=True)]


def _size_token(value: str) -> str:
    from anteroom.models.sizes import size_key

    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if compact in {"os", "onesize", "onesizefitsall", "osfa"}:
        return "ONESIZE"
    return size_key(value)


def match_size_id(listing: dict, category_or_sizes: str | dict | list[dict] | None) -> str:
    """Match General/Mercari size to Mercari's nativeSizeId, preferring Regular/Standard."""
    specifics = listing.get("mercari_specifics") or {}
    if "nativeSizeId" in specifics:
        return str(specifics.get("nativeSizeId") or "")
    if isinstance(category_or_sizes, list):
        sizes = category_or_sizes
    elif isinstance(category_or_sizes, dict):
        sizes = _sizes(category_or_sizes)
    elif category_or_sizes:
        category = _categories_by_id().get(str(category_or_sizes))
        sizes = _sizes(category)
    else:
        sizes = []
    if not sizes:
        return ""
    raw_size = specifics["size"] if "size" in specifics else listing.get("size")
    wanted = str(raw_size or "").strip()
    if not wanted:
        return ""
    folded = wanted.casefold()
    wanted_key = _size_token(wanted)
    fit = str(specifics.get("sizeType") or listing.get("sizeType") or "").strip().casefold()

    def narrow(candidates: list[dict]) -> list[dict]:
        if fit:
            if fit in {"regular", "standard"}:
                return [row for row in candidates if row["group"].casefold() in {"standard", "regular", ""}]
            return [row for row in candidates if fit in row["group"].casefold()]
        if len(candidates) > 1:
            regular = [row for row in candidates if row["group"].casefold() in {"standard", "regular"}]
            if regular:
                return regular
        return candidates

    exact = [row for row in sizes if folded in (row["label"].casefold(), row["name"].casefold())]
    keyed = [row for row in sizes if wanted_key and _size_token(row["name"]) == wanted_key] if wanted_key else []
    for candidates in (exact, keyed):
        if not candidates:
            continue
        # Plain "M" in Men's T-shirts matches Tall "M" exactly, while Regular
        # spells it "M (38-40)". Prefer Regular unless the seller asked for Tall.
        if candidates is exact and not fit and keyed:
            exact_regular = [row for row in exact if row["group"].casefold() in {"standard", "regular", ""}]
            keyed_regular = [row for row in keyed if row["group"].casefold() in {"standard", "regular"}]
            if not exact_regular and keyed_regular:
                continue
        picked = narrow(candidates)
        if len(picked) == 1:
            return picked[0]["id"]
    return ""


def form(listing: dict, shipping_classes: list[dict] | None = None, *, notes: list[str] | None = None) -> dict:
    from anteroom.services.native_editor import field

    specifics = listing.get("mercari_specifics") or {}
    category_id = str((listing.get("direct_category_ids") or {}).get("mercari") or "")
    category = _categories_by_id().get(category_id)
    rows = [dict(row) for row in catalog()["fields"]]
    fields = {row["path"]: row for row in rows}
    for path, row in fields.items():
        found = get_path(listing, path)
        row["value"] = found if found is not None else ""
    for name in ("title", "description"):
        fields[f"mercari_specifics.{name}"]["value"] = marketplace_text(listing, "mercari", name)
    fields["marketplace_photos.mercari"]["value"] = []
    price = price_for(listing, "mercari")
    fields["price"]["value"] = price if price is not None else ""
    picker = category_choices()
    fields["direct_category_ids.mercari"].update(value=category_id, options=picker["options"],
                                               option_values=picker["option_values"])
    brand, no_brand = resolve_brand(listing)
    picker = brand_choices()
    fields["mercari_specifics.brand"].update(
        value=brand, options=picker["options"], option_values=picker["option_values"],
        options_source="marketplace",
    )
    fields["mercari_specifics.noBrand"]["value"] = no_brand
    fields["mercari_specifics.condition"]["value"] = general_condition(listing)
    sizes = _sizes(category)
    if sizes:
        value = match_size_id(listing, sizes)
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
    minimum = smart.get("minimumPrice") if isinstance(smart, dict) else None
    fields["mercari_specifics.smartPricing.minimumPrice"].update(
        value=minimum if minimum not in (None, "") else math.floor(_number(price) * 0.85 + 0.5) if enabled else "",
        required=enabled)
    notes = list(notes or [])
    if category_id and not category:
        notes.append("This Mercari category is not in the native catalog. Reload the catalog before choosing its fields.")
    return {"marketplace": "mercari", "native": True, "known": not category_id or category is not None,
            "page_labels": True, "category_id": category_id if category else "", "fields": rows, "notes": notes}


async def _cached_shipping_quotes(
    category: int,
    ounces: int,
    dimensions: tuple[float, float, float],
    *,
    refresh: bool = False,
) -> list[dict]:
    """Cache anonymous public shipping quotes by ``(category, ounces, dimensions)`` and coalesce concurrent reads."""
    global _quote_seq
    from anteroom.services.mercari_api import shipping_quotes

    key: _QuoteKey = (category, ounces, (float(dimensions[0]), float(dimensions[1]), float(dimensions[2])))
    now = time.monotonic()
    if not refresh:
        cached = _quote_cache.get(key)
        if cached is not None and now - cached[0] < _QUOTE_TTL_S:
            _quote_cache.pop(key, None)
            _quote_cache[key] = cached
            return cached[1]
        inflight = _quote_inflight.get(key)
        if inflight is not None and not inflight.done() and inflight.get_loop() is asyncio.get_running_loop():
            return await asyncio.shield(inflight)

    _quote_seq += 1
    generation = _quote_seq
    _quote_generation[key] = generation
    if refresh:
        _quote_inflight.pop(key, None)

    async def fetch_and_store() -> list[dict]:
        try:
            quotes = await shipping_quotes(category, ounces, dimensions)
        except BaseException:
            if _quote_generation.get(key) == generation:
                _quote_generation.pop(key, None)
            raise
        if _quote_generation.get(key) == generation:
            _quote_cache.pop(key, None)
            _quote_cache[key] = (time.monotonic(), quotes)
            while len(_quote_cache) > _QUOTE_MAX_ENTRIES:
                evicted = next(iter(_quote_cache))
                _quote_cache.pop(evicted, None)
                if evicted not in _quote_inflight:
                    _quote_generation.pop(evicted, None)
        return quotes

    if refresh:
        return await fetch_and_store()

    task = asyncio.create_task(fetch_and_store())
    _quote_inflight[key] = task

    def _finish(done: asyncio.Task[list[dict]]) -> None:
        if _quote_inflight.get(key) is done:
            _quote_inflight.pop(key, None)
        if not done.cancelled():
            done.exception()  # Retrieve any error if every caller cancelled early.

    task.add_done_callback(_finish)
    # Shield the initiator too so aborting a background warm-up never cancels
    # another caller waiting on the same shared public shipping quote.
    return await asyncio.shield(task)


async def editor_form(listing: dict, *, refresh: bool = False) -> dict:
    ounces, dimensions, _ = package(listing)
    category_id = str((listing.get("direct_category_ids") or {}).get("mercari") or "")
    classes, notes = [], []
    if (listing.get("mercari_specifics") or {}).get("shippingMethod") != "SOYO" and category_id.isdigit() and ounces and dimensions is not None:
        try:
            quotes = await _cached_shipping_quotes(int(category_id), ounces, dimensions, refresh=refresh)
            labels = _shipping_labels_by_id()
            for quote in quotes:
                if quote["id"] not in labels:
                    raise ValueError("Unknown Mercari shipping label")
                if quote["weightRange"]["max"] >= ounces:
                    classes.append({**labels[quote["id"]], **quote})
        except Exception:  # noqa: BLE001 - all product fields remain available without a shipping quote
            classes = []
            notes.append("Mercari shipping labels could not be loaded. Reload choices to try again.")
    result = form(listing, classes, notes=notes)
    if notes:
        result["reload_recommended"] = True
    return result
