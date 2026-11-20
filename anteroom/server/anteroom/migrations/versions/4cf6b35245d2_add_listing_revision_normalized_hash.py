"""Store normalized listing hashes on revisions for fast unsent-edit checks

Revision ID: 4cf6b35245d2
Revises: 26c67b32452c
Create Date: 2026-11-20 12:46:05.845582
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '4cf6b35245d2'
down_revision = '26c67b32452c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "normalized_hash" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("listing_revisions")}:
        op.add_column("listing_revisions", sa.Column("normalized_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_revisions", "normalized_hash")
