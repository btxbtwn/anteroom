"""fair_background_task_claiming

Revision ID: ae814be47ba5
Revises: a3c9e7d12f48
Create Date: 2026-11-21 22:58:07.420680
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'ae814be47ba5'
down_revision = 'a3c9e7d12f48'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Prioritize interactive answers and draft chat over bulk generation, and
    # favor sellers with fewer tasks already running so one seller's bulk
    # regeneration cannot stall another seller. A solo seller still uses every
    # open worker slot with no artificial per-seller cap.
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
                    ORDER BY
                        -- After a minute, claim in age order ahead of fresh work so
                        -- later interactive submissions cannot starve old generations.
                        CASE
                            WHEN pending.created_at <= now() - interval '1 minute' THEN 0
                            WHEN pending.kind IN ('assistant', 'chat') THEN 1
                            ELSE 2
                        END,
                        CASE WHEN pending.created_at <= now() - interval '1 minute'
                            THEN pending.created_at END,
                        (
                            SELECT count(*) FROM public.background_tasks AS active
                            WHERE active.user_id = pending.user_id
                              AND active.status = 'running'
                              AND active.leased_until >= now()
                        ),
                        pending.created_at,
                        pending.id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                ) RETURNING task.id::text, task.user_id::text;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.claim_background_task(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.claim_background_task(text) TO anteroom_api')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE OR REPLACE FUNCTION anteroom_private.claim_background_task(worker_token text)
        RETURNS TABLE(task_id text, owner_id text) LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
        BEGIN
            IF worker_token IS NULL OR worker_token !~ '^[0-9a-f-]{36}$' THEN
                RAISE invalid_parameter_value USING MESSAGE='Invalid worker lease';
            END IF;
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

