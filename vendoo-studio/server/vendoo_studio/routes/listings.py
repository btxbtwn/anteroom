from __future__ import annotations

import asyncio
import copy
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.models.etsy_fields import etsy_eligible
from vendoo_studio.models.validation import normalize_listing_dropdowns, validate_listing
from vendoo_studio.repositories.queries import ListingRepo, ConversationRepo
from vendoo_studio.services import price_markdown
from vendoo_studio.services.price_drop import build_preview
from vendoo_studio.services.price_markdown import PriceDropError, current_listing, listing_busy

router = APIRouter(tags=["listings"])

def _first_error(validation) -> str:
    for err in validation.errors:
        message = err.get("message") or ""
        if message:
            return message
    return "Listing is invalid"


class ListingUpdate(BaseModel):
    listing: dict
    # Empty only when the editor has not yet saved a first revision.
    expected_revision_id: str = Field(max_length=100)


class ListingResponse(BaseModel):
    conversation_id: str
    current_revision_id: str | None
    listing: dict
    revision_count: int
    can_send: bool
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    # False for modern resale, which Send leaves off Etsy.
    etsy_eligible: bool = True


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    info: list[dict[str, str]]
    can_send: bool


@router.get("/api/conversations/{conv_id}/listing")
def get_listing(conv_id: str, db: Session = Depends(get_db)):
    from vendoo_studio.services.listing_generate import align_size_fields

    conv_repo = ConversationRepo(db)
    if not conv_repo.get(conv_id):
        raise HTTPException(404, "Conversation not found")

    listing_repo = ListingRepo(db)
    latest = listing_repo.latest_revision(conv_id)

    current = listing_repo.get_current(conv_id)
    revision_id = current.current_revision_id if current else None

    listing_data = {}
    if latest:
        listing_data = copy.deepcopy(latest.listing_json)

    changed = False
    if latest:
        from vendoo_studio.services.chat_listing import recover_saved_chat_clears

        recovered = recover_saved_chat_clears(db, conv_id, latest)
        if recovered != listing_data:
            listing_data = recovered
            changed = True
    if isinstance(listing_data, dict):
        from vendoo_studio.services.depop_measurements import sync_depop_measurements
        from vendoo_studio.services.seller_overrides import (
            expand_depop_multi,
            preserving_locks,
            sync_unlocked_prices,
        )
        from vendoo_studio.services.vendoo_import import parse_notes

        notes = parse_notes(conv_repo.get(conv_id).notes)

        def _normalize(listing: dict) -> None:
            align_size_fields(listing)
            normalize_listing_dropdowns(listing)

        if expand_depop_multi(listing_data):
            changed = True
        if sync_depop_measurements(listing_data, notes):
            changed = True
        before = copy.deepcopy(listing_data)
        preserving_locks(listing_data, _normalize)
        if listing_data != before:
            changed = True
        if sync_unlocked_prices(listing_data):
            changed = True
    if changed:
        revision = listing_repo.save_revision(
            conv_id,
            listing_data,
            source="dropdown_normalize",
            parent_revision_id=revision_id,
        )
        revision_id = revision.id

    from vendoo_studio.services.seller_overrides import capture_locked, restore_locked

    photo_count = len(conv_repo.get_photos(conv_id))
    locked = capture_locked(listing_data)
    validation = validate_listing(listing_data, photo_count, require_photos=True)
    restore_locked(listing_data, locked)

    return ListingResponse(
        conversation_id=conv_id,
        current_revision_id=revision_id,
        listing=listing_data,
        revision_count=listing_repo.revision_count(conv_id),
        can_send=validation.can_send,
        errors=validation.errors,
        warnings=validation.warnings,
        etsy_eligible=etsy_eligible(listing_data) if listing_data else True,
    )


@router.put("/api/conversations/{conv_id}/listing")
def update_listing(conv_id: str, body: ListingUpdate, db: Session = Depends(get_db)):
    from vendoo_studio.repositories.queries import RevisionConflict
    from vendoo_studio.services.listing_editor import save_listing_edit

    try:
        return save_listing_edit(conv_id, body.listing, db, expected_revision_id=body.expected_revision_id)
    except RevisionConflict as exc:
        raise HTTPException(409, "This listing changed. Reload it before saving your edits.") from exc


@router.post("/api/conversations/{conv_id}/listing/validate")
def validate(conv_id: str, db: Session = Depends(get_db)):
    conv_repo = ConversationRepo(db)
    if not conv_repo.get(conv_id):
        raise HTTPException(404, "Conversation not found")

    listing_repo = ListingRepo(db)
    latest = listing_repo.latest_revision(conv_id)
    listing_data = copy.deepcopy(latest.listing_json) if latest else {}
    normalize_listing_dropdowns(listing_data)

    photo_count = len(conv_repo.get_photos(conv_id))
    result = validate_listing(listing_data, photo_count, require_photos=True)

    return ValidationResponse(
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
        info=result.info,
        can_send=result.can_send,
    )


@router.get("/api/conversations/{conv_id}/revisions")
def get_revisions(conv_id: str, db: Session = Depends(get_db)):
    listing_repo = ListingRepo(db)
    revisions = listing_repo.get_revisions(conv_id)
    return [
        {
            "id": r.id,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "parent_revision_id": r.parent_revision_id,
            "title": r.listing_json.get("title", ""),
        }
        for r in revisions
    ]


@router.get("/api/conversations/{conv_id}/revisions/{revision_id}")
def get_revision(conv_id: str, revision_id: str, db: Session = Depends(get_db)):
    target = ListingRepo(db).get_revision(revision_id)
    if not target or target.conversation_id != conv_id:
        raise HTTPException(404, "Revision not found")
    return {"id": target.id, "listing": target.listing_json}


class RevisionRestoreRequest(BaseModel):
    expected_revision_id: str


@router.post("/api/conversations/{conv_id}/revisions/{revision_id}/restore")
def restore_revision(conv_id: str, revision_id: str, body: RevisionRestoreRequest, db: Session = Depends(get_db)):
    listing_repo = ListingRepo(db)
    target = listing_repo.get_revision(revision_id)
    if not target or target.conversation_id != conv_id:
        raise HTTPException(404, "Revision not found")

    current = listing_repo.get_current(conv_id)
    if not current or current.current_revision_id != body.expected_revision_id:
        raise HTTPException(409, "This listing changed while you were comparing revisions. Review it again before restoring.")
    if listing_busy(conv_id, db):
        raise HTTPException(409, "Wait for generation or sending to finish before restoring a revision.")
    result = update_listing(conv_id, ListingUpdate(listing=copy.deepcopy(target.listing_json),
                                                 expected_revision_id=body.expected_revision_id), db)
    listing_repo.get_revision(result["revision_id"]).source = "restore"
    from vendoo_studio.models.listing_evidence import ListingCorrection
    db.query(ListingCorrection).filter_by(revision_id=result["revision_id"]).update({"source": "restore"})
    db.commit()
    return result


class PriceDropApply(BaseModel):
    price: float = Field(..., gt=0)
    percent: float | None = None
    mode: Literal["percent", "comps", "custom"] = "custom"


def _current_listing(conv_id: str, db: Session) -> tuple[dict, list]:
    try:
        return current_listing(conv_id, db)
    except PriceDropError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _preview_inputs(conv_id: str, db: Session) -> tuple[dict, list, str, list, int | None]:
    from vendoo_studio.services.listing_generate import current_photo_analysis
    from vendoo_studio.services.sell_through import collect_outcomes, listing_age_days

    listing, revisions = _current_listing(conv_id, db)
    conv_repo = ConversationRepo(db)
    analysis = current_photo_analysis(conv_repo, conv_id)
    conv = conv_repo.get(conv_id)
    age_days = listing_age_days(conv.notes if conv else None)
    return listing, revisions, analysis, collect_outcomes(db), age_days


@router.post("/api/conversations/{conv_id}/price-drop/preview")
def preview_price_drop(conv_id: str, db: Session = Depends(get_db)):
    """History and sell-through suggestion. Does not change the listing.

    Sold comps are a web search that can take minutes; the dialog streams them
    from ``/price-drop/comps`` on top of this.
    """
    listing, revisions, _analysis, outcomes, age_days = _preview_inputs(conv_id, db)
    try:
        return build_preview(listing, revisions, sold_outcomes=outcomes, age_days=age_days)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/conversations/{conv_id}/price-drop/comps")
def stream_price_drop_comps(conv_id: str, db: Session = Depends(get_db)):
    """Stream sold comps as each source answers, with the suggestion they imply.

    Events: ``step`` (a search the models ran), ``source`` (JSON: one source's
    state and counts), ``preview`` (JSON: the full preview with the comps so
    far), then ``[DONE]``. Does not change the listing.
    """
    from vendoo_studio.services import live_trace
    from vendoo_studio.services.comp_research import stream_sold_comps
    from vendoo_studio.services.price_drop import fields_from_listing
    from vendoo_studio.services.streaming import KEEPALIVE, SSE_HEADERS, sse_event

    listing, revisions, analysis, outcomes, age_days = _preview_inputs(conv_id, db)
    evidence = fields_from_listing(listing)
    # Sold comps run for minutes; do not pin a pool connection for the stream.
    db.close()

    def preview(comps_text: str) -> str:
        return json.dumps(build_preview(
            listing, revisions, comps_text=comps_text, sold_outcomes=outcomes, age_days=age_days,
        ))

    async def events():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        forwarded: dict[str, str] = {}

        def trace(kind: str, text: str) -> None:
            if kind == "step":
                queue.put_nowait(sse_event("step", text))
            elif kind == "agent":
                # Searches run as parallel agents; their progress and result
                # lines ("Found 8 sold comps") are this dialog's steps.
                state = json.loads(text)
                detail = state.get("detail")
                if detail and forwarded.get(state.get("id")) != detail:
                    forwarded[state.get("id")] = detail
                    queue.put_nowait(sse_event("step", detail))

        async def research() -> None:
            live_trace.bind(trace)
            try:
                async for event in stream_sold_comps(analysis, evidence):
                    if event.kind == "source":
                        queue.put_nowait(sse_event("source", json.dumps({
                            "source": event.source,
                            "state": event.state,
                            "sold": event.sold,
                            "live": event.live,
                            "detail": event.detail,
                        })))
                    else:
                        queue.put_nowait(sse_event("preview", preview(event.text)))
            except Exception as exc:
                queue.put_nowait(sse_event("error", str(exc) or "Sold comps search failed."))
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(research())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield KEEPALIVE
                    continue
                if item is None:
                    break
                yield item
            yield "data: [DONE]\n\n"
        finally:
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/conversations/{conv_id}/price-drop")
def apply_price_drop(conv_id: str, body: PriceDropApply, db: Session = Depends(get_db)):
    """Apply a confirmed whole-dollar price; records a ``price_drop`` revision."""
    try:
        result = price_markdown.apply_price_drop(conv_id, body.price, db)
    except PriceDropError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {
        "ok": True,
        "revision_id": result["revision_id"],
        "price": result["price"],
        "previous_price": result["previous_price"],
        "percent": body.percent,
        "mode": body.mode,
        "validation": result["validation"].model_dump(),
    }


class MarkdownPreviewRequest(BaseModel):
    conversation_ids: list[str] = Field(..., min_length=1)


class MarkdownDrop(BaseModel):
    conversation_id: str
    price: float = Field(..., gt=0)
    expected_revision_id: str | None = None


class MarkdownApply(BaseModel):
    drops: list[MarkdownDrop] = Field(..., min_length=1)


@router.post("/api/price-drop/batch/preview")
def preview_markdowns(body: MarkdownPreviewRequest, db: Session = Depends(get_db)):
    """A suggested markdown for each listing, from the seller's own sales. Changes nothing."""
    return {"rows": price_markdown.batch_preview(body.conversation_ids, db)}


@router.post("/api/price-drop/batch")
def apply_markdowns(body: MarkdownApply, db: Session = Depends(get_db)):
    """Save each confirmed price as a ``price_drop`` revision. Never sends to Vendoo."""
    return price_markdown.batch_apply([drop.model_dump() for drop in body.drops], db)
