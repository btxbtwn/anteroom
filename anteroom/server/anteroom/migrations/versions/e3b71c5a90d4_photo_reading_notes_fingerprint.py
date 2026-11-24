"""Remember which Item Details a photo reading was given, so retries reuse it

Revision ID: e3b71c5a90d4
Revises: e5c2a8f4d1b7
Create Date: 2026-11-24 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e3b71c5a90d4'
down_revision = 'e5c2a8f4d1b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy SQLite adoption can create the table from current metadata first.
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('messages')}
    if 'notes_fingerprint' not in columns:
        op.add_column('messages', sa.Column('notes_fingerprint', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('notes_fingerprint')
