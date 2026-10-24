"""marketplace sales reported through Anteroom's relay

Sales eBay and Etsy report directly, shown on the listing until the seller
has delisted it elsewhere and marks it done.

Revision ID: 0fd9a293057b
Revises: d3f6a2c81e94
Create Date: 2026-10-23 21:50:17.291885
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0fd9a293057b'
down_revision = 'd3f6a2c81e94'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('marketplace_sales',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('marketplace', sa.String(), nullable=False),
    sa.Column('order_id', sa.String(), nullable=False),
    sa.Column('line_id', sa.String(), nullable=False),
    sa.Column('listing_id', sa.String(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('sold_at', sa.DateTime(), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('marketplace', 'order_id', 'line_id', name='uq_marketplace_sales_order_line')
    )
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_marketplace_sales_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index('ix_marketplace_sales_state', ['state'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('marketplace_sales', schema=None) as batch_op:
        batch_op.drop_index('ix_marketplace_sales_state')
        batch_op.drop_index(batch_op.f('ix_marketplace_sales_conversation_id'))

    op.drop_table('marketplace_sales')
