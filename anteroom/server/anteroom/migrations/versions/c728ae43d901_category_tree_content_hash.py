"""Version imported category node content independently of seed metadata.

Revision ID: c728ae43d901
Revises: c92e35f8b041
"""
from alembic import op
import sqlalchemy as sa

revision = "c728ae43d901"
down_revision = "c92e35f8b041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A legacy SQLite adoption may have already created current model columns.
    if "content_hash" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("category_trees")}:
        op.add_column("category_trees", sa.Column("content_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("category_trees", "content_hash")
