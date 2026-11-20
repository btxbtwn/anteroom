"""index messages, photos and jobs by listing

The activity poll runs about once a second for every open listing and read
these tables in full each time.

Revision ID: d3f6a2c81e94
Revises: a9c3e7b14d20
Create Date: 2026-10-24 00:30:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "d3f6a2c81e94"
down_revision = "a9c3e7b14d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_conversation_id_created_at", "messages", ["conversation_id", "created_at"]
    )
    op.create_index("ix_photos_conversation_id", "photos", ["conversation_id"])
    op.create_index("ix_jobs_conversation_id", "jobs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_conversation_id", table_name="jobs")
    op.drop_index("ix_photos_conversation_id", table_name="photos")
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
