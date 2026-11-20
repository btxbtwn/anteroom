"""generation timings: seconds per stage of each listing generation

Revision ID: e0520d48a61e
Revises: a3c9e1f4b682
Create Date: 2026-10-20 21:14:34.358248
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e0520d48a61e'
down_revision = 'a3c9e1f4b682'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'generation_timings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('conversation_id', sa.String(), nullable=False),
        sa.Column('outcome', sa.String(), nullable=False),
        sa.Column('stages', sa.JSON(), nullable=False),
        sa.Column('total_sec', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('generation_timings', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_generation_timings_conversation_id'), ['conversation_id'], unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('generation_timings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generation_timings_conversation_id'))
    op.drop_table('generation_timings')
