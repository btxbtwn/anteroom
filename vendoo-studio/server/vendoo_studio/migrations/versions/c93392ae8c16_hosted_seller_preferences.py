"""Hosted seller preferences

Revision ID: c93392ae8c16
Revises: b87e924216cb
Create Date: 2026-10-30 04:04:22.553104
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c93392ae8c16'
down_revision = 'b87e924216cb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'user_preferences' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('user_preferences',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('key', sa.String(100), nullable=False),
            sa.Column('value', sa.JSON(), nullable=False),
            sa.Column('user_id', sa.String(36), server_default='00000000-0000-0000-0000-000000000000', nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'key', name='uq_user_preferences_seller_key'))
        op.create_index('ix_user_preferences_user_id', 'user_preferences', ['user_id'])
    if op.get_bind().dialect.name == 'postgresql':
        owner = "user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') AND user_id <> '00000000-0000-0000-0000-000000000000'"
        op.execute('ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY')
        op.execute('CREATE POLICY seller_owner ON user_preferences AS RESTRICTIVE FOR ALL TO PUBLIC '
                   f'USING ({owner}) WITH CHECK ({owner})')
        op.execute('CREATE POLICY seller_access ON user_preferences FOR ALL TO anteroom_seller USING (true) WITH CHECK (true)')
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO anteroom_seller')


def downgrade() -> None:
    raise RuntimeError('Seller preferences are forward-only; never discard account settings')
