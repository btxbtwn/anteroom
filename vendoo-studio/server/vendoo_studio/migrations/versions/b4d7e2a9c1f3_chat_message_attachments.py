"""images attached to listing chat and assistant messages

Revision ID: b4d7e2a9c1f3
Revises: a3c9e1f4b682
Create Date: 2026-10-20 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b4d7e2a9c1f3'
down_revision = 'a3c9e1f4b682'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('messages') as batch:
        batch.add_column(sa.Column('attachments', sa.JSON(), nullable=True))
    with op.batch_alter_table('assistant_messages') as batch:
        batch.add_column(sa.Column('attachments', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('assistant_messages') as batch:
        batch.drop_column('attachments')
    with op.batch_alter_table('messages') as batch:
        batch.drop_column('attachments')
