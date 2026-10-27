"""Promotion automations and their rounds

Revision ID: ea3c87866204
Revises: 3ece513f8e0d
Create Date: 2026-10-26 21:04:49.851933
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'ea3c87866204'
down_revision = '3ece513f8e0d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('promotion_automations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('marketplace', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('scope', sa.String(), nullable=False),
    sa.Column('conversation_ids', sa.JSON(), nullable=False),
    sa.Column('excluded_ids', sa.JSON(), nullable=False),
    sa.Column('min_days_listed', sa.Integer(), nullable=False),
    sa.Column('share_order', sa.String(), nullable=True),
    sa.Column('speed', sa.String(), nullable=True),
    sa.Column('daily_cap', sa.Integer(), nullable=True),
    sa.Column('times', sa.JSON(), nullable=True),
    sa.Column('timezone', sa.String(), nullable=True),
    sa.Column('discount_percent', sa.Integer(), nullable=True),
    sa.Column('min_likes', sa.Integer(), nullable=True),
    sa.Column('shipping_label', sa.String(), nullable=True),
    sa.Column('paused_reason', sa.String(), nullable=True),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('promotion_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('automation_id', sa.String(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('sent', sa.Integer(), nullable=False),
    sa.Column('skipped', sa.Integer(), nullable=False),
    sa.Column('failed', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['automation_id'], ['promotion_automations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('promotion_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promotion_runs_automation_id'), ['automation_id'], unique=False)

    with op.batch_alter_table('listing_promotions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('automation_id', sa.String(), nullable=True))
        batch_op.create_index(batch_op.f('ix_listing_promotions_automation_id'), ['automation_id'], unique=False)
        batch_op.create_foreign_key('fk_listing_promotions_automation_id', 'promotion_automations', ['automation_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('listing_promotions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_listing_promotions_automation_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_listing_promotions_automation_id'))
        batch_op.drop_column('automation_id')

    with op.batch_alter_table('promotion_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promotion_runs_automation_id'))

    op.drop_table('promotion_runs')
    op.drop_table('promotion_automations')
