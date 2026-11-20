"""Store listing videos separately from photos

Revision ID: 5909d3d92ccf
Revises: 67d4b23e9a7a
Create Date: 2026-10-26 14:17:46.217867
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '5909d3d92ccf'
down_revision = '67d4b23e9a7a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('listing_videos',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=False),
    sa.Column('original_filename', sa.String(), nullable=False),
    sa.Column('stored_filename', sa.String(), nullable=False),
    sa.Column('mime_type', sa.String(), nullable=False),
    sa.Column('checksum', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('listing_videos', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listing_videos_conversation_id'), ['conversation_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('listing_videos', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listing_videos_conversation_id'))

    op.drop_table('listing_videos')
