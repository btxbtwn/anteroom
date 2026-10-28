"""Saving Studio's listing onto an item Vendoo already holds.

Vendoo's own edit writes only the touched Firestore paths. This copies the
general form onto the marketplace forms the way its Update All does, works
out which paths changed, and checks afterwards that Vendoo stored what was
sent.
"""

from __future__ import annotations

from collections.abc import Collection
from copy import deepcopy
from typing import Any

from vendoo_studio.models.etsy_fields import sanitize_etsy_tags
from vendoo_studio.models.schema import DEPOP_OPTION_CODES
from vendoo_studio.services.vendoo_encodings import (
    _condition_keys,
    _is_condition_path,
    _is_general_condition_value,
    _learned_condition_is_usable,
    marketplace_condition,
)
from vendoo_studio.services.vendoo_item_shape import (
    GENERAL_KEY,
    LISTINGS_KEY,
    _bucket,
    _code_of,
    _label_of,
    _norm,
    _string_list,
    default_general_details,
)
from vendoo_studio.services.vendoo_marketplace_fields import (
    _apply_mercari_shipping,
    _apply_mercari_smart_pricing,
    ebay_offer_terms,
    poshmark_smart_sell,
)
from vendoo_studio.services.vendoo_specifics import is_not_applicable

# Only the parts of an item Studio is responsible for. Vendoo owns the rest —
# ids, dates, listing state — and a save must not reach into them.
_OWNED_LISTING_BUCKETS = ("overrides", "categorySpecifics", "marketplaceSpecifics")
_EXACT_DEPOP_PATHS = frozenset(
    f"{LISTINGS_KEY}.depop.marketplaceSpecifics.{field}" for field in DEPOP_OPTION_CODES
)
# General-form fields Vendoo's Update All copies onto marketplace forms.
_UPDATE_ALL_OVERRIDE_KEYS = ("title", "description", "sku", "quantity", "tags")
# Every form that keeps its own copy of the package. Depop has none — it prices
# the parcel by tier, which ``ensure_depop_category_optionals`` rewrites.
_PACKAGE_OVERRIDE_KEYS = ("weight", "dimensions")
_PACKAGE_OVERRIDE_MARKETPLACES = frozenset({"ebay", "etsy", "poshmark", "mercari", "sellwild"})
_PRICE_OVERRIDE_MARKETPLACES = frozenset({"etsy", "poshmark", "mercari", "depop"})
# Forms whose condition is re-sent on every save — see ``force_condition_updates``.
_ALWAYS_WRITE_CONDITION_MARKETPLACES = ("poshmark", "mercari")
_TAGS_SPECIFICS_MARKETPLACES = frozenset({"etsy", "mercari"})


def _set_ebay_bin_price(section: dict[str, Any], price: Any, listing: dict[str, Any] | None = None) -> None:
    """The Buy It Now price and the Best Offer floors that follow from it.

    Writing only the price left the floors from the previous price in place. An
    item repriced from $30 to $16 kept a $28 auto-accept under its $26 minimum,
    which eBay rejects and the form will not save. The floors are derived from
    the same number here that create uses, so the two can't drift apart — and a
    seller who typed their own floors keeps them.
    """
    ebay = (listing or {}).get("ebay_specifics") if isinstance(listing, dict) else None
    ebay = ebay if isinstance(ebay, dict) else {}
    chosen = ebay.get("bestOffer") if isinstance(ebay.get("bestOffer"), dict) else {}
    specs = _bucket(section, "marketplaceSpecifics")
    details = specs.get("pricingFormatDetails")
    if not isinstance(details, dict):
        details = {}
        specs["pricingFormatDetails"] = details
    fixed = details.get("fixedPrice")
    if not isinstance(fixed, dict):
        fixed = {}
        details["fixedPrice"] = fixed
    fixed["buyItNowPrice"] = deepcopy(price)
    fixed.update(deepcopy(ebay_offer_terms(price, chosen)))


def _apply_condition(
    have_section: dict[str, Any],
    want_section: dict[str, Any],
    general: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> None:
    """Write each marketplace's own condition code, or clear a stale one.

    After Regenerate, marketplace forms still hold the previous listing's
    codes. A Vendoo general label must never land on a marketplace form —
    Poshmark Relist rejects ``v_preowned`` outright, and eBay crashes on
    human labels. When nothing maps the new condition, blank the old value
    so the form does not keep showing the wrong tier.
    """
    want_over = _bucket(want_section, "overrides")
    have_over = have_section.get("overrides") if isinstance(have_section.get("overrides"), dict) else {}
    have_specs = have_section.get("categorySpecifics") if isinstance(have_section.get("categorySpecifics"), dict) else {}
    want_specs = _bucket(want_section, "categorySpecifics")
    marketplace = str(want_section.get("marketplaceID") or "")

    condition = want_over.get("condition")
    # Remap empty values, Vendoo general labels/codes, and invented marketplace
    # codes Poshmark rejects (``good`` → ``ug``). A usable short code already
    # on the form is left alone.
    needs_remap = (
        condition in (None, "", [])
        or _is_general_condition_value(condition)
        or not _learned_condition_is_usable(marketplace, condition)
    )
    if needs_remap:
        mapped = None
        for candidate in (general.get("condition"), condition):
            if candidate in (None, ""):
                continue
            mapped = marketplace_condition(schema, marketplace, candidate)
            if mapped not in (None, ""):
                break
        if mapped not in (None, ""):
            condition = mapped
        elif have_over.get("condition") not in (None, "") or _condition_keys(have_specs):
            condition = ""
        else:
            return

    want_over["condition"] = deepcopy(condition)
    # Refresh every condition key the draft already carries (old leaf ids
    # included) so a category change does not leave a stale ``{id}_condition``.
    for key in {*_condition_keys(have_specs), *_condition_keys(want_specs)}:
        want_specs[key] = deepcopy(condition)


def force_condition_updates(desired: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Always write Poshmark's and Mercari's condition, changed or not.

    Both list from whatever their form holds, and a save that writes nothing
    there leaves Vendoo free to fall back to the general ``v_`` code — which
    Poshmark rejects outright. Re-sending the code Studio computed costs one
    field and takes the form off that fallback every time.
    """
    for marketplace in _ALWAYS_WRITE_CONDITION_MARKETPLACES:
        section = (desired.get(LISTINGS_KEY) or {}).get(marketplace)
        overrides = section.get("overrides") if isinstance(section, dict) else None
        if not isinstance(overrides, dict):
            continue
        condition = overrides.get("condition")
        if condition in (None, "", []):
            continue
        updates[f"{LISTINGS_KEY}.{marketplace}.overrides.condition"] = deepcopy(condition)
    return updates


def apply_update_all(
    current: dict[str, Any],
    desired: dict[str, Any],
    schema: dict[str, Any] | None = None,
    *,
    leave: Collection[str] = (),
    listing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy general-form values onto marketplace listings, like Vendoo's Update All.

    Send writes Firestore; it does not click the form. Marketplace forms that
    already have their own title, description, tags, condition, or price keep
    those copies under ``listings.<mp>`` unless we write them too. Condition is
    remapped through the learned marketplace vocabulary (or cleared) so a
    regenerated listing does not leave the previous tier on eBay/Poshmark/….

    Forms in ``leave`` get nothing written to them, not even Update All's
    copies: their section is dropped from ``desired`` so no change is found.
    """
    general = desired.get(GENERAL_KEY) if isinstance(desired.get(GENERAL_KEY), dict) else {}
    listings_want = desired.get(LISTINGS_KEY)
    if not isinstance(listings_want, dict):
        listings_want = {}
        desired[LISTINGS_KEY] = listings_want
    for marketplace in leave:
        listings_want.pop(marketplace, None)
    listings_have = current.get(LISTINGS_KEY) if isinstance(current.get(LISTINGS_KEY), dict) else {}
    tags = _string_list(general.get("tags"))
    price = general.get("price")
    for marketplace, have_section in listings_have.items():
        if not isinstance(have_section, dict) or marketplace in leave:
            continue
        want_section = listings_want.get(marketplace)
        if not isinstance(want_section, dict):
            want_section = {"marketplaceID": marketplace}
            listings_want[marketplace] = want_section
        want_section.setdefault("marketplaceID", marketplace)
        want_over = _bucket(want_section, "overrides")
        have_over = have_section.get("overrides") if isinstance(have_section.get("overrides"), dict) else {}
        for key in _UPDATE_ALL_OVERRIDE_KEYS:
            value = tags if key == "tags" else general.get(key)
            if value in (None, "", []):
                continue
            if key == "tags" and marketplace == "etsy":
                value = sanitize_etsy_tags(value)
                if not value:
                    continue
            # A title, description, or quantity already on this form and different
            # from General was edited there. Update All does not replace it.
            if key in ("title", "description", "quantity") and key in want_over and str(want_over.get(key) if want_over.get(key) is not None else "").strip() != str(value).strip():
                continue
            want_over[key] = deepcopy(value)
        # A regenerated listing weighs and measures whatever the new item does;
        # without this the forms keep the previous package and ship on it.
        for key in _PACKAGE_OVERRIDE_KEYS:
            value = general.get(key)
            if not isinstance(value, dict) or not value:
                continue
            if marketplace in _PACKAGE_OVERRIDE_MARKETPLACES or isinstance(have_over.get(key), dict):
                want_over[key] = deepcopy(value)
        if tags and (
            marketplace in _TAGS_SPECIFICS_MARKETPLACES
            or isinstance((have_section.get("marketplaceSpecifics") or {}).get("tags"), list)
        ):
            specifics = _bucket(want_section, "marketplaceSpecifics")
            copied = sanitize_etsy_tags(tags) if marketplace == "etsy" else list(tags)
            if copied:
                specifics["tags"] = copied
        _apply_condition(have_section, want_section, general, schema)
        if marketplace == "mercari":
            # Force USPS Ground Advantage onto the Mercari form on every save,
            # matching create — a regenerated draft may still hold another
            # carrier from the previous listing.
            weight = want_over.get("weight") or have_over.get("weight") or general.get("weight")
            if isinstance(weight, dict):
                want_over["weight"] = deepcopy(weight)
            specifics = _bucket(want_section, "marketplaceSpecifics")
            _apply_mercari_shipping(
                specifics,
                weight if isinstance(weight, dict) else None,
                want_over,
            )
            _apply_mercari_smart_pricing(specifics)
        _keep_seller_shipping_choices(marketplace, have_section, want_section)
        _clear_stale_category_specifics(have_section, want_section)
        # Keep the original form-created stamp; refresh only last-modified so
        # Vendoo marks the form saved the way first Send does.
        if have_section.get("dateCreated") not in (None, "", {}):
            want_section["dateCreated"] = deepcopy(have_section["dateCreated"])
        if price not in (None, ""):
            if marketplace == "ebay":
                _set_ebay_bin_price(want_section, price, listing)
            if marketplace == "poshmark":
                # Smart Sell's floor follows the price the same way Best Offer's does.
                _bucket(want_section, "marketplaceSpecifics")["smartSell"] = deepcopy(
                    poshmark_smart_sell(price)
                )
            if marketplace in _PRICE_OVERRIDE_MARKETPLACES or "price" in (
                have_section.get("overrides") if isinstance(have_section.get("overrides"), dict) else {}
            ):
                want_over["price"] = deepcopy(price)
    return desired


def _keep_seller_shipping_choices(
    marketplace: str, have_section: dict[str, Any], want_section: dict[str, Any]
) -> None:
    """A carrier or package size already on the form outranks a learned one.

    Studio only knows the seller's usual pick; one they made on this draft is
    the answer for this item. The carrier holds only while the weight tier it
    was offered for still stands.
    """
    have_specifics = have_section.get("marketplaceSpecifics")
    have_specifics = have_specifics if isinstance(have_specifics, dict) else {}
    if marketplace == "facebook":
        want_specifics = _bucket(want_section, "marketplaceSpecifics")
        if have_specifics.get("carrier") and (
            have_specifics.get("packageWeightLabel") == want_specifics.get("packageWeightLabel")
        ):
            want_specifics["carrier"] = have_specifics["carrier"]
    elif marketplace == "vinted":
        have_aspects = have_section.get("categorySpecifics")
        want_aspects = _bucket(want_section, "categorySpecifics")
        if not isinstance(have_aspects, dict):
            return
        for key, value in have_aspects.items():
            if key.endswith("_packageSizeId") and key in want_aspects and value not in (None, ""):
                want_aspects[key] = value


def _clear_stale_category_specifics(have_section: dict[str, Any], want_section: dict[str, Any]) -> None:
    """Blank old-leaf aspect keys when the marketplace category changed.

    Firestore updates only touch paths we send. After Regenerate picks a new
    leaf, keys like ``53159_Department`` would otherwise linger beside the new
    leaf's fields and leave the form half-stale.
    """
    have_over = have_section.get("overrides") if isinstance(have_section.get("overrides"), dict) else {}
    want_over = want_section.get("overrides") if isinstance(want_section.get("overrides"), dict) else {}
    have_leaf = have_over.get("categoryV2") if isinstance(have_over.get("categoryV2"), dict) else {}
    want_leaf = want_over.get("categoryV2") if isinstance(want_over.get("categoryV2"), dict) else {}
    have_id = str(have_leaf.get("id") or "").strip()
    want_id = str(want_leaf.get("id") or "").strip()
    if not want_id or not have_id or have_id == want_id:
        return
    have_specs = have_section.get("categorySpecifics") if isinstance(have_section.get("categorySpecifics"), dict) else {}
    want_specs = _bucket(want_section, "categorySpecifics")
    for key in have_specs:
        if key not in want_specs:
            want_specs[key] = ""


def changed_fields(current: dict[str, Any], desired: dict[str, Any]) -> dict[str, str | Any]:
    """Dotted Firestore paths where ``desired`` differs from what Vendoo holds.

    Vendoo's own edit writes just the touched paths rather than the whole
    document, so an unrelated field a seller changed in Vendoo survives a save
    from Studio. Empty values in ``desired`` are skipped: Studio not knowing
    something is not the same as the seller clearing it — the exceptions being
    a "Does Not Apply" on the draft (cleared because the form shows the phrase
    as the item specific's value), an intentional marketplace condition reset
    after Regenerate, stale category-aspect keys after a leaf change, and an
    offer floor the current price no longer earns.
    """
    # A floor is derived, never the seller's own value, so clearing one is always
    # wanted: a leftover auto-accept is what makes eBay reject the offer.
    _CLEARABLE_DERIVED = (
        ".marketplaceSpecifics.floorPrice",
        ".pricingFormatDetails.fixedPrice.acceptOffersOfAtLeast",
        ".pricingFormatDetails.fixedPrice.declineOffersLowerThan",
        ".marketplaceSpecifics.smartSell.minPrice",
    )
    out: dict[str, Any] = {}

    def walk(prefix: str, want: Any, have: Any) -> None:
        if isinstance(want, dict):
            for key, value in want.items():
                walk(f"{prefix}.{key}" if prefix else key, value, (have or {}).get(key) if isinstance(have, dict) else None)
            return
        if want in (None, "", []):
            # Push empties that clear a real value: "Does Not Apply" leftovers,
            # marketplace condition resets, stale leaf aspect keys, and a
            # Mercari floor price left behind after Smart Pricing is turned off.
            if is_not_applicable(have) or (
                have not in (None, "", []) and (
                    _is_condition_path(prefix)
                    or ".categorySpecifics." in prefix
                    or prefix.endswith(_CLEARABLE_DERIVED)
                )
            ):
                out[prefix] = want
            return
        # Brand labels are case-sensitive on the form (``Unbranded`` ≠ ``unbranded``),
        # and Depop option codes must replace stored labels (``modern`` ≠ ``Modern``).
        if prefix.endswith((".brand", ".noBrand")) or prefix in _EXACT_DEPOP_PATHS:
            if want != have:
                out[prefix] = want
            return
        if _comparable(want) != _comparable(have):
            out[prefix] = want

    # Labels sit on the item itself. Add Studio's to whatever the seller tagged
    # in Vendoo; Studio never learns those, so it cannot mean to drop them.
    have_labels = _string_list(current.get("labels"))
    want_labels = [label for label in _string_list(desired.get("labels")) if label not in have_labels]
    if want_labels:
        out["labels"] = [*have_labels, *want_labels]

    general_want = desired.get(GENERAL_KEY) or {}
    general_have = current.get(GENERAL_KEY) or {}
    for key, value in general_want.items():
        if key == "images":  # uploaded separately; never re-sent as a diff
            continue
        walk(f"{GENERAL_KEY}.{key}", value, general_have.get(key))

    listings_want = desired.get(LISTINGS_KEY) or {}
    listings_have = current.get(LISTINGS_KEY) or {}
    for marketplace, section in listings_want.items():
        if not isinstance(section, dict):
            continue
        have_section = listings_have.get(marketplace) or {}
        # Stamp forms the way first Send does so Vendoo marks them saved, not
        # untouched — without rewriting ids/status Vendoo owns at the item root.
        for stamp in ("dateCreated", "dateLastModified"):
            value = section.get(stamp)
            if value not in (None, "", {}):
                walk(
                    f"{LISTINGS_KEY}.{marketplace}.{stamp}",
                    value,
                    have_section.get(stamp),
                )
        for bucket in _OWNED_LISTING_BUCKETS:
            if not isinstance(section.get(bucket), dict):
                continue
            for key, value in section[bucket].items():
                if key == "images":
                    continue
                walk(
                    f"{LISTINGS_KEY}.{marketplace}.{bucket}.{key}",
                    value,
                    (have_section.get(bucket) or {}).get(key),
                )
    return out


# --------------------------------------------------------------------------
# Round-trip verification
# --------------------------------------------------------------------------


def _comparable(value: Any) -> Any:
    if isinstance(value, dict):
        label = _label_of(value)
        if label:
            return _norm(label)
        code = _code_of(value)
        if code is not None:
            return _norm(code)
        return {key: _comparable(part) for key, part in sorted(value.items())}
    if isinstance(value, list):
        return [_comparable(part) for part in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return _norm(value)
    return value


def diff_updates(updates: dict[str, Any], stored: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify every explicitly written path, including clears and coded values."""
    out = []
    for path, expected in updates.items():
        actual: Any = stored
        for part in path.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if actual != expected:
            out.append({"field": path, "sent": expected, "stored": actual})
    return out


def diff_roundtrip(sent: dict[str, Any], stored: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields Vendoo stored differently from what we sent (generalDetails only).

    Only values we actually set are compared; Vendoo defaults everything else.
    """
    sent_general = sent.get(GENERAL_KEY) or {}
    stored_general = stored.get(GENERAL_KEY) or {}
    defaults = default_general_details()
    out: list[dict[str, Any]] = []
    for key, value in sent_general.items():
        # Blanks and untouched form defaults are Vendoo's to fill in, not ours to check.
        if value in ("", [], None) or key == "images" or value == defaults.get(key):
            continue
        want, got = _comparable(value), _comparable(stored_general.get(key))
        if want != got:
            out.append({"field": key, "sent": value, "stored": stored_general.get(key)})
    return out
