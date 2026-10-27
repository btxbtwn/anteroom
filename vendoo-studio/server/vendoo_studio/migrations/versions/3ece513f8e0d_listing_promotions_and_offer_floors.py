"""Listing promotions and offer floors

Revision ID: 3ece513f8e0d
Revises: 151f7674a7c7
Create Date: 2026-10-26 19:16:06.696082
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3ece513f8e0d'
down_revision = '151f7674a7c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('listing_promotions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('batch_id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('marketplace', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('listing_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('price_cents', sa.Integer(), nullable=True),
    sa.Column('offer_price_cents', sa.Integer(), nullable=True),
    sa.Column('floor_cents', sa.Integer(), nullable=True),
    sa.Column('discount_percent', sa.Integer(), nullable=True),
    sa.Column('shipping_options', sa.JSON(), nullable=True),
    sa.Column('shipping_discount_id', sa.String(), nullable=True),
    sa.Column('shipping_discount_label', sa.String(), nullable=True),
    sa.Column('like_count', sa.Integer(), nullable=True),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('note', sa.String(), nullable=True),
    sa.Column('marketplace_message', sa.String(), nullable=True),
    sa.Column('job_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('listing_promotions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listing_promotions_batch_id'), ['batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listing_promotions_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listing_promotions_listing_id'), ['listing_id'], unique=False)

    op.create_table('marketplace_offer_floors',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('marketplace', sa.String(), nullable=False),
    sa.Column('listing_id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('floor_cents', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('marketplace', 'listing_id', name='uq_marketplace_offer_floor_listing')
    )


def downgrade() -> None:
    op.drop_table('marketplace_offer_floors')
    with op.batch_alter_table('listing_promotions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listing_promotions_listing_id'))
        batch_op.drop_index(batch_op.f('ix_listing_promotions_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_listing_promotions_batch_id'))

    op.drop_table('listing_promotions')
