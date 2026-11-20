"""bind photo analysis to image fingerprints

Revision ID: 3ec056c582ed
Revises: e748219a0f6c
Create Date: 2026-10-29 05:00:59.126105
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3ec056c582ed'
down_revision = 'e748219a0f6c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("photo_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "photo_fingerprint")
