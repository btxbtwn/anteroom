"""Persist unresolved eBay ad listing membership

Revision ID: b0d24fa61ce8
Revises: 4cf6b35245d2
Create Date: 2026-11-21 05:49:35.118086
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b0d24fa61ce8'
down_revision = '4cf6b35245d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ebay_actions", sa.Column("ad_listing_ids", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ebay_actions", "ad_listing_ids")
