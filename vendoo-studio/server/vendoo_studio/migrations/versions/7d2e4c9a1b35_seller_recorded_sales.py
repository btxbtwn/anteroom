"""Seller-recorded sales on the order-line ledger

Revision ID: 7d2e4c9a1b35
Revises: 151f7674a7c7
Create Date: 2026-10-26 20:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7d2e4c9a1b35'
down_revision = '151f7674a7c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing line came from a marketplace.
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(), server_default='marketplace', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.drop_column('source')
