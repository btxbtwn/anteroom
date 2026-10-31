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
FORM_MARKETPLACES = (*MARKETPLACES, "facebook", "grailed")

# Shared General fields a marketplace form shows until that form edits them.
# Size is handled beside these: equality is the size propagation would write
# (eBay petite PL, Depop kids age), not the raw general size.
FOLLOWED_FIELDS = ("title", "description", "brand", "department")
# Copies of General that are not themselves item specifics. Brand, condition,
# size and department stay on the form as that marketplace's own fields.
FOLLOWED_NOT_ASPECTS = frozenset({"title", "description", "quantity", "primarycolor", "secondarycolor"})

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
_SKIP_PRICE_CONTAINERS = (*_SKIP_PRICE, "shipping", "postage", "delivery", "tax", "variation")
_TOP_LEVEL = (
    "sku", "title", "size", "sizeType", "category_path", "package_dimensions_in",
    "zipCode", "zip_code", "department", "price", "description", "brand", "condition",
)
_DEPOP_MULTI = ("style", "occasion", "material", "type", "fit", "bodyFit", "attributeBottomStyle")


def fold_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").casefold())


def is_price_key(key: str) -> bool:
    folded = fold_key(key)
    if not folded or any(part in folded for part in _SKIP_PRICE):
        return False
    return folded in _PRICE_FOLDS


def is_price_path(path: str) -> bool:
    parts = path.split(".")
    return is_price_key(parts[-1]) and not any(
        word in fold_key(part) for part in parts[1:-1] for word in _SKIP_PRICE_CONTAINERS
    )


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


def field_locked(listing: dict | None, marketplace: str, field: str) -> bool:
    """True when any seller lock names this marketplace field, whatever its capitalization."""
    want = fold_key(field)
    prefix = f"{marketplace}_specifics."
    for path in locked_paths(listing):
        if not path.startswith(prefix):
            continue
        if fold_key(path.split(".")[-1]) == want:
            return True
    return False


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
            if is_price_path(path) and not isinstance(value, (dict, list)):
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
        if marketplace_of(path) == marketplace and is_price_path(path):
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
        prev = previous.get(bucket)
        if not isinstance(prev, dict):
            prev = {}
        sub = submitted.get(bucket)
        if not isinstance(sub, dict):
            sub = {}
        for key in set(prev) | set(sub):
            if _stable(prev.get(key)) != _stable(sub.get(key)):
                changed.add(f"{bucket}.{key}")
    for bucket in ("marketplace_category_ids", "marketplace_categories", "direct_category_ids", "marketplace_photos"):
        prev = previous.get(bucket)
        if not isinstance(prev, dict):
            prev = {}
        sub = submitted.get(bucket)
        if not isinstance(sub, dict):
            sub = {}
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
        if is_price_path(path):
            continue
        locks.add(path)
    _write_locks(submitted, locks)


def _specifics_block(listing: dict | None, marketplace: str) -> dict | None:
    raw = listing.get(f"{marketplace}_specifics") if isinstance(listing, dict) else None
    return raw if isinstance(raw, dict) else None


def _field_key(block: dict, field: str) -> str | None:
    want = fold_key(field)
    for key in block:
        if fold_key(key) == want:
            return key
    return None


def followed_entry(listing: dict | None, marketplace: str, field: str) -> tuple[str, Any] | None:
    """The key and value this form stores, including a cleared value.

    ``None`` means the form has no copy and still shows General.
    """
    block = _specifics_block(listing, marketplace)
    if block is None:
        return None
    key = _field_key(block, field)
    if key is None:
        return None
    return key, block[key]


def _same_followed(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if left is None or right is None:
        return False
    return str(left).strip() == str(right).strip()


def marketplace_text(listing: dict | None, marketplace: str, field: str) -> str:
    """The marketplace override when the seller set one, otherwise the general value.

    A stored empty string is an override. A missing key still follows General.
    """
    if not isinstance(listing, dict):
        return ""
    entry = followed_entry(listing, marketplace, field)
    value = entry[1] if entry is not None else listing.get(field)
    if value is None:
        return ""
    return str(value)


def _unlock_field(locks: set[str], marketplace: str, field: str) -> set[str]:
    prefix = f"{marketplace}_specifics."
    want = fold_key(field)
    return {
        item for item in locks
        if not (item.startswith(prefix) and fold_key(item.split(".")[-1]) == want)
    }


def propagated_size(listing: dict | None, marketplace: str) -> str:
    """The size string propagation would write for this marketplace."""
    if not isinstance(listing, dict) or marketplace not in MARKETPLACES:
        return ""
    from vendoo_studio.services.listing_generate import ebay_size, sanitize_size_value

    size = sanitize_size_value(listing.get("size"))
    if not size:
        return ""
    block = _specifics_block(listing, marketplace) or {}
    if marketplace == "ebay":
        return ebay_size(listing, block, size)
    if marketplace == "depop":
        from vendoo_studio.models.depop_fields import depop_size_for

        return depop_size_for(size, listing)
    return size


def overlay_followed_fields(listing: dict, marketplace: str) -> dict:
    """A shallow copy whose shared fields are this form's own, when it has them."""
    if not isinstance(listing, dict):
        return listing
    overlaid = None
    for field in FOLLOWED_FIELDS:
        entry = followed_entry(listing, marketplace, field)
        if entry is None:
            continue
        if overlaid is None:
            overlaid = dict(listing)
        overlaid[field] = copy.deepcopy(entry[1])
    return overlaid if overlaid is not None else listing


def apply_marketplace_overrides(snapshot: dict, marketplace: str) -> None:
    """Point one marketplace's send at the shared values that form edited."""
    if not isinstance(snapshot, dict):
        return
    for field in FOLLOWED_FIELDS:
        entry = followed_entry(snapshot, marketplace, field)
        if entry is None:
            continue
        snapshot[field] = copy.deepcopy(entry[1])


def reconcile_followed_fields(previous: dict | None, submitted: dict) -> None:
    """Drop marketplace copies that still match General so the next edit fills them.

    A copy equal to the current General value is not an independent edit. A copy
    still equal to the previous General value is the old fill, so this General
    edit replaces it. A different copy stays on that marketplace and is locked.
    """
    if not isinstance(submitted, dict):
        return
    previous = previous if isinstance(previous, dict) else {}
    locks = locked_paths(submitted)
    changed = _changed_paths(previous, submitted)
    prior = locked_paths(previous)
    for marketplace in FORM_MARKETPLACES:
        block = _specifics_block(submitted, marketplace)
        if block is None:
            continue
        for field in FOLLOWED_FIELDS:
            entry = followed_entry(submitted, marketplace, field)
            if entry is None:
                continue
            key, value = entry
            path = f"{marketplace}_specifics.{key}"
            general_changed = not _same_followed(previous.get(field), submitted.get(field))
            owned_lock = path in prior or any(
                item.startswith(f"{marketplace}_specifics.") and fold_key(item.split(".")[-1]) == fold_key(field)
                for item in prior
            )
            follows = (
                _same_followed(value, submitted.get(field))
                or (general_changed and _same_followed(value, previous.get(field)))
                or (general_changed and not owned_lock and path not in changed)
            )
            if follows:
                # Still the general value, the previous fill, or an unlocked
                # difference. Drop it so the next general edit fills this form.
                block.pop(key, None)
                locks = _unlock_field(locks, marketplace, field)
            elif path in changed or owned_lock:
                locks.add(path)
    _write_locks(submitted, locks)


def reconcile_size_fields(previous: dict | None, submitted: dict) -> None:
    """A marketplace size follows General until the seller types a different one.

    Matching the general size, or the spelling propagation would write for the
    current or previous general size, unlocks the field and drops the copy.
    A seller edit that differs stays locked. An unlocked difference is left
    alone: eBay propagation still overwrites it, and Depop keeps a size that
    already differs.
    """
    if not isinstance(submitted, dict):
        return
    previous = previous if isinstance(previous, dict) else {}
    locks = locked_paths(submitted)
    changed = _changed_paths(previous, submitted)
    prior = locked_paths(previous)
    from vendoo_studio.services.listing_generate import sanitize_size_value

    for marketplace in MARKETPLACES:
        block = _specifics_block(submitted, marketplace)
        if block is None:
            continue
        entry = followed_entry(submitted, marketplace, "size")
        if entry is None:
            continue
        key, value = entry
        path = f"{marketplace}_specifics.{key}"
        current_size = sanitize_size_value(submitted.get("size"))
        previous_size = sanitize_size_value(previous.get("size")) if str(previous.get("size") or "").strip() else ""
        matches = (
            propagated_size(submitted, marketplace),
            current_size,
            propagated_size(previous, marketplace) if previous_size else "",
            previous_size,
        )
        owned_lock = any(
            item.startswith(f"{marketplace}_specifics.") and fold_key(item.split(".")[-1]) == "size"
            for item in prior
        )
        if any(_same_followed(value, item) for item in matches if item):
            block.pop(key, None)
            locks = _unlock_field(locks, marketplace, "size")
        elif path in changed or owned_lock:
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
    reconcile_followed_fields(previous, submitted)
    reconcile_size_fields(previous, submitted)
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
    if any(is_price_path(path) for path in applied):
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
