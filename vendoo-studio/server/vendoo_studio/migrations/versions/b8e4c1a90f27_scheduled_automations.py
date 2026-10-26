"""scheduled automations and original asking prices

Revision ID: b8e4c1a90f27
Revises: 2af6da7c6db1
Create Date: 2026-10-26 06:10:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "b8e4c1a90f27"
down_revision = "2af6da7c6db1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("marketplaces", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("automations") as batch:
        batch.create_index(batch.f("ix_automations_next_run_at"), ["next_run_at"], unique=False)

    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("automation_id", "scheduled_for", name="uq_automation_runs_slot"),
    )
    with op.batch_alter_table("automation_runs") as batch:
        batch.create_index(batch.f("ix_automation_runs_automation_id"), ["automation_id"], unique=False)

    op.create_table(
        "automation_applications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("listed_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("automation_id", "conversation_id", name="uq_automation_applications_item"),
    )
    with op.batch_alter_table("automation_applications") as batch:
        batch.create_index(batch.f("ix_automation_applications_automation_id"), ["automation_id"], unique=False)
        batch.create_index(batch.f("ix_automation_applications_conversation_id"), ["conversation_id"], unique=False)

    op.create_table(
        "item_price_baselines",
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("original_price", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )

    with op.batch_alter_table("assistant_messages") as batch:
        batch.add_column(sa.Column("automation", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_messages") as batch:
        batch.drop_column("automation")
    op.drop_table("item_price_baselines")
    op.drop_table("automation_applications")
    op.drop_table("automation_runs")
    op.drop_table("automations")
