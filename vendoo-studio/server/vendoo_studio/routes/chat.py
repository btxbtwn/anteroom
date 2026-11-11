from __future__ import annotations

import asyncio
import functools
import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal, in_worker_session
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

# Seconds between keepalives while photo analysis and sold comps prepare the prompt.
PREPARE_KEEPALIVE_SECONDS = 3.0


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


STOPPED_NOTE = "*Stopped*"


def stopped_prose(text: str) -> str:
    """The part of a stopped reply a seller can read: its prose before any listing payload."""
    payload = re.search(r"```|^[ \t]*[\[{]", text, re.M)
    return (text[: payload.start()] if payload else text).strip()


def _save_stopped_answer(conv_id: str, full_text: str, provider_name: str, provider_model: str) -> None:
    """Keep what a stopped reply had said. Its listing edits are never applied."""
    prose = stopped_prose(full_text)
    if not prose:
        return
    db = SessionLocal()
    try:
        ConversationRepo(db).add_message(
            conv_id, "assistant", f"{prose}\n\n{STOPPED_NOTE}", provider=provider_name, model=provider_model)
    except Exception:
        log.exception("failed to keep the stopped answer for %s", conv_id)
    finally:
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
async def send_message(conv_id: str, body: ChatMessage):
    return await run_message(conv_id, body)


def _no_provider(db: Session, conv_id: str) -> None:
    repo = ConversationRepo(db)
    repo.add_message(conv_id, "system",
                     "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key, then retry this prompt.",
                     provider="system", model="")
    repo.update_status(conv_id, "draft")


def _start_answer(db: Session, conv_id: str) -> bool:
    """Mark the chat answering; False when the seller is still answering generation's questions."""
    repo = ConversationRepo(db)
    revisions = ListingRepo(db).get_revisions(conv_id)
    if repo.get_photos(conv_id) and (not revisions or revisions[0].source == "category_analysis"
                                   or not (revisions[0].listing_json or {}).get("title")):
        return False
    repo.update_status(conv_id, "in_progress")
    return True


async def run_message(conv_id: str, body: ChatMessage, *, stored_attachments=None):
    await in_worker_session(_require_conversation, conv_id)
    attachments = stored_attachments if stored_attachments is not None else await asyncio.to_thread(
        store_uploads, body.attachments, collection_id=conv_id)
    await in_worker_session(lambda db: ConversationRepo(db).add_message(conv_id, "user", body.text, attachments=attachments))
    # Provider settings and credentials use their own short sessions.
    provider = await asyncio.to_thread(get_listing_provider)
    if provider is None:
        await in_worker_session(_no_provider, conv_id)
        raise HTTPException(400, "Sign in with ChatGPT in Settings, or add a MiMo or Cursor API key.")
    provider_name, provider_model = _provider_meta(provider)

    if not await in_worker_session(_start_answer, conv_id):
        # Seller answers during category discovery resume the same generation
        # pipeline; chat must not bypass the schema prerequisite.
        return await generate_listing(conv_id)

    async def stream_response():
        full_text = ""
        stream_error = ""
        # Set once the reply is complete or failed; until then a stop keeps its prose.
        settled = False
        work = activity.begin(conv_id, "Answering…")
        try:
            # Photo analysis and sold-comps research can take minutes; say so
            # before they start instead of leaving the chat silent.
            yield sse_event("status", "Reading the listing…")
            from vendoo_studio.services.option_lookup import OptionCatalog, chat_with_options

            option_catalog = OptionCatalog({})
            build_db = SessionLocal()
            try:
                # Keep the stream talking while it runs: the browser treats
                # 20 s of silence as a dropped connection.
                build = asyncio.create_task(build_chat_messages(conv_id, build_db, body.text, option_catalog))
                try:
                    async for _ in wait_task_keepalives(build, timeout=PREPARE_KEEPALIVE_SECONDS):
                        yield KEEPALIVE
                finally:
                    if not build.done():
                        build.cancel()
                        await asyncio.gather(build, return_exceptions=True)
                messages = build.result()
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
            settled = True
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
            settled = True
            message = str(e).strip() or type(e).__name__
            log.exception("chat stream failed for %s: %s", conv_id, message)
            stream_error = message
            yield sse_data(f"Error: {message}")
            _reset_status(conv_id, "chat stream error")
            yield "data: [DONE]\n\n"
        finally:
            # Stop, or a closed connection, ends the reply where it was.
            if not settled and full_text.strip():
                _save_stopped_answer(conv_id, full_text, provider_name, provider_model)
            activity.end(work)

    return StreamingResponse(stream_response(), media_type="text/event-stream", headers=SSE_HEADERS)


def _require_conversation(db: Session, conv_id: str) -> str:
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv.notes or ""


def _photo_inputs(db: Session, conv_id: str) -> tuple[list[str], str | None]:
    photos = ConversationRepo(db).get_photos(conv_id)
    return [p.stored_filename for p in photos], photo_fingerprint(photos) if photos else None


def _providers():
    return _require_provider(), get_photo_provider()


@router.post("/api/conversations/{conv_id}/analyze-photos")
async def analyze_photos(conv_id: str):
    notes = await in_worker_session(_require_conversation, conv_id)

    _provider, provider = await asyncio.to_thread(_providers)
    vision_name, vision_model = _vision_meta(provider)

    filenames, analysis_fingerprint = await in_worker_session(_photo_inputs, conv_id)
    if not filenames:
        raise HTTPException(400, "No photos to analyze")

    paths = [str(path) for path in await asyncio.to_thread(photo_store().local_paths, filenames)]

    try:
        result = await analyze_photos_with_tag_retry(provider, paths, notes=notes)
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
    await in_worker_session(lambda db: ConversationRepo(db).add_message(
        conv_id,
        "system",
        analysis_text,
        provider=vision_name,
        model=vision_model,
        photo_fingerprint=analysis_fingerprint,
    ))

    return result


def _generation_inputs(db: Session, conv_id: str) -> dict:
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
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
    return {"notes": notes, "item_details": item_details, "seller_answers": seller_answers,
            "filenames": [p.stored_filename for p in photos], "analysis_fingerprint": photo_fingerprint(photos),
            "photo_count": len(photos)}


@router.post("/api/conversations/{conv_id}/generate")
async def generate_listing(conv_id: str, resume: bool = False):
    await in_worker_session(_require_conversation, conv_id)

    existing = active_generation(conv_id)
    if existing is not None:
        return stream_generation(existing)
    if resume:
        # Reconnecting after the run ended must not start a second one: that
        # posted a second photo analysis and comps card beside the first.
        return stream_finished()

    # Provider settings and credentials use their own short sessions.
    provider, vision_provider = await asyncio.to_thread(_providers)
    vision_name, vision_model = _vision_meta(vision_provider)

    inputs = await in_worker_session(_generation_inputs, conv_id)
    # Private photo downloads must not retain an inventory connection either.
    paths = [str(path) for path in await asyncio.to_thread(photo_store().local_paths, inputs["filenames"])]

    run = start_generation(conv_id, functools.partial(
        run_listing_generation,
        conv_id=conv_id,
        provider=provider,
        vision_provider=vision_provider,
        vision_name=vision_name,
        vision_model=vision_model,
        notes=inputs["notes"],
        item_details=inputs["item_details"],
        seller_answers=inputs["seller_answers"],
        paths=paths,
        photo_count=inputs["photo_count"],
        analysis_fingerprint=inputs["analysis_fingerprint"],
    ))
    await asyncio.sleep(0)
    return stream_generation(run)


@router.post("/api/conversations/{conv_id}/generate/cancel")
async def cancel_generate_listing(conv_id: str):
    stop_generation(conv_id)

    await in_worker_session(_mark_draft, conv_id)
    return {"ok": True}


def _mark_draft(db: Session, conv_id: str) -> None:
    ConversationRepo(db).update_status(conv_id, "draft")


@router.post("/api/conversations/{conv_id}/messages/cancel")
async def cancel_chat_message(conv_id: str):
    await in_worker_session(_mark_draft, conv_id)
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/stop")
async def stop_everything(conv_id: str):
    """Chat's Stop: end every piece of work that can still write to this listing."""
    from vendoo_studio.routes.conversations import cancel_conversation_jobs

    stop_generation(conv_id)
    activity.cancel(conv_id)
    await cancel_conversation_jobs(conv_id)
    await in_worker_session(_mark_draft, conv_id)
    return {"ok": True}
