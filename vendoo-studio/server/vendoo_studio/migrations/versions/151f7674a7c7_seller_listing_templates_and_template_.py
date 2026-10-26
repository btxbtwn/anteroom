"""Seller listing templates and template draft receipts

Revision ID: 151f7674a7c7
Revises: 2af6da7c6db1
Create Date: 2026-10-26 07:17:40.386667
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '151f7674a7c7'
down_revision = '2af6da7c6db1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('listing_templates',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('fields', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('listing_copy_receipts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_kind', sa.String(), server_default='listing', nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('listing_copy_receipts', schema=None) as batch_op:
        batch_op.drop_column('source_kind')

    op.drop_table('listing_templates')
