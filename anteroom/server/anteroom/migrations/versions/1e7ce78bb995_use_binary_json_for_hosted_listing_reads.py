"""Use binary JSON for hosted listing reads

Revision ID: 1e7ce78bb995
Revises: e5b1d7a3c920
Create Date: 2026-11-02 18:27:33.577186
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '1e7ce78bb995'
down_revision = 'e5b1d7a3c920'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    # Fail rather than hold up readers behind a long-running transaction.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column('listing_revisions', 'listing_json', existing_type=sa.JSON(),
                    type_=postgresql.JSONB(), existing_nullable=False,
                    postgresql_using='listing_json::jsonb')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column('listing_revisions', 'listing_json', existing_type=postgresql.JSONB(),
                    type_=sa.JSON(), existing_nullable=False,
                    postgresql_using='listing_json::json')
