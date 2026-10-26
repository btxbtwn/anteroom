"""Require item-specific seller approval for marketplace automations

Revision ID: 67d4b23e9a7a
Revises: c4d8e2b71a05
Create Date: 2026-10-26 07:41:42.050811
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '67d4b23e9a7a'
down_revision = 'c4d8e2b71a05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('automation_approvals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('action', sa.String(), server_default='publish', nullable=False))
        batch_op.add_column(sa.Column('targets', sa.JSON(), server_default='{}', nullable=False))
        batch_op.add_column(sa.Column('review_basis', sa.String(), server_default='', nullable=False))
        batch_op.alter_column('revision_id',
               existing_type=sa.VARCHAR(),
               nullable=True)

    # Older pending cards had no immutable action/target review. Ask for a new
    # proposal rather than treating an old card as permission for a new write.
    op.execute("UPDATE automation_approvals SET status = 'superseded', decided_at = CURRENT_TIMESTAMP WHERE status = 'pending'")

    with op.batch_alter_table('automations', schema=None) as batch_op:
        batch_op.drop_column('require_approval')



def downgrade() -> None:
    with op.batch_alter_table('automations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('require_approval', sa.BOOLEAN(), server_default=sa.text("'1'"), nullable=False))

    with op.batch_alter_table('automation_approvals', schema=None) as batch_op:
        batch_op.alter_column('revision_id',
               existing_type=sa.VARCHAR(),
               nullable=False)
        batch_op.drop_column('review_basis')
        batch_op.drop_column('targets')
        batch_op.drop_column('action')
