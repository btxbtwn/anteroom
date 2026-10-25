"""The marketplace section Vendoo would save, built for a direct listing.

Direct List used to send the raw listing JSON. Vendoo Send runs the same
section builder first, which is where defaults, brands, shipping, and
condition text are written. Direct List now sends that section too.
"""
from __future__ import annotations

from typing import Any

from vendoo_studio.services.vendoo_create import load_schema
from vendoo_studio.services.vendoo_item_build import _general_details, _listing_section


def vendoo_form(listing: dict[str, Any], marketplace: str) -> dict[str, Any]:
    """Overrides, marketplace specifics, and category aspects for one marketplace.

    Built even when Vendoo would leave the form empty (modern resale on Etsy).
    Direct listing warns and still sends the fields.
    """
    schema = load_schema()
    unresolved: list[dict[str, str]] = []
    general = _general_details(listing, schema, [], unresolved)
    section = _listing_section(marketplace, listing, general, schema, unresolved, None)
    return {
        "overrides": section.get("overrides") or {},
        "specifics": section.get("marketplaceSpecifics") or {},
        "aspects": section.get("categorySpecifics") or {},
    }


def attach_form(snapshot: dict[str, Any], marketplace: str) -> dict[str, Any]:
    snapshot["form"] = vendoo_form(snapshot, marketplace)
    return snapshot
