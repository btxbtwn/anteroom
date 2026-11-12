"""Run claimed draft tasks and assistant answers using the canonical services."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import background_tasks as queue
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.background_loop import BackgroundLoop
from vendoo_studio.services.service_context import current, use

# Handlers for queued work that is not a draft task, by kind. The assistant's
# route registers its answer here, so this service never imports a route.
_handlers = {}


def register(kind, handler):
    """``handler(task_id, token, task)`` runs one claimed task and completes or fails it itself."""
    _handlers[kind] = handler


def _claimed_task(db, task_id, token):
    """The claimed task's listing, kind, payload and handler, checked against its lease and draft."""
    from vendoo_studio.services.listing_generate import photo_fingerprint

    task = db.get(BackgroundTask, task_id)
    queue.check_write_lease(db, task_id, token)
    if task is None:
        raise queue.TaskLeaseExpired()
    conversation_id, kind, payload = task.conversation_id, task.kind, task.payload
    if (handler := _handlers.get(kind)) is not None:
        # The handler runs after this session closes, with the row detached:
        # reading an attribute a rollback expired would check a connection
        # out again and hold it for the whole model answer.
        db.expunge(task)
    else:
        if conversation_id is None:
            raise ValueError('A draft task needs its listing')
        repo = ConversationRepo(db)
        conversation = repo.get(conversation_id)
        if (conversation is None
                or (repo.current_revision_id(conversation_id) or '') != payload['revision_id']
                or photo_fingerprint(repo.get_photos(conversation_id)) != payload['photos']
                or (conversation.notes or '') != payload['notes']):
            raise RuntimeError('The draft changed before this task started. Review it and retry.')
    # Release the lease lock before waiting for a provider. Every save
    # obtains it again through SellerDatabaseSession.before_flush.
    db.rollback()
    return conversation_id, kind, payload, handler, task


async def _execute(task_id, token):
    """Run one claimed task. Returns True when the handler already completed it."""
    from vendoo_studio.database import in_worker_session
    from vendoo_studio.routes.chat import ChatMessage, generate_listing, run_message

    context = current()
    if context is None:
        raise RuntimeError("Hosted draft work requires a seller context")
    await asyncio.to_thread(context.settings.read)
    conversation_id, kind, payload, handler, task = await in_worker_session(_claimed_task, task_id, token)
    if handler is not None:
        return await handler(task_id, token, task)
    assert conversation_id is not None  # a draft task without one was refused above
    if kind == 'generate':
        response = await generate_listing(conversation_id)
    elif kind == 'chat':
        response = await run_message(conversation_id, ChatMessage(text=payload['text']),
                                     stored_attachments=payload.get('attachments', []))
    else:
        raise ValueError('Unsupported draft task')
    frames, flushed_at, failed = [], time.monotonic(), False
    async for frame in response.body_iterator:
        if isinstance(frame, (bytes, memoryview)):
            frame = bytes(frame).decode('utf-8')
        # The durable stream closes only after all draft preparation finishes.
        if frame == 'data: [DONE]\n\n':
            continue
        if frame.startswith('event: error\n') or frame.startswith('data: Error:'):
            failed = True
        if frame.strip() and not frame.startswith(':'):
            frames.append(frame)
        if frames and time.monotonic() - flushed_at >= queue.EVENT_INTERVAL:
            await asyncio.to_thread(queue.append_events, task_id, token, frames)
            frames, flushed_at = [], time.monotonic()
    await asyncio.to_thread(queue.append_events, task_id, token, frames)
    while context.spawned_tasks:
        await asyncio.gather(*list(context.spawned_tasks), return_exceptions=False)
        await asyncio.sleep(0)
    if failed:
        raise RuntimeError('The draft task failed. Review the conversation before retrying.')
    return False


async def run_claimed(task_id, owner, token, *, heartbeat_loop: BackgroundLoop):
    with use(for_seller(owner, task_lease=(task_id, token))) as context:
        work = asyncio.create_task(_execute(task_id, token))
        completed = threading.Event()

        async def heartbeat():
            while not completed.is_set():
                await asyncio.sleep(20)
                if not completed.is_set():
                    await asyncio.to_thread(queue.renew, task_id, token)

        # Canonical generation still makes synchronous database calls. Its
        # event loop may block, but lease renewal must keep running for every
        # draft, in that draft's captured seller context.
        pulse = asyncio.wrap_future(heartbeat_loop.run(heartbeat()))
        try:
            done, _pending = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
            if pulse in done:
                pulse.result()
            if not await work:
                await asyncio.to_thread(queue.finish, task_id, token)
        except asyncio.CancelledError:
            # On shutdown the durable lease expires; never mark an incomplete
            # draft complete or silently replay it after a process restart.
            raise
        except queue.TaskStopped:
            # Stop already settled the task and freed the draft; just end the work.
            logging.getLogger(__name__).info('Draft task stopped by the seller')
        except queue.TaskLeaseExpired:
            # Terminal saves revoke the lease too. This worker no longer owns
            # the row and must not try to mark it failed. Actual expiry is
            # settled by the queue's claim sweep, without replaying work.
            logging.getLogger(__name__).warning('Draft task lease lost; stopping worker')
        except Exception as exc:
            # Exception messages and formatted tracebacks can include private
            # SQL parameters. Record only the type and source locations.
            locations = ' > '.join(f'{frame.filename.rsplit("/", 1)[-1]}:{frame.lineno}:{frame.name}'
                                   for frame in traceback.extract_tb(exc.__traceback__))
            logging.getLogger(__name__).error('Draft task failed (%s at %s)', type(exc).__name__, locations)
            try:
                await asyncio.to_thread(queue.finish, task_id, token,
                                        error='The draft task stopped. Review the saved draft and try again.')
            except queue.TaskLeaseExpired:
                pass
        finally:
            completed.set()
            pending = {work, pulse, *context.spawned_tasks}
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def serve(engine, stopping):
    active = set()
    heartbeats = BackgroundLoop('anteroom-draft-leases')

    def settled(task):
        active.discard(task)
        if not task.cancelled() and task.exception() is not None:
            # Exception text can contain private database parameters.
            logging.getLogger(__name__).error('Draft task worker stopped unexpectedly')

    try:
        while not stopping.is_set():
            try:
                claimed = await asyncio.to_thread(queue.claim, engine)
            except SQLAlchemyError:
                # Retry queue availability, never a claimed task or marketplace action.
                logging.getLogger(__name__).warning('Draft queue database is temporarily unavailable')
                await asyncio.sleep(5)
                continue
            if claimed is None:
                await asyncio.sleep(1)
            else:
                task = asyncio.create_task(run_claimed(*claimed, heartbeat_loop=heartbeats))
                active.add(task)
                task.add_done_callback(settled)
        # Stop claiming on shutdown, but let every claimed draft save first.
        await asyncio.gather(*active, return_exceptions=True)
    finally:
        remaining = list(active)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        await asyncio.to_thread(heartbeats.stop)
