"""Vendoo's coded vocabularies, learned from the seller's own items.

Vendoo's coded values (condition codes, colour codes, each marketplace's own
condition ids, which aspects hold lists) are not guessed.
``observe_item_schema`` learns them from the user's existing items, and
``encode_field`` / ``marketplace_condition`` encode against what it learned,
reporting anything it could not encode rather than storing a value that looks
right and is wrong.
"""

from __future__ import annotations

import re
from typing import Any

from vendoo_studio.models.mercari_shipping import package_ounces
from vendoo_studio.services.vendoo_item_shape import (
    _CAMEL_BOUNDARY,
    GENERAL_KEY,
    LISTINGS_KEY,
    _code_of,
    _label_of,
    _norm,
)
from vendoo_studio.services.vendoo_specifics import specifics_key


def observe_item_schema(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an encoding table from real Vendoo items.

    ``{"fields": {name: {"labels": {normalised label: code}, "codes": [...],
    "shape": "object"|"scalar"}}, "item_count": n}``. Anything unseen is
    reported by the serializer instead of being invented.
    """
    fields: dict[str, dict[str, Any]] = {}

    def record(name: str, raw: Any) -> None:
        entry = fields.setdefault(name, {"labels": {}, "codes": [], "shape": "scalar"})
        if isinstance(raw, list):
            for part in raw:
                record(name, part)
            return
        if isinstance(raw, dict):
            entry["shape"] = "object"
            label, code = _label_of(raw), _code_of(raw)
            if label and code not in (None, ""):
                entry["labels"][_norm(label)] = code
            if code not in (None, "") and code not in entry["codes"]:
                entry["codes"].append(code)
            return
        if raw not in (None, "") and raw not in entry["codes"]:
            entry["codes"].append(raw)
            if isinstance(raw, str) and raw.startswith("v_"):
                entry["labels"].setdefault(_norm(raw[2:]), raw)

    marketplaces: dict[str, dict[str, Any]] = {}
    aspects: dict[str, dict[str, str]] = {}

    counted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        counted += 1
        general = item.get(GENERAL_KEY)
        if isinstance(general, dict):
            for name, raw in general.items():
                if raw in (None, ""):
                    continue
                record(name, raw)
        observe_listing_encodings(item, marketplaces, aspects)

    return {"fields": fields, "marketplaces": marketplaces, "aspects": aspects, "item_count": counted}


def aspect_suffix(key: str, category_id: Any = "") -> str:
    """``53159_Season`` -> ``Season``. The prefix is the leaf's own id.

    Slug ids contain underscores themselves, so the id is stripped literally
    when known and only a numeric prefix is guessed when it is not.
    """
    text = str(key or "")
    prefix = str(category_id or "")
    if prefix and text.startswith(f"{prefix}_"):
        return text[len(prefix) + 1:]
    return re.sub(r"^\d+_", "", text)


def _observe_shipping_choices(
    marketplace: str,
    section: dict[str, Any],
    leaf_id: str,
    general: dict[str, Any],
    marketplaces: dict[str, dict[str, Any]],
) -> None:
    """Learn the shipping picks only the marketplace itself can offer.

    Facebook's carrier is ``"<service type>:<name>"`` from Facebook's own
    answer for a weight tier; Vinted's Package Size is an id from Vinted's
    list for the category. Neither is in Vendoo's schema, so both are read off
    forms the seller filled in: the carrier per Package weight tier, the
    package size against the package's weight in ounces.
    """
    if marketplace == "facebook":
        specifics = section.get("marketplaceSpecifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        carrier, tier = specifics.get("carrier"), specifics.get("packageWeightLabel")
        if isinstance(carrier, str) and ":" in carrier and isinstance(tier, str) and tier:
            marketplaces.setdefault(marketplace, {}).setdefault("shippingCarrier", {})[tier] = carrier
    elif marketplace == "vinted" and leaf_id:
        specifics = section.get("categorySpecifics")
        size = (specifics or {}).get(specifics_key(leaf_id, "packageSizeId")) if isinstance(specifics, dict) else None
        weight = general.get("weight")
        if not isinstance(weight, dict):
            weight = {}
        ounces = package_ounces(weight.get("pounds"), weight.get("ounces"))
        if size not in (None, "") and ounces > 0:
            marketplaces.setdefault(marketplace, {}).setdefault("packageSize", {})[str(ounces)] = str(size)


def observe_listing_encodings(
    item: dict[str, Any],
    marketplaces: dict[str, dict[str, Any]],
    aspects: dict[str, dict[str, str]],
) -> None:
    """Learn each marketplace's own vocabulary from one stored item.

    Every marketplace codes condition differently (eBay ``"3000"``, Poshmark
    ``"ug"``, Depop ``"used_good"``) against the same Vendoo ``v_`` code, and
    eBay stores multi-select aspects as lists. Both are read off the seller's
    own items rather than guessed.
    """
    general = item.get(GENERAL_KEY)
    if not isinstance(general, dict):
        general = {}
    general_condition = _code_of(general.get("condition")) or general.get("condition")
    general_key = _norm(general_condition)
    listings = item.get(LISTINGS_KEY)
    if not isinstance(listings, dict):
        return
    for marketplace, section in listings.items():
        if not isinstance(section, dict):
            continue
        overrides = section.get("overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        code = overrides.get("condition")
        if general_key and code not in (None, ""):
            entry = marketplaces.setdefault(str(marketplace), {})
            entry.setdefault("condition", {})[general_key] = code
        leaf = overrides.get("categoryV2")
        if not isinstance(leaf, dict):
            leaf = {}
        leaf_id = leaf.get("id") or ""
        _observe_shipping_choices(str(marketplace), section, str(leaf_id), general, marketplaces)
        specifics = section.get("categorySpecifics")
        if not isinstance(specifics, dict):
            continue
        shapes = aspects.setdefault(str(marketplace), {})
        for key, value in specifics.items():
            suffix = aspect_suffix(key, leaf_id)
            if not suffix:
                continue
            # A filled list proves multi-select; an empty value proves nothing.
            if isinstance(value, list):
                shapes[suffix] = "list"
            elif value not in (None, "") and suffix not in shapes:
                shapes[suffix] = "scalar"


# Vendoo's own condition vocabulary, lifted from its web bundle's
# ``conditionMap``. Marketplace forms store these codes and nothing else:
# Poshmark shows an empty Condition when handed a code it does not own
# (``good``), and rejects a Vendoo ``v_`` general code on Relist. eBay is
# omitted on purpose — its ids are category-specific and stay learned-only.
_VENDOO_MARKETPLACE_CONDITIONS: dict[str, dict[str, str | int]] = {
    "poshmark": {
        "v_newWithTagsBox": "nwt",
        "v_newWithOutTags": "uln",
        "v_newWithDefects": "ug",
        "v_preowned_excellent": "uln",
        "v_preowned": "ug",
        "v_preowned_fair": "uf",
        "v_poor": "uf",
    },
    "mercari": {
        "v_newWithTagsBox": "1",
        "v_newWithOutTags": "2",
        "v_newWithDefects": "3",
        "v_preowned_excellent": "3",
        "v_preowned": "3",
        "v_preowned_fair": "4",
        "v_poor": "5",
    },
    "depop": {
        "v_newWithTagsBox": "brand_new",
        "v_newWithOutTags": "used_like_new",
        "v_newWithDefects": "used_good",
        "v_preowned_excellent": "used_excellent",
        "v_preowned": "used_good",
        "v_preowned_fair": "used_fair",
        "v_poor": "used_fair",
    },
    "grailed": {
        "v_newWithTagsBox": "is_new",
        "v_newWithOutTags": "is_new",
        "v_newWithDefects": "is_gently_used",
        "v_preowned_excellent": "is_gently_used",
        "v_preowned": "is_used",
        "v_preowned_fair": "is_used",
        "v_poor": "is_worn",
    },
    # Vendoo leaves New with imperfections unmapped for Facebook; Used - Good
    # is the nearest of its four.
    "facebook": {
        "v_newWithTagsBox": "new",
        "v_newWithOutTags": "used_like_new",
        "v_newWithDefects": "used_good",
        "v_preowned_excellent": "used_good",
        "v_preowned": "used_good",
        "v_preowned_fair": "used_good",
        "v_poor": "used_fair",
    },
    # Sellwild's own ids: 5 Brand New, 4 Almost New, 3 Gently Used, 2 Used,
    # 1 Broken — Vendoo's mapping, which puts every pre-owned grade on Used.
    "sellwild": {
        "v_newWithTagsBox": "5",
        "v_newWithOutTags": "4",
        "v_newWithDefects": "3",
        "v_preowned_excellent": "2",
        "v_preowned": "2",
        "v_preowned_fair": "2",
        "v_poor": "1",
    },
    # Vinted's status ids: 6 new with tags, 1 new without, 2 very good,
    # 3 good, 4 satisfactory.
    "vinted": {
        "v_newWithTagsBox": 6,
        "v_newWithOutTags": 1,
        "v_newWithDefects": 2,
        "v_preowned_excellent": 3,
        "v_preowned": 3,
        "v_preowned_fair": 3,
        "v_poor": 4,
    },
}

# Wordings that resolve to a Vendoo condition code. The codes themselves are
# added below, both squashed (``v preowned excellent``) and split on camel
# case (``v new with tags box``), because learnings key on the raw code while
# Studio's own wording arrives as a label.
_VENDOO_CONDITION_ALIASES: dict[str, str] = {
    "new with tags box": "v_newWithTagsBox",
    "new with tags": "v_newWithTagsBox",
    "nwt": "v_newWithTagsBox",
    "brand new": "v_newWithTagsBox",
    "new": "v_newWithTagsBox",
    "new without tags box": "v_newWithOutTags",
    "new without tags": "v_newWithOutTags",
    "nwot": "v_newWithOutTags",
    "like new": "v_newWithOutTags",
    "new with imperfections": "v_newWithDefects",
    "new with defects": "v_newWithDefects",
    "pre owned excellent": "v_preowned_excellent",
    "excellent": "v_preowned_excellent",
    "pre owned good": "v_preowned",
    "pre owned": "v_preowned",
    "preowned": "v_preowned",
    "good": "v_preowned",
    "used": "v_preowned",
    "pre owned fair": "v_preowned_fair",
    "fair": "v_preowned_fair",
    "poor major flaws": "v_poor",
    "major flaws": "v_poor",
    "poor": "v_poor",
    "damaged": "v_poor",
}

def _add_condition_code_aliases() -> None:
    for codes in _VENDOO_MARKETPLACE_CONDITIONS.values():
        for code in codes:
            _VENDOO_CONDITION_ALIASES.setdefault(_norm(code), code)
            _VENDOO_CONDITION_ALIASES.setdefault(_norm(_CAMEL_BOUNDARY.sub(" ", code)), code)


_add_condition_code_aliases()


def _condition_lookup_keys(general_condition: Any) -> list[str]:
    """Normalised keys to try for a generalDetails condition value.

    ``v_newWithTagsBox`` squashes to ``v newwithtagsbox`` and also splits to
    ``v new with tags box``; both are tried so a camel-cased Vendoo code
    still finds its wording.
    """
    keys: list[str] = []
    for candidate in (
        _code_of(general_condition),
        _label_of(general_condition),
        general_condition if not isinstance(general_condition, dict) else None,
    ):
        if candidate in (None, ""):
            continue
        for key in (_norm(candidate), _norm(_CAMEL_BOUNDARY.sub(" ", str(candidate)))):
            if key and key not in keys:
                keys.append(key)
    return keys


def _builtin_condition(marketplace: str, key: str) -> Any:
    """Vendoo's shipped code for one marketplace, or None."""
    code = _VENDOO_CONDITION_ALIASES.get(key)
    if not code and key.startswith("v "):
        # Codes Studio itself coined, such as ``v_pre_owned_good``, read as
        # their wording once the ``v`` prefix is dropped.
        code = _VENDOO_CONDITION_ALIASES.get(key[2:])
    if not code:
        return None
    return (_VENDOO_MARKETPLACE_CONDITIONS.get(marketplace) or {}).get(code)


def _learned_condition_is_usable(marketplace: str, code: Any) -> bool:
    """Whether a learned code is one this marketplace actually accepts.

    Learnings come back from items we ourselves wrote, so a code Studio got
    wrong once would otherwise teach itself forever. Where Vendoo ships the
    vocabulary, hold learnings to it.
    """
    known = _VENDOO_MARKETPLACE_CONDITIONS.get(marketplace)
    if not known:
        return True
    return str(code) in {str(value) for value in known.values()}


def marketplace_condition(schema: dict[str, Any] | None, marketplace: str, general_condition: Any) -> Any:
    """The marketplace's own condition code for a Vendoo condition, or None.

    Prefers codes learned from the seller's items. Falls back to a small
    built-in table for Poshmark/Mercari/Depop so Relist never receives a
    Vendoo ``v_`` general code. eBay stays learned-only (category-specific).
    """
    if general_condition in (None, ""):
        return None
    table = (((schema or {}).get("marketplaces") or {}).get(marketplace) or {}).get("condition") or {}
    # generalDetails stores ``{value, displayName}``; learnings are keyed by
    # either the ``v_`` code or the human label, so try both. A bare label
    # such as "Pre-Owned - Good" resolves through the general field table
    # first, then into the marketplace map.
    field_labels = (((schema or {}).get("fields") or {}).get("condition") or {}).get("labels") or {}
    keys = _condition_lookup_keys(general_condition)
    # A label such as "Pre-Owned - Good" resolves through the general field
    # table into the ``v_`` code the marketplace maps are keyed by.
    for key in list(keys):
        via_general = field_labels.get(key)
        if via_general in (None, ""):
            continue
        for extra in _condition_lookup_keys(via_general):
            if extra not in keys:
                keys.append(extra)
    for key in keys:
        code = table.get(key)
        if code not in (None, "") and _learned_condition_is_usable(marketplace, code):
            return code
    for key in keys:
        code = _builtin_condition(marketplace, key)
        if code not in (None, ""):
            return code
    return None


def _is_general_condition_value(value: Any) -> bool:
    """True when ``value`` belongs on generalDetails, not a marketplace form.

    Marketplace overrides store short codes (Poshmark ``ug``/``nwt``, Mercari
    ``3``/``4``, eBay category ids). A Vendoo ``v_preowned`` object or label
    left there is what Poshmark rejects on Relist as
    ``Invalid condition v_preowned``. The invented code ``good`` is also
    invalid on Poshmark — see ``_learned_condition_is_usable``.
    """
    if isinstance(value, dict):
        return True
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith("v_") or lower.startswith("v "):
        return True
    key = _norm(text)
    return (
        key.startswith("pre owned")
        or key.startswith("new with")
        or key.startswith("new without")
        or key.startswith("poor ")
        or "imperfection" in key
    )


def _is_condition_path(path: str) -> bool:
    """Firestore paths that hold a marketplace condition value."""
    lower = path.lower()
    return lower.endswith(".condition") or lower.endswith("_condition")


def _condition_keys(bucket: dict[str, Any] | None) -> list[str]:
    if not isinstance(bucket, dict):
        return []
    return [
        key for key in bucket
        if str(key).lower() == "condition" or str(key).lower().endswith("_condition")
    ]


def _set_learned_condition(
    section: dict[str, Any],
    schema: dict[str, Any] | None,
    marketplace: str,
    leaf_id: str,
    candidates: list[Any],
    reports: list[dict[str, str]],
) -> None:
    """Encode condition from the seller's learned marketplace vocabulary."""
    for candidate in candidates:
        code = marketplace_condition(schema, marketplace, candidate)
        if code not in (None, ""):
            section["overrides"]["condition"] = code
            if marketplace == "ebay" and leaf_id:
                section["categorySpecifics"][f"{leaf_id}_condition"] = code
            return
    if candidates:
        field = f"condition:{marketplace}"
        if not any(row.get("field") == field for row in reports):
            reports.append({"field": field, "value": str(candidates[0])})


def aspect_is_multi(schema: dict[str, Any] | None, marketplace: str, suffix: str) -> bool:
    """Whether this marketplace stores that aspect as a list."""
    shapes = ((schema or {}).get("aspects") or {}).get(marketplace) or {}
    return shapes.get(suffix) == "list"


def encode_field(schema: dict[str, Any] | None, name: str, label: str | None) -> tuple[Any, bool]:
    """Encode a human label into Vendoo's stored value.

    Returns ``(value, resolved)``; ``resolved`` is False when nothing learned
    covers this label, so the caller can report it instead of guessing.
    """
    if label in (None, ""):
        return None, True
    entry = ((schema or {}).get("fields") or {}).get(name) or {}
    labels: dict[str, Any] = entry.get("labels") or {}
    key = _norm(label)
    if key in labels:
        code = labels[key]
        if entry.get("shape") == "object":
            return {"value": code, "displayName": label}, True
        return code, True
    if label in (entry.get("codes") or []):
        return label, True
    # Vendoo's general vocabulary is coarser than Studio's: one ``v_preowned``
    # covers Pre-Owned Excellent/Good/Fair. Match on the squashed text so the
    # tier Studio adds still lands on the code the seller's items use.
    squashed = key.replace(" ", "")
    for learned, code in labels.items():
        target = learned.replace(" ", "")
        if target and (squashed.startswith(target) or target.startswith(squashed)):
            if entry.get("shape") == "object":
                return {"value": code, "displayName": label}, True
            return code, True
    return label, False
