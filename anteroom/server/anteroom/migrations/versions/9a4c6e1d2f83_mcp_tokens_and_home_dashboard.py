"""Add MCP access tokens, seller goals, and Needs Attention dismissals

Revision ID: 9a4c6e1d2f83
Revises: 8580524be7de
Create Date: 2026-11-22 10:40:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9a4c6e1d2f83"
down_revision = "8580524be7de"
branch_labels = None
depends_on = None

OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    "AND user_id <> '00000000-0000-0000-0000-000000000000'"
)

NEW_TABLES = (
    "mcp_access_tokens",
    "seller_goals",
    "attention_dismissals",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "mcp_access_tokens" not in tables:
        op.create_table(
            "mcp_access_tokens",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("token_prefix", sa.String(24), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_mcp_access_tokens_user_id", "mcp_access_tokens", ["user_id"])
        op.create_index("ix_mcp_access_tokens_token_hash", "mcp_access_tokens", ["token_hash"], unique=True)
        op.create_index("ix_mcp_access_tokens_owner_created", "mcp_access_tokens", ["user_id", "created_at"])

    if "seller_goals" not in tables:
        op.create_table(
            "seller_goals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("period", sa.String(20), nullable=False),
            sa.Column("metric", sa.String(32), nullable=False),
            sa.Column("target", sa.Float(), nullable=False),
            sa.Column("label", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "period", "metric", name="uq_seller_goals_owner_period_metric"),
        )
        op.create_index("ix_seller_goals_user_id", "seller_goals", ["user_id"])

    if "attention_dismissals" not in tables:
        op.create_table(
            "attention_dismissals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("item_key", sa.String(200), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("dismissed_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "item_key", name="uq_attention_dismissals_owner_key"),
        )
        op.create_index("ix_attention_dismissals_user_id", "attention_dismissals", ["user_id"])
        op.create_index("ix_attention_dismissals_owner_expires", "attention_dismissals", ["user_id", "expires_at"])

    if bind.dialect.name != "postgresql":
        return

    for table in NEW_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY seller_owner ON "{table}" AS RESTRICTIVE FOR ALL TO PUBLIC '
            f"USING ({OWNER}) WITH CHECK ({OWNER})"
        )
        op.execute(
            f'CREATE POLICY seller_access ON "{table}" FOR ALL TO anteroom_seller '
            "USING (true) WITH CHECK (true)"
        )
        op.execute(f'REVOKE ALL ON "{table}" FROM PUBLIC')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO anteroom_seller')
        for role in ("anon", "authenticated"):
            if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
                op.execute(f'REVOKE ALL ON "{table}" FROM {role}')
        op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{table}"')
        op.execute(
            f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public."{table}" '
            "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
        )

    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.authenticate_mcp_token(expected_hash text)
        RETURNS TABLE(token_id text, owner_id text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            found_id text;
            found_owner text;
        BEGIN
            IF expected_hash IS NULL OR length(expected_hash) <> 64 THEN
                RETURN;
            END IF;
            SELECT tok.id::text, tok.user_id::text
            INTO found_id, found_owner
            FROM public.mcp_access_tokens AS tok
            WHERE tok.token_hash = expected_hash
              AND tok.revoked_at IS NULL
              AND (tok.expires_at IS NULL OR tok.expires_at > now())
              AND tok.user_id <> '00000000-0000-0000-0000-000000000000'
            LIMIT 1;
            IF found_id IS NULL THEN
                RETURN;
            END IF;
            IF NOT anteroom_private.account_write_allowed(found_owner) THEN
                RETURN;
            END IF;
            UPDATE public.mcp_access_tokens
            SET last_used_at = now()
            WHERE id = found_id;
            token_id := found_id;
            owner_id := found_owner;
            RETURN NEXT;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.authenticate_mcp_token(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'anteroom_api'")):
        op.execute("GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api")
        op.execute("GRANT EXECUTE ON FUNCTION anteroom_private.authenticate_mcp_token(text) TO anteroom_api")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.authenticate_mcp_token(text)")

    for table in reversed(NEW_TABLES):
        if table in tables:
            op.drop_table(table)
