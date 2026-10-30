"""Optional shared minimum for offer rules

Revision ID: 4696bd599cea
Revises: 84bc2a03cbad
Create Date: 2026-10-29 21:51:27.997810
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '4696bd599cea'
down_revision = '84bc2a03cbad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy SQLite adoption may have created this table from current models.
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('promotion_automations')}
    if 'minimum_price_cents' not in columns:
        with op.batch_alter_table('promotion_automations', schema=None) as batch_op:
            batch_op.add_column(sa.Column('minimum_price_cents', sa.Integer(), nullable=True))
    # Existing approvals required an individual floor. Require a new approval
    # before allowing those rules to cover listings without one.
    op.execute(sa.text("UPDATE promotion_automations SET enabled = false, next_run_at = NULL, "
                       "paused_reason = :note WHERE action = 'offer' AND enabled = true").bindparams(
        note='Review the optional minimum price, then turn this offer rule back on.'
    ))


def downgrade() -> None:
    with op.batch_alter_table('promotion_automations', schema=None) as batch_op:
        batch_op.drop_column('minimum_price_cents')

