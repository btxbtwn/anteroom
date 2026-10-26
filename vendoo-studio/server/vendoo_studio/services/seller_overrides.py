"""Values the seller set on a listing, and the prices that follow the item price.

``seller_fields`` is a list of dotted paths. Generation, dropdown cleanup, and
a recommended price leave those paths alone. A marketplace price follows
``listing.price`` until the seller types a different number on that marketplace.
"""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

SELLER_FIELDS = "seller_fields"

MARKETPLACES = ("ebay", "poshmark", "mercari", "depop", "etsy", "vinted", "sellwild")

# The form row labeled "{Marketplace} price", and the key each payload prefers.
CANONICAL_PRICE = {
    "ebay": "ebay_specifics.price",
    "poshmark": "poshmark_specifics.poshmarkPrice",
    "mercari": "mercari_specifics.price",
    "depop": "depop_specifics.price",
    "etsy": "etsy_specifics.price",
    "vinted": "vinted_specifics.price",
    "sellwild": "sellwild_specifics.price",
}

_PRICE_FOLDS = frozenset({
    "price",
    "ebayprice",
    "poshmarkprice",
    "mercariprice",
    "depopprice",
    "etsyprice",
    "vintedprice",
    "sellwildprice",
    "listingprice",
    "buyitnowprice",
})
_SKIP_PRICE = ("original", "cost", "minimum", "floor", "smart", "offer", "accept", "decline")
_TOP_LEVEL = (
    "sku", "title", "size", "sizeType", "category_path", "package_dimensions_in",
    "zipCode", "zip_code", "department", "price", "description", "brand", "condition",
)
_DEPOP_MULTI = ("style", "occasion", "material", "type", "fit", "bodyFit", "attributeBottomStyle")


def fold_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").casefold())


def is_price_key(key: str) -> bool:
    folded = fold_key(key)
    if not folded or any(part in folded for part in _SKIP_PRICE):
        return False
    return folded in _PRICE_FOLDS


def money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return amount.quantize(Decimal("0.01"))


def price_number(value: Any) -> int | float | None:
    amount = money(value)
    if amount is None:
        return None
    if amount == amount.to_integral():
        return int(amount)
    return float(amount)


def locked_paths(listing: dict | None) -> set[str]:
    raw = listing.get(SELLER_FIELDS) if isinstance(listing, dict) else None
    if not isinstance(raw, list):
        return set()
    return {str(path).strip() for path in raw if str(path).strip()}


def is_locked(listing: dict | None, path: str) -> bool:
    return path in locked_paths(listing)


def _write_locks(listing: dict, paths: set[str]) -> None:
    if paths:
        listing[SELLER_FIELDS] = sorted(paths)
    else:
        listing.pop(SELLER_FIELDS, None)


def get_path(listing: dict, path: str) -> Any:
    current: Any = listing
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def set_path(listing: dict, path: str, value: Any) -> None:
    parts = [part for part in str(path).split(".") if part]
    if not parts:
        return
    current = listing
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if value is None:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = value


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def marketplace_of(path: str) -> str | None:
    head = str(path).split(".", 1)[0]
    if head.endswith("_specifics"):
        name = head[: -len("_specifics")]
        return name if name in MARKETPLACES else None
    return None


def _price_paths(listing: dict) -> list[str]:
    found: list[str] = []

    def walk(node: dict, prefix: str, depth: int) -> None:
        if depth > 3:
            return
        for key, value in node.items():
            path = f"{prefix}.{key}"
            if is_price_key(key) and not isinstance(value, (dict, list)):
                found.append(path)
            elif isinstance(value, dict):
                walk(value, path, depth + 1)

    for marketplace in MARKETPLACES:
        block = listing.get(f"{marketplace}_specifics")
        if isinstance(block, dict):
            walk(block, f"{marketplace}_specifics", 0)
    return found


def _market_locked(paths: set[str], marketplace: str) -> str | None:
    """A locked price path for this marketplace, preferring its canonical key."""
    canonical = CANONICAL_PRICE[marketplace]
    if canonical in paths:
        return canonical
    for path in sorted(paths):
        if marketplace_of(path) == marketplace and path.split(".")[-1] and is_price_key(path.split(".")[-1]):
            return path
    return None


def price_for(listing: dict | None, marketplace: str) -> int | float | None:
    """The price a marketplace is sent: an explicit override, else the item price."""
    if not isinstance(listing, dict):
        return None
    locked = _market_locked(locked_paths(listing), marketplace)
    if locked:
        amount = price_number(get_path(listing, locked))
        if amount is not None:
            return amount
    return price_number(listing.get("price"))


def capture_locked(listing: dict) -> dict[str, Any]:
    return {path: copy.deepcopy(get_path(listing, path)) for path in locked_paths(listing)}


def restore_locked(listing: dict, captured: dict[str, Any]) -> None:
    for path, value in captured.items():
        set_path(listing, path, copy.deepcopy(value))
    _write_locks(listing, set(captured) | locked_paths(listing))


def preserving_locks(listing: dict, mutate) -> None:
    """Run a normalizer, then put seller-owned values back."""
    captured = capture_locked(listing)
    mutate(listing)
    if captured:
        restore_locked(listing, captured)


def expand_depop_multi(listing: dict) -> bool:
    """Store Depop multi-value fields as separate choices, not one comma-joined string."""
    block = listing.get("depop_specifics")
    if not isinstance(block, dict):
        return False
    changed = False
    updated = dict(block)
    for key in _DEPOP_MULTI:
        if key not in updated:
            continue
        raw = updated.get(key)
        pieces = _split_choices(raw)
        if pieces != raw and not (isinstance(raw, list) and pieces == list(raw)):
            updated[key] = pieces
            changed = True
    if changed:
        listing["depop_specifics"] = updated
    return changed


def _split_choices(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and "," in item:
            parts = [part.strip() for part in item.split(",") if part.strip()]
        else:
            text = str(item).strip() if item is not None else ""
            parts = [text] if text else []
        for part in parts:
            if part not in out:
                out.append(part)
    return out


def _changed_paths(previous: dict, submitted: dict) -> set[str]:
    changed: set[str] = set()
    for key in _TOP_LEVEL:
        if _stable(previous.get(key)) != _stable(submitted.get(key)):
            changed.add(key)
    for marketplace in MARKETPLACES:
        bucket = f"{marketplace}_specifics"
        prev = previous.get(bucket) if isinstance(previous.get(bucket), dict) else {}
        sub = submitted.get(bucket) if isinstance(submitted.get(bucket), dict) else {}
        for key in set(prev) | set(sub):
            if _stable(prev.get(key)) != _stable(sub.get(key)):
                changed.add(f"{bucket}.{key}")
    for bucket in ("marketplace_category_ids", "marketplace_categories", "direct_category_ids", "marketplace_photos"):
        prev = previous.get(bucket) if isinstance(previous.get(bucket), dict) else {}
        sub = submitted.get(bucket) if isinstance(submitted.get(bucket), dict) else {}
        for key in set(prev) | set(sub):
            if _stable(prev.get(key)) != _stable(sub.get(key)):
                changed.add(f"{bucket}.{key}")
    return changed


def apply_price_policy(previous: dict, submitted: dict) -> None:
    """Rewrite marketplace prices that were still showing the previous item price.

    A marketplace price the seller changed to something other than the new item
    price stays, and is locked. One that still equals the old item price — the
    recommended price that was copied onto every marketplace — takes the new
    item price. A price that already differed, and that this save did not touch,
    is an override and is locked.
    """
    if not isinstance(submitted, dict):
        return
    previous = previous if isinstance(previous, dict) else {}
    locks = locked_paths(previous) | locked_paths(submitted)
    old = money(previous.get("price"))
    new = money(submitted.get("price"))
    new_number = price_number(submitted.get("price"))
    edited = _changed_paths(previous, submitted)
    if "price" in edited and new is not None:
        locks.add("price")

    paths = list(dict.fromkeys([*_price_paths(previous), *_price_paths(submitted), *CANONICAL_PRICE.values()]))
    for path in paths:
        market = marketplace_of(path)
        if not market:
            continue
        parent = ".".join(path.split(".")[:-1])
        parent_exists = isinstance(get_path(previous, parent), dict) or isinstance(get_path(submitted, parent), dict)
        if not parent_exists:
            continue
        present = get_path(previous, path) is not None or get_path(submitted, path) is not None
        if not present and not ("price" in edited and path == CANONICAL_PRICE[market]):
            continue
        previous_amount = money(get_path(previous, path)) if get_path(previous, path) is not None else None
        submitted_amount = money(get_path(submitted, path)) if get_path(submitted, path) is not None else None
        touched = path in edited
        if touched and submitted_amount is not None and new is not None and submitted_amount != new:
            locks.add(path)
            continue
        if touched and (submitted_amount is None or (new is not None and submitted_amount == new)):
            locks.discard(path)
            if new_number is not None:
                set_path(submitted, path, new_number)
            continue
        if _market_locked(locks, market) and path != _market_locked(locks, market):
            held = price_number(get_path(submitted, _market_locked(locks, market)))
            if held is not None and (path in _price_paths(submitted) or path == CANONICAL_PRICE[market]):
                set_path(submitted, path, held)
            continue
        if path in locks:
            continue
        if new_number is None:
            continue
        follower = (
            previous_amount is None
            or (old is not None and previous_amount == old)
            or (submitted_amount is not None and old is not None and submitted_amount == old)
        )
        if follower:
            set_path(submitted, path, new_number)
            locks.discard(path)
        elif previous_amount is not None and old is not None and previous_amount != old and not touched:
            locks.add(path)
    _write_locks(submitted, locks)


def sync_unlocked_prices(listing: dict) -> bool:
    """Point every unlocked marketplace price at the item price."""
    if not isinstance(listing, dict):
        return False
    general = price_number(listing.get("price"))
    if general is None:
        return False
    locks = locked_paths(listing)
    changed = False
    paths = list(dict.fromkeys([*CANONICAL_PRICE.values(), *_price_paths(listing)]))
    for path in paths:
        market = marketplace_of(path)
        if not market:
            continue
        if _market_locked(locks, market):
            held = price_number(get_path(listing, _market_locked(locks, market)))
            if held is None:
                continue
            if money(get_path(listing, path)) != money(held):
                set_path(listing, path, held)
                changed = True
            continue
        if get_path(listing, path) is None or money(get_path(listing, path)) == money(general):
            continue
        set_path(listing, path, general)
        changed = True
    return changed


def lock_seller_edits(previous: dict, submitted: dict) -> None:
    """A value the seller just changed stays, including a cleared Depop style."""
    if not isinstance(submitted, dict):
        return
    locks = locked_paths(previous) | locked_paths(submitted)
    for path in _changed_paths(previous if isinstance(previous, dict) else {}, submitted):
        leaf = path.split(".")[-1]
        if path == "price" or is_price_key(leaf):
            continue
        locks.add(path)
    _write_locks(submitted, locks)


def prepare_user_listing(previous: dict | None, submitted: dict, *, notes: dict | None = None) -> dict:
    """Apply one seller save: locks, followed prices, Depop style chips, measurements."""
    from vendoo_studio.services.depop_measurements import sync_depop_measurements

    previous = copy.deepcopy(previous) if isinstance(previous, dict) else {}
    if not isinstance(submitted, dict):
        return submitted
    expand_depop_multi(previous)
    expand_depop_multi(submitted)
    lock_seller_edits(previous, submitted)
    apply_price_policy(previous, submitted)
    if not is_locked(submitted, "depop_specifics.measurements"):
        sync_depop_measurements(submitted, notes)
    return submitted


def _parse_field_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def apply_field_updates(listing: dict, fields: list[dict]) -> list[str]:
    """Set dotted paths and lock them. A clear stores an empty list or removes the key."""
    locks = locked_paths(listing)
    applied: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        path = str(field.get("path") or "").strip()
        if not path or path == SELLER_FIELDS:
            continue
        leaf = path.split(".")[-1]
        clearing = field.get("clear") is True or field.get("value") in ("", None, [])
        if clearing:
            if leaf in _DEPOP_MULTI:
                set_path(listing, path, [])
            else:
                parts = path.split(".")
                parent = get_path(listing, ".".join(parts[:-1])) if len(parts) > 1 else listing
                if isinstance(parent, dict):
                    parent.pop(parts[-1], None)
                elif path in listing:
                    listing.pop(path, None)
        else:
            value = _parse_field_value(field.get("value"))
            if leaf in _DEPOP_MULTI:
                value = _split_choices(value)
            set_path(listing, path, value)
        locks.add(path)
        applied.append(path)
    _write_locks(listing, locks)
    if any(path == "price" or is_price_key(path.split(".")[-1]) for path in applied):
        sync_unlocked_prices(listing)
    return applied


def materialize_prices(listing: dict) -> None:
    """Write the price each marketplace will be sent into the keys the payload reads."""
    if isinstance(listing, dict):
        sync_unlocked_prices(listing)


def notes_for_listing_edits(previous: dict, submitted: dict) -> dict[str, str]:
    """Item Details keys that must move with the listing, or a later prepare puts the old value back."""
    updates: dict[str, str] = {}
    if _stable(previous.get("sku")) != _stable(submitted.get("sku")):
        updates["sku"] = str(submitted.get("sku") or "").strip()
    if _stable(previous.get("package_dimensions_in")) != _stable(submitted.get("package_dimensions_in")):
        updates["packageDimensions"] = str(submitted.get("package_dimensions_in") or "").strip()
    if _stable(previous.get("category_path")) != _stable(submitted.get("category_path")):
        updates["categoryOverride"] = str(submitted.get("category_path") or "").strip()
    if _stable(previous.get("price")) != _stable(submitted.get("price")):
        number = price_number(submitted.get("price"))
        if number is not None:
            updates["askingPrice"] = f"{number:.2f}" if isinstance(number, float) else f"{number}.00"
    return updates
