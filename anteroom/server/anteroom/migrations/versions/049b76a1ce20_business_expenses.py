"""Create the seller-owned business expense ledger."""
import sqlalchemy as sa
from alembic import op

revision = "049b76a1ce20"
down_revision = "8580524be7de"
branch_labels = None
depends_on = None

LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()
    # Legacy Mac adoption creates missing model tables before replaying migrations.
    if bind.dialect.name == "sqlite" and sa.inspect(bind).has_table("business_expenses"):
        return
    op.create_table(
        "business_expenses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
        sa.Column("incurred_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("vendor", sa.String(256), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("accounting_type", sa.String(24), nullable=False),
        sa.Column("distance", sa.Numeric(12, 3)),
        sa.Column("distance_unit", sa.String(2)),
        sa.Column("mileage_rate_cents", sa.Numeric(12, 6)),
        sa.Column("origin_destination", sa.String(512)),
        sa.Column("import_source", sa.String(8), nullable=False, server_default="manual"),
        sa.Column("import_key", sa.String(64)),
    )
    op.create_index("ix_business_expenses_user_id", "business_expenses", ["user_id"])
    op.create_index("ix_business_expenses_owner_date", "business_expenses", ["user_id", "incurred_date", "id"])
    op.create_index("ux_business_expenses_owner_import", "business_expenses", ["user_id", "import_key"], unique=True)
    if bind.dialect.name == "postgresql":
        owner = ("user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
                 f"AND user_id <> '{LOCAL_OWNER}'")
        op.execute("ALTER TABLE public.business_expenses ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON public.business_expenses FROM PUBLIC")
        for role in ("anon", "authenticated"):
            if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
                op.execute(f"REVOKE ALL ON public.business_expenses FROM {role}")
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.business_expenses TO anteroom_seller")
        op.execute(f"CREATE POLICY seller_owner ON public.business_expenses AS RESTRICTIVE FOR ALL TO PUBLIC "
                   f"USING ({owner}) WITH CHECK ({owner})")
        op.execute("CREATE POLICY seller_access ON public.business_expenses FOR ALL TO anteroom_seller "
                   "USING (true) WITH CHECK (true)")
        op.execute("CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.business_expenses "
                   "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()")


def downgrade() -> None:
    op.drop_table("business_expenses")
