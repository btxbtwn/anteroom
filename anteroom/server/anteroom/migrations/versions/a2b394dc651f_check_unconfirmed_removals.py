"""Remember when to read an unconfirmed removal."""
from alembic import op
import sqlalchemy as sa

revision = "a2b394dc651f"
down_revision = "d5e1b7a3c962"
branch_labels = None
depends_on = None


def _owners(include_removals: bool) -> None:
    if op.get_bind().dialect.name == "postgresql":
        due = " OR listing.removal_check_at <= (now() AT TIME ZONE 'UTC')" if include_removals else ""
        op.execute(f"""
            CREATE OR REPLACE FUNCTION anteroom_private.unsettled_job_owners()
            RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
                SELECT job.user_id::text FROM public.jobs AS job
                    WHERE job.status IN ('queued', 'awaiting_extension', 'dispatched')
                UNION
                SELECT listing.user_id::text FROM public.marketplace_listings AS listing
                    WHERE listing.state IN ('saving', 'publishing', 'updating', 'unknown'){due}
            $$;
        """)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "removal_check_at" not in {column["name"] for column in inspector.get_columns("marketplace_listings")}:
        op.add_column("marketplace_listings", sa.Column("removal_check_at", sa.DateTime(), nullable=True))
    if "ix_marketplace_listings_removal_check_at" not in {index["name"] for index in inspector.get_indexes("marketplace_listings")}:
        op.create_index("ix_marketplace_listings_removal_check_at", "marketplace_listings", ["removal_check_at"])
    _owners(True)


def downgrade() -> None:
    _owners(False)
    op.drop_index("ix_marketplace_listings_removal_check_at", table_name="marketplace_listings")
    op.drop_column("marketplace_listings", "removal_check_at")
