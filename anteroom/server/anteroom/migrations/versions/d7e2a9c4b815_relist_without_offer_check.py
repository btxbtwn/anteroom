"""Auto-relist no longer checks buyer offers, so its skip setting goes

Revision ID: d7e2a9c4b815
Revises: 138d3602d2e2
Create Date: 2026-11-24 09:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd7e2a9c4b815'
down_revision = '138d3602d2e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database adopted from before migrations is built from the current
    # models and never had the column.
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('marketplace_relist_rules')}
    if 'skip_active_offers' in columns:
        with op.batch_alter_table('marketplace_relist_rules', schema=None) as batch_op:
            batch_op.drop_column('skip_active_offers')


def downgrade() -> None:
    with op.batch_alter_table('marketplace_relist_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skip_active_offers', sa.Boolean(), nullable=False, server_default=sa.true()))
