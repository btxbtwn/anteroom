"""Durable seller background work

Revision ID: 56b5ee7250ec
Revises: 1e3d624c0ae2
Create Date: 2026-10-30 05:56:03.204023
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '56b5ee7250ec'
down_revision = '1e3d624c0ae2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'background_tasks' not in sa.inspect(bind).get_table_names():
        op.create_table('background_tasks',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False,
                      server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('conversation_id', sa.String(), nullable=False),
            sa.Column('kind', sa.String(32), nullable=False),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('lease_token', sa.String(36)),
            sa.Column('leased_until', sa.DateTime(timezone=True)),
            sa.Column('error', sa.Text()),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'id', name='uq_background_tasks_seller_id'),
            sa.ForeignKeyConstraint(['user_id', 'conversation_id'], ['conversations.user_id', 'conversations.id'],
                name='fk_background_tasks_seller_conversation', deferrable=True, initially='DEFERRED'))
        op.create_index('ix_background_tasks_user_id', 'background_tasks', ['user_id'])
        op.create_index('ix_background_tasks_conversation_id', 'background_tasks', ['conversation_id'])
        op.create_index('ix_background_tasks_pending', 'background_tasks', ['status', 'created_at'])
    if 'background_task_events' not in sa.inspect(bind).get_table_names():
        op.create_table('background_task_events',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False,
                      server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('task_id', sa.String(), nullable=False),
            sa.Column('sequence', sa.Integer(), nullable=False),
            sa.Column('data', sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('task_id', 'sequence', name='uq_background_task_event_sequence'),
            sa.ForeignKeyConstraint(['user_id', 'task_id'], ['background_tasks.user_id', 'background_tasks.id'],
                name='fk_background_task_events_seller_task', deferrable=True, initially='DEFERRED'))
        op.create_index('ix_background_task_events_user_id', 'background_task_events', ['user_id'])
        op.create_index('ix_background_task_events_task_id', 'background_task_events', ['task_id'])
    if bind.dialect.name != 'postgresql':
        return
    owner = "user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') AND user_id <> '00000000-0000-0000-0000-000000000000'"
    op.execute('ALTER TABLE background_tasks ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY seller_owner ON background_tasks AS RESTRICTIVE FOR ALL TO PUBLIC '
               f'USING ({owner}) WITH CHECK ({owner})')
    op.execute('CREATE POLICY seller_access ON background_tasks FOR ALL TO anteroom_seller USING (true) WITH CHECK (true)')
    op.execute('REVOKE ALL ON background_tasks FROM PUBLIC')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON background_tasks TO anteroom_seller')
    for role in ('anon', 'authenticated'):
        if bind.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
            op.execute(f'REVOKE ALL ON background_tasks FROM {role}')
    op.execute('ALTER TABLE background_task_events ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY seller_owner ON background_task_events AS RESTRICTIVE FOR ALL TO PUBLIC '
               f'USING ({owner}) WITH CHECK ({owner})')
    op.execute('CREATE POLICY seller_access ON background_task_events FOR ALL TO anteroom_seller USING (true) WITH CHECK (true)')
    op.execute('REVOKE ALL ON background_task_events FROM PUBLIC')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON background_task_events TO anteroom_seller')
    for role in ('anon', 'authenticated'):
        if bind.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
            op.execute(f'REVOKE ALL ON background_task_events FROM {role}')
    # Only the trusted process login can claim across owners. HTTP handlers
    # SET LOCAL ROLE anteroom_seller, which has no EXECUTE grant here.
    op.execute("""
        CREATE FUNCTION anteroom_private.claim_background_task(worker_token text)
        RETURNS TABLE(task_id text, owner_id text) LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
        BEGIN
            IF worker_token IS NULL OR worker_token !~ '^[0-9a-f-]{36}$' THEN
                RAISE invalid_parameter_value USING MESSAGE='Invalid worker lease';
            END IF;
            -- An expired generation may have saved a partial revision. Never
            -- replay automatically and overwrite edits made since that save.
            WITH expired AS (
                UPDATE public.background_tasks SET status='failed', lease_token=NULL,
                    error='This task was interrupted. Review the saved draft before retrying.', updated_at=now()
                    WHERE status='running' AND leased_until < now()
                    RETURNING conversation_id, user_id
            ) UPDATE public.conversations AS conversation SET status='draft'
                FROM expired WHERE conversation.id=expired.conversation_id
                AND conversation.user_id=expired.user_id AND conversation.status='in_progress';
            RETURN QUERY
                UPDATE public.background_tasks AS task SET status='running', lease_token=worker_token,
                    leased_until=now() + interval '2 minutes', updated_at=now()
                WHERE task.id = (
                    SELECT pending.id FROM public.background_tasks AS pending
                    WHERE pending.status='queued' AND pending.kind IN ('generate', 'chat')
                    AND pending.user_id <> '00000000-0000-0000-0000-000000000000'
                    ORDER BY pending.created_at, pending.id FOR UPDATE SKIP LOCKED LIMIT 1
                ) RETURNING task.id::text, task.user_id::text;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.claim_background_task(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api')
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.claim_background_task(text) TO anteroom_api')


def downgrade() -> None:
    raise RuntimeError('Background work migrations are forward-only')
