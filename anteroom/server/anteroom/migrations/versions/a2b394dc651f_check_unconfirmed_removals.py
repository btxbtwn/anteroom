"""Remember when to read an unconfirmed removal."""
from contextlib import nullcontext

from alembic import op
import sqlalchemy as sa

revision = "a2b394dc651f"
down_revision = "d5e1b7a3c962"
branch_labels = None
depends_on = None
INDEX = "ix_marketplace_listings_removal_check_at"


def _owners(include_removals: bool) -> None:
    if op.get_bind().dialect.name == "postgresql":
        due = """ OR listing.removal_check_at <= (now() AT TIME ZONE 'UTC')
                   OR (listing.marketplace = 'depop' AND listing.state = 'published'
                       AND coalesce(listing.listing_url, '') = '' AND listing.job_id IS NOT NULL)""" if include_removals else ""
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
    postgres = op.get_bind().dialect.name == "postgresql"
    with op.get_context().autocommit_block() if postgres else nullcontext():
        if postgres:
            valid = op.get_bind().scalar(sa.text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
            ), {"name": INDEX})
            if valid is False:
                op.drop_index(INDEX, table_name="marketplace_listings", postgresql_concurrently=True)
        else:
            valid = INDEX in {index["name"] for index in inspector.get_indexes("marketplace_listings")}
        if not valid:
            op.create_index(INDEX, "marketplace_listings", ["removal_check_at"], postgresql_concurrently=True)
    _owners(True)


def downgrade() -> None:
    _owners(False)
    postgres = op.get_bind().dialect.name == "postgresql"
    with op.get_context().autocommit_block() if postgres else nullcontext():
        op.drop_index(INDEX, table_name="marketplace_listings", if_exists=True, postgresql_concurrently=True)
    op.drop_column("marketplace_listings", "removal_check_at")
