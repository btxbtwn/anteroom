"""Let the trusted web process find sellers with unfinished marketplace actions

Revision ID: a6f3d1c8e925
Revises: db9e420f870b
Create Date: 2026-11-01 14:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a6f3d1c8e925'
down_revision = 'db9e420f870b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Returns owner IDs only: sellers with a running job, or a marketplace
    # action still running or unconfirmed. The web job watchdog then enters
    # each seller's own row-secured session; HTTP handlers cannot call this.
    op.execute("""
        CREATE FUNCTION anteroom_private.unsettled_job_owners()
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT job.user_id::text FROM public.jobs AS job
                WHERE job.status IN ('queued', 'awaiting_extension', 'dispatched')
            UNION
            SELECT listing.user_id::text FROM public.marketplace_listings AS listing
                WHERE listing.state IN ('saving', 'publishing', 'updating', 'unknown')
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.unsettled_job_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.unsettled_job_owners() TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION anteroom_private.unsettled_job_owners()')
