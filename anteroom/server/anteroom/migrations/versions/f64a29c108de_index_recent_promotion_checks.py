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


INDEX_NAME = 'ix_listing_promotions_rule_recent'


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    # Legacy SQLite adoption may already have created this model index.
    if not postgres and INDEX_NAME in {
        index['name'] for index in sa.inspect(bind).get_indexes('listing_promotions')
    }:
        return
    with op.get_context().autocommit_block() if postgres else nullcontext():
        if postgres:
            # A failed concurrent build (for example on lock_timeout) leaves an
            # INVALID index behind without stamping the revision. Reuse a valid
            # index on retry and drop any invalid leftover before rebuilding.
            valid = op.get_bind().execute(sa.text(
                'SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)'
            ), {'name': INDEX_NAME}).scalar_one_or_none()
            if valid is True:
                return
            if valid is False:
                op.drop_index(INDEX_NAME, table_name='listing_promotions', postgresql_concurrently=True)
        op.create_index(INDEX_NAME, 'listing_promotions',
                        ['automation_id', 'marketplace', 'action', 'created_at'], postgresql_concurrently=True)


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == 'postgresql'
    with op.get_context().autocommit_block() if postgres else nullcontext():
        op.drop_index(INDEX_NAME, table_name='listing_promotions',
                      if_exists=postgres, postgresql_concurrently=True)
