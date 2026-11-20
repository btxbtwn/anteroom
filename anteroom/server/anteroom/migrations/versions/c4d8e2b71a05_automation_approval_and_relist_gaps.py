"""per-task price mode, regenerate approval, and relist gaps

Revision ID: c4d8e2b71a05
Revises: b8e4c1a90f27
Create Date: 2026-10-26 07:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c4d8e2b71a05"
down_revision = "b8e4c1a90f27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("automations") as batch:
        batch.add_column(sa.Column("price_repeat", sa.String(), nullable=True))
        batch.add_column(sa.Column("require_approval", sa.Boolean(), nullable=False, server_default="1"))

    op.create_table(
        "automation_relist_gaps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("marketplace", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("automation_relist_gaps") as batch:
        batch.create_index(batch.f("ix_automation_relist_gaps_automation_id"), ["automation_id"], unique=False)
        batch.create_index(batch.f("ix_automation_relist_gaps_conversation_id"), ["conversation_id"], unique=False)

    op.create_table(
        "automation_approvals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("marketplaces", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("automation_approvals") as batch:
        batch.create_index(batch.f("ix_automation_approvals_automation_id"), ["automation_id"], unique=False)
        batch.create_index(batch.f("ix_automation_approvals_conversation_id"), ["conversation_id"], unique=False)


def downgrade() -> None:
    op.drop_table("automation_approvals")
    op.drop_table("automation_relist_gaps")
    with op.batch_alter_table("automations") as batch:
        batch.drop_column("require_approval")
        batch.drop_column("price_repeat")
