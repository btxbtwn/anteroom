"""The business assistant's chat.

Asking reads Studio's data and changes none of it. An answer may propose
changes to listings; they are written only when the seller applies them here,
through the same save the listing editor uses. Nothing here sends, lists,
delists or publishes.
"""
from __future__ import annotations

import asyncio
import functools
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal, get_db
from vendoo_studio.models.assistant import AssistantMessage, AssistantThread
from vendoo_studio.routes.attachments import AttachmentUpload, store_uploads
from vendoo_studio.services import assistant_changes, business_assistant
from vendoo_studio.services.chat_attachments import attach_to_last_user_turn
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.streaming import (
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

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# The one answer that can be in progress, in the registry listing generations use.
ANSWER_RUN = "assistant"
# Which conversation that answer belongs to, so another thread does not replay it.
_answering_thread_id: str | None = None
# How a listing's revision history names a save the seller applied from here.
REVISION_SOURCE = "assistant"
UNREADABLE_CHANGES = (
    "I could not put those changes in a form Studio can apply, so nothing is waiting for review. Ask me again."
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
    if thread_id == _answering_thread_id and active_generation(ANSWER_RUN) is not None:
        raise HTTPException(409, "That conversation is still being answered.")
    if not business_assistant.delete_thread(db, thread_id):
        raise HTTPException(404, "That conversation is gone.")
    return {"ok": True}


def _require_thread(db: Session, thread_id: str | None) -> None:
    if thread_id and db.get(AssistantThread, thread_id) is None:
        raise HTTPException(404, "That conversation is gone.")


@router.get("/messages")
def get_messages(thread_id: str | None = None, db: Session = Depends(get_db)):
    _require_thread(db, thread_id)
    return [business_assistant.message_view(row) for row in business_assistant.list_messages(db, thread_id)]


@router.delete("/messages")
def clear_messages(thread_id: str | None = None, db: Session = Depends(get_db)):
    _require_thread(db, thread_id)
    business_assistant.clear_messages(db, thread_id)
    return {"ok": True}


@router.get("/note")
def get_note():
    return {"note": get_business_note()}


@router.put("/note")
def save_note(body: BusinessNote):
    return {"note": set_business_note(body.note)}


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


async def _answer(
    run: GenerationRun, provider, question: str, attachments: list[dict] | None = None,
    thread_id: str | None = None,
) -> None:
    """Answer in the background and publish each piece to whoever is following.

    The answer belongs to Studio, not to the connection that asked: a phone
    that locks or drops off the network mid-answer finds it when it comes back.
    """
    answer = ""
    changes = None
    shop = None
    try:
        run.publish(sse_event("status", "Reading your shop…"))
        messages, shop = await await_with_pulses(
            run, asyncio.to_thread(_prepare, question, attachments, thread_id), [],
        )
        async for item in iter_with_keepalives(business_assistant.answer(provider, messages, shop)):
            if item is None:
                # A comment alone does not keep some mobile connections open; pulse repeats the status too.
                run.pulse()
                continue
            kind, text = item
            if kind == "text":
                answer += text
                run.publish(sse_data(text))
            elif kind == "changes":
                changes = _propose(text)
                if changes is None:
                    note = UNREADABLE_CHANGES
                elif not answer.strip():
                    note = "These changes are ready for you to review."
                else:
                    continue
                note = f"\n\n{note}" if answer.strip() else note
                answer += note
                run.publish(sse_data(note))
            else:
                run.publish(sse_event(kind, text))
        if not answer.strip():
            run.publish(sse_event("error", "The assistant returned an empty answer. Ask again."))
    except Exception as exc:
        log.exception("business assistant failed")
        run.publish(sse_event("error", str(exc).strip() or type(exc).__name__))
    finally:
        if shop is not None:
            shop.lookups.close()
        # Also reached when the seller presses Stop: keep what was written so far.
        if answer.strip():
            save_db = SessionLocal()
            try:
                business_assistant.add_message(
                    save_db, "assistant", answer,
                    provider=getattr(provider, "name", None),
                    model=getattr(provider, "listing_model", None),
                    changes=changes,
                    thread_id=thread_id,
                )
            finally:
                save_db.close()


@router.post("/messages")
async def ask(body: AssistantQuestion, db: Session = Depends(get_db)):
    question = body.text.strip()
    if not question and not body.attachments:
        raise HTTPException(422, "Ask a question first.")
    if active_generation(ANSWER_RUN) is not None:
        raise HTTPException(409, "The assistant is still answering your last question.")
    provider = get_listing_provider()
    if provider is None:
        raise HTTPException(400, "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key.")
    attachments = store_uploads(body.attachments)
    try:
        thread = business_assistant.resolve_thread(db, body.thread_id)
    except business_assistant.UnknownThread:
        raise HTTPException(404, "That conversation is gone.") from None
    business_assistant.add_message(db, "user", question, attachments=attachments, thread_id=thread.id)
    thread_id = thread.id
    # Release the request-scoped session before background work opens its own.
    db.close()
    global _answering_thread_id
    _answering_thread_id = thread_id
    run = start_generation(
        ANSWER_RUN,
        functools.partial(_answer, provider=provider, question=question, attachments=attachments, thread_id=thread_id),
    )
    await asyncio.sleep(0)
    return stream_generation(run)


@router.post("/messages/resume")
async def resume(thread_id: str | None = None):
    """Follow the answer in progress from its start, or finish at once when there is none.

    A thread only hears the answer that was asked in it.
    """
    run = active_generation(ANSWER_RUN)
    if run is None or (thread_id and thread_id != _answering_thread_id):
        return stream_finished()
    return stream_generation(run)


@router.post("/messages/stop")
async def stop():
    stop_generation(ANSWER_RUN)
    # What was written so far is saved as the run unwinds; answer once it is there to read.
    await wait_generation(ANSWER_RUN)
    return {"ok": True}


def _proposal(message_id: str, db: Session) -> AssistantMessage:
    row = db.get(AssistantMessage, message_id)
    if row is None or not row.changes:
        raise HTTPException(404, "Those changes are no longer in the conversation.")
    if row.changes.get("status") != "proposed":
        raise HTTPException(409, "Those changes were already applied or dismissed.")
    return row


def _apply_to_listing(item: dict, db: Session) -> str | None:
    """Write one listing's proposed values. Returns why it was skipped, or None once it is saved."""
    from vendoo_studio.models.listing_evidence import ListingCorrection
    from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
    from vendoo_studio.routes.listings import ListingUpdate, update_listing
    from vendoo_studio.services.price_markdown import listing_busy
    from vendoo_studio.services import boxes

    conv_id = item["listing_id"]
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        return "It is no longer in Studio."
    if listing_busy(conv_id, db):
        return "Studio was generating or sending it."
    to_set = item["set"]
    if to_set.get("box") and boxes.get_box(db, to_set["box"]) is None:
        return "That box is no longer in Studio."
    if any(field != "box" for field in to_set):
        revisions = ListingRepo(db).get_revisions(conv_id)
        if not revisions or not isinstance(revisions[0].listing_json, dict):
            return "It has no listing to change yet."
        updated = assistant_changes.apply_fields(revisions[0].listing_json, to_set)
        try:
            saved = update_listing(conv_id, ListingUpdate(listing=updated), db)
        except HTTPException as refused:
            return str(refused.detail)
        ListingRepo(db).get_revision(saved["revision_id"]).source = REVISION_SOURCE
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


@router.post("/messages/{message_id}/changes/dismiss")
def dismiss_changes(message_id: str, db: Session = Depends(get_db)):
    row = _proposal(message_id, db)
    row.changes = {**row.changes, "status": "dismissed"}
    db.commit()
    return business_assistant.message_view(row)
