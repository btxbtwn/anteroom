"""remember seller owned Finished listings

Revision ID: d0e4f3a92b68
Revises: fd7c3a5d05ea
Create Date: 2026-11-17 02:11:13.664070
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd0e4f3a92b68'
down_revision = 'fd7c3a5d05ea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy adoption may create current metadata before replaying revisions.
    if 'settled_by_seller' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('conversations')}:
        return
    # Older builds did not distinguish automatic and explicit Finish. Never
    # reopen an existing Finished item by guessing ownership from timestamps.
    op.add_column('conversations', sa.Column('settled_by_seller', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    raise RuntimeError('Finished ownership is forward-only; never discard the seller\'s decision')
