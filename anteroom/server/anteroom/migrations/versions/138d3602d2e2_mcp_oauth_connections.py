"""Add apps connected to MCP by signing in (Claude, ChatGPT, Gemini)

Revision ID: 138d3602d2e2
Revises: 5c2e8f1a9d37
Create Date: 2026-11-23 06:30:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "138d3602d2e2"
down_revision = "5c2e8f1a9d37"
branch_labels = None
depends_on = None

TABLE = "mcp_oauth_connections"
# True unless the request's JWT was issued to a connected app (Supabase's OAuth server).
NOT_CONNECTED_APP = (
    "((SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'client_id') IS NULL)"
)
OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    "AND user_id <> '00000000-0000-0000-0000-000000000000'"
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "mcp_oauth_connections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("client_id", sa.String(128), nullable=False),
            sa.Column("client_name", sa.String(200), nullable=True),
            sa.Column("permissions", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("signed_out_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "client_id", name="uq_mcp_oauth_connections_owner_client"),
        )
        op.create_index("ix_mcp_oauth_connections_user_id", "mcp_oauth_connections", ["user_id"])

    if bind.dialect.name != "postgresql":
        return

    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY seller_owner ON "{TABLE}" AS RESTRICTIVE FOR ALL TO PUBLIC '
        f"USING ({OWNER}) WITH CHECK ({OWNER})"
    )
    op.execute(
        f'CREATE POLICY seller_access ON "{TABLE}" FOR ALL TO anteroom_seller '
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f'REVOKE ALL ON "{TABLE}" FROM PUBLIC')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABLE}" TO anteroom_seller')
    for role in ("anon", "authenticated"):
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
            op.execute(f'REVOKE ALL ON "{TABLE}" FROM {role}')
    op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{TABLE}"')
    op.execute(
        f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public."{TABLE}" '
        "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
    )

    # A connected app's token is an ordinary project JWT with a client_id claim,
    # and Supabase checks it itself on Storage and Realtime. The app reaches
    # Anteroom only through /api/mcp, so refuse it there. Restrictive policies
    # of their own, so a later rewrite of the owner policies cannot drop this.
    if not bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")):
        return
    op.execute(
        f"CREATE POLICY seller_subscriber_not_connected_app ON seller_changes AS RESTRICTIVE "
        f"FOR SELECT TO authenticated USING ({NOT_CONNECTED_APP})"
    )
    if bind.scalar(sa.text("SELECT to_regclass('realtime.messages')")) is not None:
        op.execute(
            "CREATE POLICY anteroom_seller_changes_not_connected_app ON realtime.messages AS RESTRICTIVE "
            "FOR SELECT TO authenticated USING ("
            "(SELECT realtime.topic()) IS NULL OR (SELECT realtime.topic()) NOT LIKE 'seller-changes:%' "
            f"OR {NOT_CONNECTED_APP})"
        )
    if bind.scalar(sa.text("SELECT to_regclass('storage.objects')")) is not None:
        op.execute(
            "CREATE POLICY anteroom_photo_not_connected_app ON storage.objects AS RESTRICTIVE "
            f"FOR ALL TO authenticated USING (bucket_id <> 'anteroom-photos' OR {NOT_CONNECTED_APP}) "
            f"WITH CHECK (bucket_id <> 'anteroom-photos' OR {NOT_CONNECTED_APP})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS seller_subscriber_not_connected_app ON seller_changes")
        if bind.scalar(sa.text("SELECT to_regclass('realtime.messages')")) is not None:
            op.execute("DROP POLICY IF EXISTS anteroom_seller_changes_not_connected_app ON realtime.messages")
        if bind.scalar(sa.text("SELECT to_regclass('storage.objects')")) is not None:
            op.execute("DROP POLICY IF EXISTS anteroom_photo_not_connected_app ON storage.objects")
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
