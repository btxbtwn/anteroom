"""Record when a listing price was saved, independently of other updates."""
from alembic import op
import sqlalchemy as sa

revision = "a70c21f496e8"
down_revision = "d0e4f3a92b68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy SQLite adoption can create the column from current metadata first.
    if "price_recorded_at" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("marketplace_listings")}:
        op.add_column("marketplace_listings", sa.Column("price_recorded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("marketplace_listings", "price_recorded_at")
