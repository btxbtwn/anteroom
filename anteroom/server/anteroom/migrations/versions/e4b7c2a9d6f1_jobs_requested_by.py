"""Record which agent started a job

Revision ID: e4b7c2a9d6f1
Revises: d7e2a9c4b815
Create Date: 2026-11-24 12:00:00.000000

A job an MCP agent starts (a marketplace draft, List, Update or Delist) names
the access token or connected app that asked for it, so the seller sees it
came from their AI assistant. Existing jobs were the seller's own: null.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e4b7c2a9d6f1'
down_revision = 'd7e2a9c4b815'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database adopted from before migrations is built from the current models.
    if 'requested_by' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('jobs')}:
        return
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requested_by', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('requested_by')
