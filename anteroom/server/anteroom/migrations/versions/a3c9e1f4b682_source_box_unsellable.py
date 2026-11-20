"""pieces of a bought box that were not worth listing

Revision ID: a3c9e1f4b682
Revises: f5b2d8e0a471
Create Date: 2026-10-20 19:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a3c9e1f4b682'
down_revision = 'f5b2d8e0a471'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('source_boxes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('unsellable', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('source_boxes', schema=None) as batch_op:
        batch_op.drop_column('unsellable')
