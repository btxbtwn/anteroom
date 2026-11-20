"""direct order recovery and cancellation ledger

Revision ID: 6bc19d8ea678
Revises: 5f761d58b211
Create Date: 2026-10-26 05:03:56.495346
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '6bc19d8ea678'
down_revision = '5f761d58b211'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.add_column(sa.Column('order_status', sa.String(), server_default='paid', nullable=False))
        batch_op.add_column(sa.Column('order_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('order_observed_at', sa.DateTime(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.drop_column('order_observed_at')
        batch_op.drop_column('order_updated_at')
        batch_op.drop_column('order_status')
