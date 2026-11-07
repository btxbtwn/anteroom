from __future__ import annotations

import asyncio
import functools
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal, get_db
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import activity
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.chat_citations import (
    citations_to_plain_text,
)
from vendoo_studio.routes.attachments import AttachmentUpload, store_uploads
from vendoo_studio.services.chat_attachments import attach_to_last_user_turn
from vendoo_studio.services.chat_listing import persist_chat_result
from vendoo_studio.services.chat_prompts import build_chat_messages
from vendoo_studio.services.listing_generate import (
    PhotoAnalysisError,
    analyze_photos_with_tag_retry,
    photo_analysis_failure,
    photo_fingerprint,
    require_photo_analysis,
    seller_item_details,
)
from vendoo_studio.services.listing_generation import run_listing_generation
from vendoo_studio.services.listing_provider import get_listing_provider, get_photo_provider
from vendoo_studio.services.streaming import (
    KEEPALIVE,
    SSE_HEADERS,
    active_generation,
    iter_with_keepalives,
    sse_data,
    sse_event,
    sse_for_stream_item,
    start_generation,
    stop_generation,
    stream_finished,
    stream_generation,
    wait_task_keepalives,
)


def _require_provider():
    provider = get_listing_provider()
    if provider is None:
        raise HTTPException(
            400,
            "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key.",
        )
    return provider


def _provider_meta(provider) -> tuple[str, str]:
    return (
        getattr(provider, "name", "xiaomi-mimo"),
        getattr(provider, "listing_model", "mimo-v2.5-pro"),
    )


def _vision_meta(provider) -> tuple[str, str]:
    return (
        getattr(provider, "name", "xiaomi-mimo"),
        getattr(provider, "vision_model", "mimo-v2.5"),
    )


log = logging.getLogger("vendoo_studio.chat")

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    text: str
    attachments: list[AttachmentUpload] = Field(default_factory=list)


async def _persist_chat_result(conv_id: str, full_text: str, provider, *, field_paths=None, **kwargs):
    db = SessionLocal()
    token = activity.edit_fields.set(field_paths)
    try:
        return await persist_chat_result(db, conv_id, full_text, provider, **kwargs)
    finally:
        activity.edit_fields.reset(token)
        db.close()


def _reset_status(conv_id: str, reason: str) -> None:
    db = SessionLocal()
    try:
        ConversationRepo(db).update_status(conv_id, "draft")
    except Exception:
        log.exception("failed to reset status after %s for %s", reason, conv_id)
    finally:
        db.close()


@router.post("/api/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: ChatMessage, db: Session = Depends(get_db)):
    return await run_message(conv_id, body, db)


async def run_message(conv_id: str, body: ChatMessage, db: Session, *, stored_attachments=None):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    attachments = stored_attachments if stored_attachments is not None else store_uploads(body.attachments, collection_id=conv_id)
    repo.add_message(conv_id, "user", body.text, attachments=attachments)
    provider = get_listing_provider()
    if provider is None:
        repo.add_message(
            conv_id,
            "system",
            "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key, then retry this prompt.",
            provider="system",
            model="",
        )
        repo.update_status(conv_id, "draft")
        raise HTTPException(
            400,
            "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key.",
        )
    provider_name, provider_model = _provider_meta(provider)

    revisions = ListingRepo(db).get_revisions(conv_id)
    if repo.get_photos(conv_id) and (not revisions or revisions[0].source == "category_analysis"
                                   or not (revisions[0].listing_json or {}).get("title")):
        # Seller answers during category discovery resume the same generation
        # pipeline; chat must not bypass the schema prerequisite.
        return await generate_listing(conv_id, db=db)

    repo.update_status(conv_id, "in_progress")
    db.close()

    async def stream_response():
        full_text = ""
        stream_error = ""
        work = activity.begin(conv_id, "Answering…")
        try:
            # Photo analysis and sold-comps research can take minutes; say so
            # before they start instead of leaving the chat silent.
            yield sse_event("status", "Reading the listing…")
            from vendoo_studio.services.option_lookup import OptionCatalog, chat_with_options

            option_catalog = OptionCatalog({})
            build_db = SessionLocal()
            try:
                messages = await build_chat_messages(conv_id, build_db, body.text, option_catalog)
            finally:
                build_db.close()
            messages = await asyncio.to_thread(attach_to_last_user_turn, messages, attachments)
            def lock_targets(paths):
                work.fields = tuple(sorted(set(work.fields) | set(paths)))

            async for item in iter_with_keepalives(chat_with_options(
                    provider, messages, option_catalog, on_targets=lock_targets if option_catalog.rows else None)):
                if work.cancelled:
                    yield "data: [DONE]\n\n"
                    return
                if item is None:
                    yield KEEPALIVE
                    continue
                payload, content = sse_for_stream_item(item)
                if content:
                    full_text += content
                if payload:
                    yield payload
            if not full_text.strip():
                stream_error = "The listing assistant returned an empty response. Retry this prompt."
                yield sse_event("error", stream_error)

            # Persist before [DONE] so Forms/Fields refetch the updated listing JSON.
            if work.cancelled:
                yield "data: [DONE]\n\n"
                return
            yield sse_event("status", "Saving listing…")
            work.label = "Saving listing…"
            persist_task = asyncio.create_task(
                _persist_chat_result(
                    conv_id,
                    full_text,
                    provider,
                    provider_name=provider_name,
                    provider_model=provider_model,
                    stream_error=stream_error,
                    user_message=citations_to_plain_text(body.text),
                    field_paths=work.fields if option_catalog.rows else None,
                )
            )
            # Tracked on its own: a dropped connection cancels this generator, not the save.
            activity.track_task(conv_id, "Saving listing…", persist_task, fields=work.fields)
            async for _ in wait_task_keepalives(persist_task, timeout=2.0):
                yield KEEPALIVE
            if persist_task.cancelled():
                yield "data: [DONE]\n\n"
                return
            try:
                _operations, saved = persist_task.result()
            except Exception:
                log.exception("failed to persist chat result for %s", conv_id)
                _reset_status(conv_id, "chat persist error")
                saved = False
            if saved:
                yield sse_event("listing_updated", "1")
            yield "data: [DONE]\n\n"
        except Exception as e:
            message = str(e).strip() or type(e).__name__
            log.exception("chat stream failed for %s: %s", conv_id, message)
            stream_error = message
            yield sse_data(f"Error: {message}")
            _reset_status(conv_id, "chat stream error")
            yield "data: [DONE]\n\n"
        finally:
            activity.end(work)

    return StreamingResponse(stream_response(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/conversations/{conv_id}/analyze-photos")
async def analyze_photos(conv_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    _require_provider()
    provider = get_photo_provider()
    vision_name, vision_model = _vision_meta(provider)

    photos = repo.get_photos(conv_id)
    if not photos:
        raise HTTPException(400, "No photos to analyze")

    paths = [str(path) for path in await asyncio.to_thread(
        photo_store().local_paths, [p.stored_filename for p in photos],
    )]
    analysis_fingerprint = photo_fingerprint(photos)

    try:
        result = await analyze_photos_with_tag_retry(provider, paths, notes=conv.notes or "")
    except Exception as exc:
        raise HTTPException(502, str(photo_analysis_failure(exc))) from exc
    try:
        _evidence, analysis_text = require_photo_analysis(result)
    except PhotoAnalysisError as exc:
        raise HTTPException(502, str(exc)) from exc

    # Store what was read, not that it was read. Generation looks for the most
    # recent analysis in the conversation; a summary line is not one, so
    # re-analysing left the previous reading in place for good — a listing kept
    # being generated from photos that had since been read again.
    repo.add_message(
        conv_id,
        "system",
        analysis_text,
        provider=vision_name,
        model=vision_model,
        photo_fingerprint=analysis_fingerprint,
    )

    return result


@router.post("/api/conversations/{conv_id}/generate")
async def generate_listing(conv_id: str, resume: bool = False, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    # Provider settings and credentials use their own short sessions.
    db.close()
    existing = active_generation(conv_id)
    if existing is not None:
        return stream_generation(existing)
    if resume:
        # Reconnecting after the run ended must not start a second one: that
        # posted a second photo analysis and comps card beside the first.
        return stream_finished()

    provider = _require_provider()
    vision_provider = get_photo_provider()
    vision_name, vision_model = _vision_meta(vision_provider)

    photos = repo.get_photos(conv_id)
    if not photos:
        raise HTTPException(400, "No photos to generate from. Upload product photos first.")

    repo.update_status(conv_id, "in_progress")
    notes = conv.notes or ""
    item_details = seller_item_details(notes)
    from vendoo_studio.services.item_context import seller_answers as stored_seller_answers

    seller_answers = stored_seller_answers(repo, conv_id, item_details)
    if seller_answers:
        item_details += "\nSeller answers:\n" + seller_answers
    filenames = [p.stored_filename for p in photos]
    analysis_fingerprint = photo_fingerprint(photos)
    photo_count = len(photos)
    # Private photo downloads must not retain an inventory connection either.
    db.close()
    paths = [str(path) for path in await asyncio.to_thread(photo_store().local_paths, filenames)]

    run = start_generation(conv_id, functools.partial(
        run_listing_generation,
        conv_id=conv_id,
        provider=provider,
        vision_provider=vision_provider,
        vision_name=vision_name,
        vision_model=vision_model,
        notes=notes,
        item_details=item_details,
        seller_answers=seller_answers,
        paths=paths,
        photo_count=photo_count,
        analysis_fingerprint=analysis_fingerprint,
    ))
    await asyncio.sleep(0)
    return stream_generation(run)


@router.post("/api/conversations/{conv_id}/generate/cancel")
async def cancel_generate_listing(conv_id: str):
    stop_generation(conv_id)

    def mark_draft() -> None:
        with SessionLocal() as db:
            ConversationRepo(db).update_status(conv_id, "draft")

    await asyncio.to_thread(mark_draft)
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/messages/cancel")
async def cancel_chat_message(conv_id: str):
    db = SessionLocal()
    try:
        ConversationRepo(db).update_status(conv_id, "draft")
    finally:
        db.close()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/stop")
async def stop_everything(conv_id: str, db: Session = Depends(get_db)):
    """Chat's Stop: end every piece of work that can still write to this listing."""
    from vendoo_studio.routes.conversations import cancel_conversation_jobs

    stop_generation(conv_id)
    activity.cancel(conv_id)
    await cancel_conversation_jobs(db, conv_id)
    ConversationRepo(db).update_status(conv_id, "draft")
    return {"ok": True}
