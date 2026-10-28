"""The marketplace section Vendoo would save, built for a direct listing.

Direct List used to send the raw listing JSON. Vendoo Send runs the same
section builder first, which is where defaults, brands, shipping, and
condition text are written. Direct List now sends that section too, built
against the marketplace leaf the listing's breadcrumb names today — resolved
from the seeded category tree, so no Vendoo lookup has to have run first.
"""
from __future__ import annotations

import logging
from typing import Any

from vendoo_studio.services.vendoo_categories import category_from_hit, path_parts
from vendoo_studio.services.vendoo_create import load_schema
from vendoo_studio.services.vendoo_item_build import _general_details, _listing_section

log = logging.getLogger("vendoo_studio.native_form")


def resolve_category(listing: dict[str, Any], marketplace: str) -> None:
    """Point the listing's ids for ``marketplace`` at the leaf its breadcrumb names now.

    A stored category object is kept only while it still names the same path:
    a live Vendoo hit carries extras the tree does not. Otherwise the leaf and
    its ancestor id chain come from the seeded tree, and a path the tree does
    not know clears the old ids rather than reusing them for a new category.
    """
    paths = listing.get("marketplace_categories")
    path = path_parts(paths.get(marketplace)) if isinstance(paths, dict) else []
    if not path:
        return
    ids = listing.setdefault("marketplace_category_ids", {})
    objects = listing.setdefault("marketplace_category_objects", {})
    if not isinstance(ids, dict) or not isinstance(objects, dict):
        return
    stored = objects.get(marketplace)
    if isinstance(stored, dict) and stored.get("id") and stored.get("path") \
            and path_parts(stored.get("displayPath")) == path:
        ids[marketplace] = str(stored["id"])
        return
    from vendoo_studio.services.vendoo_create import tree_leaf

    leaf = category_from_hit(tree_leaf(marketplace, " > ".join(path)), " > ".join(path))
    specifics = listing.get(f"{marketplace}_specifics")
    if isinstance(specifics, dict):
        specifics.pop("categoryId", None)
    if leaf and leaf.get("id"):
        ids[marketplace] = str(leaf["id"])
        objects[marketplace] = leaf
    else:
        ids.pop(marketplace, None)
        objects.pop(marketplace, None)


def _leaf_specs(marketplace: str, leaf_id: str):
    """Vendoo's cached field schema for the leaf, when one has been read."""
    from vendoo_studio.services.category_fields import load_fields

    try:
        return load_fields(marketplace, leaf_id) if leaf_id else None
    except Exception:  # noqa: BLE001 - a missing cache only means fewer coded fields
        log.info("No cached %s schema for %s", marketplace, leaf_id)
        return None


def vendoo_form(listing: dict[str, Any], marketplace: str) -> dict[str, Any]:
    """Overrides, marketplace specifics, and category aspects for one marketplace.

    Built even when Vendoo would leave the form empty (modern resale on Etsy).
    Direct listing warns and still sends the fields.
    """
    schema = load_schema()
    unresolved: list[dict[str, str]] = []
    general = _general_details(listing, schema, [], unresolved)
    ids = listing.get("marketplace_category_ids")
    leaf_id = str(ids.get(marketplace) or "") if isinstance(ids, dict) else ""
    section = _listing_section(marketplace, listing, general, schema, unresolved, _leaf_specs(marketplace, leaf_id))
    return {
        "overrides": section.get("overrides") or {},
        "specifics": section.get("marketplaceSpecifics") or {},
        "aspects": section.get("categorySpecifics") or {},
        # Values the section could not encode; the direct pre-flight shows them.
        "unresolved": unresolved,
    }


def attach_form(snapshot: dict[str, Any], marketplace: str) -> dict[str, Any]:
    from vendoo_studio.services.user_settings import get_mercari_shipping_label, get_ships_from_zip

    resolve_category(snapshot, marketplace)
    form = ({"overrides": {"categoryV2": (snapshot.get("marketplace_category_objects") or {}).get("mercari") or {}},
             "specifics": {}, "aspects": {}, "unresolved": []} if marketplace == "mercari" else vendoo_form(snapshot, marketplace))
    zip_code = get_ships_from_zip()
    if zip_code:
        form["ships_from_zip"] = zip_code
        if marketplace == "mercari":
            specifics = snapshot.get("mercari_specifics")
            specifics = specifics if isinstance(specifics, dict) else {}
            if not str(specifics.get("shipsFrom") or "").strip():
                specifics["shipsFrom"] = zip_code
                snapshot["mercari_specifics"] = specifics
    shipping_label = get_mercari_shipping_label()
    if marketplace == "mercari" and shipping_label:
        # The class id belongs to one weight tier, so the label is the preference;
        # the extension matches it against the labels this package can use.
        form["mercari_shipping_label"] = shipping_label
    snapshot["form"] = form
    return snapshot
