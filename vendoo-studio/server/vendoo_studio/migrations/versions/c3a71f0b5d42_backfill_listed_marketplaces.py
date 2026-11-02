"""backfill each conversation's listed marketplaces

Revision ID: c3a71f0b5d42
Revises: b07d34ffaad6
Create Date: 2026-09-20 11:10:00.000000
"""
from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = 'c3a71f0b5d42'
down_revision = 'eb12154c8bb2'
branch_labels = None
depends_on = None


# The Vendoo item readers this revision used, kept here so it still runs on an
# old database after the Vendoo import code was removed.
_ALIASES = {"vestiaireApi": "vestiaire"}


def _listing_entries(merged: dict[str, Any], *, external: bool = True) -> list[tuple[str, dict]]:
    """The item's marketplace listings, Vendoo's ``validate`` pseudo-entry aside.

    Vendoo keeps listings it posted under ``listings`` and ones the seller
    recorded elsewhere under ``externalListings``; its own selectors read both
    when deciding whether an item sold.
    """
    entries: list[tuple[str, dict]] = []
    keys = ["listings", "externalListings"] if external else ["listings"]
    for key in keys:
        group = merged.get(key)
        if not isinstance(group, dict):
            group = {}
        for name, listing in group.items():
            if name == "validate" or not isinstance(listing, dict):
                continue
            entries.append((name, listing))
    return entries


def _merge_payloads(form: dict | None, item: dict | None) -> dict[str, Any]:
    form_data = form if isinstance(form, dict) else {}
    item_data = item if isinstance(item, dict) else {}
    form_general = form_data.get("generalDetails")
    if not isinstance(form_general, dict):
        form_general = {}
    item_general = item_data.get("generalDetails")
    if not isinstance(item_general, dict):
        item_general = {}
    form_listings = form_data.get("listings")
    if not isinstance(form_listings, dict):
        form_listings = {}
    item_listings = item_data.get("listings")
    if not isinstance(item_listings, dict):
        item_listings = {}
    return {
        **form_data,
        **item_data,
        "generalDetails": {**form_general, **item_general},
        "listings": {**form_listings, **item_listings},
        "images": item_data.get("images") if item_data.get("images") is not None else form_data.get("images"),
    }


def vendoo_listed_marketplaces(item: dict | None, form: dict | None = None) -> list[str]:
    """Marketplaces the item is live on, by Vendoo's own listing flags.

    Sold counts as listed: Vendoo keeps a sold listing on the marketplace it
    sold from, and the Inventory filters still match it there. An item Vendoo
    delisted after the sale keeps only its sale record, so that record's
    marketplace stands in for the listing flag it cleared. A listing the seller
    already had on the marketplace (``externalListings``) counts the same way.
    """
    merged = _merge_payloads(form, item)
    live: set[str] = set()
    for name, listing in _listing_entries(merged, external=True):
        status = listing.get("status")
        if not isinstance(status, dict):
            status = {}
        if status.get("listed") is True or status.get("sold") is True or status.get("shipped") is True:
            live.add(_ALIASES.get(name, name))
    sale_record = merged.get("saleRecord")
    if not isinstance(sale_record, dict):
        sale_record = {}
    sold_on = str(sale_record.get("marketplace") or "").strip()
    if sold_on:
        live.add(_ALIASES.get(sold_on, sold_on))
    return sorted(live)


def upgrade() -> None:
    """Record, per conversation, the marketplaces its Vendoo item is live on.

    Imports write this into the conversation's notes from now on; the inventory
    already on disk was imported before the sidebar filtered on it, and the only
    place that state exists is inside the cached draft payloads.
    """
    from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        """
        SELECT c.id, c.notes, e.payload
        FROM conversations c
        JOIN jobs j ON j.conversation_id = c.id
        JOIN job_events e ON e.job_id = j.id AND e.event_type = 'vendoo_draft'
        ORDER BY c.id, e.created_at DESC, e.sequence DESC
        """
    )).fetchall()

    seen: set[str] = set()
    for conv_id, notes, payload in rows:
        if conv_id in seen:
            continue
        if "vendooMarketplaces" in parse_notes(notes):
            seen.add(conv_id)
            continue
        try:
            draft = json.loads(payload) if isinstance(payload, str) else payload
        except ValueError:
            continue
        if not isinstance(draft, dict):
            continue
        item = draft.get("item") if isinstance(draft.get("item"), dict) else None
        form = draft.get("form") if isinstance(draft.get("form"), dict) else None
        if item is None and form is None:
            continue
        seen.add(conv_id)
        bind.execute(
            sa.text("UPDATE conversations SET notes = :notes WHERE id = :id"),
            {
                "notes": merge_notes(notes, {
                    "vendooMarketplaces": vendoo_listed_marketplaces(item, form),
                }),
                "id": conv_id,
            },
        )


def downgrade() -> None:
    """Notes are additive; the backfilled key is left in place."""
