"""All current and future eBay offer policy

Revision ID: 84bc2a03cbad
Revises: 6dc4b77bc66e
Create Date: 2026-10-29 20:56:12.420181
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '84bc2a03cbad'
down_revision = '6dc4b77bc66e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy SQLite adoption creates missing tables from current models before
    # replaying revisions. Its new policy table already has this schema/index.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and sa.inspect(bind).has_table("ebay_offer_policies"):
        return
    op.create_table('ebay_offer_policies',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('account_id', sa.String(), nullable=False),
    sa.Column('approval_id', sa.String(), nullable=False),
    sa.Column('discount_percent', sa.Integer(), nullable=False),
    sa.Column('minimum_price_cents', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('user_id', sa.String(length=36), server_default='00000000-0000-0000-0000-000000000000', nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'account_id', name='uq_ebay_offer_policy_account')
    )
    with op.batch_alter_table('ebay_offer_policies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ebay_offer_policies_user_id'), ['user_id'], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        owner_check = """(
            user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
            AND user_id <> '00000000-0000-0000-0000-000000000000'
        )"""
        op.execute('ALTER TABLE ebay_offer_policies ENABLE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY seller_owner ON ebay_offer_policies AS RESTRICTIVE FOR ALL TO PUBLIC '
            f'USING ({owner_check}) WITH CHECK ({owner_check})'
        )
        op.execute(
            'CREATE POLICY seller_access ON ebay_offer_policies FOR ALL TO anteroom_seller '
            'USING (true) WITH CHECK (true)'
        )
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON ebay_offer_policies TO anteroom_seller')


def downgrade() -> None:
    with op.batch_alter_table('ebay_offer_policies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ebay_offer_policies_user_id'))

    op.drop_table('ebay_offer_policies')
