"""Authenticated draft work, streamed from durable, seller-owned events."""
import asyncio
from datetime import UTC, datetime, timedelta

import anyio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from vendoo_studio.routes.attachments import AttachmentUpload, store_uploads
from vendoo_studio.services.chat_attachments import MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, delete_attachments
from sqlalchemy import select

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.background_task import BackgroundTask, BackgroundTaskEvent
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import background_tasks as queue, task_signals
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.service_context import current
from vendoo_studio.services.streaming import KEEPALIVE, SSE_HEADERS, sse_event, stream_finished

def drafts_run(app) -> bool:
    """Whether queued drafts will run: on the worker Machines for a web process, otherwise here."""
    if app.state.process_role == 'web':
        return True
    worker = getattr(app.state, 'background_worker', None)
    return bool(app.state.background_worker_enabled and worker is not None and not worker.done())


def schedules_run(app) -> bool:
    """Whether the schedulers (watchdog, offers, promotions, sales) run on some Machine.

    A web process never runs them; the worker Machine holding their lease does.
    Elsewhere they run on whichever Machine holds the lease, so one still waiting
    for it during a deploy offers what they serve.
    """
    if app.state.process_role == 'web':
        return True
    handoff = getattr(app.state, 'handoff', None)
    return bool(app.state.background_worker_enabled and handoff is not None and not handoff.done())


def require_worker(request: Request):
    if not drafts_run(request.app):
        raise HTTPException(503, 'Draft generation is being configured. Try again shortly.')


router = APIRouter(prefix='/api/conversations/{conv_id}', tags=['draft tasks'])


def owned_conversation(db, conv_id, *, lock=False):
    statement = select(Conversation).where(Conversation.id == conv_id)
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(404, 'Listing not found')
    return row


def task_stream(task_id):
    context = current()
    assert context is not None
    sessions = context.session_factory

    def poll(sequence):
        with sessions() as db:
            task = db.get(BackgroundTask, task_id)
            if task is None:
                raise HTTPException(404, 'Task not found')
            if (task.status == 'queued'
                    and datetime.now(UTC) - task.created_at > timedelta(seconds=queue.UNCLAIMED_LIMIT_S)
                    and queue.expire_unclaimed(db, task_id)):
                return [], 'failed', queue.WORKER_UNAVAILABLE
            rows = db.execute(select(BackgroundTaskEvent.sequence, BackgroundTaskEvent.data)
                              .where(BackgroundTaskEvent.task_id == task_id,
                                     BackgroundTaskEvent.sequence > sequence)
                              .order_by(BackgroundTaskEvent.sequence).limit(200)).all()
            return rows, task.status, task.error

    async def stream():
        sequence = 0
        yield KEEPALIVE
        # Read only when Postgres announces new progress or a status change for
        # this task, or after task_signals' fallback; see services/task_signals.py.
        with task_signals.subscribe(task_id) as signal:
            while True:
                signal.clear()
                rows, status, error = await asyncio.to_thread(poll, sequence)
                for number, frame in rows:
                    sequence = number
                    yield frame
                if len(rows) == 200:
                    continue
                if status not in queue.ACTIVE:
                    if status == 'failed':
                        yield sse_event('error', error or 'Review the saved draft before retrying.')
                    yield 'data: [DONE]\n\n'
                    return
                yield KEEPALIVE
                await signal.wait()
    return StreamingResponse(stream(), media_type='text/event-stream', headers=SSE_HEADERS)


def submit(db, conv_id, kind, text='', uploads=None, request=None):
    owned_conversation(db, conv_id)
    # Provider selection reads seller settings and may read Vault in sessions
    # of its own. Return the ownership-read connection before requesting those;
    # require_idle/enqueue below recheck ownership under the submission lock.
    db.rollback()
    if get_listing_provider() is None:
        raise HTTPException(400, 'Connect an AI provider in Settings first.')
    repo = ConversationRepo(db)
    photos = repo.get_photos(conv_id)
    if kind == 'generate' and not photos:
        raise HTTPException(400, 'Upload product photos before generating a listing.')
    queue.require_idle(db, conv_id)
    attachments = []
    if uploads:
        # Each Storage upload can take 20 s. Release the conversation lock and
        # the pooled connection first, then check again under the lock.
        db.rollback()
        attachments = store_uploads(uploads, collection_id=conv_id)
    try:
        if request is not None and anyio.from_thread.run(request.is_disconnected):
            raise HTTPException(499, 'Message upload was cancelled')
        if uploads:
            queue.require_idle(db, conv_id)
        task = queue.enqueue(db, conv_id, kind, queue.draft_payload(db, conv_id, text, attachments))
    except Exception:
        db.rollback()
        try:
            delete_attachments(attachments)
        except HTTPException:
            pass
        raise
    task_id = task.id
    db.rollback()
    return task_id


def _seller_context():
    context = current()
    if context is None:
        raise HTTPException(401, 'A verified seller session is required')
    return context


def _seller_sessions():
    return _seller_context().session_factory


def limit_submissions(request: Request):
    """Abuse protection on new background work; see services/hosted_limits.py."""
    request.app.state.submission_limit.take(_seller_context().seller_id)


@router.post('/generate', dependencies=[Depends(require_worker)])
def generate(conv_id: str, request: Request, resume: bool = False):
    # Open and close the seller session before streaming. A Depends(get_seller_db)
    # session stays checked out until the SSE generator finishes, which drained
    # the five-connection hosted pool while waiting on a Mac.
    with _seller_sessions()() as db:
        owned_conversation(db, conv_id)
        existing = db.scalar(select(BackgroundTask).where(
            BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(queue.ACTIVE)))
        if existing is not None:
            task_id = existing.id
            db.rollback()
        elif resume:
            return stream_finished()
        else:
            limit_submissions(request)
            task_id = submit(db, conv_id, 'generate')
    return task_stream(task_id)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default='', max_length=50000)
    attachments: list[AttachmentUpload] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


# Six 10 MB images as base64, plus names and a UTF-8 prompt. Read before
# Pydantic so an oversized body never becomes an unbounded JSON allocation.
MAX_MESSAGE_BYTES = MAX_ATTACHMENTS * (MAX_ATTACHMENT_BYTES * 4 // 3 + 4096) + 500000


@router.post('/messages', dependencies=[Depends(require_worker)])
async def message(conv_id: str, request: Request):
    def authorize():
        # Reject foreign drafts before reading uploads, without retaining a
        # connection while the seller uploads images or waits for an answer.
        with _seller_sessions()() as db:
            owned_conversation(db, conv_id)

    await run_in_threadpool(authorize)
    limit_submissions(request)
    # One upload per seller at a time, and a few across sellers to bound memory.
    async with request.app.state.upload_slots.hold(_seller_context().seller_id):
        data = bytearray()
        try:
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_MESSAGE_BYTES:
                        raise HTTPException(413, 'This message has too much image data')
                    data.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, 'Image upload timed out. Try again.') from None
        try:
            body = await run_in_threadpool(MessageInput.model_validate_json, data)
        except ValidationError:
            raise HTTPException(422, 'Check the message and its images') from None
        del data
        if not body.text.strip() and not body.attachments:
            raise HTTPException(422, 'Write a message or attach an image')

        def enqueue():
            with _seller_sessions()() as db:
                owned_conversation(db, conv_id)
                db.rollback()
                return submit(db, conv_id, 'chat', body.text, body.attachments, request)

        task_id = await run_in_threadpool(enqueue)
        return task_stream(task_id)


@router.post('/generate/cancel')
@router.post('/messages/cancel')
@router.post('/stop')
def cancel(conv_id: str, db=Depends(get_seller_db)):
    owned_conversation(db, conv_id, lock=True)
    tasks = db.scalars(select(BackgroundTask).where(
        BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(queue.ACTIVE))).all()
    for task in tasks:
        queue.cancel(db, task.id)
    return {'ok': True}
