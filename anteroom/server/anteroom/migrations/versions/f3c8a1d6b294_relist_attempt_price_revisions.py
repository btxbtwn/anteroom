"""Remember which price revision each Auto-Relist attempt saved.

Revision ID: f3c8a1d6b294
Revises: 68f2ba103d5c
"""
import sqlalchemy as sa
from alembic import op

revision = "f3c8a1d6b294"
down_revision = "68f2ba103d5c"
branch_labels = None
depends_on = None

COLUMNS = ("price_revision_id", "original_revision_id")


def upgrade() -> None:
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("marketplace_relist_logs")}
    for name in COLUMNS:
        if name not in existing:
            op.add_column("marketplace_relist_logs", sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("marketplace_relist_logs")}
    for name in reversed(COLUMNS):
        if name in existing:
            op.drop_column("marketplace_relist_logs", name)
