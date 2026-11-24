"""Record which agent started a job or scheduled a publish

Revision ID: e4b7c2a9d6f1
Revises: d7e2a9c4b815
Create Date: 2026-11-24 12:00:00.000000

A job an MCP agent starts (a marketplace draft, List, Update or Delist) names
the access token or connected app that asked for it, so the seller sees it
came from their AI assistant. A publish an agent schedules keeps its name on
the listing (``scheduled_requested_by``) until the jobs that publish it start.
Existing rows were the seller's own: null.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e4b7c2a9d6f1'
down_revision = 'd7e2a9c4b815'
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # A database adopted from before migrations is built from the current models.
    if 'requested_by' not in _columns('jobs'):
        with op.batch_alter_table('jobs', schema=None) as batch_op:
            batch_op.add_column(sa.Column('requested_by', sa.String(length=120), nullable=True))
    if 'scheduled_requested_by' not in _columns('listings'):
        with op.batch_alter_table('listings', schema=None) as batch_op:
            batch_op.add_column(sa.Column('scheduled_requested_by', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('listings', schema=None) as batch_op:
        batch_op.drop_column('scheduled_requested_by')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('requested_by')
