"""track eBay form promotions

Revision ID: 6d0cac7a2206
Revises: f1b4c8d2e930
Create Date: 2026-11-25 21:38:36.744837
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '6d0cac7a2206'
down_revision = 'f1b4c8d2e930'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adopting a database from before migrations creates the column with its tables.
    inspector = sa.inspect(op.get_bind())
    if "ebay_promotion" not in {column["name"] for column in inspector.get_columns("marketplace_listings")}:
        op.add_column("marketplace_listings", sa.Column("ebay_promotion", sa.JSON(), nullable=True))
    if "scheduled_ebay_ad_rate" not in {column["name"] for column in inspector.get_columns("listings")}:
        op.add_column("listings", sa.Column("scheduled_ebay_ad_rate", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "scheduled_ebay_ad_rate")
    op.drop_column("marketplace_listings", "ebay_promotion")
