"""listed at on marketplace listings

Revision ID: b1f4c7e2d903
Revises: a7d3e5c1b9f4
Create Date: 2026-11-08 06:30:00.000000

Live records are backfilled with when their last List finished: the newest
completed ``native_<marketplace>_publish`` or ``native_<marketplace>_list``
job on the item. A live record with no such job, such as one the seller linked
by hand, stays empty.
"""
from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'b1f4c7e2d903'
down_revision = 'a7d3e5c1b9f4'
branch_labels = None
depends_on = None


_LIVE_STATES = ("published", "pending_review")

listings_table = sa.table(
    "marketplace_listings",
    sa.column("id", sa.String),
    sa.column("conversation_id", sa.String),
    sa.column("marketplace", sa.String),
    sa.column("state", sa.String),
    sa.column("listed_at", sa.DateTime),
)
jobs_table = sa.table(
    "jobs",
    sa.column("conversation_id", sa.String),
    sa.column("current_step", sa.String),
    sa.column("status", sa.String),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def upgrade() -> None:
    connection = op.get_bind()
    # Adopting a database from before migrations creates the column with its tables.
    if "listed_at" in {column["name"] for column in sa.inspect(connection).get_columns("marketplace_listings")}:
        return
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('listed_at', sa.DateTime(), nullable=True))

    finished = sa.func.max(sa.func.coalesce(jobs_table.c.updated_at, jobs_table.c.created_at))
    listed: dict[tuple[str, str], datetime] = {
        (conversation_id, step): when
        for conversation_id, step, when in connection.execute(
            sa.select(jobs_table.c.conversation_id, jobs_table.c.current_step, finished)
            .where(jobs_table.c.status == "completed", jobs_table.c.current_step.like("native\\_%", escape="\\"))
            .group_by(jobs_table.c.conversation_id, jobs_table.c.current_step)
        )
    }
    live = connection.execute(
        sa.select(listings_table.c.id, listings_table.c.conversation_id, listings_table.c.marketplace)
        .where(listings_table.c.state.in_(_LIVE_STATES))
    ).all()
    for row_id, conversation_id, marketplace in live:
        stamps = [when for action in ("publish", "list")
                  if (when := listed.get((conversation_id, f"native_{marketplace}_{action}"))) is not None]
        if stamps:
            connection.execute(sa.update(listings_table).where(listings_table.c.id == row_id)
                               .values(listed_at=max(stamps)))


def downgrade() -> None:
    with op.batch_alter_table('marketplace_listings', schema=None) as batch_op:
        batch_op.drop_column('listed_at')
