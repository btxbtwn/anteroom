"""Cloud marketplace sessions, audit trail, write-ahead journals, and listing executor

Revision ID: a3c9e7d12f48
Revises: f2a9c4e71b83
Create Date: 2026-11-21 18:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a3c9e7d12f48'
down_revision = 'f2a9c4e71b83'
branch_labels = None
depends_on = None

OWNER = ("user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
         "AND user_id <> '00000000-0000-0000-0000-000000000000'")

NEW_TABLES = (
    'cloud_marketplace_sessions',
    'cloud_session_audit',
    'cloud_operation_journals',
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'marketplace_listings' in tables:
        columns = {col['name'] for col in inspector.get_columns('marketplace_listings')}
        if 'executor' not in columns:
            op.add_column('marketplace_listings', sa.Column('executor', sa.String(16), nullable=True))

    if 'cloud_marketplace_sessions' not in tables:
        op.create_table(
            'cloud_marketplace_sessions',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column(
                'user_id', sa.String(36), nullable=False,
                server_default='00000000-0000-0000-0000-000000000000',
            ),
            sa.Column('marketplace', sa.String(32), nullable=False),
            sa.Column('state', sa.String(24), nullable=False, server_default='pending'),
            sa.Column('account_label', sa.String(80), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_error', sa.String(300), nullable=True),
            sa.Column('proxy_session_id', sa.String(64), nullable=True),
            sa.Column('upload_token_hash', sa.String(64), nullable=True),
            sa.Column('upload_token_expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'marketplace', name='uq_cloud_marketplace_sessions_owner_market'),
        )
        op.create_index('ix_cloud_marketplace_sessions_user_id', 'cloud_marketplace_sessions', ['user_id'])
        op.create_index('ix_cloud_marketplace_sessions_market_state', 'cloud_marketplace_sessions', ['marketplace', 'state'])

    if 'cloud_session_audit' not in tables:
        op.create_table(
            'cloud_session_audit',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column(
                'user_id', sa.String(36), nullable=False,
                server_default='00000000-0000-0000-0000-000000000000',
            ),
            sa.Column('marketplace', sa.String(32), nullable=False),
            sa.Column('event', sa.String(32), nullable=False),
            sa.Column('detail', sa.String(200), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_cloud_session_audit_user_id', 'cloud_session_audit', ['user_id'])
        op.create_index('ix_cloud_session_audit_owner_created', 'cloud_session_audit', ['user_id', 'created_at'])

    if 'cloud_operation_journals' not in tables:
        op.create_table(
            'cloud_operation_journals',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column(
                'user_id', sa.String(36), nullable=False,
                server_default='00000000-0000-0000-0000-000000000000',
            ),
            sa.Column('marketplace', sa.String(32), nullable=False),
            sa.Column('journal_key', sa.String(160), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'journal_key', name='uq_cloud_operation_journals_owner_key'),
        )
        op.create_index('ix_cloud_operation_journals_user_id', 'cloud_operation_journals', ['user_id'])
        op.create_index('ix_cloud_operation_journals_owner_market', 'cloud_operation_journals', ['user_id', 'marketplace'])

    if bind.dialect.name != 'postgresql':
        return

    for table in NEW_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY seller_owner ON "{table}" AS RESTRICTIVE FOR ALL TO PUBLIC '
            f'USING ({OWNER}) WITH CHECK ({OWNER})'
        )
        op.execute(
            f'CREATE POLICY seller_access ON "{table}" FOR ALL TO anteroom_seller '
            'USING (true) WITH CHECK (true)'
        )
        op.execute(f'REVOKE ALL ON "{table}" FROM PUBLIC')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO anteroom_seller')
        for role in ('anon', 'authenticated'):
            if bind.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                op.execute(f'REVOKE ALL ON "{table}" FROM {role}')

    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.cloud_session_owners(market text)
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT session.user_id::text
            FROM public.cloud_marketplace_sessions AS session
            WHERE session.marketplace = market
              AND session.state = 'connected'
              AND session.user_id <> '00000000-0000-0000-0000-000000000000'
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.cloud_session_owners(text) FROM PUBLIC, anteroom_seller;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.consume_cloud_upload_token(
            target_owner text, market text, expected_hash text)
        RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path='' AS $$
        BEGIN
            IF target_owner IS NULL
               OR target_owner = '00000000-0000-0000-0000-000000000000'
               OR expected_hash IS NULL
               OR length(expected_hash) <> 64 THEN
                RETURN false;
            END IF;
            UPDATE public.cloud_marketplace_sessions
            SET upload_token_hash = NULL,
                upload_token_expires_at = NULL,
                updated_at = now()
            WHERE user_id = target_owner
              AND marketplace = market
              AND upload_token_hash = expected_hash
              AND upload_token_expires_at IS NOT NULL
              AND upload_token_expires_at > now();
            RETURN FOUND;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.consume_cloud_upload_token(text, text, text)
            FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api')
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.cloud_session_owners(text) TO anteroom_api')
        op.execute(
            'GRANT EXECUTE ON FUNCTION anteroom_private.consume_cloud_upload_token(text, text, text) TO anteroom_api'
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if bind.dialect.name == 'postgresql':
        op.execute('DROP FUNCTION IF EXISTS anteroom_private.consume_cloud_upload_token(text, text, text)')
        op.execute('DROP FUNCTION IF EXISTS anteroom_private.cloud_session_owners(text)')

    for table in reversed(NEW_TABLES):
        if table in tables:
            op.drop_table(table)

    if 'marketplace_listings' in tables:
        columns = {col['name'] for col in inspector.get_columns('marketplace_listings')}
        if 'executor' in columns:
            with op.batch_alter_table('marketplace_listings') as batch:
                batch.drop_column('executor')
