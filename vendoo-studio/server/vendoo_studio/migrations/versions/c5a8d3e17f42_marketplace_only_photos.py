"""Photos added on one marketplace's form

Revision ID: c5a8d3e17f42
Revises: 3ece513f8e0d
Create Date: 2026-10-26 21:30:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c5a8d3e17f42'
down_revision = '3ece513f8e0d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('marketplace', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.drop_column('marketplace')
