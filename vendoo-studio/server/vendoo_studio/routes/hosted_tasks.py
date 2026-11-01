"""Authenticated draft work, streamed from durable, seller-owned events."""
import asyncio
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
from vendoo_studio.services import background_tasks as queue
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.service_context import current
from vendoo_studio.services.streaming import KEEPALIVE, SSE_HEADERS, sse_event, stream_finished

def require_worker(request: Request):
    worker = getattr(request.app.state, 'background_worker', None)
    if not request.app.state.background_worker_enabled or worker is None or worker.done():
        raise HTTPException(503, 'Draft generation is being configured. Try again shortly.')


router = APIRouter(prefix='/api/conversations/{conv_id}', tags=['draft tasks'])


def owned_conversation(db, conv_id, *, lock=False):
    statement = select(Conversation).where(Conversation.id == conv_id)
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(404, 'Listing not found')
    return row


def task_stream(task_id):
    sessions = current().session_factory

    def poll(sequence):
        with sessions() as db:
            task = db.get(BackgroundTask, task_id)
            if task is None:
                raise HTTPException(404, 'Task not found')
            rows = db.execute(select(BackgroundTaskEvent.sequence, BackgroundTaskEvent.data)
                              .where(BackgroundTaskEvent.task_id == task_id,
                                     BackgroundTaskEvent.sequence > sequence)
                              .order_by(BackgroundTaskEvent.sequence).limit(200)).all()
            return rows, task.status, task.error

    async def stream():
        sequence = 0
        yield KEEPALIVE
        while True:
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
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type='text/event-stream', headers=SSE_HEADERS)


def submit(db, conv_id, kind, text='', uploads=None, request=None):
    owned_conversation(db, conv_id)
    if get_listing_provider() is None:
        raise HTTPException(400, 'Connect an AI provider in Settings first.')
    repo = ConversationRepo(db)
    photos = repo.get_photos(conv_id)
    if kind == 'generate' and not photos:
        raise HTTPException(400, 'Upload product photos before generating a listing.')
    queue.require_idle(db, conv_id)
    attachments = store_uploads(uploads or [], collection_id=conv_id)
    try:
        if request is not None and anyio.from_thread.run(request.is_disconnected):
            raise HTTPException(499, 'Message upload was cancelled')
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
    return task_stream(task_id)


@router.post('/generate', dependencies=[Depends(require_worker)])
def generate(conv_id: str, resume: bool = False, db=Depends(get_seller_db)):
    owned_conversation(db, conv_id)
    existing = db.scalar(select(BackgroundTask).where(
        BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(queue.ACTIVE)))
    if existing is not None:
        task_id = existing.id
        db.rollback()
        return task_stream(task_id)
    if resume:
        return stream_finished()
    return submit(db, conv_id, 'generate')


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default='', max_length=50000)
    attachments: list[AttachmentUpload] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


# Six 10 MB images as base64, plus names and a UTF-8 prompt. Read before
# Pydantic so an oversized body never becomes an unbounded JSON allocation.
MAX_MESSAGE_BYTES = MAX_ATTACHMENTS * (MAX_ATTACHMENT_BYTES * 4 // 3 + 4096) + 500000


@router.post('/messages', dependencies=[Depends(require_worker)])
async def message(conv_id: str, request: Request, db=Depends(get_seller_db)):
    async with request.app.state.chat_upload_slots:
        await run_in_threadpool(owned_conversation, db, conv_id)
        await run_in_threadpool(db.rollback)
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
        return await run_in_threadpool(submit, db, conv_id, 'chat', body.text, body.attachments, request)


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
