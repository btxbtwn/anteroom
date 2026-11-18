"""The business assistant's chat.

Asking reads Studio's data and changes none of it. An answer may propose
changes to listings; they are written only when the seller applies them here,
through the same save the listing editor uses. Nothing here sends, lists,
delists or publishes.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal, get_db, in_worker_session
from vendoo_studio.models.assistant import AssistantMessage, AssistantThread
from vendoo_studio.routes.attachments import AttachmentUpload, store_uploads
from vendoo_studio.services import assistant_changes, assistant_memory, background_tasks, business_assistant
from vendoo_studio.services.chat_attachments import attach_to_last_user_turn
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.provider_deadlines import ProviderStalled, provider_label
from vendoo_studio.services.streaming import (
    KEEPALIVE,
    SSE_HEADERS,
    GenerationRun,
    active_generation,
    await_with_pulses,
    iter_with_keepalives,
    sse_data,
    sse_event,
    start_generation,
    stop_generation,
    stream_finished,
    stream_generation,
    wait_generation,
)
from vendoo_studio.services.user_settings import (
    MAX_BUSINESS_NOTE_CHARS,
    get_business_note,
    set_business_note,
)

log = logging.getLogger("vendoo_studio.assistant")
timing_log = logging.getLogger("vendoo_studio.assistant.timing")

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# The one answer that can be in progress, in the registry listing generations use.
ANSWER_RUN = "assistant"
# Which conversation each seller's answer belongs to, so another thread does not replay it.
_answering_thread_ids: dict[str, str] = {}


def _answer_run() -> str:
    """One answer at a time per seller. On the web, each account has its own."""
    from vendoo_studio.services.service_context import current

    context = current()
    return f"{ANSWER_RUN}:{context.seller_id}" if context is not None else ANSWER_RUN
# How a listing's revision history names a save the seller applied from here.
REVISION_SOURCE = "assistant"
UNREADABLE_CHANGES = (
    "I could not put those changes in a form Anteroom can apply, so nothing is waiting for review. Ask me again."
)


class AssistantQuestion(BaseModel):
    text: str = Field(default="", max_length=8000)
    attachments: list[AttachmentUpload] = Field(default_factory=list)
    thread_id: str | None = None


class BusinessNote(BaseModel):
    note: str = Field(max_length=MAX_BUSINESS_NOTE_CHARS)


@router.get("/threads")
def get_threads(db: Session = Depends(get_db)):
    return [business_assistant.thread_view(row) for row in business_assistant.list_threads(db)]


@router.post("/threads")
def create_thread(db: Session = Depends(get_db)):
    return business_assistant.thread_view(business_assistant.create_thread(db))


@router.delete("/threads/{thread_id}")
def remove_thread(thread_id: str, db: Session = Depends(get_db)):
    from vendoo_studio.services.chat_attachments import delete_attachments

    if _queued():
        answering = background_tasks.active_answer(db)
        if answering is not None and answering.assistant_thread_id == thread_id:
            raise HTTPException(409, "That conversation is still being answered.")
    elif thread_id == _answering_thread_ids.get(_answer_run()) and active_generation(_answer_run()) is not None:
        raise HTTPException(409, "That conversation is still being answered.")
    images = [item for (batch,) in db.query(AssistantMessage.attachments).filter(AssistantMessage.thread_id == thread_id)
              for item in (batch or [])]
    if db.get(AssistantThread, thread_id) is None:
        raise HTTPException(404, "That conversation is gone.")
    background_tasks.delete_answers(db, thread_id)
    if not business_assistant.delete_thread(db, thread_id):
        raise HTTPException(404, "That conversation is gone.")
    # The conversation is gone; its images go with it.
    delete_attachments(images)
    return {"ok": True}


def _require_thread(db: Session, thread_id: str | None) -> None:
    if thread_id and db.get(AssistantThread, thread_id) is None:
        raise HTTPException(404, "That conversation is gone.")


@router.get("/messages")
def get_messages(thread_id: str | None = None, db: Session = Depends(get_db)):
    _require_thread(db, thread_id)
    return [business_assistant.message_view(row) for row in business_assistant.list_messages(db, thread_id)]


@router.get("/note")
def get_note():
    return {"note": get_business_note()}


@router.put("/note")
def save_note(body: BusinessNote):
    return {"note": set_business_note(body.note)}


@router.get("/memory")
def get_memory(db: Session = Depends(get_db)):
    """Facts the assistant keeps across conversations."""
    return {"memories": assistant_memory.list_memories(db)}


@router.delete("/memory/{memory_id}")
def forget_memory(memory_id: str, db: Session = Depends(get_db)):
    """Drop one fact the seller says is wrong."""
    if not assistant_memory.forget(db, memory_id):
        raise HTTPException(404, "That memory is no longer kept.")
    return {"memories": assistant_memory.list_memories(db)}


def _propose(block: str) -> dict | None:
    db = SessionLocal()
    try:
        return assistant_changes.propose(db, block)
    finally:
        db.close()


def _prepare(question: str, attachments: list[dict] | None = None, thread_id: str | None = None):
    """Read the shop and build the prompt, on a worker thread with its own session."""
    db = SessionLocal()
    try:
        shop = business_assistant.read_shop(db)
        messages = business_assistant.build_messages(db, question, shop, thread_id)
        return attach_to_last_user_turn(messages, attachments), shop
    finally:
        db.close()


def elapsed_ms_since(moment: datetime) -> int:
    """Whole milliseconds since a stored time, never negative and capped like every timing field."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - moment).total_seconds()
    return min(business_assistant.TIMING_CAP_MS, max(0, round(seconds * 1000)))


async def _answer(
    run: GenerationRun, provider, question: str, attachments: list[dict] | None = None,
    thread_id: str | None = None, settle: Callable[[Session, str | None], None] | None = None,
    queued_at: datetime | None = None,
) -> str | None:
    """Answer in the background and publish each piece to whoever is following.

    The answer belongs to Studio, not to the connection that asked: a phone
    that locks or drops off the network mid-answer finds it when it comes back.
    One log line says where the time went (``AnswerTiming``), however it ends.
    """
    timing = business_assistant.AnswerTiming()
    if queued_at is not None:
        timing.queue_wait_ms = elapsed_ms_since(queued_at)
    outcome = "cancelled"
    answer = ""
    changes = None
    memory_block = None
    automation_block = None
    shop = None
    error = None
    preparing = True
    try:
        run.publish(sse_event("status", "Reading your shop…"))
        try:
            messages, shop = await await_with_pulses(
                run, asyncio.to_thread(_prepare, question, attachments, thread_id), [],
            )
        finally:
            timing.prepare_ms = business_assistant.elapsed_ms(timing.started)
        preparing = False
        async for item in iter_with_keepalives(business_assistant.answer(provider, messages, shop, timing)):
            if item is None:
                # A comment alone does not keep some mobile connections open; pulse repeats the status too.
                run.pulse()
                continue
            kind, text = item
            if kind == "text":
                answer += text
                run.publish(sse_data(text))
            elif kind == "memory":
                memory_block = text
            elif kind == "automation":
                automation_block = text
                if not answer.strip():
                    note = "This automation is ready for you to review. Nothing runs until you confirm it."
                    answer += note
                    run.publish(sse_data(note))
            elif kind == "changes":
                changes = await asyncio.to_thread(_propose, text)
                if changes is None:
                    note = UNREADABLE_CHANGES
                elif not answer.strip():
                    note = "These changes are ready for you to review."
                else:
                    continue
                note = f"\n\n{note}" if answer.strip() else note
                answer += note
                run.publish(sse_data(note))
            elif kind == "error":
                error = text
                run.publish(sse_event("error", error))
                break
            else:
                run.publish(sse_event(kind, text))
        if not answer.strip() and error is None:
            error = "The assistant returned an empty answer. Ask again."
            run.publish(sse_event("error", error))
        outcome = "error" if error else "answered"
    except background_tasks.TaskStopped:
        outcome = "cancelled"
        raise
    except background_tasks.TaskLeaseExpired:
        outcome = "lease_lost"
        raise
    except Exception as exc:
        outcome = "error"
        _log_answer_failure(exc)
        # Provider/SQL exception bodies can include credentials and private shop
        # data. Only our deadline error has a controlled seller-facing message.
        if preparing:
            error = "Anteroom could not read your shop for this answer. Try again."
        elif isinstance(exc, ProviderStalled):
            error = str(exc)
        else:
            error = f"{provider_label(getattr(provider, 'name', None))} could not finish the assistant answer. Try again."
        run.publish(sse_event("error", error))
    finally:
        if shop is not None:
            shop.lookups.close()
        # Also reached when the seller presses Stop: keep what was written so far.
        # Off the event loop: a save that waits on SQLite's write lock must not
        # freeze every other request, or hold this answer's stream open.
        try:
            if answer.strip():
                save_started = time.monotonic()
                on_committed = None
                if isinstance(run, _Journal):
                    saved_outcome = outcome
                    on_committed = lambda: run.confirm_saved(saved_outcome)
                try:
                    await asyncio.to_thread(
                        _save_answer, provider, answer, changes, memory_block, automation_block, thread_id, settle,
                        error,
                        on_committed=on_committed,
                    )
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except background_tasks.TaskStopped:
                    outcome = "cancelled"
                    raise
                except background_tasks.TaskLeaseExpired:
                    outcome = "lease_lost"
                    raise
                except Exception:
                    outcome = "error"
                    raise
                finally:
                    timing.save_ms = business_assistant.elapsed_ms(save_started)
        finally:
            total_ms = business_assistant.elapsed_ms(timing.started)
            if isinstance(run, _Journal) and settle is not None:
                run.timing_sample = (timing, outcome, total_ms)
            else:
                _emit_timing(timing, outcome, total_ms)
    return error


def _emit_timing(timing: business_assistant.AnswerTiming, outcome: str, total_ms: int) -> None:
    slow = total_ms >= business_assistant.SLOW_ANSWER_MS
    timing_log.log(logging.WARNING if slow else logging.INFO, "%s", timing.line(outcome, total_ms))


def _log_answer_failure(exc: Exception) -> None:
    # Keep exception identity and code locations, never exception text or SQL parameters.
    locations = " > ".join(f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
                           for frame in traceback.extract_tb(exc.__traceback__))
    sqlstate = getattr(exc.orig, 'sqlstate', None) if isinstance(exc, DBAPIError) else None
    # SQLSTATE is a five-character diagnostic code, never a query or parameter.
    code = sqlstate if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum() else '-'
    log.error("business assistant failed (%s at %s; SQLSTATE %s)", type(exc).__name__, locations, code)


def _save_answer(
    provider, answer: str, changes: dict | None, memory_block: str | None,
    automation_block: str | None, thread_id: str | None, settle: Callable[[Session, str | None], None] | None = None,
    error: str | None = None,
    on_committed: Callable[[], None] | None = None,
) -> None:
    save_db = SessionLocal()
    try:
        if memory_block:
            try:
                assistant_memory.apply(save_db, memory_block)
            except Exception:
                log.exception("could not update assistant memory")
                save_db.rollback()
        automation = None
        if automation_block:
            from vendoo_studio.services import automations

            try:
                automation = {"status": "proposed", "proposal": automations.proposal_from_block(save_db, automation_block)}
            except Exception:
                log.exception("could not read the automation the assistant drafted")
                automation = {"status": "proposed", "proposal": {"ready": False, "issues": ["That automation could not be read."], "actions": []}}
        if settle is not None:
            # A queued answer completes in the same commit that saves it.
            settle(save_db, error)
        business_assistant.add_message(
            save_db, "assistant", answer,
            provider=getattr(provider, "name", None),
            model=getattr(provider, "listing_model", None),
            changes=changes,
            automation=automation,
            thread_id=thread_id,
            on_committed=on_committed,
        )
    finally:
        save_db.close()


@router.post("/messages")
async def ask(body: AssistantQuestion, request: Request):
    question = body.text.strip()
    if not question and not body.attachments:
        raise HTTPException(422, "Ask a question first.")
    if _queued():
        return await in_worker_session(_queue_question, request, question, body)
    key = _answer_run()
    if active_generation(key) is not None:
        raise HTTPException(409, "The assistant is still answering your last question.")
    provider = _require_provider()
    # The question is saved, and its session closed, before the answer starts.
    thread_id, attachments = await in_worker_session(_save_question, question, body)
    return await _start_answer(key, provider, question, attachments, thread_id)


def _save_question(db: Session, question: str, body: AssistantQuestion) -> tuple[str, list[dict]]:
    try:
        thread = business_assistant.resolve_thread(db, body.thread_id)
    except business_assistant.UnknownThread:
        raise HTTPException(404, "That conversation is gone.") from None
    # On the web, images are stored under the seller's own conversation.
    attachments = store_uploads(body.attachments, collection_id=thread.id)
    business_assistant.add_message(db, "user", question, attachments=attachments, thread_id=thread.id)
    return thread.id, attachments


async def _start_answer(key: str, provider, question: str, attachments: list[dict] | None, thread_id: str):
    _answering_thread_ids[key] = thread_id

    async def answer(run: GenerationRun) -> None:
        await _answer(run, provider, question, attachments, thread_id)

    run = start_generation(key, answer)
    await asyncio.sleep(0)
    return stream_generation(run)


def _require_provider():
    provider = get_listing_provider()
    if provider is None:
        raise HTTPException(400, "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key.")
    return provider


def _unanswered(db: Session, thread_id: str) -> AssistantMessage:
    """The thread's last turn, when it is a question nothing answered."""
    if db.get(AssistantThread, thread_id) is None:
        raise HTTPException(404, "That conversation is gone.")
    turns = business_assistant.list_messages(db, thread_id)
    if not turns or turns[-1].role != "user":
        raise HTTPException(409, "That question was already answered.")
    return turns[-1]


def _unanswered_question(db: Session, thread_id: str) -> tuple[str, list[dict] | None]:
    turn = _unanswered(db, thread_id)
    return turn.text, turn.attachments or None


@router.post("/messages/retry")
async def retry(thread_id: str, request: Request):
    """Answer the thread's last question again, after an answer was interrupted or failed.

    The question is not asked twice: it is already saved, and only a thread
    whose last turn is still unanswered can be retried.
    """
    if _queued():
        return await in_worker_session(_queue_retry, request, thread_id)
    key = _answer_run()
    if active_generation(key) is not None:
        raise HTTPException(409, "The assistant is still answering your last question.")
    provider = _require_provider()
    question, attachments = await in_worker_session(_unanswered_question, thread_id)
    return await _start_answer(key, provider, question, attachments, thread_id)


@router.post("/messages/resume")
async def resume(thread_id: str | None = None):
    """Follow the answer in progress from its start, or finish at once when there is none.

    A thread only hears the answer that was asked in it.
    """
    if _queued():
        return await in_worker_session(_follow_queued, thread_id)
    run = active_generation(_answer_run())
    if run is None or (thread_id and thread_id != _answering_thread_ids.get(_answer_run())):
        return stream_finished()
    return stream_generation(run)


@router.post("/messages/stop")
async def stop():
    if _queued():
        await in_worker_session(_stop_queued)
        return {"ok": True}
    stop_generation(_answer_run())
    # What was written so far is saved as the run unwinds; answer once it is there to read.
    await wait_generation(_answer_run())
    return {"ok": True}


# On the web, an answer is a task in the seller's durable queue (the draft
# worker, services/background_worker.py). Its events are journaled, so any tab
# or Machine can follow it, and it survives a dropped connection. An answer an
# API restart cut off saved nothing; it is marked interrupted for the seller to
# retry, never replayed on its own. The worker completes the task in the same
# commit that saves the answer, so a saved answer is never retried twice.


def _queued() -> bool:
    from vendoo_studio.services.service_context import current

    return current() is not None


def _queue_question(db: Session, request: Request, question: str, body: AssistantQuestion):
    from vendoo_studio.routes.hosted_tasks import limit_submissions, require_worker
    from vendoo_studio.services.chat_attachments import delete_attachments

    require_worker(request)
    limit_submissions(request)
    if background_tasks.active_answer(db) is not None:
        raise HTTPException(409, "The assistant is still answering your last question.")
    _require_provider()
    try:
        thread = business_assistant.resolve_thread(db, body.thread_id)
    except business_assistant.UnknownThread:
        raise HTTPException(404, "That conversation is gone.") from None
    thread_id = thread.id
    # Each Storage upload can take 20 s; do not hold a pooled connection meanwhile.
    db.rollback()
    attachments = store_uploads(body.attachments, collection_id=thread_id)
    try:
        task = background_tasks.add_answer(db, thread_id, {"question": question, "attachments": attachments})
        # The question and its task commit together.
        business_assistant.add_message(db, "user", question, attachments=attachments, thread_id=thread_id)
    except Exception as exc:
        db.rollback()
        delete_attachments(attachments)
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "The assistant is still answering your last question.") from None
        raise
    return _follow_task(db, task.id)


def _queue_retry(db: Session, request: Request, thread_id: str):
    from vendoo_studio.routes.hosted_tasks import limit_submissions, require_worker

    require_worker(request)
    limit_submissions(request)
    _require_provider()
    # Two Retry presses serialize on the thread's row.
    db.scalar(select(AssistantThread.id).where(AssistantThread.id == thread_id).with_for_update())
    turn = _unanswered(db, thread_id)
    if background_tasks.active_answer(db) is not None:
        raise HTTPException(409, "The assistant is still answering your last question.")
    task = background_tasks.add_answer(db, thread_id, {"question": turn.text, "attachments": turn.attachments or []})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "The assistant is still answering your last question.") from None
    return _follow_task(db, task.id)


def _follow_task(db: Session, task_id: str):
    from vendoo_studio.routes.hosted_tasks import task_stream

    # Close before SSE keepalives so Depends(get_seller_db) cannot hold a pool
    # connection for the whole answer wait.
    db.rollback()
    db.close()
    return task_stream(task_id)


def _follow_queued(db: Session, thread_id: str | None):
    task = background_tasks.active_answer(db)
    if task is not None and (not thread_id or task.assistant_thread_id == thread_id):
        return _follow_task(db, task.id)
    if thread_id:
        latest = background_tasks.latest_answer(db, thread_id)
        if latest is not None and latest.status == "failed":
            # Partial answers stay saved too; explain why they stopped on reconnect.
            return _said(sse_event("error", latest.error or "This answer did not finish. Retry to ask again."))
    db.rollback()
    return stream_finished()


def _said(*frames: str) -> StreamingResponse:
    async def stream():
        for frame in frames:
            yield frame
        yield "data: [DONE]\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


def _stop_queued(db: Session) -> None:
    task = background_tasks.active_answer(db, lock=True)
    if task is None:
        db.rollback()
        return
    # Keep what was written so far, as on the Mac. The worker's own save waits
    # on this row lock and then finds its task cancelled, so only one is kept.
    written = journal_text(background_tasks.journal(db, task.id))
    task.status, task.lease_token, task.leased_until = "cancelled", None, None
    if written.strip():
        business_assistant.add_message(db, "assistant", written, thread_id=task.assistant_thread_id)
    else:
        db.commit()


def journal_text(frames: list[str]) -> str:
    """The answer's words in journaled SSE frames, without status or other named events."""
    pieces = []
    for frame in frames:
        lines = frame.rstrip("\n").split("\n")
        if not lines or lines[0].startswith("event:"):
            continue
        pieces.append("\n".join(line[6:] if line.startswith("data: ") else line[5:]
                                for line in lines if line.startswith("data:")))
    return "".join(pieces)


class _Journal(GenerationRun):
    """Collects what an answer publishes, for the worker to append to the task's journal."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: list[str] = []
        self.stop_outcome: str | None = None
        self.saved_outcome: str | None = None
        self.timing_sample: tuple[business_assistant.AnswerTiming, str, int] | None = None

    def confirm_saved(self, outcome: str) -> None:
        self.saved_outcome = outcome

    def publish(self, item: str, *, record: bool = True) -> None:
        # Pulses only keep a live connection open; followers poll the journal.
        if record and item != KEEPALIVE:
            self.pending.append(item)

    def take(self) -> list[str]:
        taken, self.pending = self.pending, []
        return taken


async def answer_task(task_id: str, token: str, task) -> bool:
    """Run one queued answer in the claimed seller's context; settle the task either way."""
    journal = _Journal()
    settled = shutting_down = False

    def settle(db: Session, error: str | None) -> None:
        nonlocal settled
        if shutting_down:
            # A restart cut the answer off: save nothing, so it reads as interrupted.
            raise RuntimeError("The API is stopping")
        background_tasks.complete_in(db, task_id, token, journal.take(), error=error)
        settled = True

    payload = task.payload or {}
    work = None
    try:
        provider = _require_provider()
        work = asyncio.create_task(_answer(
            journal, provider, payload.get("question", ""), payload.get("attachments") or None,
            task.assistant_thread_id, settle=settle, queued_at=task.created_at,
        ))
        while not work.done():
            await asyncio.wait({work}, timeout=background_tasks.EVENT_INTERVAL)
            if not settled and (frames := journal.take()):
                # Fails once the seller stops the answer, which ends the work below.
                await asyncio.to_thread(background_tasks.append_events, task_id, token, frames)
        error = await work
        if not settled:
            await asyncio.to_thread(background_tasks.append_events, task_id, token, journal.take())
            await asyncio.to_thread(background_tasks.finish, task_id, token, error=error)
    except asyncio.CancelledError:
        shutting_down = True
        raise
    except Exception as exc:
        if isinstance(exc, background_tasks.TaskLeaseExpired):
            journal.stop_outcome = "cancelled" if isinstance(exc, background_tasks.TaskStopped) else "lease_lost"
        if not isinstance(exc, background_tasks.TaskLeaseExpired):
            journal.stop_outcome = "error"
            _log_answer_failure(exc)
        try:
            await asyncio.to_thread(background_tasks.finish, task_id, token,
                                    error="The assistant stopped before it finished. Retry to ask again.")
        except background_tasks.TaskLeaseExpired:
            pass
    finally:
        if work is not None and not work.done():
            # settle() runs before commit and cannot prove persistence. Preserve
            # cancellation even then; an unconfirmed thread save may finish later.
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        if journal.timing_sample is not None:
            from vendoo_studio.services.service_context import current

            timing, outcome, total_ms = journal.timing_sample
            context = current()
            stop = journal.stop_outcome or (context.task_outcome.get("stop") if context else None)
            if stop is None and shutting_down:
                stop = "interrupted"
            if journal.saved_outcome is not None:
                outcome = journal.saved_outcome
            elif stop is not None:
                outcome = stop
            _emit_timing(timing, outcome, total_ms)
    return True


def _proposal(message_id: str, db: Session) -> AssistantMessage:
    row = db.get(AssistantMessage, message_id)
    if row is None or not row.changes:
        raise HTTPException(404, "Those changes are no longer in the conversation.")
    if row.changes.get("status") != "proposed":
        raise HTTPException(409, "Those changes were already applied or dismissed.")
    return row


STALE_PROPOSAL = "It changed since this was proposed. Ask again."


def _apply_to_listing(item: dict, db: Session) -> str | None:
    """Write one listing's proposed values. Returns why it was skipped, or None once it is saved."""
    from vendoo_studio.models.listing_evidence import ListingCorrection
    from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo, RevisionConflict
    from vendoo_studio.services.listing_editor import save_listing_edit
    from vendoo_studio.services.price_markdown import listing_busy
    from vendoo_studio.services import boxes

    conv_id = item["listing_id"]
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        return "It is no longer in Anteroom."
    if listing_busy(conv_id, db):
        return "Anteroom was generating or sending it."
    from vendoo_studio.services.service_context import current

    if current() is not None:
        from vendoo_studio.services.background_tasks import require_idle

        # On the web, a save serializes with draft tasks on the item's row lock.
        try:
            require_idle(db, conv_id)
        except HTTPException as refused:
            return str(refused.detail)
    to_set = item["set"]
    if to_set.get("box") and boxes.get_box(db, to_set["box"]) is None:
        return "That box is no longer in Anteroom."
    if any(field != "box" for field in to_set):
        revisions = ListingRepo(db).get_revisions(conv_id)
        if not revisions or not isinstance(revisions[0].listing_json, dict):
            return "It has no listing to change yet."
        # The seller reviewed the listing as it was when the change was proposed; never overwrite a later edit.
        reviewed = item.get("revision_id")
        if revisions[0].id != reviewed:
            return STALE_PROPOSAL
        updated = assistant_changes.apply_fields(revisions[0].listing_json, to_set)
        try:
            saved = save_listing_edit(conv_id, updated, db, expected_revision_id=reviewed)
        except RevisionConflict as refused:
            return str(refused)
        except HTTPException as refused:
            return str(refused.detail)
        saved_revision = ListingRepo(db).get_revision(saved["revision_id"])
        assert saved_revision is not None
        saved_revision.source = REVISION_SOURCE
        # A change the assistant wrote is not an example of how the seller edits.
        db.query(ListingCorrection).filter_by(revision_id=saved["revision_id"]).update({"source": REVISION_SOURCE})
    if "box" in to_set:
        boxes.assign_box(db, conv, to_set["box"])
    db.commit()
    return None


@router.post("/messages/{message_id}/changes/apply")
def apply_changes(message_id: str, db: Session = Depends(get_db)):
    """Write the changes an answer proposed, now that the seller has reviewed them.

    Each listing is saved as the listing editor saves it, as a new revision the
    seller can restore from. A listing that cannot be changed right now is
    skipped and says why; the rest still apply.
    """
    row = _proposal(message_id, db)
    items = []
    for item in row.changes["items"]:
        skipped = _apply_to_listing(item, db)
        items.append({**item, "skipped": skipped} if skipped else item)
    row.changes = {"status": "applied", "items": items}
    db.commit()
    return business_assistant.message_view(row)


def _automation_message(message_id: str, db: Session) -> AssistantMessage:
    row = db.get(AssistantMessage, message_id)
    if row is None or not row.automation:
        raise HTTPException(404, "That automation is no longer in the conversation.")
    if row.automation.get("status") != "proposed":
        raise HTTPException(409, "That automation was already confirmed or dismissed.")
    return row


@router.post("/messages/{message_id}/automation/confirm")
def confirm_automation_message(message_id: str, db: Session = Depends(get_db)):
    """Save the automation an answer drafted, now that the seller has confirmed it."""
    from vendoo_studio.services import automations
    from vendoo_studio.services.automations import AutomationError

    row = _automation_message(message_id, db)
    try:
        saved = automations.confirm_proposal(db, row.automation.get("proposal") or {})
    except AutomationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    row.automation = {"status": "confirmed", "proposal": row.automation.get("proposal"), "automation_id": saved.get("id")}
    db.commit()
    return business_assistant.message_view(row)


@router.post("/messages/{message_id}/automation/dismiss")
def dismiss_automation_message(message_id: str, db: Session = Depends(get_db)):
    row = _automation_message(message_id, db)
    row.automation = {**row.automation, "status": "dismissed"}
    db.commit()
    return business_assistant.message_view(row)


@router.post("/messages/{message_id}/changes/dismiss")
def dismiss_changes(message_id: str, db: Session = Depends(get_db)):
    row = _proposal(message_id, db)
    row.changes = {**row.changes, "status": "dismissed"}
    db.commit()
    return business_assistant.message_view(row)
