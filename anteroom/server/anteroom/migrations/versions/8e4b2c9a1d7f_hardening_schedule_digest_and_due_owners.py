"""Harden scheduled publishing digest and abandoned-publish owner discovery.

Adds ``listings.scheduled_content_digest`` so scheduled publishing verifies that
photos, listing videos, and active pricing rules still match what the seller
confirmed at schedule time. Updates PostgreSQL ``anteroom_private.due_automation_owners()``
so abandoned ``publishing`` claims (>15 min) are recovered even when no other
scheduled draft is due, and throttles idle auto-relist owner wakeups to 5 minutes.

Revision ID: 8e4b2c9a1d7f
Revises: 3f6a9d2c7b14
Create Date: 2026-11-22 20:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '8e4b2c9a1d7f'
down_revision = '3f6a9d2c7b14'
branch_labels = None
depends_on = None


def _add_column_if_missing(inspector, table_name: str, column: sa.Column) -> None:
    if not inspector.has_table(table_name):
        return
    existing = {col['name'] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def _set_due_owners(bind, extra: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION anteroom_private.due_automation_owners()
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
        SELECT automation.user_id::text FROM public.automations AS automation
            WHERE automation.status = 'active' AND automation.next_run_at <= (now() AT TIME ZONE 'utc')
        UNION
        SELECT run.user_id::text FROM public.automation_runs AS run
            WHERE run.status IN ('running', 'waiting')
        {extra}
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.due_automation_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.due_automation_owners() TO anteroom_api')


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_content_digest', sa.String(64), nullable=True))

    if bind.dialect.name != 'postgresql':
        return

    _set_due_owners(bind, """
        UNION
        SELECT listing.user_id::text FROM public.listings AS listing
            WHERE (
                listing.ready_to_list AND listing.scheduled_status = 'scheduled'
                AND listing.scheduled_confirmed_at IS NOT NULL
                AND listing.scheduled_publish_at <= (now() AT TIME ZONE 'utc')
            ) OR (
                listing.scheduled_status = 'publishing'
                AND listing.updated_at IS NOT NULL
                AND listing.updated_at <= (now() AT TIME ZONE 'utc') - interval '15 minutes'
            )
        UNION
        SELECT rule.user_id::text FROM public.marketplace_relist_rules AS rule
            WHERE rule.enabled
                AND (rule.last_run_at IS NULL OR rule.last_run_at <= (now() AT TIME ZONE 'utc') - interval '5 minutes')
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _set_due_owners(bind, """
            UNION
            SELECT listing.user_id::text FROM public.listings AS listing
                WHERE listing.ready_to_list AND listing.scheduled_status = 'scheduled'
                    AND listing.scheduled_confirmed_at IS NOT NULL
                    AND listing.scheduled_publish_at <= (now() AT TIME ZONE 'utc')
            UNION
            SELECT rule.user_id::text FROM public.marketplace_relist_rules AS rule WHERE rule.enabled
        """)

    inspector = sa.inspect(bind)
    if inspector.has_table('listings') and 'scheduled_content_digest' in {col['name'] for col in inspector.get_columns('listings')}:
        with op.batch_alter_table('listings') as batch_op:
            batch_op.drop_column('scheduled_content_digest')
