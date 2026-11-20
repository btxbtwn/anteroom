"""Job leases and cross-Machine cancel

Revision ID: cd33c540e489
Revises: 7f3a9c2e5d14
Create Date: 2026-11-06 17:48:50.007490

A running marketplace job records which Machine runs it and until when, and a
Cancel is recorded on the job row, so liveness and cancellation no longer live
in one process's memory. Existing rows have no lease: they are judged by the
watchdog exactly as before. Postgres adds these columns without rewriting the
table (constant default).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'cd33c540e489'
down_revision = '7f3a9c2e5d14'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adopting a database from before migrations creates these columns with its tables.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    # Plain ADD COLUMN on both databases: no rebuild of the jobs table is needed.
    for column in (sa.Column('lease_owner', sa.String(length=64), nullable=True),
                   sa.Column('leased_until', sa.DateTime(timezone=True), nullable=True),
                   sa.Column('cancel_requested', sa.Boolean(), server_default=sa.false(), nullable=False)):
        if column.name not in existing:
            op.add_column('jobs', column)


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('cancel_requested')
        batch_op.drop_column('leased_until')
        batch_op.drop_column('lease_owner')
