"""listed price on marketplace listings

Revision ID: e3bea75f1b9b
Revises: ca4d82e917f6
Create Date: 2026-11-05 19:44:12.578187

Live records are backfilled with the price their last List or Update sent:
the revision they were written from, read the way ``price_for`` read it then
(the seller's marketplace price when they set one, else the item price).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = 'e3bea75f1b9b'
down_revision = 'ca4d82e917f6'
branch_labels = None
depends_on = None


# Each marketplace's own price key, as Anteroom wrote it when this revision was made.
_CANONICAL_PRICE = {
    "ebay": ("ebay_specifics", "price"),
    "poshmark": ("poshmark_specifics", "poshmarkPrice"),
    "mercari": ("mercari_specifics", "price"),
    "depop": ("depop_specifics", "price"),
    "etsy": ("etsy_specifics", "price"),
}
_LIVE_STATES = ("published", "pending_review")

listings_table = sa.table(
    "marketplace_listings",
    sa.column("id", sa.String),
    sa.column("marketplace", sa.String),
    sa.column("state", sa.String),
    sa.column("revision_id", sa.String),
    sa.column("listed_price", sa.Float),
)
revisions_table = sa.table(
    "listing_revisions",
    sa.column("id", sa.String),
    sa.column("listing_json", sa.JSON),
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, (bool, dict, list)):
        return None
    try:
        amount = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except InvalidOperation:
        return None
    return float(amount) if amount.is_finite() and amount > 0 else None


def _sent_price(listing: Any, marketplace: str) -> float | None:
    if not isinstance(listing, dict):
        return None
    canonical = _CANONICAL_PRICE.get(marketplace)
    locks = listing.get("seller_fields")
    if canonical and isinstance(locks, list) and ".".join(canonical) in locks:
        section = listing.get(canonical[0])
        amount = _number(section.get(canonical[1])) if isinstance(section, dict) else None
        if amount is not None:
            return amount
    return _number(listing.get("price"))


def upgrade() -> None:
    connection = op.get_bind()
    # Adopting a database from before migrations creates the column with its tables.
    if "listed_price" in {column["name"] for column in sa.inspect(connection).get_columns("marketplace_listings")}:
        return
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('listed_price', sa.Float(), nullable=True))

    rows = connection.execute(
        sa.select(listings_table.c.id, listings_table.c.marketplace, revisions_table.c.listing_json)
        .join(revisions_table, revisions_table.c.id == listings_table.c.revision_id)
        .where(listings_table.c.state.in_(_LIVE_STATES))
    ).all()
    for row_id, marketplace, listing in rows:
        amount = _sent_price(listing, marketplace)
        if amount is not None:
            connection.execute(sa.update(listings_table).where(listings_table.c.id == row_id).values(listed_price=amount))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.drop_column('listed_price')
