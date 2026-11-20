"""mark ad spend rows written by an Etsy Ads CSV upload

Revision ID: e4a1c7b93f58
Revises: d2f6a80c3b91
Create Date: 2026-10-12 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e4a1c7b93f58'
down_revision = 'd2f6a80c3b91'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ad_spend') as batch:
        batch.add_column(sa.Column('source', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ad_spend') as batch:
        batch.drop_column('source')
