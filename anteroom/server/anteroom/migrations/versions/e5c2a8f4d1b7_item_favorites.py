"""The seller can star an item

Revision ID: e5c2a8f4d1b7
Revises: d7e2a9c4b815
Create Date: 2026-11-24 12:00:00.000000

Every existing item starts unstarred. Row security on ``conversations`` is
per row, so the new column needs no policy of its own.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e5c2a8f4d1b7'
down_revision = 'd7e2a9c4b815'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database adopted from before migrations is built from the current models.
    if 'favorite' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('conversations')}:
        return
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('favorite', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_column('favorite')
