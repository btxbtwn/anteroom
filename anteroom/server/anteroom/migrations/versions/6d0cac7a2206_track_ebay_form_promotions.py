"""track eBay form promotions

Revision ID: 6d0cac7a2206
Revises: d5e1b7a3c962
Create Date: 2026-11-25 21:38:36.744837
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '6d0cac7a2206'
down_revision = 'd5e1b7a3c962'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adopting a database from before migrations creates the column with its tables.
    if "ebay_promotion" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("marketplace_listings")}:
        return
    op.add_column("marketplace_listings", sa.Column("ebay_promotion", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("marketplace_listings", "ebay_promotion")
