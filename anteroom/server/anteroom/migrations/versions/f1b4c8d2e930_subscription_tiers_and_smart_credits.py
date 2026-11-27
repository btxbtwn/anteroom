"""Hosted subscription tiers and Smart Credit usage ledger.

Revision ID: f1b4c8d2e930
Revises: d5e1b7a3c962
Create Date: 2026-11-26 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1b4c8d2e930"
down_revision = "d5e1b7a3c962"
branch_labels = None
depends_on = None

LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"
UNLIMITED_SEED_EMAILS = ("cristianarielcanizares@gmail.com", "xyzcanizarez@gmail.com")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name != "sqlite" or not inspector.has_table("seller_plans"):
        op.create_table(
            "seller_plans",
            sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
            sa.Column("plan_id", sa.String(32), nullable=False, server_default="free"),
            sa.Column("billing_interval", sa.String(16), nullable=False, server_default="monthly"),
            sa.Column("assigned_by", sa.String(64), nullable=True),
            sa.Column("note", sa.String(256), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("user_id", name="pk_seller_plans"),
        )
        op.create_index("ix_seller_plans_user_id", "seller_plans", ["user_id"])

    if bind.dialect.name != "sqlite" or not inspector.has_table("plan_usage_events"):
        op.create_table(
            "plan_usage_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
            sa.Column("period", sa.String(7), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("credits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conversation_id", sa.String(), nullable=True),
            sa.Column("reference_id", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_plan_usage_events_user_id", "plan_usage_events", ["user_id"])
        op.create_index(
            "ix_plan_usage_events_owner_period",
            "plan_usage_events",
            ["user_id", "period", "kind"],
        )
        op.create_index(
            "ix_plan_usage_events_owner_period_conv",
            "plan_usage_events",
            ["user_id", "period", "kind", "conversation_id"],
        )

    if bind.dialect.name != "postgresql":
        return

    owner = (
        "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
        f"AND user_id <> '{LOCAL_OWNER}'"
    )
    roles_to_revoke = [
        role for role in ("anon", "authenticated")
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
    ]

    # seller_plans: sellers can read their own plan and account deletion can clear it,
    # but sellers can never insert or update their own tier.
    op.execute("ALTER TABLE public.seller_plans ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.seller_plans FROM PUBLIC")
    for role in roles_to_revoke:
        op.execute(f"REVOKE ALL ON public.seller_plans FROM {role}")
    op.execute("GRANT SELECT, DELETE ON public.seller_plans TO anteroom_seller")
    op.execute(
        f"CREATE POLICY seller_owner ON public.seller_plans AS RESTRICTIVE FOR ALL TO PUBLIC "
        f"USING ({owner}) WITH CHECK ({owner})"
    )
    op.execute(
        "CREATE POLICY seller_access ON public.seller_plans FOR ALL TO anteroom_seller "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.seller_plans "
        "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
    )

    # plan_usage_events: append-only per-seller ledger; DELETE lets account deletion clear it.
    op.execute("ALTER TABLE public.plan_usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.plan_usage_events FROM PUBLIC")
    for role in roles_to_revoke:
        op.execute(f"REVOKE ALL ON public.plan_usage_events FROM {role}")
    op.execute("GRANT SELECT, INSERT, DELETE ON public.plan_usage_events TO anteroom_seller")
    op.execute(
        f"CREATE POLICY seller_owner ON public.plan_usage_events AS RESTRICTIVE FOR ALL TO PUBLIC "
        f"USING ({owner}) WITH CHECK ({owner})"
    )
    op.execute(
        "CREATE POLICY seller_access ON public.plan_usage_events FOR ALL TO anteroom_seller "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.plan_usage_events "
        "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
    )

    # Operator-only plan assignment and reporting functions.
    op.execute("""
        CREATE FUNCTION anteroom_private.set_seller_plan(
            p_user_id text,
            p_plan_id text,
            p_billing_interval text,
            p_assigned_by text,
            p_note text
        )
        RETURNS TABLE (
            user_id text,
            plan_id text,
            billing_interval text,
            assigned_by text,
            note text,
            created_at timestamptz,
            updated_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            v_now timestamptz := now();
        BEGIN
            INSERT INTO public.seller_plans (
                user_id, plan_id, billing_interval, assigned_by, note, created_at, updated_at
            )
            VALUES (p_user_id, p_plan_id, p_billing_interval, p_assigned_by, p_note, v_now, v_now)
            ON CONFLICT ON CONSTRAINT pk_seller_plans DO UPDATE SET
                plan_id = EXCLUDED.plan_id,
                billing_interval = EXCLUDED.billing_interval,
                assigned_by = EXCLUDED.assigned_by,
                note = EXCLUDED.note,
                updated_at = v_now;

            RETURN QUERY
            SELECT p.user_id::text, p.plan_id::text, p.billing_interval::text,
                   p.assigned_by::text, p.note::text, p.created_at, p.updated_at
            FROM public.seller_plans AS p
            WHERE p.user_id = p_user_id;
        END;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION anteroom_private.list_seller_plans(p_period text)
        RETURNS TABLE (
            user_id text,
            plan_id text,
            billing_interval text,
            assigned_by text,
            note text,
            created_at timestamptz,
            updated_at timestamptz,
            credits_used integer,
            listed_items_used integer
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            WITH owners AS (
                SELECT sp.user_id FROM public.seller_plans AS sp
                UNION
                SELECT DISTINCT pe.user_id FROM public.plan_usage_events AS pe WHERE pe.period = p_period
            ),
            credit_totals AS (
                SELECT pe.user_id, GREATEST(0, COALESCE(SUM(pe.credits), 0))::integer AS credits_used
                FROM public.plan_usage_events AS pe
                WHERE pe.period = p_period AND pe.kind <> 'listed_item'
                GROUP BY pe.user_id
            ),
            item_totals AS (
                SELECT pe.user_id, COUNT(DISTINCT pe.conversation_id)::integer AS listed_items_used
                FROM public.plan_usage_events AS pe
                WHERE pe.period = p_period AND pe.kind = 'listed_item' AND pe.conversation_id IS NOT NULL
                GROUP BY pe.user_id
            )
            SELECT o.user_id::text,
                   COALESCE(sp.plan_id, 'free')::text,
                   COALESCE(sp.billing_interval, 'monthly')::text,
                   sp.assigned_by::text,
                   sp.note::text,
                   sp.created_at,
                   sp.updated_at,
                   COALESCE(ct.credits_used, 0)::integer,
                   COALESCE(it.listed_items_used, 0)::integer
            FROM owners AS o
            LEFT JOIN public.seller_plans AS sp ON sp.user_id = o.user_id
            LEFT JOIN credit_totals AS ct ON ct.user_id = o.user_id
            LEFT JOIN item_totals AS it ON it.user_id = o.user_id
            ORDER BY o.user_id ASC;
        $$;
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION anteroom_private.set_seller_plan(text, text, text, text, text) "
        "FROM PUBLIC, anteroom_seller"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION anteroom_private.list_seller_plans(text) "
        "FROM PUBLIC, anteroom_seller"
    )
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute("GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api")
        op.execute(
            "GRANT EXECUTE ON FUNCTION anteroom_private.set_seller_plan(text, text, text, text, text) "
            "TO anteroom_api"
        )
        op.execute(
            "GRANT EXECUTE ON FUNCTION anteroom_private.list_seller_plans(text) "
            "TO anteroom_api"
        )

    # Seed the two approved owner/unlimited accounts if Supabase's auth.users table is accessible.
    emails_sql = ", ".join(f"'{email}'" for email in UNLIMITED_SEED_EMAILS)
    op.execute(f"""
        DO $$
        BEGIN
            INSERT INTO public.seller_plans (
                user_id, plan_id, billing_interval, assigned_by, note, created_at, updated_at
            )
            SELECT u.id::text, 'unlimited', 'monthly', 'seed', 'Approved unlimited account', now(), now()
            FROM auth.users AS u
            WHERE lower(u.email) IN ({emails_sql})
            ON CONFLICT (user_id) DO NOTHING;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_table OR invalid_schema_name THEN
                NULL;
        END;
        $$;
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.list_seller_plans(text)")
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.set_seller_plan(text, text, text, text, text)")
    op.drop_table("plan_usage_events")
    op.drop_table("seller_plans")
