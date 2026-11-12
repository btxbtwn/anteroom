"""Durable queue for draft generation, draft chat and assistant answers, with fenced worker leases."""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, text, update
from fastapi import HTTPException

from vendoo_studio.models.background_task import BackgroundTask, BackgroundTaskEvent
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services.service_context import current

DRAFT_KINDS = frozenset({'generate', 'chat'})
ASSISTANT = 'assistant'
ACTIVE = ('queued', 'running')
# How often a running task stores its progress and an open stream reads it.
# Each store is a short transaction on a shared pooled connection, so writing
# more often than readers poll only takes connections from the seller's pages.
EVENT_INTERVAL = .5
# A worker claims a queued task within seconds. One still queued after this
# long has no worker to run it, and would otherwise keep its draft busy.
UNCLAIMED_LIMIT_S = 300
WORKER_UNAVAILABLE = 'The draft worker is unavailable; try again shortly.'


class TaskLeaseExpired(RuntimeError):
    def __init__(self):
        super().__init__('This task no longer owns the draft. Refresh to see its saved state.')


class TaskStopped(TaskLeaseExpired):
    """The seller stopped the task: an expected end, not a failure."""


def _lost(db, task_id):
    if db.scalar(select(BackgroundTask.status).where(BackgroundTask.id == task_id)) == 'cancelled':
        return TaskStopped()
    return TaskLeaseExpired()


def require_idle(db, conversation_id):
    # Share the submission lock so a manual save cannot race a queued task.
    if db.scalar(select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()) is None:
        raise HTTPException(404, 'Listing not found')
    if db.scalar(select(BackgroundTask.id).where(
            BackgroundTask.conversation_id == conversation_id, BackgroundTask.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, 'Wait for the draft task to finish, or stop it before editing.',
                            headers={'X-Anteroom-Reason': 'draft_busy'})


def draft_payload(db, conversation_id, text='', attachments=()):
    """The inputs a draft task checks again before it starts, so a changed draft is never overwritten."""
    from vendoo_studio.repositories.queries import ConversationRepo
    from vendoo_studio.services.listing_generate import photo_fingerprint

    repo = ConversationRepo(db)
    row = repo.get(conversation_id)
    return {'revision_id': repo.current_revision_id(conversation_id) or '',
            'photos': photo_fingerprint(repo.get_photos(conversation_id)), 'notes': (row.notes if row else '') or '',
            'text': text, 'attachments': list(attachments)}


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
        # Lease metadata is not a listing edit. Broadcasting each heartbeat
        # makes every open device refetch its editor while generation runs.
        db.info['quiet_changes'] = True
        changed = db.execute(update(BackgroundTask).where(
            BackgroundTask.id == task_id, BackgroundTask.status == 'running',
            BackgroundTask.lease_token == token, BackgroundTask.leased_until > func.now(),
        ).values(leased_until=func.now() + text("interval '2 minutes'"), updated_at=func.now())).rowcount
        db.commit()
        if changed != 1:
            raise _lost(db, task_id)


def finish(task_id, token, *, error=None):
    with _sessions()() as db:
        finished = db.execute(update(BackgroundTask).where(
            BackgroundTask.id == task_id, BackgroundTask.status == 'running',
            BackgroundTask.lease_token == token, BackgroundTask.leased_until > func.now(),
        ).values(status='failed' if error else 'completed', error=error,
                 lease_token=None, leased_until=None, updated_at=func.now())
            .returning(BackgroundTask.id, BackgroundTask.conversation_id)).first()
        if finished is not None and finished.conversation_id is not None:
            db.execute(update(Conversation).where(Conversation.id == finished.conversation_id, Conversation.status == 'in_progress')
                       .values(status='draft'))
        db.commit()
        if finished is None:
            raise _lost(db, task_id)


def expire_unclaimed(db, task_id):
    """Fail a task no worker claimed within UNCLAIMED_LIMIT_S and free its draft.

    Returns False when a worker claimed it meanwhile; the update and the
    worker's claim cannot both succeed.
    """
    expired = db.execute(update(BackgroundTask).where(
        BackgroundTask.id == task_id, BackgroundTask.status == 'queued',
        BackgroundTask.created_at < func.now() - text(f"interval '{UNCLAIMED_LIMIT_S} seconds'"),
    ).values(status='failed', error=WORKER_UNAVAILABLE, updated_at=func.now())
        .returning(BackgroundTask.conversation_id)).first()
    if expired is not None and expired.conversation_id is not None:
        db.execute(update(Conversation).where(Conversation.id == expired.conversation_id, Conversation.status == 'in_progress')
                   .values(status='draft'))
    db.commit()
    return expired is not None


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
        raise _lost(db, task_id)


def append_events(task_id, token, frames):
    if not frames:
        return
    with _sessions()() as db:
        # A worker's session checks its lease when the events flush (before_flush).
        if db.info.get('background_lease') != (task_id, token):
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


def add_answer(db, thread_id, payload):
    """Queue the seller's assistant answer in the caller's transaction.

    A second active answer for the same seller fails that transaction's commit
    (``uq_background_tasks_one_assistant_answer``).
    """
    task = BackgroundTask(kind=ASSISTANT, assistant_thread_id=thread_id, payload=payload)
    db.add(task)
    return task


def active_answer(db, *, lock=False):
    statement = select(BackgroundTask).where(BackgroundTask.kind == ASSISTANT, BackgroundTask.status.in_(ACTIVE))
    return db.scalar(statement.with_for_update() if lock else statement)


def latest_answer(db, thread_id):
    return db.scalar(select(BackgroundTask).where(
        BackgroundTask.kind == ASSISTANT, BackgroundTask.assistant_thread_id == thread_id,
    ).order_by(BackgroundTask.created_at.desc(), BackgroundTask.id.desc()).limit(1))


def complete_in(db, task_id, token, frames=()):
    """Write the last events and complete the task in the caller's transaction.

    The caller commits its own save with this, so a saved answer is never also
    left running to be marked interrupted. The task row stays locked until
    that commit, so a Stop cannot slip in between.
    """
    check_write_lease(db, task_id, token)
    sequence = db.scalar(select(func.max(BackgroundTaskEvent.sequence))
                         .where(BackgroundTaskEvent.task_id == task_id)) or 0
    for offset, frame in enumerate(frames, 1):
        db.add(BackgroundTaskEvent(task_id=task_id, sequence=sequence + offset, data=frame))
    db.flush()
    db.execute(update(BackgroundTask).where(BackgroundTask.id == task_id).values(
        status='completed', lease_token=None, leased_until=None, updated_at=func.now()))
    # Later flushes in this transaction belong to the completed task.
    db.info.pop('background_lease', None)


def journal(db, task_id):
    return list(db.scalars(select(BackgroundTaskEvent.data).where(BackgroundTaskEvent.task_id == task_id)
                           .order_by(BackgroundTaskEvent.sequence)))


def delete_answers(db, thread_id):
    task_ids = select(BackgroundTask.id).where(BackgroundTask.assistant_thread_id == thread_id)
    db.execute(delete(BackgroundTaskEvent).where(BackgroundTaskEvent.task_id.in_(task_ids)))
    db.execute(delete(BackgroundTask).where(BackgroundTask.assistant_thread_id == thread_id))
