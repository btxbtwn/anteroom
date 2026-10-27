"""Keep the original photo under non-destructive edits

Revision ID: a7e3c1f09b42
Revises: a7c3e91f5d20
Create Date: 2026-10-26 20:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a7e3c1f09b42'
down_revision = 'a7c3e91f5d20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_filename', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('edits', sa.JSON(), nullable=True))
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('etsy_image_checksums', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.drop_column('etsy_image_checksums')
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.drop_column('edits')
        batch_op.drop_column('source_filename')
