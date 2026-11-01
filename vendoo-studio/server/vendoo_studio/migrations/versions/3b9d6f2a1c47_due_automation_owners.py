"""Let the trusted web process find sellers with due automations

Revision ID: 3b9d6f2a1c47
Revises: c7e2a41d9b05
Create Date: 2026-10-31 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3b9d6f2a1c47'
down_revision = 'c7e2a41d9b05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Returns owner IDs only. The scheduler then enters each seller's own
    # row-secured session; HTTP handlers (anteroom_seller) cannot call this.
    op.execute("""
        CREATE FUNCTION anteroom_private.due_automation_owners()
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT automation.user_id::text FROM public.automations AS automation
                WHERE automation.status = 'active' AND automation.next_run_at <= (now() AT TIME ZONE 'utc')
            UNION
            SELECT run.user_id::text FROM public.automation_runs AS run
                WHERE run.status IN ('running', 'waiting')
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.due_automation_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.due_automation_owners() TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION anteroom_private.due_automation_owners()')
