"""Managed OpenRouter and Photoroom usage ledger for beta invoicing.

Revision ID: a8f1c4e29b70
Revises: 5c2e8f1a9d37
Create Date: 2026-11-23 06:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a8f1c4e29b70"
down_revision = "5c2e8f1a9d37"
branch_labels = None
depends_on = None

LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and sa.inspect(bind).has_table("api_usage_events"):
        return
    op.create_table(
        "api_usage_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("model_or_edit", sa.String(256), nullable=False, server_default=""),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cost_source", sa.String(32), nullable=False),
        sa.Column("units", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_api_usage_events_user_id", "api_usage_events", ["user_id"])
    op.create_index(
        "ix_api_usage_events_owner_created",
        "api_usage_events",
        ["user_id", "created_at", "id"],
    )
    if bind.dialect.name != "postgresql":
        return
    owner = (
        "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
        f"AND user_id <> '{LOCAL_OWNER}'"
    )
    op.execute("ALTER TABLE public.api_usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.api_usage_events FROM PUBLIC")
    for role in ("anon", "authenticated"):
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
            op.execute(f"REVOKE ALL ON public.api_usage_events FROM {role}")
    op.execute("GRANT SELECT, INSERT ON public.api_usage_events TO anteroom_seller")
    op.execute(
        f"CREATE POLICY seller_owner ON public.api_usage_events AS RESTRICTIVE FOR ALL TO PUBLIC "
        f"USING ({owner}) WITH CHECK ({owner})"
    )
    op.execute(
        "CREATE POLICY seller_access ON public.api_usage_events FOR ALL TO anteroom_seller "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.api_usage_events "
        "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
    )
    # Operator invoicing reads every seller; sellers keep insert-only isolation above.
    op.execute("""
        CREATE FUNCTION anteroom_private.api_usage_events(
            p_since timestamptz, p_until timestamptz, p_seller text
        )
        RETURNS TABLE (
            id text,
            user_id text,
            provider text,
            kind text,
            model_or_edit text,
            cost_usd double precision,
            cost_source text,
            units integer,
            input_tokens integer,
            output_tokens integer,
            conversation_id text,
            provider_request_id text,
            created_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            SELECT e.id::text, e.user_id::text, e.provider::text, e.kind::text, e.model_or_edit::text,
                   e.cost_usd::double precision, e.cost_source::text, e.units, e.input_tokens,
                   e.output_tokens, e.conversation_id, e.provider_request_id,
                   e.created_at::timestamptz
            FROM public.api_usage_events AS e
            WHERE e.created_at >= p_since AND e.created_at < p_until
              AND (p_seller IS NULL OR e.user_id = p_seller)
            ORDER BY e.created_at ASC, e.id ASC
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION anteroom_private.api_usage_events(timestamptz, timestamptz, text) "
               "FROM PUBLIC, anteroom_seller")
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute("GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api")
        op.execute(
            "GRANT EXECUTE ON FUNCTION anteroom_private.api_usage_events(timestamptz, timestamptz, text) "
            "TO anteroom_api"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.api_usage_events(timestamptz, timestamptz, text)")
    op.drop_table("api_usage_events")
