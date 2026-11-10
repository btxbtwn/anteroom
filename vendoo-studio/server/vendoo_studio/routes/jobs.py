from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db, in_worker_session
from vendoo_studio.repositories.queries import JobRepo, ConversationRepo
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.models.conversation import Photo

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    id: str
    conversation_id: str
    status: str
    current_step: str | None
    attempt_count: int
    last_error: str | None
    listing_title: str
    mode: str | None = None
    created_at: str
    updated_at: str


@router.get("/queue")
def get_queue(db: Session = Depends(get_db)):
    from vendoo_studio.services import activity
    from vendoo_studio.services.streaming import active_generations
    from vendoo_studio.models.conversation import Conversation

    work = []
    generations = active_generations()
    background = activity.running_all()
    ids = set(generations) | set(background)
    for conv in db.query(Conversation.id, Conversation.title).filter(Conversation.id.in_(ids)).all():
        run = generations.get(conv.id)
        labels = background.get(conv.id, [])
        if run or labels:
            work.append({
                "conversation_id": conv.id, "title": conv.title,
                "detail": run.last_status or "Generating listing…" if run else " · ".join(labels),
            })
    jobs = JobRepo(db).list_all()
    active = JobRepo(db).get_active()
    recent = [j for j in jobs if j.status in {"completed", "failed", "cancelled"}][:30]
    return {"work": work, "jobs": [_job_response(j) for j in active + recent]}


@router.get("", response_model=list[JobResponse])
def list_jobs(conversation_id: str | None = None, db: Session = Depends(get_db)):
    repo = JobRepo(db)
    if conversation_id:
        conv = ConversationRepo(db).get(conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
    return [_job_response(job) for job in repo.list_summaries(conversation_id)]


@router.get("/fill-stats")
def get_fill_stats(days: int = Query(30, ge=1, le=365), limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    from vendoo_studio.services.job_metrics import fill_success_stats

    return fill_success_stats(db, days=days, limit=limit)


@router.get("/marketplace-statuses")
def get_marketplace_statuses(job_ids: str = Query(""), db: Session = Depends(get_db)):
    """Per-marketplace status from each imported job's cached Vendoo draft.

    Returns only the status-bearing slices of the draft so the sidebar can badge
    every thread with the same logic as the hover popup.
    """
    repo = JobRepo(db)
    result: dict[str, dict] = {}
    wanted = list(dict.fromkeys(item.strip() for item in job_ids.split(",") if item.strip()))
    drafts = repo.latest_vendoo_drafts(wanted)
    for job_id in wanted:
        cached = drafts.get(job_id)
        if not cached:
            continue
        item = cached.get("item")
        if not isinstance(item, dict):
            item = {}
        form = cached.get("form")
        if not isinstance(form, dict):
            form = {}
        listings = item.get("listings")
        if not isinstance(listings, dict):
            listings = {}
        external = item.get("externalListings")
        if not isinstance(external, dict):
            external = {}
        item_out: dict = {
            "listings": {
                marketplace: {"status": listing.get("status")}
                for marketplace, listing in listings.items()
                if isinstance(listing, dict)
            },
        }
        if external:
            item_out["externalListings"] = {
                marketplace: {"status": listing.get("status")}
                for marketplace, listing in external.items()
                if isinstance(listing, dict)
            }
        result[job_id] = {
            "statuses": cached.get("statuses"),
            "form": {"statuses": form.get("statuses")},
            "item": item_out,
        }
    return result


@router.get("/{job_id}/timing")
def get_job_timing(job_id: str, db: Session = Depends(get_db)):
    from vendoo_studio.services.job_metrics import job_timing

    if not JobRepo(db).get(job_id):
        raise HTTPException(404, "Job not found")
    return job_timing(db, job_id)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_response(job)


@router.get("/{job_id}/events")
def get_job_events(job_id: str, db: Session = Depends(get_db)):
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    events = repo.get_events(job_id)
    return [
        {
            "id": e.id,
            "sequence": e.sequence,
            "event_type": e.event_type,
            "step": e.step,
            "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in events
    ]


@router.get("/{job_id}/preview")
async def stream_job_preview(job_id: str):
    # The stream outlives the request, so the lookup uses a session of its own
    # that is closed before the first frame.
    if not await in_worker_session(lambda db: JobRepo(db).get(job_id) is not None):
        raise HTTPException(404, "Job not found")

    from vendoo_studio.services.preview_hub import preview_hub

    async def events():
        queue = await preview_hub.subscribe(job_id)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(frame)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            await preview_hub.unsubscribe(job_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/logs")
def get_job_logs(job_id: str, db: Session = Depends(get_db)):
    """Events and the fill log for one job, in one place."""
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    from vendoo_studio.services.fill_log import FillLogService

    events = [
        {
            "id": event.id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "step": event.step,
            "payload": event.payload,
            "created_at": event.created_at.isoformat() if event.created_at else "",
        }
        for event in repo.get_events(job_id)
    ]
    return {"job_id": job_id, "events": events, "fill_log": FillLogService(db).report_for_job(job)}


@router.get("/{job_id}/fill-log")
def get_job_fill_log(job_id: str, db: Session = Depends(get_db)):
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    from vendoo_studio.services.fill_log import FillLogService
    return FillLogService(db).report_for_job(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    response, live = await in_worker_session(_cancel, job_id)
    if live is None:
        return response
    from vendoo_studio.services import native_marketplaces

    # A direct listing stops at its next safe point and keeps the slot until
    # the marketplace or Chrome has answered; its own worker ends the job.
    await native_marketplaces.request_cancel(job_id, live)
    return await in_worker_session(lambda db: _job_response(JobRepo(db).get(job_id)))


def _cancel(db: Session, job_id: str) -> tuple[JobResponse, dict | None]:
    """The database part of Cancel. A live direct job's record comes back for ``request_cancel``."""
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # A job that finished while the seller was reaching for Cancel has nothing
    # left to stop. Answer with the job as it stands instead of an error.
    if job.status in {"completed", "cancelled"}:
        return _job_response(job), None
    if str(job.current_step or "").startswith("native_"):
        from vendoo_studio.services import native_marketplaces

        live = native_marketplaces.stop_job(db, job)
        return _job_response(job), live

    job.status = "cancelled"
    job.current_step = None
    db.commit()
    repo.add_event(job_id, "cancelled")
    return _job_response(job), None


def _job_response(job) -> JobResponse:
    title = job.listing_snapshot.get("title", "") if job.listing_snapshot else ""
    return JobResponse(
        id=job.id,
        conversation_id=job.conversation_id,
        status=job.status,
        current_step=job.current_step,
        attempt_count=job.attempt_count,
        last_error=job.last_error,
        listing_title=title,
        mode="native_marketplace" if str(job.current_step or "").startswith("native_") else None,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


@router.get("/{job_id}/photos/{photo_id}")
def serve_job_photo(job_id: str, photo_id: str, db: Session = Depends(get_db)):
    job_repo = JobRepo(db)
    job = job_repo.photo_source(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.has_approved_photos:
        photo = next((row for row in (job.approved_photos or []) if row["id"] == photo_id), None)
        if photo is None:
            raise HTTPException(404, "Photo not approved for this job")
        filename, mime_type = photo["stored_filename"], photo["mime_type"]
        db.commit()
        try:
            filepath = photo_store().local_path(filename)
        except FileNotFoundError:
            raise HTTPException(404, "Approved photo file not found") from None
        return FileResponse(str(filepath), media_type=mime_type)

    photo = db.query(Photo).filter(
        Photo.id == photo_id,
        Photo.conversation_id == job.conversation_id,
    ).first()
    if not photo:
        raise HTTPException(404, "Photo not found for this job")

    filename, mime_type = photo.stored_filename, photo.mime_type
    db.commit()
    try:
        filepath = photo_store().local_path(filename)
    except FileNotFoundError:
        raise HTTPException(404, "Photo file not found") from None

    return FileResponse(str(filepath), media_type=mime_type)
