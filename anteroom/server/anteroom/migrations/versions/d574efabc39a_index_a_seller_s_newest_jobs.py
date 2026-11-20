"""Index a seller's newest jobs

Revision ID: d574efabc39a
Revises: a6f3d1c8e925
Create Date: 2026-11-01 23:28:11.879096
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd574efabc39a'
down_revision = 'a6f3d1c8e925'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The jobs list asks for a seller's newest jobs. Without this index Postgres
    # sorted all of them on every request.
    # Legacy adoption may already have created this index from model metadata.
    if 'ix_jobs_seller_created_at' not in {index['name'] for index in sa.inspect(op.get_bind()).get_indexes('jobs')}:
        op.create_index('ix_jobs_seller_created_at', 'jobs', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_seller_created_at')
