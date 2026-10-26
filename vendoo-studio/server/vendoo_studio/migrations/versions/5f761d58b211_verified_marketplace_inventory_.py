"""verified marketplace inventory observations

Revision ID: 5f761d58b211
Revises: 292cc5d3b9a5
Create Date: 2026-10-26 04:49:46.526986
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '5f761d58b211'
down_revision = '292cc5d3b9a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inventory_observation', sa.JSON(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.drop_column('inventory_observation')
