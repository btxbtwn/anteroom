from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from vendoo_studio.routes.api_contract import (
    CONFLICT,
    NOT_FOUND,
    REFUSED,
    Conversation,
    ConversationSummary,
    DeleteConversationResult,
    ListingPhoto,
)
from vendoo_studio.services.unsent_edits import unsent_edits
from vendoo_studio.services import inventory_facets
from vendoo_studio.services import listing_persistence
from vendoo_studio.database import get_db, in_worker_session
from vendoo_studio.repositories.queries import (
    BUSY_LISTING_STATUSES,
    ConversationRepo,
    ListingRepo,
)
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.marketplace_listing import LIVE_STATES
from vendoo_studio.services import boxes
from vendoo_studio.services.listing_copy import CopyRequest
from vendoo_studio.services.listing_delete import ListingBusy, delete_listing, wipe_contents
from vendoo_studio.services.photos import cover_thumb_url, delete_photo_files, photo_url

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# Sidebar thumbnails render at 40px, so 96px covers high-DPI screens.


class ConversationCreate(BaseModel):
    title: str | None = None
    notes: str | None = None
    box_id: str | None = None


@router.post("", response_model=Conversation, responses=NOT_FOUND)
def create_conversation(body: ConversationCreate, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    if body.box_id and boxes.get_box(db, body.box_id) is None:
        raise HTTPException(404, "Box not found")
    conv = repo.create(title=body.title, notes=body.notes, box_id=body.box_id)
    return _conv_response(conv)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    repo.reconcile_job_statuses()
    covers = repo.cover_photos()
    facets = inventory_facets.listing_facets(repo.db)
    revisions = repo.current_revision_ids()
    conversations = repo.list_all()
    unsent = unsent_edits(db, conversations, revisions)
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

    direct = MarketplaceListingRepo(db).summaries()
    from vendoo_studio.services.item_status import sold_units

    sold = sold_units(db, conversations)
    from vendoo_studio.services.sales_records import missing_by_conversation

    missing = missing_by_conversation(db)
    return [
        _conv_response(c, _extras(
            _cover_photo_url(covers.get(c.id)), facets.get(c.id), revisions.get(c.id), c.id in unsent,
            direct.get(c.id, {}), sold.get(c.id), missing.get(c.id, []),
        ))
        for c in conversations
    ]


@router.get("/{conv_id}", response_model=Conversation, responses=NOT_FOUND)
def get_conversation(conv_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return _conv_response(conv, _extras_for(db, conv_id))


@router.get("/{conv_id}/copy-preview")
def preview_copy(conv_id: str, response: Response, db: Session = Depends(get_db, scope="function")):
    from vendoo_studio.services import listing_copy

    response.headers["Cache-Control"] = "no-store"
    try:
        return listing_copy.preview(db, conv_id)
    except listing_copy.CopyError as exc:
        raise HTTPException(exc.status, str(exc), headers={"Cache-Control": "no-store"}) from exc


@router.post("/{conv_id}/copy", response_model=Conversation)
def copy_conversation(conv_id: str, body: CopyRequest, db: Session = Depends(get_db)):
    from vendoo_studio.services import listing_copy

    try:
        new_id = listing_copy.create_copy(db, conv_id, body)
    except listing_copy.CopyError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return _conv_response(ConversationRepo(db).get(new_id), _extras_for(db, new_id))


@router.get("/{conv_id}/copy-result/{request_id}", response_model=Conversation)
def copy_result(conv_id: str, request_id: str, response: Response, db: Session = Depends(get_db, scope="function")):
    from vendoo_studio.services import listing_copy

    response.headers["Cache-Control"] = "no-store"
    try:
        new_id = listing_copy.copy_result(db, conv_id, request_id)
    except listing_copy.CopyError as exc:
        raise HTTPException(exc.status, str(exc), headers={"Cache-Control": "no-store"}) from exc
    return _conv_response(ConversationRepo(db).get(new_id), _extras_for(db, new_id))


class ConversationUpdate(BaseModel):
    """Title, notes and box only: a listing's status follows its marketplaces."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    notes: str | None = None
    # Item Details' changed keys, merged into the notes as they are now.
    note_changes: dict | None = None
    # Null takes the listing out of its box; leaving it out changes nothing.
    box_id: str | None = None


@router.post("/{conv_id}/settle", response_model=Conversation)
def settle_conversation(conv_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.status in BUSY_LISTING_STATUSES:
        raise HTTPException(409, "Cannot settle a listing that is still in progress")
    return _conv_response(repo.settle(conv_id), _extras_for(db, conv_id))


@router.post("/{conv_id}/unsettle", response_model=Conversation)
def unsettle_conversation(conv_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return _conv_response(repo.unsettle(conv_id), _extras_for(db, conv_id))


@router.patch("/{conv_id}", response_model=Conversation, responses={**REFUSED, **NOT_FOUND, **CONFLICT})
def update_conversation(conv_id: str, body: ConversationUpdate, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if body.note_changes:
        # Lock and reload before changing anything: note changes merge into the
        # notes as they are now, and a reload after the title or notes were set
        # would throw those away.
        db.refresh(conv, with_for_update=True)
    changed = False
    renamed = False
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Title cannot be empty")
        renamed = title != (conv.title or "")
        if renamed:
            conv.title = title
            conv.updated_at = utcnow()
            changed = True
    if body.notes is not None:
        from vendoo_studio.services.vendoo_import import merge_notes, parse_notes
        incoming = parse_notes(body.notes)
        if incoming:
            conv.notes = merge_notes(conv.notes, incoming)
        else:
            conv.notes = body.notes
        changed = True
    if body.note_changes:
        from vendoo_studio.services.vendoo_import import merge_note_changes

        conv.notes = merge_note_changes(conv.notes, body.note_changes)
        changed = True
    if renamed:
        from vendoo_studio.services.vendoo_import import merge_notes

        # A listing the seller renamed keeps that name when the listing saves
        # or regenerates, instead of following the listing's title.
        conv.notes = merge_notes(conv.notes, {"titlePinned": True})
    if "box_id" in body.model_fields_set and body.box_id != conv.box_id:
        try:
            boxes.assign_box(db, conv, body.box_id)
        except LookupError as exc:
            raise HTTPException(404, "Box not found") from exc
        changed = True
    if changed:
        db.commit()
        db.refresh(conv)
    return _conv_response(conv, _extras_for(db, conv_id))


@router.get("/{conv_id}/messages")
def get_messages(conv_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    msgs = repo.get_messages(conv_id)
    return [_msg_response(m) for m in msgs]


class ActivityResponse(BaseModel):
    busy: bool
    # Something is changing the listing itself (generation, field fills, a Vendoo
    # job). A direct marketplace action holds only its marketplace, so it is busy
    # but not writing: List stays open on the other marketplaces.
    writing: bool
    items: list[str]
    message_count: int
    last_message_id: str | None
    # The latest state of each parallel agent in this listing's current
    # generation, in the same shape as the generate stream's ``agent`` events.
    # Marketplace fields are filled after that stream ends; this is how the
    # client still sees those agents.
    agents: list[dict] = []
    locked_fields: list[str] = []


def _job_activity_label(job) -> str:
    from vendoo_studio.services.native_marketplaces import step_label

    return step_label(str(job.current_step or "")) or "Working in Chrome…"


@router.get("/{conv_id}/activity", response_model=ActivityResponse)
def get_activity(conv_id: str, db: Session = Depends(get_db)):
    """Everything still running for this listing — chat is only done when this is empty."""
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
    from vendoo_studio.models.conversation import Message
    from vendoo_studio.services import activity
    from vendoo_studio.services.native_marketplaces import is_direct_action
    from vendoo_studio.services.streaming import active_generation

    items: list[str] = []
    run = active_generation(conv_id)
    if run is not None:
        items.append(run.last_status or "Generating listing…")
    items.extend(activity.running(conv_id))
    # Polled about once a second per open listing: read only ids and statuses,
    # and load a job (and its listing snapshot) only when it is still running.
    writing = bool(items)
    jobs = db.query(Job.id, Job.status).filter(Job.conversation_id == conv_id).all()
    for job_id, status in jobs:
        if status in ACTIVE_JOB_STATUSES:
            job = db.get(Job, job_id)
            if job is not None:
                items.append(_job_activity_label(job))
                writing = writing or not is_direct_action(str(job.current_step or ""))
    items = list(dict.fromkeys(items))

    message_count = (
        db.query(func.count(Message.id)).filter(Message.conversation_id == conv_id).scalar() or 0
    )
    last_id = (
        db.query(Message.id)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )
    return ActivityResponse(
        busy=bool(items),
        writing=writing,
        items=items,
        message_count=message_count,
        last_message_id=last_id,
        agents=activity.agents(conv_id),
        locked_fields=activity.locked_fields(conv_id),
    )


@router.get("/{conv_id}/generation-timings")
def get_generation_timings(conv_id: str, limit: int = 20, db: Session = Depends(get_db)):
    """Seconds per stage of this listing's recent generations, newest first."""
    from vendoo_studio.services.generation_timing import for_conversation

    if not ConversationRepo(db).get(conv_id):
        raise HTTPException(404, "Conversation not found")
    return for_conversation(db, conv_id, limit=max(1, min(limit, 100)))


async def cancel_conversation_jobs(conv_id: str) -> int:
    """Cancel this listing's running direct marketplace jobs."""
    from vendoo_studio.services import native_marketplaces

    live = await in_worker_session(_stop_conversation_jobs, conv_id)
    for job_id, record in live["requests"]:
        # Stops at its next safe point; the record says what happened.
        await native_marketplaces.request_cancel(job_id, record)
    return live["count"]


def _stop_conversation_jobs(db: Session, conv_id: str) -> dict:
    """End the jobs nothing runs any more; return the live direct jobs that still need a cancel request."""
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
    from vendoo_studio.repositories.queries import JobRepo
    from vendoo_studio.services import native_marketplaces

    jobs = db.query(Job).filter(Job.conversation_id == conv_id, Job.status.in_(ACTIVE_JOB_STATUSES)).all()
    requests: list[tuple[str, dict]] = []
    for job in jobs:
        if str(job.current_step or "").startswith("native_"):
            job_id = job.id
            if (record := native_marketplaces.stop_job(db, job)) is not None:
                requests.append((job_id, record))
            continue
        job.status = "cancelled"
        job.current_step = None
        db.commit()
        JobRepo(db).add_event(job.id, "cancelled")
    return {"count": len(jobs), "requests": requests}


@router.get("/{conv_id}/photos", response_model=list[ListingPhoto], responses=NOT_FOUND)
def get_photos(conv_id: str, marketplace: str | None = None, db: Session = Depends(get_db)):
    """The item's photos, or with ``marketplace`` the photos added on that marketplace's form only."""
    repo = ConversationRepo(db)
    photos = repo.get_photos(conv_id) if marketplace is None else [
        p for p in repo.get_all_photos(conv_id) if p.marketplace == marketplace]
    return [_photo_response(p) for p in photos]


@router.delete("/{conv_id}/photos/{photo_id}")
def delete_photo(conv_id: str, photo_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    photos = repo.get_all_photos(conv_id)
    target = next((p for p in photos if p.id == photo_id), None)
    if not target:
        raise HTTPException(404, "Photo not found")

    delete_photo_files(target)
    listing_persistence.delete_photo(repo.db, conv_id, photo_id)
    return {"ok": True}


@router.get("/{conv_id}/regenerate-details")
def regenerate_details(conv_id: str, db: Session = Depends(get_db)):
    """The Item Details Regenerate will start from, for the bulk review. Nothing is saved."""
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return {"notes": _carried_notes(db, conv_id, conv.notes)}


class ResetRequest(BaseModel):
    # Regenerate: drop chat, jobs and generated fields but keep photos and item
    # details, so the next generate starts from the same inputs as a new listing.
    keep_inputs: bool = False


@router.post("/{conv_id}/reset", response_model=Conversation)
async def reset_conversation(conv_id: str, body: ResetRequest | None = None):
    keep_inputs = bool(body and body.keep_inputs)
    notes = await in_worker_session(_reset_check, conv_id)

    from vendoo_studio.services.streaming import stop_generation
    from vendoo_studio.services.hidden_fields import clear_listing_hidden_fields

    from vendoo_studio.services import activity

    stop_generation(conv_id, discard=True)
    # Post-generate field fills would otherwise write into the cleared chat.
    activity.cancel(conv_id)

    if keep_inputs:
        notes = await in_worker_session(_carried_notes, conv_id, notes)

    # Stop running marketplace jobs before the wipe.
    await cancel_conversation_jobs(conv_id)

    await in_worker_session(_wipe, conv_id, keep_inputs, notes)
    await asyncio.to_thread(clear_listing_hidden_fields, conv_id)
    return await in_worker_session(_conversation_view, conv_id)


def _reset_check(db: Session, conv_id: str) -> str | None:
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    from vendoo_studio.services.native_marketplaces import running_marketplace

    busy = running_marketplace(db, conv_id)
    if busy:
        # The marketplace record outlives Clear; the action writing it has to finish first.
        raise HTTPException(409, f"Wait for the {busy} listing to finish, or cancel it, before clearing.")
    return conv.notes


def _carried_notes(db: Session, conv_id: str, notes: str | None) -> str | None:
    """Regenerate starts from Item Details, so the facts that only lived on the listing (cost of
    goods, labels, internal notes, and the measurements and flaws in the description) move there first."""
    from vendoo_studio.services.listing_carryover import notes_with_carryover

    revisions = ListingRepo(db).get_revisions(conv_id)
    return notes_with_carryover(notes, dict(revisions[0].listing_json) if revisions else None)


def _wipe(db: Session, conv_id: str, keep_inputs: bool, notes: str | None) -> None:
    from vendoo_studio.services.vendoo_import import parse_notes

    # Marketplace records survive Clear and Regenerate, with the jobs they name.
    wipe_contents(db, conv_id, keep_photos=keep_inputs, keep_marketplace_listings=True)
    db.expire_all()
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # A name the seller gave the listing outlives Regenerate; Clear drops it.
    if not (keep_inputs and parse_notes(notes).get("titlePinned")):
        conv.title = "New Listing"
    if keep_inputs:
        conv.notes = notes
    else:
        conv.notes = None
        # Clear removes this item's facts as well as its visible form. Otherwise
        # generation and chat reuse corrections from the listing just erased.
        from vendoo_studio.models.listing_evidence import ListingCorrection

        db.query(ListingCorrection).filter_by(conversation_id=conv_id).delete(synchronize_session=False)
    conv.status = "draft"
    conv.settled_at = None
    conv.unsettled_at = None
    conv.updated_at = utcnow()
    db.commit()


def _conversation_view(db: Session, conv_id: str) -> Conversation:
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return _conv_response(conv, _extras_for(db, conv_id))


@router.delete("/{conv_id}", response_model=DeleteConversationResult, responses={**REFUSED, **NOT_FOUND, **CONFLICT})
def delete_conversation(conv_id: str, db: Session = Depends(get_db)):
    try:
        deleted_jobs, deleted_photos = delete_listing(db, conv_id)
    except ListingBusy as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DeleteConversationResult(ok=True, deleted_jobs=deleted_jobs, deleted_photos=deleted_photos)


class DiscardBlankResponse(BaseModel):
    discarded: bool


@router.post("/{conv_id}/discard-blank", response_model=DiscardBlankResponse)
def discard_blank_conversation(conv_id: str, db: Session = Depends(get_db)):
    """Delete a new listing the seller left without adding anything; keep it otherwise."""
    from vendoo_studio.services import blank_drafts

    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    if not blank_drafts.is_blank(db, conv):
        return DiscardBlankResponse(discarded=False)
    delete_listing(db, conv_id)
    return DiscardBlankResponse(discarded=True)


def _msg_response(msg) -> dict:
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "text": msg.text,
        "provider": msg.provider,
        "model": msg.model,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
        "attachments": msg.attachments or [],
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _cover_photo_url(cover: tuple[str, str | None] | None) -> str | None:
    return cover_thumb_url(*cover) if cover else None


def _extras(
    cover_photo_url: str | None,
    facet: dict | None,
    revision_id: str | None = None,
    unsent_edits: bool = False,
    direct: dict[str, dict] | None = None,
    sold=None,
    missing_sale_data: list[str] | None = None,
) -> dict:
    """The per-row fields the sidebar needs alongside the conversation itself."""
    return {
        "cover_photo_url": cover_photo_url,
        "sku": (facet or {}).get("sku"),
        "price": (facet or {}).get("price"),
        "current_revision_id": revision_id,
        "unsent_edits": unsent_edits,
        "etsy_eligible": (facet or {}).get("etsy_eligible", True),
        "direct_marketplaces": [market for market, record in (direct or {}).items() if record["state"] in LIVE_STATES],
        "direct_records": dict(direct or {}),
        "units_sold": sold.units_sold if sold else 0,
        "quantity": sold.quantity if sold else None,
        "sold_at": _iso(sold.sold_at) if sold else None,
        "missing_sale_data": missing_sale_data or [],
    }


def _extras_for(db: Session, conv_id: str) -> dict:
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    revision_id = repo.current_revision_id(conv_id)
    unsent = unsent_edits(db, [conv] if conv else [], {conv_id: revision_id} if revision_id else {})
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

    from vendoo_studio.services.item_status import sold_units
    from vendoo_studio.services.sales_records import missing_by_conversation

    return _extras(
        _cover_photo_url(repo.cover_photo(conv_id)),
        inventory_facets.listing_facet(repo.db, conv_id),
        revision_id,
        conv_id in unsent,
        MarketplaceListingRepo(db).summaries(conv_id).get(conv_id, {}),
        sold_units(db, [conv] if conv else []).get(conv_id),
        missing_by_conversation(db, conv_id).get(conv_id, []),
    )


def _conv_response(conv, extras: dict | None = None) -> Conversation:
    from vendoo_studio.services.imported_listing import imported_fields
    from vendoo_studio.services.vendoo_import import parse_notes

    row = extras or {}
    return Conversation(
        id=conv.id,
        title=conv.title,
        notes=conv.notes,
        status=conv.status,
        box_id=conv.box_id,
        settled_at=_iso(conv.settled_at),
        unsettled_at=_iso(conv.unsettled_at),
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else "",
        cover_photo_url=row.get("cover_photo_url"),
        sku=row.get("sku"),
        price=row.get("price"),
        direct_marketplaces=[str(m) for m in row.get("direct_marketplaces") or []],
        direct_records=row.get("direct_records") or {},
        units_sold=int(row.get("units_sold") or 0),
        quantity=row.get("quantity"),
        sold_at=row.get("sold_at"),
        missing_sale_data=row.get("missing_sale_data", []),
        unsent_edits=bool(row.get("unsent_edits")),
        etsy_eligible=bool(row.get("etsy_eligible", True)),
        **imported_fields(parse_notes(conv.notes), row.get("direct_records") or {}),
    )


def _photo_response(photo) -> ListingPhoto:
    return ListingPhoto(
        id=photo.id,
        conversation_id=photo.conversation_id,
        original_filename=photo.original_filename,
        mime_type=photo.mime_type,
        size_bytes=photo.size_bytes,
        display_order=photo.display_order,
        width=photo.width,
        height=photo.height,
        created_at=photo.created_at.isoformat() if photo.created_at else "",
        url=photo_url(photo.id, photo.checksum),
        edits=photo.edits,
        marketplace=photo.marketplace,
    )
