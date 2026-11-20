"""conversations for the business assistant

Revision ID: c8e1a4b72d05
Revises: b4d7e2a9c1f3
Create Date: 2026-10-20 22:20:00.000000
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "c8e1a4b72d05"
down_revision = "b4d7e2a9c1f3"
branch_labels = None
depends_on = None

_TITLE_LIMIT = 80


def _title(text: str) -> str:
    title = " ".join((text or "").split())
    if not title:
        return "New thread"
    if len(title) <= _TITLE_LIMIT:
        return title
    return title[: _TITLE_LIMIT - 1].rstrip() + "…"


def upgrade() -> None:
    op.create_table(
        "assistant_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_threads_updated_at", "assistant_threads", ["updated_at"], unique=False)
    with op.batch_alter_table("assistant_messages") as batch:
        batch.add_column(sa.Column("thread_id", sa.String(), nullable=True))

    conn = op.get_bind()
    messages = conn.execute(
        sa.text("SELECT role, text, created_at FROM assistant_messages ORDER BY created_at, id")
    ).fetchall()
    if messages:
        thread_id = uuid.uuid4().hex[:12]
        title = "New thread"
        for role, text, _created in messages:
            if role == "user":
                title = _title(text)
                if title != "New thread":
                    break
        created = messages[0].created_at or datetime.now(UTC)
        updated = messages[-1].created_at or created
        conn.execute(
            sa.text(
                "INSERT INTO assistant_threads (id, title, created_at, updated_at) "
                "VALUES (:id, :title, :created, :updated)"
            ),
            {"id": thread_id, "title": title, "created": created, "updated": updated},
        )
        conn.execute(
            sa.text("UPDATE assistant_messages SET thread_id = :id"),
            {"id": thread_id},
        )

    with op.batch_alter_table("assistant_messages") as batch:
        batch.alter_column("thread_id", existing_type=sa.String(), nullable=False)
        batch.create_index("ix_assistant_messages_thread_id", ["thread_id"])


def downgrade() -> None:
    with op.batch_alter_table("assistant_messages") as batch:
        batch.drop_index("ix_assistant_messages_thread_id")
        batch.drop_column("thread_id")
    op.drop_index("ix_assistant_threads_updated_at", table_name="assistant_threads")
    op.drop_table("assistant_threads")
