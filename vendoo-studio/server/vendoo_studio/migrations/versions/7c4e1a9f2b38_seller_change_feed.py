"""A per-seller change feed that other devices follow live

Revision ID: 7c4e1a9f2b38
Revises: 3b9d6f2a1c47
Create Date: 2026-10-31 18:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7c4e1a9f2b38'
down_revision = '3b9d6f2a1c47'
branch_labels = None
depends_on = None

OWNER = ("user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
         "AND user_id <> '00000000-0000-0000-0000-000000000000'")


def upgrade() -> None:
    bind = op.get_bind()
    if 'seller_changes' not in sa.inspect(bind).get_table_names():
        op.create_table('seller_changes',
            sa.Column('user_id', sa.String(36), nullable=False,
                      server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('topic', sa.String(32), nullable=False),
            sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('user_id', 'topic', name='pk_seller_changes'))
        op.create_index('ix_seller_changes_user_id', 'seller_changes', ['user_id'])
    if bind.dialect.name != 'postgresql':
        return
    op.execute('ALTER TABLE seller_changes ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY seller_owner ON seller_changes AS RESTRICTIVE FOR ALL TO PUBLIC '
               f'USING ({OWNER}) WITH CHECK ({OWNER})')
    op.execute('CREATE POLICY seller_access ON seller_changes FOR ALL TO anteroom_seller USING (true) WITH CHECK (true)')
    op.execute('REVOKE ALL ON seller_changes FROM PUBLIC')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON seller_changes TO anteroom_seller')
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anon'")):
        op.execute('REVOKE ALL ON seller_changes FROM anon')
    # Supabase Realtime reads changes as the signed-in browser or phone
    # (role authenticated, with its JWT claims). That role may read its own
    # feed rows (topic names and times) and nothing else; every other seller
    # table keeps no permissive policy for it.
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='authenticated'")):
        op.execute('REVOKE ALL ON seller_changes FROM authenticated')
        op.execute('GRANT SELECT ON seller_changes TO authenticated')
        op.execute('CREATE POLICY seller_subscriber ON seller_changes FOR SELECT TO authenticated '
                   f'USING ({OWNER})')
    # Supabase creates this publication; plain Postgres (the hosted sandbox
    # and tests) gets an equivalent empty one so the same revision applies.
    if not bind.scalar(sa.text("SELECT 1 FROM pg_publication WHERE pubname='supabase_realtime'")):
        op.execute('CREATE PUBLICATION supabase_realtime')
    covered = bind.scalar(sa.text("""
        SELECT puballtables OR EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'seller_changes')
        FROM pg_publication WHERE pubname = 'supabase_realtime'"""))
    if not covered:
        op.execute('ALTER PUBLICATION supabase_realtime ADD TABLE seller_changes')


def downgrade() -> None:
    raise RuntimeError('The change feed migration is forward-only')
