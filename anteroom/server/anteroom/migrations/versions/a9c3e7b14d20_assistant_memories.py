"""facts the business assistant remembers across conversations

Revision ID: a9c3e7b14d20
Revises: c8e1a4b72d05
Create Date: 2026-10-20 23:40:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a9c3e7b14d20"
down_revision = "c8e1a4b72d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("assistant_memories")
