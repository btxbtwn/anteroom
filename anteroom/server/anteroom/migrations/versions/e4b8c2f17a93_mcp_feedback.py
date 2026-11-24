"""Keep feedback a seller's AI assistant sends to the Anteroom team

Revision ID: e4b8c2f17a93
Revises: f2a8d5c3e917, e3a7c91d5b20
Create Date: 2026-11-24 12:00:00.000000

Also joins the two heads main gained from #2050 and #2041.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e4b8c2f17a93"
down_revision = ("f2a8d5c3e917", "e3a7c91d5b20")
branch_labels = None
depends_on = None

TABLE = "mcp_feedback"
OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    "AND user_id <> '00000000-0000-0000-0000-000000000000'"
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "mcp_feedback",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("conversation_id", sa.String(64), nullable=True),
            sa.Column("source", sa.String(200), nullable=False),
            sa.Column("delivery", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_mcp_feedback_user_id", "mcp_feedback", ["user_id"])
        op.create_index("ix_mcp_feedback_owner_created", "mcp_feedback", ["user_id", "created_at"])

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


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
