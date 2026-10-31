"""Drop size fields Sellwild and Vinted forms do not have

Revision ID: c7e2a41d9b05
Revises: 8fac6ea04af1
Create Date: 2026-10-31 01:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c7e2a41d9b05'
down_revision = '8fac6ea04af1'
branch_labels = None
depends_on = None

# The bottoms size helper wrote these, but Sellwild's sell form has no size or
# size type field and Vinted's has no size type, so the editor flagged them as
# "not provided by the selected marketplace form".
STRAY = {"sellwild_specifics": ("size", "sizeType"), "vinted_specifics": ("sizeType",)}


def upgrade() -> None:
    rows = sa.table("listing_revisions", sa.column("id", sa.String), sa.column("listing_json", sa.JSON))
    connection = op.get_bind()
    blocks = [rows.c.listing_json[block] for block in STRAY]
    affected = []
    last_id = ""
    # Read only the two small blocks in keyset batches, so large listing bodies stay in SQL.
    while True:
        batch = connection.execute(sa.select(rows.c.id, *blocks).where(rows.c.id > last_id)
                                   .order_by(rows.c.id).limit(500)).all()
        if not batch:
            break
        for revision_id, *values in batch:
            if any(isinstance(value, dict) and any(key in value for key in STRAY[block])
                   for block, value in zip(STRAY, values, strict=True)):
                affected.append(revision_id)
        last_id = batch[-1][0]
    for revision_id in affected:
        listing = connection.execute(sa.select(rows.c.listing_json).where(rows.c.id == revision_id)).scalar_one()
        for block, keys in STRAY.items():
            for key in keys:
                (listing.get(block) or {}).pop(key, None)
        connection.execute(sa.update(rows).where(rows.c.id == revision_id).values(listing_json=listing))


def downgrade() -> None:
    pass
