"""Durable queue for draft generation and chat, with fenced worker leases."""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, text, update
from fastapi import HTTPException

from vendoo_studio.models.background_task import BackgroundTask, BackgroundTaskEvent
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services.service_context import current

DRAFT_KINDS = frozenset({'generate', 'chat'})
ACTIVE = ('queued', 'running')


class TaskLeaseExpired(RuntimeError):
    def __init__(self):
        super().__init__('This task no longer owns the draft. Refresh to see its saved state.')


def require_idle(db, conversation_id):
    # Share the submission lock so a manual save cannot race a queued task.
    if db.scalar(select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()) is None:
        raise HTTPException(404, 'Listing not found')
    if db.scalar(select(BackgroundTask.id).where(
            BackgroundTask.conversation_id == conversation_id, BackgroundTask.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, 'Wait for the draft task to finish, or stop it before editing.')


def enqueue(db, conversation_id, kind, payload):
    if kind not in DRAFT_KINDS:
        raise ValueError('Only draft generation and chat may enter the background queue')
    # A conversation lock serializes submissions across tabs and Machines.
    row = db.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Listing not found')
    existing = db.scalar(select(BackgroundTask).where(
        BackgroundTask.conversation_id == conversation_id, BackgroundTask.status.in_(ACTIVE)))
    if existing is not None:
        raise HTTPException(409, 'Wait for the current draft task to finish')
    task = BackgroundTask(conversation_id=conversation_id, kind=kind, payload=payload)
    db.add(task)
    db.commit()
    return task


def claim(engine):
    """Called by the trusted worker, before entering the claimed seller scope."""
    token = str(uuid.uuid4())
    with engine.begin() as connection:
        row = connection.execute(text('SELECT * FROM anteroom_private.claim_background_task(:token)'),
                                 {'token': token}).first()
    return (row.task_id, row.owner_id, token) if row else None


def _sessions():
    context = current()
    if context is None:
        raise RuntimeError('Background work requires a verified seller context')
    return context.session_factory


def renew(task_id, token):
    with _sessions()() as db:
        changed = db.execute(update(BackgroundTask).where(
            BackgroundTask.id == task_id, BackgroundTask.status == 'running',
            BackgroundTask.lease_token == token, BackgroundTask.leased_until > func.now(),
        ).values(leased_until=func.now() + text("interval '2 minutes'"), updated_at=func.now())).rowcount
        db.commit()
    if changed != 1:
        raise TaskLeaseExpired()


def finish(task_id, token, *, error=None):
    with _sessions()() as db:
        conversation_id = db.execute(update(BackgroundTask).where(
            BackgroundTask.id == task_id, BackgroundTask.status == 'running',
            BackgroundTask.lease_token == token, BackgroundTask.leased_until > func.now(),
        ).values(status='failed' if error else 'completed', error=error,
                 lease_token=None, leased_until=None, updated_at=func.now())
            .returning(BackgroundTask.conversation_id)).scalar_one_or_none()
        if conversation_id is not None:
            db.execute(update(Conversation).where(Conversation.id == conversation_id, Conversation.status == 'in_progress')
                       .values(status='draft'))
        db.commit()
    if conversation_id is None:
        raise TaskLeaseExpired()


def cancel(db, task_id):
    row = db.scalar(select(BackgroundTask).where(BackgroundTask.id == task_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Task not found')
    if row.status in ACTIVE:
        row.status, row.lease_token, row.leased_until = 'cancelled', None, None
        db.execute(update(Conversation).where(Conversation.id == row.conversation_id, Conversation.status == 'in_progress')
                   .values(status='draft'))
        db.commit()
    return row


def check_write_lease(db, task_id, token):
    # Called in the writing transaction. Cancellation/expiry and the last save
    # serialize on this lock, so a replaced worker cannot keep writing later.
    valid = db.scalar(select(BackgroundTask.id).where(
        BackgroundTask.id == task_id, BackgroundTask.status == 'running',
        BackgroundTask.lease_token == token, BackgroundTask.leased_until > func.now(),
    ).with_for_update())
    if valid is None:
        raise TaskLeaseExpired()


def append_events(task_id, token, frames):
    if not frames:
        return
    with _sessions()() as db:
        check_write_lease(db, task_id, token)
        sequence = db.scalar(select(func.max(BackgroundTaskEvent.sequence))
                             .where(BackgroundTaskEvent.task_id == task_id)) or 0
        for offset, frame in enumerate(frames, 1):
            db.add(BackgroundTaskEvent(task_id=task_id, sequence=sequence + offset, data=frame))
        db.commit()


def delete_history(db, conversation_id):
    require_idle(db, conversation_id)
    task_ids = select(BackgroundTask.id).where(BackgroundTask.conversation_id == conversation_id)
    db.execute(delete(BackgroundTaskEvent).where(BackgroundTaskEvent.task_id.in_(task_ids)))
    db.execute(delete(BackgroundTask).where(BackgroundTask.conversation_id == conversation_id))
