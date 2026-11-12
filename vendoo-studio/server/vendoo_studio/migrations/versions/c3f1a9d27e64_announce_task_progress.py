"""Announce new draft and assistant progress so open streams stop polling for it

Postgres sends ``pg_notify('anteroom_task_events', <task id>)`` when a task
gets new events or changes status, on commit of the writing transaction. The
notice carries only the task id; see services/task_signals.py.

Revision ID: c3f1a9d27e64
Revises: 5d2a8c71e4f9
Create Date: 2026-11-12 12:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = 'c3f1a9d27e64'
down_revision = '5d2a8c71e4f9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return  # The Mac's SQLite has one process and no hosted task streams.
    op.execute("""
        CREATE SCHEMA IF NOT EXISTS anteroom_private;
        CREATE OR REPLACE FUNCTION anteroom_private.announce_task_progress()
        RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $$
        BEGIN
            -- Identical notices in one transaction arrive once. Each branch is
            -- planned only when it runs, so each reads its own table's column.
            IF TG_TABLE_NAME = 'background_task_events' THEN
                PERFORM pg_notify('anteroom_task_events', NEW.task_id);
            ELSE
                PERFORM pg_notify('anteroom_task_events', NEW.id);
            END IF;
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.announce_task_progress() FROM PUBLIC;
        DROP TRIGGER IF EXISTS announce_task_progress ON public.background_task_events;
        CREATE TRIGGER announce_task_progress AFTER INSERT ON public.background_task_events
            FOR EACH ROW EXECUTE FUNCTION anteroom_private.announce_task_progress();
        DROP TRIGGER IF EXISTS announce_task_status ON public.background_tasks;
        CREATE TRIGGER announce_task_status AFTER UPDATE OF status ON public.background_tasks
            FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
            EXECUTE FUNCTION anteroom_private.announce_task_progress();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("""
        DROP TRIGGER IF EXISTS announce_task_progress ON public.background_task_events;
        DROP TRIGGER IF EXISTS announce_task_status ON public.background_tasks;
        DROP FUNCTION IF EXISTS anteroom_private.announce_task_progress();
    """)
