"""index offer analytics and date ebay sends

Revision ID: fd7c3a5d05ea
Revises: f64a29c108de
Create Date: 2026-11-16 04:56:21.741302
"""
from __future__ import annotations

from contextlib import nullcontext

from alembic import op
import sqlalchemy as sa


revision = 'fd7c3a5d05ea'
down_revision = 'f64a29c108de'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Never assign preview creation dates to historical sends.
    # Legacy adoption creates missing tables from the current models first.
    if 'sent_at' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('ebay_offer_attempts')}:
        op.add_column('ebay_offer_attempts', sa.Column('sent_at', sa.DateTime(), nullable=True))
    postgres = op.get_bind().dialect.name == 'postgresql'
    for table in ('ebay_offer_attempts', 'listing_promotions'):
        name = f'ix_{table}_seller_sent'
        if not postgres and name in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}:
            continue
        with op.get_context().autocommit_block() if postgres else nullcontext():
            if postgres:
                # Each concurrent build commits independently. A retry after a
                # later failure must reuse completed indexes and repair invalid
                # leftovers, which CREATE INDEX IF NOT EXISTS would preserve.
                valid = op.get_bind().execute(sa.text(
                    "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
                ), {"name": name}).scalar_one_or_none()
                if valid is True:
                    continue
                if valid is False:
                    op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(name, table, ['user_id', 'sent_at'], postgresql_concurrently=True)


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == 'postgresql'
    for table in ('ebay_offer_attempts', 'listing_promotions'):
        with op.get_context().autocommit_block() if postgres else nullcontext():
            op.drop_index(f'ix_{table}_seller_sent', table_name=table, postgresql_concurrently=True)
    op.drop_column('ebay_offer_attempts', 'sent_at')
