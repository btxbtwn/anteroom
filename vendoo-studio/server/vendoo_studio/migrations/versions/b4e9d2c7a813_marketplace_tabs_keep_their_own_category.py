"""copy each marketplace tab's category onto the tab's own key

Revision ID: b4e9d2c7a813
Revises: 56b5ee7250ec
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "b4e9d2c7a813"
down_revision = "56b5ee7250ec"
branch_labels = None
depends_on = None

_NUMERIC = re.compile(r"[1-9]\d{0,14}")


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def adopt(listing: dict, rows: dict[str, list[dict]]) -> bool:
    """Give each tab the category it showed before, from Vendoo's matching category.

    The eBay, Etsy, Depop, Mercari and Poshmark tabs used to fall back to
    Vendoo's category when they had none of their own. They now read only their
    own key, so a draft that relied on the fallback keeps it here. Only an id
    the marketplace's own list has, or Poshmark's exact path, is copied.
    """
    vendoo_ids = _mapping(listing.get("marketplace_category_ids"))
    vendoo_paths = _mapping(listing.get("marketplace_categories"))
    direct = dict(_mapping(listing.get("direct_category_ids")))
    before = dict(direct)
    for market in ("ebay", "etsy"):
        value = str(vendoo_ids.get(market) or "").strip()
        if not direct.get(market) and _NUMERIC.fullmatch(value):
            direct[market] = value
            if market == "ebay" and not direct.get("ebay_path") and vendoo_paths.get("ebay"):
                direct["ebay_path"] = str(vendoo_paths["ebay"])
    for market in ("depop", "mercari"):
        value = str(vendoo_ids.get(market) or "").strip()
        if not direct.get(market) and any(row["id"] == value for row in rows[market]):
            direct[market] = value
    path = " > ".join(part.strip() for part in str(vendoo_paths.get("poshmark") or "").split(">") if part.strip())
    if not direct.get("poshmark") and path:
        found = [row["id"] for row in rows["poshmark"] if row["path"] in (path, f"{path} > None")]
        if len(found) == 1:
            direct["poshmark"] = found[0]
    if direct == before:
        return False
    listing["direct_category_ids"] = direct
    return True


def upgrade() -> None:
    from vendoo_studio.services.native_category_match import native_rows

    rows = native_rows()
    bind = op.get_bind()
    revisions = sa.table("listing_revisions", sa.column("id", sa.String), sa.column("listing_json", sa.JSON))
    listings = sa.table("listings", sa.column("current_revision_id", sa.String))
    current = bind.execute(
        sa.select(revisions.c.id, revisions.c.listing_json)
        .where(revisions.c.id.in_(sa.select(listings.c.current_revision_id)))
    ).fetchall()
    for revision_id, listing in current:
        if isinstance(listing, dict) and adopt(listing, rows):
            bind.execute(revisions.update().where(revisions.c.id == revision_id).values(listing_json=listing))


def downgrade() -> None:
    """The copied ids name the same categories the tabs showed; they are left in place."""
