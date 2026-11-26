"""Keep eBay and Etsy sign-ins in progress in the database

A sign-in started on one process (before a hosted deploy, or on another web
Machine) now finishes on any other. Expand-only: a new seller table; the
release before this one never reads it.

Revision ID: d5e1b7a3c962
Revises: f3c8a1d6e270
Create Date: 2026-11-25 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d5e1b7a3c962"
down_revision = "f3c8a1d6e270"
branch_labels = None
depends_on = None

TABLE = "marketplace_sign_ins"
OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    "AND user_id <> '00000000-0000-0000-0000-000000000000'"
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "marketplace_sign_ins",
            sa.Column(
                "user_id",
                sa.String(36),
                nullable=False,
                server_default="00000000-0000-0000-0000-000000000000",
            ),
            sa.Column("marketplace", sa.String(16), nullable=False),
            sa.Column("state", sa.String(200), nullable=False),
            sa.Column("authorize_url", sa.Text(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("user_id", "marketplace", name="pk_marketplace_sign_ins"),
        )
        op.create_index("ix_marketplace_sign_ins_user_id", "marketplace_sign_ins", ["user_id"])

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
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        op.drop_table(TABLE)
