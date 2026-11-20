"""Record marketplace offer confirmation evidence

Revision ID: a12a51c75c22
Revises: b1f4c7e2d903
Create Date: 2026-11-08 07:11:34.564531
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a12a51c75c22'
down_revision = 'b1f4c7e2d903'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy adoption may create tables from the current models first.
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('listing_promotions')}
    if 'confirmation' not in columns:
        op.add_column('listing_promotions', sa.Column('confirmation', sa.String(), nullable=True))
    if 'recipient_count' not in columns:
        op.add_column('listing_promotions', sa.Column('recipient_count', sa.Integer(), nullable=True))
    # Old rows never stored receipt evidence. Preserve the no-resend state,
    # but do not continue presenting their preview likes as delivery proof.
    op.execute(sa.text("UPDATE listing_promotions SET confirmation = 'legacy_unverified', "
                       "note = 'Previously recorded as completed. Recipient delivery was not verified.' "
                       "WHERE action = 'offer' AND state = 'done' AND confirmation IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table('listing_promotions', schema=None) as batch_op:
        batch_op.drop_column('recipient_count')
        batch_op.drop_column('confirmation')
