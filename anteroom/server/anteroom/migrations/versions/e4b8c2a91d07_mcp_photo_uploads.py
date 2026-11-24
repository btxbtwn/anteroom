"""Add single-use photo upload links for MCP clients

Revision ID: e4b8c2a91d07
Revises: e3b71c5a90d4
Create Date: 2026-11-24 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e4b8c2a91d07"
down_revision = "e3b71c5a90d4"
branch_labels = None
depends_on = None

TABLE = "mcp_photo_uploads"
OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    "AND user_id <> '00000000-0000-0000-0000-000000000000'"
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "mcp_photo_uploads",
            sa.Column("id", sa.String(32), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_mcp_photo_uploads_user_id", "mcp_photo_uploads", ["user_id"])
        op.create_index("ix_mcp_photo_uploads_owner_expires", "mcp_photo_uploads", ["user_id", "expires_at"])

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

    # The upload arrives without a seller session: only the link's secret.
    # Spend the link and name its seller and listing in one statement, so the
    # same link can never upload twice, and nothing else about the table is
    # reachable before the seller is known.
    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.consume_mcp_photo_upload(upload_id text, expected_hash text)
        RETURNS TABLE(owner_id text, listing_id text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
        DECLARE
            found_owner text;
            found_listing text;
        BEGIN
            IF upload_id IS NULL OR length(upload_id) <> 32
               OR expected_hash IS NULL OR length(expected_hash) <> 64 THEN
                RETURN;
            END IF;
            SELECT up.user_id::text INTO found_owner
            FROM public.mcp_photo_uploads AS up
            WHERE up.id = upload_id;
            IF found_owner IS NULL OR found_owner = '00000000-0000-0000-0000-000000000000'
               OR NOT anteroom_private.account_write_allowed(found_owner) THEN
                RETURN;
            END IF;
            UPDATE public.mcp_photo_uploads AS up
            SET used_at = now()
            WHERE up.id = upload_id
              AND up.token_hash = expected_hash
              AND up.used_at IS NULL
              AND up.expires_at > now()
            RETURNING up.conversation_id INTO found_listing;
            IF found_listing IS NULL THEN
                RETURN;
            END IF;
            owner_id := found_owner;
            listing_id := found_listing;
            RETURN NEXT;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.consume_mcp_photo_upload(text, text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'anteroom_api'")):
        op.execute("GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api")
        op.execute("GRANT EXECUTE ON FUNCTION anteroom_private.consume_mcp_photo_upload(text, text) TO anteroom_api")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.consume_mcp_photo_upload(text, text)")
    if TABLE in set(sa.inspect(bind).get_table_names()):
        op.drop_table(TABLE)
