"""eBay listings with variations: inventory item group and variation offers

Revision ID: a7c3e91f5d20
Revises: 151f7674a7c7
Create Date: 2026-10-26 20:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a7c3e91f5d20'
down_revision = '151f7674a7c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ebay_group_key', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('ebay_variant_offers', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.drop_column('ebay_variant_offers')
        batch_op.drop_column('ebay_group_key')
