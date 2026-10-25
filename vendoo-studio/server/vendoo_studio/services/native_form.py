"""The marketplace section Vendoo would save, built for a direct listing.

Direct List used to send the raw listing JSON. Vendoo Send runs the same
section builder first, which is where defaults, brands, shipping, and
condition text are written. Direct List now sends that section too, built
against the marketplace leaf the listing's breadcrumb names today — resolved
from the seeded category tree, so no Vendoo lookup has to have run first.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from vendoo_studio.services.vendoo_categories import category_from_hit, path_parts
from vendoo_studio.services.vendoo_create import load_schema
from vendoo_studio.services.vendoo_item_build import _general_details, _listing_section

log = logging.getLogger("vendoo_studio.native_form")

MERCARI_MASTER_MAX_AGE_S = 24 * 3600
_mercari_master: tuple[float, dict[str, Any]] | None = None


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


def mercari_master() -> dict[str, Any] | None:
    """Mercari's size master (the public file Vendoo's form reads), kept for a day."""
    global _mercari_master
    if _mercari_master and time.monotonic() - _mercari_master[0] < MERCARI_MASTER_MAX_AGE_S:
        return _mercari_master[1]
    import httpx

    from vendoo_studio.services.vendoo_specifics import MERCARI_STATIC_URL

    try:
        response = httpx.get(MERCARI_STATIC_URL, timeout=8.0, headers={"Accept-Encoding": "gzip"})
        response.raise_for_status()
        master = response.json()
    except Exception as exc:  # noqa: BLE001 - Chrome then relies on Mercari's own verdict
        log.info("No Mercari size master: %s", exc)
        return None
    if not isinstance(master, dict) or not isinstance(master.get("itemSizes"), list):
        return None
    _mercari_master = (time.monotonic(), master)
    return master


def mercari_sizes(category_id: str) -> list[dict[str, Any]] | None:
    """The sizes Mercari offers in one category, in its display order.

    ``[]`` means the category has no size; ``None`` that the table could not be read.
    """
    master = mercari_master()
    if master is None:
        return None
    group = (master.get("categorySizeGroup") or {}).get(str(category_id))
    if group is None:
        return []
    rows = [size for size in master["itemSizes"]
            if isinstance(size, dict) and size.get("itemSizeGroupId") == group and size.get("id") is not None]
    rows.sort(key=lambda size: (size.get("itemSizeSubGroupId") or 0, size.get("displayOrder") or 0))
    return [{"id": size["id"], "name": str(size.get("name") or ""), "title": str(size.get("title") or ""),
             "group": size.get("itemSizeSubGroupId")} for size in rows]


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
    from vendoo_studio.services.user_settings import get_ships_from_zip

    resolve_category(snapshot, marketplace)
    form = vendoo_form(snapshot, marketplace)
    zip_code = get_ships_from_zip()
    if zip_code:
        form["ships_from_zip"] = zip_code
        shipping = form["specifics"].get("shipping")
        if marketplace == "mercari" and isinstance(shipping, dict) and not str(shipping.get("location") or "").strip():
            # Vendoo's own field for Mercari's ships-from ZIP.
            shipping["location"] = zip_code
    if marketplace == "mercari":
        # The category the direct list sends: a stored Mercari id wins, as in Chrome.
        direct = snapshot.get("direct_category_ids") if isinstance(snapshot.get("direct_category_ids"), dict) else {}
        leaf = str(direct.get("mercari") or "").strip() or (form["overrides"].get("categoryV2") or {}).get("id")
        if leaf:
            sizes = mercari_sizes(str(leaf))
            if sizes is not None:
                form["sizes"] = sizes
    snapshot["form"] = form
    return snapshot
