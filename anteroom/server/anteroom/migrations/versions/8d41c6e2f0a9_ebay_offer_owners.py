"""Let the trusted web process find sellers with eBay offers to check

Revision ID: 8d41c6e2f0a9
Revises: 3b9d6f2a1c47
Create Date: 2026-11-01 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '8d41c6e2f0a9'
down_revision = '3b9d6f2a1c47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Returns owner IDs only: sellers with automatic offers turned on, and
    # sellers whose send was cut off by a restart. The offer timer then enters
    # each seller's own row-secured session; HTTP handlers cannot call this.
    op.execute("""
        CREATE FUNCTION anteroom_private.ebay_offer_owners()
        RETURNS TABLE(owner_id text, interrupted boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT owner_id, bool_or(interrupted) FROM (
                SELECT policy.user_id::text AS owner_id, false AS interrupted
                    FROM public.ebay_offer_policies AS policy WHERE policy.enabled
                UNION ALL
                SELECT rule.user_id::text, false FROM public.ebay_offer_rules AS rule WHERE rule.enabled
                UNION ALL
                SELECT attempt.user_id::text, true FROM public.ebay_offer_attempts AS attempt
                    WHERE attempt.state = 'sending'
            ) AS owners GROUP BY owner_id
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.ebay_offer_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.ebay_offer_owners() TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION anteroom_private.ebay_offer_owners()')
