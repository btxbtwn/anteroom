"""Needs Attention items can be resolved, not only snoozed for 7 days

A resolved row has no expiry and keeps a fingerprint of the item's state; the
item shows again when the fingerprint changes. Adding columns leaves the
table's row-level security on hosted Postgres as it is.

Revision ID: e3b7a1c9d402
Revises: d7e2a9c4b815
Create Date: 2026-11-24 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e3b7a1c9d402"
down_revision = "d7e2a9c4b815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attention_dismissals") as batch:
        batch.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))
        batch.alter_column("expires_at", existing_type=sa.DateTime(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM attention_dismissals WHERE expires_at IS NULL")
    with op.batch_alter_table("attention_dismissals") as batch:
        batch.alter_column("expires_at", existing_type=sa.DateTime(), nullable=False)
        batch.drop_column("fingerprint")
