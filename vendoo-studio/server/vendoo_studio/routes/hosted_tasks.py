"""Authenticated draft work, streamed from durable, seller-owned events."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from vendoo_studio.models.background_task import BackgroundTask, BackgroundTaskEvent
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import background_tasks as queue
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.listing_generate import photo_fingerprint
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.service_context import current
from vendoo_studio.services.streaming import KEEPALIVE, SSE_HEADERS, sse_event, stream_finished

def require_worker(request: Request):
    worker = getattr(request.app.state, 'background_worker', None)
    if not request.app.state.background_worker_enabled or worker is None or worker.done():
        raise HTTPException(503, 'Draft generation is being configured. Try again shortly.')


router = APIRouter(prefix='/api/conversations/{conv_id}', tags=['draft tasks'],
                   dependencies=[Depends(require_worker)])


def owned_conversation(db, conv_id):
    row = ConversationRepo(db).get(conv_id)
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


def submit(db, conv_id, kind, text=''):
    row = owned_conversation(db, conv_id)
    if get_listing_provider() is None:
        raise HTTPException(400, 'Connect an AI provider in Settings first.')
    repo = ConversationRepo(db)
    photos = repo.get_photos(conv_id)
    if kind == 'generate' and not photos:
        raise HTTPException(400, 'Upload product photos before generating a listing.')
    task = queue.enqueue(db, conv_id, kind, {
        'revision_id': repo.current_revision_id(conv_id) or '',
        'photos': photo_fingerprint(photos), 'notes': row.notes or '', 'text': text,
    })
    return task_stream(task.id)


@router.post('/generate')
def generate(conv_id: str, resume: bool = False, db=Depends(get_seller_db)):
    owned_conversation(db, conv_id)
    existing = db.scalar(select(BackgroundTask).where(
        BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(queue.ACTIVE)))
    if existing is not None:
        return task_stream(existing.id)
    if resume:
        return stream_finished()
    return submit(db, conv_id, 'generate')


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=50000)


@router.post('/messages')
def message(conv_id: str, body: MessageInput, db=Depends(get_seller_db)):
    return submit(db, conv_id, 'chat', body.text)


@router.post('/generate/cancel')
@router.post('/messages/cancel')
@router.post('/stop')
def cancel(conv_id: str, db=Depends(get_seller_db)):
    owned_conversation(db, conv_id)
    tasks = db.scalars(select(BackgroundTask).where(
        BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(queue.ACTIVE))).all()
    for task in tasks:
        queue.cancel(db, task.id)
    return {'ok': True}
