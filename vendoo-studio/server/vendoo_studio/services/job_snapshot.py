"""The listing snapshot a direct marketplace action sends."""

from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from vendoo_studio.models.mercari_shipping import DEFAULT_SHIPPING_LABEL


def prepare_listing_snapshot(
    db: Session,
    conv,
    listing_json: dict,
    *,
    prefer_listing_category: bool = False,
) -> dict:
    from vendoo_studio.services.marketplaces import selected_fillable_platforms
    from vendoo_studio.services.registry import RegistryService, align_listing_gender
    from vendoo_studio.services.vendoo_import import parse_notes, split_vendoo_labels

    listing_snapshot = copy.deepcopy(listing_json or {})
    conv_notes = parse_notes(getattr(conv, "notes", None))
    labels = split_vendoo_labels(conv_notes.get("vendooLabels"))
    if labels:
        listing_snapshot["labels"] = labels
    from vendoo_studio.services.seller_overrides import is_locked, materialize_prices

    seller_sku = str(conv_notes.get("sku") or "").strip()
    if seller_sku and not is_locked(listing_snapshot, "sku"):
        listing_snapshot["sku"] = seller_sku
    package = str(conv_notes.get("packageDimensions") or "").strip()
    if package and not is_locked(listing_snapshot, "package_dimensions_in"):
        listing_snapshot["package_dimensions_in"] = package
    # Item Details COG and Notes are the listing's cost of goods and internal notes.
    cog_raw = str(conv_notes.get("cog") or "").strip()
    try:
        cog = float(cog_raw) if cog_raw else None
    except ValueError:
        cog = None
    if cog is None and getattr(conv, "box_id", None):
        from vendoo_studio.services.boxes import cost_share, get_box
        cog = cost_share(get_box(db, conv.box_id))
    if cog is not None:
        listing_snapshot["cost"] = cog
    seller_notes = str(conv_notes.get("sellerNotes") or "").strip()
    if seller_notes:
        listing_snapshot["internal_notes"] = seller_notes
    listing_category = str(listing_snapshot.get("category_path") or "").strip()
    category_override = str(conv_notes.get("categoryOverride") or "").strip()
    if category_override and not is_locked(listing_snapshot, "category_path") and (
        not prefer_listing_category or not listing_category
    ):
        listing_snapshot["category_path"] = category_override
    poshmark = listing_snapshot.get("poshmark_specifics") or {}
    if not isinstance(poshmark, dict):
        poshmark = {}
    from vendoo_studio.services.seller_overrides import is_locked

    if not is_locked(listing_snapshot, "poshmark_specifics.originalPrice"):
        poshmark["originalPrice"] = 0
    listing_snapshot["poshmark_specifics"] = poshmark
    align_listing_gender(listing_snapshot)
    from vendoo_studio.services.depop_category import align_depop_category

    # A revision saved before the Depop keys moved together can still name the
    # old leaf in direct_category_ids. The category the seller set wins.
    align_depop_category(listing_snapshot)
    ensure_listing_defaults(listing_snapshot)
    from vendoo_studio.services.depop_measurements import sync_depop_measurements

    sync_depop_measurements(listing_snapshot, conv_notes)
    materialize_prices(listing_snapshot)
    registry = RegistryService(db)
    registry.merge_learned_fields(listing_snapshot)
    for marketplace in selected_fillable_platforms():
        registry.validate_dropdown_fields(
            listing_snapshot, marketplace, listing_snapshot.get("category_path"),
        )
    # A native generation contract uses the marketplace's own field paths.
    # Vendoo defaults/learned aliases must not rewrite those answers.
    for marketplace in (listing_json or {}).get("_native_forms") or {}:
        listing_snapshot[f"{marketplace}_specifics"] = copy.deepcopy(
            (listing_json or {}).get(f"{marketplace}_specifics") or {})
    return listing_snapshot


def ensure_listing_defaults(listing_snapshot: dict) -> None:
    if not isinstance(listing_snapshot, dict):
        return

    from vendoo_studio.models.schema import ListingSchema

    condition = listing_snapshot.get("condition")
    if condition:
        listing_snapshot["condition"] = ListingSchema.validate_condition(condition)

    from vendoo_studio.services.registry import map_vendoo_category_path

    mapped_category = map_vendoo_category_path(
        str(listing_snapshot.get("category_path") or ""),
        listing_snapshot,
    )
    if mapped_category:
        listing_snapshot["category_path"] = mapped_category

    mercari = listing_snapshot.get("mercari_specifics") or {}
    if not isinstance(mercari, dict):
        mercari = {}
    label = str(mercari.get("shippingLabel") or "").strip()
    mercari["shippingLabel"] = label or DEFAULT_SHIPPING_LABEL
    listing_snapshot["mercari_specifics"] = mercari

    from vendoo_studio.services.validation import normalize_listing_dropdowns
    from vendoo_studio.services.listing_generate import align_size_fields

    from vendoo_studio.services.seller_overrides import preserving_locks

    def _normalize(listing: dict) -> None:
        align_size_fields(listing)
        normalize_listing_dropdowns(listing)

    preserving_locks(listing_snapshot, _normalize)
