"""Index a rule's recent promotion checks.

Revision ID: f64a29c108de
Revises: d7be65e14c2f
"""
from contextlib import nullcontext

from alembic import op
import sqlalchemy as sa


revision = 'f64a29c108de'
down_revision = 'd7be65e14c2f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    # Legacy SQLite adoption may already have created this model index.
    if not postgres and 'ix_listing_promotions_rule_recent' in {
        index['name'] for index in sa.inspect(bind).get_indexes('listing_promotions')
    }:
        return
    with op.get_context().autocommit_block() if postgres else nullcontext():
        op.create_index('ix_listing_promotions_rule_recent', 'listing_promotions',
                        ['automation_id', 'marketplace', 'action', 'created_at'], postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block() if op.get_bind().dialect.name == 'postgresql' else nullcontext():
        op.drop_index('ix_listing_promotions_rule_recent', table_name='listing_promotions', postgresql_concurrently=True)
