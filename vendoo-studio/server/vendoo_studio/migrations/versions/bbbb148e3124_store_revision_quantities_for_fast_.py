"""Store revision quantities for fast inventory reads

Revision ID: bbbb148e3124
Revises: b295f60b1610
Create Date: 2026-10-29 20:25:09.772132
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'bbbb148e3124'
down_revision = 'b295f60b1610'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy adoption may already have created missing tables from the model.
    if "inventory_quantity" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("listing_revisions")}:
        op.add_column("listing_revisions", sa.Column("inventory_quantity", sa.Integer(), nullable=True))
    # Parse history once during migration, instead of during every inventory poll.
    # Select only quantity, with keyset batches so large JSON bodies stay in SQL.
    rows = sa.table("listing_revisions", sa.column("id", sa.String),
                    sa.column("listing_json", sa.JSON), sa.column("inventory_quantity", sa.Integer))
    connection = op.get_bind()
    last_id = ""
    while True:
        batch = connection.execute(sa.select(rows.c.id, rows.c.listing_json["quantity"])
                                   .where(rows.c.id > last_id).order_by(rows.c.id).limit(500)).all()
        if not batch:
            break
        quantities = []
        for revision_id, value in batch:
            try:
                quantity = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 < quantity <= 2_147_483_647:
                quantities.append({"revision_id": revision_id, "quantity": quantity})
        if quantities:
            connection.execute(sa.update(rows).where(rows.c.id == sa.bindparam("revision_id"))
                               .values(inventory_quantity=sa.bindparam("quantity")), quantities)
        last_id = batch[-1][0]
    if "ix_listing_revisions_quantity_history" not in {index["name"] for index in sa.inspect(connection).get_indexes("listing_revisions")}:
        op.create_index("ix_listing_revisions_quantity_history", "listing_revisions",
                        ["user_id", "conversation_id", "created_at", "inventory_quantity"])


def downgrade() -> None:
    op.drop_index("ix_listing_revisions_quantity_history", table_name="listing_revisions")
    with op.batch_alter_table('listing_revisions', schema=None) as batch_op:
        batch_op.drop_column('inventory_quantity')

