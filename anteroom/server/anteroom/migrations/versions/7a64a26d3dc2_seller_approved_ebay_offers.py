"""seller approved eBay offers

Revision ID: 7a64a26d3dc2
Revises: 0fd9a293057b
Create Date: 2026-10-24 00:17:27.809480
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7a64a26d3dc2'
down_revision = '0fd9a293057b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ebay_offer_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('account_id', sa.String(), nullable=False),
    sa.Column('listing_id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('discount_percent', sa.Integer(), nullable=False),
    sa.Column('minimum_price_cents', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'listing_id', name='uq_ebay_offer_rule_listing')
    )
    op.create_table('ebay_offer_attempts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('rule_id', sa.String(), nullable=False),
    sa.Column('current_price_cents', sa.Integer(), nullable=False),
    sa.Column('offer_price_cents', sa.Integer(), nullable=False),
    sa.Column('discount_percent', sa.Integer(), nullable=False),
    sa.Column('minimum_price_cents', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('note', sa.String(), nullable=True),
    sa.Column('recipient_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['rule_id'], ['ebay_offer_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ebay_offer_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ebay_offer_attempts_rule_id'), ['rule_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('ebay_offer_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ebay_offer_attempts_rule_id'))

    op.drop_table('ebay_offer_attempts')
    op.drop_table('ebay_offer_rules')
