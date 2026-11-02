"""Build Vendoo items in the exact shape Vendoo's own form produces.

Vendoo's create-item form never fills anything: it builds an item object,
uploads photos through the inventory microservice, mints a Firestore id and
calls the ``items`` Cloud Function with ``{type: "createItem"}``. Studio does the
same (``vendoo_create``), so a generated listing is stored as data with every
marketplace section exactly as computed.

See ``docs/vendoo-listing-architecture.md``. This module is the inverse of
``vendoo_import.listing_from_vendoo`` and mirrors Vendoo's ``getInit*Form``
defaults, which is what makes the result indistinguishable from a form save.

Vendoo's coded vocabularies (condition codes, colour codes) are not guessed.
``observe_item_schema`` learns them from the user's own existing items, and
``build_vendoo_item`` reports any field it could not encode confidently rather
than storing a value that looks right and is wrong.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from vendoo_studio.services.ebay_fields import EBAY_CONDITION_DESCRIPTION
from vendoo_studio.services.etsy_fields import etsy_eligible
from vendoo_studio.services.vendoo_categories import (
    _category,
    _resolved_category,
    category_v2,
    path_parts,
)
from vendoo_studio.services.vendoo_encodings import (
    _set_learned_condition,
    aspect_is_multi,
    encode_field,
)
from vendoo_studio.services.vendoo_item_shape import (
    _CAMEL_BOUNDARY,
    ALL_MARKETPLACES,
    CURRENT_ITEM_VERSION,
    DEFAULT_ORIGIN,
    GENERAL_KEY,
    LISTINGS_KEY,
    SPECIFICS_SOURCES,
    _clean,
    _norm,
    _num_str,
    _string_list,
    default_general_details,
    default_listing_section,
)
from vendoo_studio.services.vendoo_marketplace_fields import (
    _apply_depop_option_codes,
    _apply_ebay_pricing,
    _apply_etsy_listing_codes,
    _apply_etsy_tags,
    _apply_facebook_offers,
    _apply_facebook_shipping,
    _apply_marketplace_brand,
    _apply_mercari_shipping,
    _apply_mercari_smart_pricing,
    _apply_poshmark_original_price,
    _apply_poshmark_smart_sell,
    _apply_vinted_package,
)
from vendoo_studio.services.vendoo_specifics import (
    FieldSpec,
    encode_scaled,
    encode_specific,
    is_not_applicable,
    missing_required,
    scale_key,
    specifics_key,
)

# Vendoo's own colour vocabulary, as its app ships it. Learning these from the
# seller's items only ever covers colours they have already used, which is how
# a beige top was stored as the word "Beige" next to a correctly coded
# "v_Pink". Real items confirm the encoding: v_Silver, v_Black.
GENERAL_COLORS: dict[str, str] = {
    "beige": "v_Beige", "black": "v_Black", "blue": "v_Blue", "brown": "v_Brown",
    "cream": "v_Cream", "gold": "v_Gold", "gray": "v_Gray", "grey": "v_Gray",
    "green": "v_Green", "multicolor": "v_Multicolor", "multicolour": "v_Multicolor",
    "orange": "v_Orange", "pink": "v_Pink", "purple": "v_Purple", "red": "v_Red",
    "silver": "v_Silver", "tan": "v_Tan", "white": "v_White", "yellow": "v_Yellow",
}

_PACKAGE_DIMS_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*$", re.I
)

# Field names the general form uses for the package, not the item.
_PACKAGE_DIMENSION_WORDS = frozenset({"length", "width", "height"})

# Keys in <marketplace>_specifics that Studio keeps for itself, not Vendoo.
_STUDIO_ONLY_SPECIFIC_KEYS = frozenset({"categoryPath", "category_specifics", "size", "sizeType"})

# Studio names a few Etsy fields differently from Vendoo.
ETSY_KEY_MAP = {"who_made": "whoMade", "what_is": "whatIsIt", "when_made": "whenMade"}

# Keys Studio keeps in <marketplace>_specifics that are never category fields.
_NOT_CATEGORY_FIELDS = frozenset({
    "categorypath", "categoryid", "category specifics", "condition description",
    "conditiondescription", "vendoo internal notes", "vendoo labels", "labels",
    # Condition is encoded from the item's own condition against the leaf's
    # option list, not from whatever text sits in <marketplace>_specifics.
    "condition",
})

# --------------------------------------------------------------------------
# Studio listing -> Vendoo item
# --------------------------------------------------------------------------


def _dimensions(raw: Any) -> dict[str, str] | None:
    match = _PACKAGE_DIMS_RE.match(str(raw or ""))
    if not match:
        return None
    length, width, height = match.groups()
    return {"length": _num_str(length), "width": _num_str(width), "height": _num_str(height)}


def _size(listing: dict[str, Any], category_id: Any = None) -> dict[str, Any] | None:
    """The general form's size, as Vendoo stores it.

    ``categoryId`` ties the size to the general category; without it the form
    has no scale to read the option against. Options and scales are their own
    labels here — Vendoo's size vocabulary is not coded.
    """
    size = _clean(listing.get("size")) or _clean(listing.get("size_us"))
    scale = _clean(listing.get("sizeType"))
    if not size and not scale:
        return None
    out: dict[str, Any] = {
        "option": {"label": size or "", "value": size or ""},
        "scale": {"label": scale or "", "value": scale or ""},
    }
    category = _clean(category_id)
    if category:
        out["categoryId"] = category
    return out


def _general_details(
    listing: dict[str, Any],
    schema: dict[str, Any] | None,
    images: list[Any],
    unresolved: list[dict[str, str]],
) -> dict[str, Any]:
    general = default_general_details()

    def coded(name: str, label: Any) -> Any:
        text = _clean(label)
        value, ok = encode_field(schema, name, text)
        if not ok and text and name in ("primaryColor", "secondaryColor"):
            # What the seller's own items taught us wins — it carries the shape
            # this account stores. Vendoo's shipped vocabulary only rescues a
            # colour they have not used before, which is otherwise reported.
            known = GENERAL_COLORS.get(_norm(text))
            if known:
                return known
        if not ok and text and name == "condition" and _ebay_grade(text):
            # Words eBay itself reads as a grade but Vendoo has not taught us a
            # code for are kept as a label, not reported: each marketplace's
            # condition is encoded from it (eBay's grade included). A word no
            # one knows, such as "Salvage", still names a field left empty.
            return text
        if not ok:
            unresolved.append({"field": name, "value": str(label)})
        return value if value is not None else ""

    general.update({
        "images": [{"url": img} if isinstance(img, str) else img for img in images],
        "title": _clean(listing.get("title")) or "",
        "description": _clean(listing.get("description")) or "",
        "notes": _clean(listing.get("internal_notes")) or "",
        "brand": _clean(listing.get("brand")) or "",
        "sku": _clean(listing.get("sku")) or "",
        "price": _num_str(listing.get("price")),
        "cost": _num_str(listing.get("cost")),
        "quantity": _num_str(listing.get("quantity") or 1),
        "tags": _string_list(listing.get("tags")),
        "condition": coded("condition", listing.get("condition")),
        "primaryColor": coded("primaryColor", listing.get("primaryColor")),
        "secondaryColor": coded("secondaryColor", listing.get("secondaryColor")),
    })
    category = _category(listing)
    if isinstance(category, dict):
        general["categoryV2"] = category
        general["category"] = None
    elif category:
        general["category"] = category
    size = _size(listing, (general.get("categoryV2") or {}).get("id") if isinstance(general.get("categoryV2"), dict) else None)
    if size:
        general["size"] = size
    weight_lb, weight_oz = listing.get("weight_lb"), listing.get("weight_oz")
    if weight_lb not in (None, "") or weight_oz not in (None, ""):
        general["weight"] = {"pounds": _num_str(weight_lb or 0), "ounces": _num_str(weight_oz or 0)}
    dims = _dimensions(listing.get("package_dimensions_in"))
    if dims:
        general["dimensions"] = dims
    from vendoo_studio.services.user_settings import listing_inventory_location

    general["zipCode"] = listing_inventory_location(listing)
    return general


def _field_words(name: Any) -> str:
    """``sleeveLength`` and ``Sleeve Length`` both reduce to ``sleeve length``."""
    return _norm(_CAMEL_BOUNDARY.sub(" ", str(name or "")))


def _spec_index(specs: dict[str, FieldSpec]) -> dict[str, FieldSpec]:
    """Every field of a category, reachable by its key or its label's words."""
    index: dict[str, FieldSpec] = {}
    for key, spec in specs.items():
        index.setdefault(_field_words(key), spec)
        index.setdefault(_field_words(spec.display), spec)
    return index


def _force_numeric_fabric_weight(out: dict[str, Any], key: str) -> None:
    """eBay's Fabric Weight is a number or nothing — never a word.

    eBay refuses the listing with "Fabric weight must be greater than 0", and
    Studio never guesses one, so anything that is not an evidenced number is
    written as "": that clears whatever the form still shows, including a word
    left there by the listing this draft carried before Regenerate.
    """
    from vendoo_studio.services.ebay_fields import normalize_ebay_fabric_weight

    normalized, _ = normalize_ebay_fabric_weight(out.get(key))
    out[key] = normalized


def _category_specifics(
    category_id: str,
    specs: dict[str, FieldSpec],
    studio_specifics: dict[str, Any],
    listing: dict[str, Any],
    unresolved: list[dict[str, str]],
    marketplace: str,
) -> dict[str, Any]:
    """Encode Studio's values as the ``{categoryId}_{field}`` map Vendoo stores.

    Vendoo's schema for the chosen category decides everything: which keys
    exist, which hold a list (``maxValues > 1``) and which accept only a coded
    option id. A value that a ``SelectionOnly`` field has no option for is
    reported instead of stored — storing it is what makes the form's own lookup
    return undefined and crash the marketplace tab.
    """
    merged = dict(studio_specifics)
    for key in ("size", "sizeType", "department", "type", "brand"):
        if merged.get(key) in (None, "", []) and listing.get(key) not in (None, "", []):
            merged[key] = listing[key]

    index = _spec_index(specs)
    out: dict[str, Any] = {}
    answered: list[tuple[FieldSpec, Any]] = []
    for studio_key, value in merged.items():
        words = _field_words(studio_key)
        if not words or words in _NOT_CATEGORY_FIELDS:
            continue
        spec = index.get(words)
        if spec is None:
            continue
        answered.append((spec, value))
    # A field the marketplace's own answers skip is read the way the gap check
    # reads it — the listing's general color, size, material — or that check
    # counts it answered and the form is left blank. Package dimensions share
    # their names with garment measurements, so those are never borrowed.
    from vendoo_studio.services.fill_log import listing_value_for_field

    taken = {spec.key for spec, _value in answered}
    for spec in specs.values():
        words = _field_words(spec.display or spec.key)
        if spec.key in taken or not words or words in _NOT_CATEGORY_FIELDS or words in _PACKAGE_DIMENSION_WORDS:
            continue
        value = listing_value_for_field(listing, marketplace, spec.display or spec.key)
        if value:
            answered.append((spec, value))
    for spec, value in answered:
        if spec.scales:
            # Size and friends: Vendoo stores the chosen scale alongside the
            # value, and the value alone means nothing without it.
            chosen_scale, code, resolved = encode_scaled(spec, value, merged.get("department"))
            if not resolved:
                unresolved.append({"field": f"{marketplace}:{spec.display}", "value": str(value)})
                continue
            if code:
                out[specifics_key(category_id, spec.key)] = code
                out[scale_key(category_id, spec.key)] = chosen_scale
            continue
        stored, resolved = encode_specific(spec, value)
        if not resolved:
            unresolved.append({
                "field": f"{marketplace}:{spec.display}",
                "value": ", ".join(str(part) for part in (value if isinstance(value, list) else [value])),
            })
            continue
        if stored in (None, "", []):
            # An explicit "Does Not Apply" blanks the form field; having no
            # value at all leaves whatever the seller already put there.
            if not is_not_applicable(value):
                continue
            stored = [] if spec.multi else ""
        out[specifics_key(category_id, spec.key)] = stored
    if marketplace == "ebay":
        weight_spec = index.get("fabric weight")
        if weight_spec is not None:
            _force_numeric_fabric_weight(out, specifics_key(category_id, weight_spec.key))
    return out


def _ebay_grade(wording: str) -> str | None:
    """eBay's grade for this wording when eBay's own table names one, else None."""
    from vendoo_studio.services.ebay_listings import _CONDITION

    hay = str(wording or "").lower()
    return next((code for pattern, code in _CONDITION if re.search(pattern, hay)), None)


def _condition_code(spec: FieldSpec, marketplace: str, candidate: Any) -> Any:
    """The leaf's own condition id for the seller's wording, or None.

    The seller writes the condition in Studio's words (``Pre-Owned - Good``),
    which the leaf's option list spells its own way. ``encode_specific`` only
    knows an exact spelling, so a plain label reached the form as nothing and
    the listing went to the marketplace with its condition empty while the
    editor showed the grade eBay would have used. eBay's grade comes from the
    same table the editor reads (``ebay_condition``), so both end up the same.
    """
    code, ok = encode_specific(spec, candidate)
    if ok and code not in (None, "", []):
        return code
    if marketplace != "ebay":
        return None
    from vendoo_studio.services.ebay_listings import CONDITION_IDS, ebay_condition
    from vendoo_studio.services.vendoo_specifics import is_not_applicable

    if is_not_applicable(candidate):
        return None
    native = CONDITION_IDS.get(ebay_condition({"condition": candidate}))
    # eBay numbers its conditions (1000, 3000…), which ``encode_specific``
    # deliberately refuses to read as an id. Here the number is eBay's own
    # grade, so the leaf's option for it is the answer.
    return native if native in spec.options else None


def _learned_category_specifics(
    category_id: str,
    studio_specifics: dict[str, Any],
    listing: dict[str, Any],
    schema: dict[str, Any] | None = None,
    marketplace: str = "ebay",
) -> dict[str, Any]:
    """Fallback for when Vendoo's schema for the leaf could not be fetched.

    Field names and their list-vs-scalar shapes come from the seller's own
    items via ``observe_listing_encodings``, so only fields their account has
    already used get written. Anything else is left out rather than guessed.
    """
    merged = dict(studio_specifics)
    for key in ("size", "sizeType", "department", "type", "brand"):
        if merged.get(key) in (None, "", []) and listing.get(key) not in (None, "", []):
            merged[key] = listing[key]
    shapes = ((schema or {}).get("aspects") or {}).get(marketplace) or {}
    index = {_field_words(suffix): suffix for suffix in shapes}
    out: dict[str, Any] = {}
    for studio_key, value in merged.items():
        words = _field_words(studio_key)
        if not words or words in _NOT_CATEGORY_FIELDS:
            continue
        suffix = index.get(words)
        if not suffix:
            continue
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part or "").strip()]
        else:
            parts = [str(value).strip()] if str(value or "").strip() else []
        if is_not_applicable(value):
            # Same rule without Vendoo's schema: the phrase never reaches the
            # form, and the field it was answered for is blanked.
            out[specifics_key(category_id, suffix)] = (
                [] if aspect_is_multi(schema, marketplace, suffix) else ""
            )
            continue
        if not parts:
            continue
        out[specifics_key(category_id, suffix)] = (
            parts if aspect_is_multi(schema, marketplace, suffix) else ", ".join(parts)
        )
    if marketplace == "ebay":
        weight_suffix = index.get("fabric weight")
        if weight_suffix:
            _force_numeric_fabric_weight(out, specifics_key(category_id, weight_suffix))
    return out


def _apply_ebay_inventory_location(known: dict[str, Any], general: dict[str, Any]) -> None:
    """Copy the general ship-from ZIP onto eBay when that form's location is blank."""
    shipping = known.get("shipping")
    if not isinstance(shipping, dict):
        return
    if str(shipping.get("location") or "").strip():
        return
    location = str(general.get("zipCode") or "").strip()
    if location:
        shipping["location"] = location


def _listing_section(
    marketplace: str,
    listing: dict[str, Any],
    general: dict[str, Any],
    schema: dict[str, Any] | None = None,
    unresolved: list[dict[str, str]] | None = None,
    specs: dict[str, FieldSpec] | None = None,
) -> dict[str, Any]:
    """Fill one marketplace section from ``<marketplace>_specifics``.

    Known ``marketplaceSpecifics`` keys land there and a resolved leaf becomes
    ``overrides.categoryV2``. Once that leaf is known, its item fields are
    written as ``{categoryId}_{field}``. ``specs`` is Vendoo's own schema for
    that leaf and is authoritative when present — it is the only thing that
    knows which fields hold lists and which coded option ids are legal. Without
    it we fall back to what ``observe_listing_encodings`` learned from the
    seller's existing items.
    """
    from vendoo_studio.services.seller_overrides import (
        FOLLOWED_NOT_ASPECTS, fold_key, followed_entry, overlay_followed_fields,
    )

    listing = overlay_followed_fields(listing, marketplace)
    section = default_listing_section(marketplace)
    raw = listing.get(SPECIFICS_SOURCES.get(marketplace, f"{marketplace}_specifics"))
    specifics = dict(raw) if isinstance(raw, dict) else {}
    # A listing video is uploaded by Anteroom's direct List, never through Vendoo.
    specifics.pop("videoId", None)
    if marketplace == "ebay":
        from vendoo_studio.services.ebay_fields import normalize_ebay_sizes

        sized = {**listing, "ebay_specifics": specifics}
        normalize_ebay_sizes(sized)
        specifics = sized["ebay_specifics"]
    known = section["marketplaceSpecifics"]

    for key in ("weight", "dimensions"):
        if key in section["overrides"] and general.get(key):
            section["overrides"][key] = deepcopy(general[key])
    if "quantity" in section["overrides"]:
        owned_quantity = followed_entry(listing, marketplace, "quantity")
        if owned_quantity is not None and str(owned_quantity[1]).strip() != "":
            section["overrides"]["quantity"] = str(owned_quantity[1]).strip()
        else:
            section["overrides"]["quantity"] = general.get("quantity") or "1"
    for key in ("title", "description"):
        owned = followed_entry(listing, marketplace, key)
        if owned is None:
            continue
        text = str(owned[1] if owned[1] is not None else "").strip()
        # An empty owned value stays empty so General does not fill it back in.
        section["overrides"][key] = text

    path = specifics.pop("categoryPath", None)
    parts = path_parts(path)
    if not parts:
        cats = listing.get("marketplace_categories")
        if isinstance(cats, dict):
            parts = path_parts(cats.get(marketplace))
    if parts:
        from vendoo_studio.services.registry import map_marketplace_category_path

        mapped = map_marketplace_category_path(marketplace, " > ".join(parts), listing)
        parts = path_parts(mapped) or parts
    cat_ids = listing.get("marketplace_category_ids")
    cat_id = None
    if isinstance(cat_ids, dict):
        cat_id = cat_ids.get(marketplace)
    cat_id = specifics.pop("categoryId", None) or specifics.pop("category_id", None) or cat_id
    # Snapshot before the marketplaceSpecifics pass prunes Studio-only keys.
    # Marketplace form fields that live in marketplaceSpecifics (style, age,
    # shipping, …) must not be fed to the category-aspects encoder — a leaf
    # field with the same label would consume them. Size/brand stay in aspects.
    aspects = {
        key: value for key, value in specifics.items()
        if key not in known and key != "category_specifics" and fold_key(key) not in FOLLOWED_NOT_ASPECTS
    }
    # Generation stores leaf fields in category_specifics; the gap filler
    # writes corrections at the top level. Encode both, preferring a current
    # top-level answer even when its spelling differs from the nested label.
    nested_aspects = specifics.get("category_specifics")
    if isinstance(nested_aspects, dict):
        answered = {
            _field_words(key) for key, value in aspects.items()
            if value not in (None, "", [], {})
        }
        for key, value in nested_aspects.items():
            if _field_words(key) not in answered:
                aspects[key] = value
    for key in list(specifics.keys()):
        if key in _STUDIO_ONLY_SPECIFIC_KEYS or key == "category_specifics":
            specifics.pop(key, None)

    resolved = _resolved_category(listing, marketplace) or category_v2(cat_id, parts)
    if resolved:
        section["overrides"]["categoryV2"] = resolved

    if resolved and resolved.get("id"):
        leaf_id = str(resolved["id"])
        reports = unresolved if unresolved is not None else []
        # The seller's own wording encodes against the leaf's options; the
        # coded generalDetails value is only a fallback for it.
        candidate_conditions = [
            value for value in (listing.get("condition"), general.get("condition"))
            if value not in (None, "")
        ]
        if specs:
            section["categorySpecifics"] = _category_specifics(
                leaf_id, specs, aspects, listing, reports, marketplace
            )
            condition_spec = specs.get("condition") or specs.get("Condition")
            if condition_spec is not None and candidate_conditions:
                for candidate in candidate_conditions:
                    code = _condition_code(condition_spec, marketplace, candidate)
                    if code not in (None, "", []):
                        # The leaf keeps its condition in both places, as its id.
                        section["overrides"]["condition"] = code
                        section["categorySpecifics"][specifics_key(leaf_id, condition_spec.key)] = code
                        break
                else:
                    reports.append({
                        "field": f"condition:{marketplace}",
                        "value": str(candidate_conditions[0]),
                    })
            elif candidate_conditions:
                # Leaf schema has no condition field (Poshmark/Mercari/Depop).
                _set_learned_condition(section, schema, marketplace, leaf_id, candidate_conditions, reports)
            for field in missing_required(specs, section["categorySpecifics"], leaf_id):
                reports.append({"field": f"{marketplace}:{field}", "value": ""})
        else:
            if marketplace == "ebay":
                section["categorySpecifics"] = _learned_category_specifics(
                    leaf_id, aspects, listing, schema
                )
            if candidate_conditions:
                _set_learned_condition(section, schema, marketplace, leaf_id, candidate_conditions, reports)
        if marketplace == "vinted":
            _apply_vinted_package(section, leaf_id, listing, general.get("weight"), schema, unresolved)

    for key, value in specifics.items():
        if value in (None, "", []):
            continue
        dest = ETSY_KEY_MAP.get(key, key) if marketplace == "etsy" else key
        if dest not in known:
            continue
        if isinstance(known[dest], dict) and isinstance(value, dict):
            known[dest] = {**known[dest], **value}
        elif isinstance(known[dest], list):
            known[dest] = _string_list(value)
        elif dest == "originalPrice":
            known[dest] = _num_str(value)
        else:
            known[dest] = value

    # Mercari / eBay / Etsy / Poshmark form defaults the seller expects every time.
    if marketplace == "ebay":
        _apply_ebay_pricing(known, general, listing)
        # The seller's Condition Description never varies per item.
        known["conditionDescription"] = EBAY_CONDITION_DESCRIPTION
        _apply_ebay_inventory_location(known, general)
    elif marketplace == "etsy":
        _apply_etsy_listing_codes(known, listing)
        _apply_etsy_tags(known, general)
    elif marketplace == "poshmark":
        _apply_poshmark_original_price(known)
        _apply_poshmark_smart_sell(known, general, listing)
    elif marketplace == "mercari":
        _apply_mercari_shipping(known, section["overrides"].get("weight"), section["overrides"])
        _apply_mercari_smart_pricing(known)
    elif marketplace == "depop":
        _apply_depop_option_codes(known, unresolved)
    elif marketplace == "facebook" and section["overrides"].get("categoryV2"):
        _apply_facebook_shipping(known, general.get("weight"), schema, unresolved)
        _apply_facebook_offers(known, listing.get("price") or general.get("price"))

    _apply_marketplace_brand(section, marketplace, listing)
    return section


def _firestore_now() -> dict[str, int]:
    """A Firestore timestamp, the shape saved items carry."""
    import time

    now = time.time()
    return {"_seconds": int(now), "_nanoseconds": int((now % 1) * 1_000_000_000)}


def _mark_saved(item: dict[str, Any], *, incomplete: bool = False) -> None:
    """Record the item as saved, the way Vendoo's own save does.

    A new item starts ``{"notSaved": True}`` — literally "Not Saved" in
    Vendoo's UI — and its save replaces that with ``complete`` or
    ``inProgress`` depending on whether anything required is still missing,
    stamping every marketplace form it wrote. Creating an item without doing
    the same leaves every form looking untouched.
    """
    # Complete, not inProgress: a draft Studio created is finished work as far
    # as Studio is concerned. Vendoo recomputes this on its own next save, so
    # a required field still missing will show there rather than here.
    item["status"] = {"complete": True}
    stamp = _firestore_now()
    for section in (item.get(LISTINGS_KEY) or {}).values():
        if not isinstance(section, dict):
            continue
        # Only forms that were actually filled out.
        if not (section.get("categorySpecifics") or (section.get("overrides") or {}).get("categoryV2")):
            continue
        section["dateCreated"] = stamp
        section["dateLastModified"] = stamp


def marketplaces_left_empty(listing: dict[str, Any]) -> frozenset[str]:
    """Marketplace forms Studio must not fill for this listing.

    Etsy removes modern resale items, and a filled Etsy form shows as ready to
    list in Vendoo. Left empty, Etsy cannot be picked for the item by mistake;
    an Update leaves an existing Etsy form as it is (``apply_update_all``).
    """
    return frozenset() if etsy_eligible(listing) else frozenset({"etsy"})


def build_vendoo_item(
    listing: dict[str, Any],
    schema: dict[str, Any] | None = None,
    *,
    images: list[Any] | None = None,
    user_id: str = "",
    item_id: str = "",
    origin: str = DEFAULT_ORIGIN,
    specifics: dict[str, dict[str, FieldSpec]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """A complete Vendoo item, shaped exactly like a form save.

    ``images`` are the ``{version: 3, id, originalMaxDimension}`` objects the
    inventory upload returns. ``specifics`` maps a marketplace to Vendoo's own
    field schema for the leaf chosen there; where it is present the item fields
    are encoded exactly as that marketplace's form would store them.

    Returns ``(item, unresolved)``; ``unresolved`` names every field sent as
    plain text because nothing covered it, every value a coded field had no
    option for, and every required field left empty — show those to the seller
    rather than assuming they landed.

    Forms named by ``marketplaces_left_empty`` stay as Vendoo seeds them.
    """
    unresolved: list[dict[str, str]] = []
    by_marketplace = specifics or {}
    general = _general_details(listing, schema, images or [], unresolved)
    left_out = marketplaces_left_empty(listing)
    listings = {
        mp: _listing_section(mp, listing, general, schema, unresolved, by_marketplace.get(mp))
        if mp not in left_out else default_listing_section(mp)
        for mp in ALL_MARKETPLACES
    }
    item = {
        "origin": origin,
        "version": CURRENT_ITEM_VERSION,
        "status": {"notSaved": True},  # replaced by _mark_saved below
        "type": "item",
        "userID": user_id,
        "itemID": item_id,
        "labels": _string_list(listing.get("labels")),
        GENERAL_KEY: general,
        LISTINGS_KEY: listings,
    }
    _mark_saved(item)
    return item, unresolved


def create_item_payload(item: dict[str, Any], subscription_version: str | None) -> dict[str, Any]:
    """Body for the ``items`` Cloud Function (callable envelope added by the extension)."""
    return {"type": "createItem", "payload": {"item": item, "subscriptionVersion": subscription_version}}
