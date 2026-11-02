"""Durable business-assistant answers in the seller's background queue

Revision ID: 5e7a2c9b4d18
Revises: d574efabc39a
Create Date: 2026-11-02 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '5e7a2c9b4d18'
down_revision = 'd574efabc39a'
branch_labels = None
depends_on = None

ONE_SUBJECT = (
    "CASE WHEN kind = 'assistant' THEN conversation_id IS NULL AND assistant_thread_id IS NOT NULL "
    "ELSE conversation_id IS NOT NULL AND assistant_thread_id IS NULL END"
)


def upgrade() -> None:
    bind = op.get_bind()
    # An assistant answer belongs to an assistant thread, not to a listing.
    with op.batch_alter_table('background_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('assistant_thread_id', sa.String(), nullable=True))
        batch_op.alter_column('conversation_id', existing_type=sa.String(), nullable=True)
        batch_op.create_index('ix_background_tasks_assistant_thread_id', ['assistant_thread_id'], unique=False)
        batch_op.create_check_constraint('ck_background_tasks_one_subject', ONE_SUBJECT)
    # One answer at a time per seller, across tabs and Machines.
    op.create_index(
        'uq_background_tasks_one_assistant_answer', 'background_tasks', ['user_id'], unique=True,
        sqlite_where=sa.text("kind = 'assistant' AND status IN ('queued', 'running')"),
        postgresql_where=sa.text("kind = 'assistant' AND status IN ('queued', 'running')"),
    )
    if bind.dialect.name != 'postgresql':
        return
    # Same claim as 56b5ee7250ec, now also for assistant answers. An expired
    # answer saved nothing (its save completes the task in one transaction),
    # so it is marked interrupted for the seller to retry, never replayed.
    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.claim_background_task(worker_token text)
        RETURNS TABLE(task_id text, owner_id text) LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
        BEGIN
            IF worker_token IS NULL OR worker_token !~ '^[0-9a-f-]{36}$' THEN
                RAISE invalid_parameter_value USING MESSAGE='Invalid worker lease';
            END IF;
            -- An expired generation may have saved a partial revision. Never
            -- replay automatically and overwrite edits made since that save.
            WITH expired AS (
                UPDATE public.background_tasks SET status='failed', lease_token=NULL,
                    error=CASE WHEN kind='assistant'
                        THEN 'This answer was interrupted before it finished. Nothing from it was saved. Retry to ask again.'
                        ELSE 'This task was interrupted. Review the saved draft before retrying.' END,
                    updated_at=now()
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
                    WHERE pending.status='queued' AND pending.kind IN ('generate', 'chat', 'assistant')
                    AND pending.user_id <> '00000000-0000-0000-0000-000000000000'
                    ORDER BY pending.created_at, pending.id FOR UPDATE SKIP LOCKED LIMIT 1
                ) RETURNING task.id::text, task.user_id::text;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.claim_background_task(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.claim_background_task(text) TO anteroom_api')


def downgrade() -> None:
    raise RuntimeError('Background work migrations are forward-only')
