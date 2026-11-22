"""Keep the dispatched job identity for late relist reconciliation."""
import sqlalchemy as sa
from alembic import op

revision = "1af734b029ed"
down_revision = "c2d9f4a8e103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "marketplace_relist_logs"
    if "native_job_id" not in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, sa.Column("native_job_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("marketplace_relist_logs", "native_job_id")
