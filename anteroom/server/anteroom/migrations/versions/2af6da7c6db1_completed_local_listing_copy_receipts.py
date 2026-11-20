"""Completed local listing copy receipts

Revision ID: 2af6da7c6db1
Revises: 9661374eb3a1
Create Date: 2026-10-26 07:00:41.754640
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '2af6da7c6db1'
down_revision = '9661374eb3a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('listing_copy_receipts',
    sa.Column('request_id', sa.String(), nullable=False),
    sa.Column('fingerprint', sa.String(), nullable=False),
    sa.Column('source_id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('request_id')
    )


def downgrade() -> None:
    op.drop_table('listing_copy_receipts')
