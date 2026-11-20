"""changes the business assistant proposed with an answer

Revision ID: f5b2d8e0a471
Revises: e4a1c7b93f58
Create Date: 2026-10-13 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'f5b2d8e0a471'
down_revision = 'e4a1c7b93f58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('assistant_messages') as batch:
        batch.add_column(sa.Column('changes', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('assistant_messages') as batch:
        batch.drop_column('changes')
