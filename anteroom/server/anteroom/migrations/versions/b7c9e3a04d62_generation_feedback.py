"""Store seller feedback on generated listing revisions."""

import sqlalchemy as sa
from alembic import op

revision = "b7c9e3a04d62"
down_revision = "8580524be7de"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("listing_revisions", sa.Column("generation_feedback", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("listing_revisions", "generation_feedback")
