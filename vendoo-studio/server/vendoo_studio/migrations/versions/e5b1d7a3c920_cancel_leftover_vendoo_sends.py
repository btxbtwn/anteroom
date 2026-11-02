"""cancel Send to Vendoo jobs left open when Send to Vendoo was removed

Revision ID: e5b1d7a3c920
Revises: e4b7c2d9f1a3
Create Date: 2026-11-02 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e5b1d7a3c920"
down_revision = "e4b7c2d9f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """A Send to Vendoo still queued or running would hold Chrome forever.

    Nothing dispatches those jobs any more, and an active job blocks a delist
    or page inspection. Direct marketplace jobs (``native_*`` steps) are left
    to the job watchdog. A listing labelled busy by such a send returns to
    draft; the next inventory refresh gives it its own status.
    """
    bind = op.get_bind()
    leftover = sa.text(
        "status IN ('queued', 'awaiting_extension', 'dispatched') "
        "AND (current_step IS NULL OR current_step NOT LIKE 'native\\_%' ESCAPE '\\')"
    )
    conversation_ids = [row[0] for row in bind.execute(
        sa.select(sa.column("conversation_id")).select_from(sa.table("jobs")).where(leftover)
    )]
    bind.execute(
        sa.update(sa.table("jobs", sa.column("status"), sa.column("current_step"), sa.column("last_error")))
        .where(leftover)
        .values(status="cancelled", current_step=None, last_error="Send to Vendoo was removed from Anteroom.")
    )
    if conversation_ids:
        conversations = sa.table("conversations", sa.column("id"), sa.column("status"))
        bind.execute(
            sa.update(conversations)
            .where(conversations.c.id.in_(conversation_ids), conversations.c.status.in_(("listing", "failed")))
            .values(status="draft")
        )


def downgrade() -> None:
    """The cancelled jobs had nothing left to run them."""
