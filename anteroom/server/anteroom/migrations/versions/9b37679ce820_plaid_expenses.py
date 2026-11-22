"""Add Plaid connections and expense reconciliation history."""
import sqlalchemy as sa
from alembic import op

revision = "9b37679ce820"
down_revision = "049b76a1ce20"
branch_labels = None
depends_on = None

LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"
TABLES = ("plaid_connections", "plaid_transactions")


def _identity():
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER),
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names() if bind.dialect.name == "sqlite" else ()
    if "plaid_connections" not in existing:
        op.create_table(
            "plaid_connections", *_identity(),
            sa.Column("institution_id", sa.String(), nullable=False),
            sa.Column("institution_name", sa.String(), nullable=False),
            sa.Column("environment", sa.String(), nullable=False),
            sa.Column("accounts", sa.JSON(), nullable=False),
            sa.Column("cursor", sa.Text()),
            sa.Column("sync_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_synced_at", sa.DateTime()),
            sa.Column("last_attempted_at", sa.DateTime()),
            sa.Column("last_error", sa.Text()),
            sa.Column("history_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("disconnecting", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.UniqueConstraint("user_id", "institution_id", name="uq_plaid_owner_institution"),
        )
        op.create_index("ix_plaid_connections_user_id", "plaid_connections", ["user_id"])
    if "plaid_transactions" not in existing:
        op.create_table(
            "plaid_transactions", *_identity(),
            sa.Column("import_key", sa.String(64), nullable=False),
            sa.Column("imported_revision", sa.Integer()),
            sa.UniqueConstraint("user_id", "import_key", name="uq_plaid_owner_import"),
        )
        op.create_index("ix_plaid_transactions_user_id", "plaid_transactions", ["user_id"])
    if bind.dialect.name != "postgresql":
        return
    owner = ("user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
             f"AND user_id <> '{LOCAL_OWNER}'")
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        for role in ("anon", "authenticated"):
            if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
                op.execute(f"REVOKE ALL ON public.{table} FROM {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO anteroom_seller")
        op.execute(f"CREATE POLICY seller_owner ON public.{table} AS RESTRICTIVE FOR ALL TO PUBLIC "
                   f"USING ({owner}) WITH CHECK ({owner})")
        op.execute(f"CREATE POLICY seller_access ON public.{table} FOR ALL TO anteroom_seller "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.{table} "
                   "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()")
    op.execute("""
        CREATE FUNCTION anteroom_private.plaid_owners()
        RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT DISTINCT user_id::text FROM public.plaid_connections WHERE NOT disconnecting
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.plaid_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute("GRANT EXECUTE ON FUNCTION anteroom_private.plaid_owners() TO anteroom_api")


def downgrade() -> None:
    bind = op.get_bind()
    hosted = bind.dialect.name == "postgresql"
    if hosted:
        op.execute("LOCK TABLE public.plaid_connections IN ACCESS EXCLUSIVE MODE")
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plaid_connections)")):
        raise RuntimeError("Disconnect all Plaid connections before downgrading the database.")
    if hosted:
        if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM public.user_secret_references WHERE account LIKE 'plaid:%')")):
            raise RuntimeError("Clear remaining Plaid Vault credentials before downgrading the database.")
        op.execute("DROP FUNCTION anteroom_private.plaid_owners()")
    for table in reversed(TABLES):
        op.drop_table(table)
