"""record per-call generation usage

Revision ID: 9664651bec7c
Revises: f1c84a9d7b32
Create Date: 2026-10-29 12:49:31.487870
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '9664651bec7c'
down_revision = 'f1c84a9d7b32'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy adoption can create a missing table at the current model shape.
    # Existing timing tables still need the same additive upgrade as a normal
    # versioned database. Do not duplicate this column in BACKFILLED_COLUMNS.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("generation_timings")}
    if "calls" in columns:
        return
    with op.batch_alter_table('generation_timings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('calls', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))



def downgrade() -> None:
    with op.batch_alter_table('generation_timings', schema=None) as batch_op:
        batch_op.drop_column('calls')
