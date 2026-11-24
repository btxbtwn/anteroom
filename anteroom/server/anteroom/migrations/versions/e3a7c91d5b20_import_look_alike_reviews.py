"""Keep the look-alike listings an import leaves for the seller to review

Revision ID: e3a7c91d5b20
Revises: 1c42be3d1f71
Create Date: 2026-11-24 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e3a7c91d5b20"
down_revision = "1c42be3d1f71"
branch_labels = None
depends_on = None

TABLE = "import_reviews"
LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"
OWNER = (
    "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
    f"AND user_id <> '{LOCAL_OWNER}'"
)


def upgrade() -> None:
    bind = op.get_bind()
    # Legacy Mac adoption creates missing model tables before replaying migrations.
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "import_reviews",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
            sa.Column("marketplace", sa.String(32), nullable=False),
            sa.Column("listing_id", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("price", sa.Numeric(12, 2), nullable=True),
            sa.Column("url", sa.String(512), nullable=True),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("decision", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
                                    name="fk_import_reviews_seller_conversation_id", deferrable=True, initially="DEFERRED"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "marketplace", "listing_id", "conversation_id", name="uq_import_reviews_listing_item"),
        )
        # A table new in this revision: nothing reads it yet, so its indexes need no concurrent build.
        op.create_index("ix_import_reviews_user_id", "import_reviews", ["user_id"])
        op.create_index("ix_import_reviews_conversation_id", "import_reviews", ["conversation_id"])
        op.create_index("ix_import_reviews_owner_decision", "import_reviews", ["user_id", "decision", "created_at"])

    if bind.dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE public."{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON public."{TABLE}" FROM PUBLIC')
    for role in ("anon", "authenticated"):
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
            op.execute(f'REVOKE ALL ON public."{TABLE}" FROM {role}')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{TABLE}" TO anteroom_seller')
    op.execute(f'CREATE POLICY seller_owner ON public."{TABLE}" AS RESTRICTIVE FOR ALL TO PUBLIC '
               f"USING ({OWNER}) WITH CHECK ({OWNER})")
    op.execute(f'CREATE POLICY seller_access ON public."{TABLE}" FOR ALL TO anteroom_seller '
               "USING (true) WITH CHECK (true)")
    op.execute(f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public."{TABLE}" '
               "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()")


def downgrade() -> None:
    op.drop_table(TABLE)
