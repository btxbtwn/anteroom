"""Let the hosted API read which schema revision its database is at.

Revision ID: 7f3a9c2e5d14
Revises: e3bea75f1b9b
Create Date: 2026-11-06 12:00:00.000000

``alembic_version`` has row security with no policy, so the runtime login
reads nothing from it. ``/api/ready`` compares this value with the revision
the running build was made for.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7f3a9c2e5d14'
down_revision = 'e3bea75f1b9b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE FUNCTION anteroom_private.schema_revision() RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT version_num FROM public.alembic_version LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.schema_revision() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.schema_revision() TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION IF EXISTS anteroom_private.schema_revision()')
