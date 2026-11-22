"""Store seller feedback on generated listing revisions."""

import sqlalchemy as sa
from alembic import op

revision = "b7c9e3a04d62"
down_revision = "9a4c6e1d2f83"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy adoption can create or repair this column from the current ORM
    # metadata before replaying the revisions after the baseline stamp.
    if not op.get_context().as_sql:
        columns = sa.inspect(op.get_bind()).get_columns("listing_revisions")
        if any(column["name"] == "generation_feedback" for column in columns):
            return
    op.add_column("listing_revisions", sa.Column("generation_feedback", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("listing_revisions", "generation_feedback")
