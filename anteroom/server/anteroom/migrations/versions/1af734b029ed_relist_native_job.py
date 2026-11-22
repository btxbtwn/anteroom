"""Keep the dispatched job identity for late relist reconciliation."""
import sqlalchemy as sa
from alembic import op

revision = "1af734b029ed"
down_revision = "c2d9f4a8e103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("marketplace_relist_logs", "sale_sync_events"):
        if "native_job_id" not in {col["name"] for col in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("native_job_id", sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("sale_sync_events", "marketplace_relist_logs"):
        if "native_job_id" in {col["name"] for col in inspector.get_columns(table)}:
            op.drop_column(table, "native_job_id")
