"""Run claimed draft tasks using the canonical generation/chat services."""
from __future__ import annotations

import asyncio
import logging
import time
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import background_tasks as queue
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import current, use


async def _execute(task_id, token):
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.routes.chat import ChatMessage, generate_listing, run_message
    from vendoo_studio.services.listing_generate import photo_fingerprint

    with SessionLocal() as db:
        task = db.get(BackgroundTask, task_id)
        queue.check_write_lease(db, task_id, token)
        conversation_id, kind, payload = task.conversation_id, task.kind, task.payload
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
        if kind == 'generate':
            response = await generate_listing(conversation_id, db=db)
        elif kind == 'chat':
            response = await run_message(conversation_id, ChatMessage(text=payload['text']), db=db,
                                         stored_attachments=payload.get('attachments', []))
        else:
            raise ValueError('Unsupported draft task')
    frames, flushed_at, failed = [], time.monotonic(), False
    async for frame in response.body_iterator:
        if isinstance(frame, bytes):
            frame = frame.decode('utf-8')
        # The durable stream closes only after all draft preparation finishes.
        if frame == 'data: [DONE]\n\n':
            continue
        if frame.startswith('event: error\n') or frame.startswith('data: Error:'):
            failed = True
        if frame.strip() and not frame.startswith(':'):
            frames.append(frame)
        if frames and time.monotonic() - flushed_at >= .25:
            await asyncio.to_thread(queue.append_events, task_id, token, frames)
            frames, flushed_at = [], time.monotonic()
    await asyncio.to_thread(queue.append_events, task_id, token, frames)
    context = current()
    while context.spawned_tasks:
        await asyncio.gather(*list(context.spawned_tasks), return_exceptions=False)
        await asyncio.sleep(0)
    if failed:
        raise RuntimeError('The draft task failed. Review the conversation before retrying.')


async def run_claimed(task_id, owner, token):
    with use(for_seller(owner, task_lease=(task_id, token))):
        context = current()
        work = asyncio.create_task(_execute(task_id, token))

        async def heartbeat():
            while not work.done():
                await asyncio.sleep(20)
                if not work.done():
                    await asyncio.to_thread(queue.renew, task_id, token)

        pulse = asyncio.create_task(heartbeat())
        try:
            done, _pending = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
            if pulse in done:
                pulse.result()
            await work
            await asyncio.to_thread(queue.finish, task_id, token)
        except asyncio.CancelledError:
            # On shutdown the durable lease expires; never mark an incomplete
            # draft complete or silently replay it after a process restart.
            raise
        except Exception:
            try:
                await asyncio.to_thread(queue.finish, task_id, token,
                                        error='The draft task stopped. Review the saved draft and try again.')
            except queue.TaskLeaseExpired:
                pass
        finally:
            pending = {work, pulse, *context.spawned_tasks}
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def serve(engine):
    while True:
        try:
            claimed = await asyncio.to_thread(queue.claim, engine)
        except SQLAlchemyError:
            # This retries queue availability, never a claimed task or a
            # marketplace action. Database diagnostics can include parameters.
            logging.getLogger(__name__).warning('Draft queue database is temporarily unavailable')
            await asyncio.sleep(5)
            continue
        if claimed is None:
            await asyncio.sleep(1)
        else:
            await run_claimed(*claimed)
