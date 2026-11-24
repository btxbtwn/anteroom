"""Remember which agent scheduled a publish

Revision ID: f2a8d5c3e917
Revises: 1c42be3d1f71
Create Date: 2026-11-24 14:00:00.000000

A publish an MCP agent schedules keeps the agent's name on the listing
(``scheduled_requested_by``, ``mcp:<name>``) until the scheduler claims it, so
the jobs that publish it record ``requested_by`` like any action the agent
started. Existing schedules were the seller's own: null.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'f2a8d5c3e917'
down_revision = '1c42be3d1f71'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database adopted from before migrations is built from the current models.
    if 'scheduled_requested_by' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('listings')}:
        return
    with op.batch_alter_table('listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scheduled_requested_by', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('listings', schema=None) as batch_op:
        batch_op.drop_column('scheduled_requested_by')
