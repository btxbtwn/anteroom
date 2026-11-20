"""seller financial records on direct sale lines

Revision ID: 9661374eb3a1
Revises: 6bc19d8ea678
Create Date: 2026-10-26 06:10:22.223759
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '9661374eb3a1'
down_revision = '6bc19d8ea678'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.add_column(sa.Column('financials', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('financial_revision', sa.Integer(), server_default='0', nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.drop_column('financial_revision')
        batch_op.drop_column('financials')
