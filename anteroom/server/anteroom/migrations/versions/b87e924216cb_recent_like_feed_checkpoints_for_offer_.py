"""Recent like feed checkpoints for offer rules

Revision ID: b87e924216cb
Revises: 4696bd599cea
Create Date: 2026-10-29 22:54:08.340300
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b87e924216cb'
down_revision = '4696bd599cea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('promotion_automations')}
    if 'like_events_since' not in columns:
        op.add_column('promotion_automations', sa.Column('like_events_since', sa.DateTime(), nullable=True))
    # Faster sends to genuinely new likers may happen within 24 hours of the
    # previous offer. Require approval of the new behavior before enabling it.
    op.execute(sa.text("UPDATE promotion_automations SET enabled = false, next_run_at = NULL, "
                       "paused_reason = :note WHERE action = 'offer' AND enabled = true "
                       "AND marketplace IN ('poshmark', 'depop')").bindparams(
        note='Review new-like checks, then turn this offer rule back on.'
    ))


def downgrade() -> None:
    with op.batch_alter_table('promotion_automations', schema=None) as batch_op:
        batch_op.drop_column('like_events_since')
