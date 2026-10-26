"""Video upload, preview and job-scoped delivery; never marketplace writes."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.models.listing_video import ListingVideo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import listing_videos as videos

router = APIRouter(tags=["listing-videos"])


def _conversation(db: Session, conv_id: str):
    if db.get(Conversation, conv_id) is None:
        raise HTTPException(404, "Conversation not found")


@router.get("/api/conversations/{conv_id}/videos")
def library(conv_id: str, db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    rows = db.query(ListingVideo).filter_by(conversation_id=conv_id).order_by(ListingVideo.created_at, ListingVideo.id).all()
    return {"videos": [videos.video_view(row) for row in rows], "max_bytes": videos.MAX_VIDEO_BYTES}


@router.post("/api/conversations/{conv_id}/videos")
async def upload(conv_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    try:
        row = await videos.store_upload(db, conv_id, file)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return videos.video_view(row)


def _serve(row):
    if row is None or not videos.video_path(row).is_file():
        raise HTTPException(404, "Video file not found")
    return FileResponse(str(videos.video_path(row)), media_type=row.mime_type)


@router.get("/api/videos/{video_id}")
def preview(video_id: str, db: Session = Depends(get_db)):
    return _serve(db.get(ListingVideo, video_id))


@router.get("/api/jobs/{job_id}/videos/{video_id}")
def job_video(job_id: str, video_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    expected = (job.listing_snapshot or {}).get("_native_video") if job else None
    row = db.get(ListingVideo, video_id)
    if (not row or not expected or expected.get("id") != video_id or row.conversation_id != job.conversation_id
            or row.checksum != expected.get("checksum")):
        raise HTTPException(404, "Video not found for this job revision")
    return _serve(row)


@router.delete("/api/conversations/{conv_id}/videos/{video_id}")
def delete(conv_id: str, video_id: str, db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    row = db.query(ListingVideo).filter_by(id=video_id, conversation_id=conv_id).first()
    if not row:
        raise HTTPException(404, "Video not found")
    if db.query(Job).filter(Job.conversation_id == conv_id, Job.status.in_(ACTIVE_JOB_STATUSES)).first():
        raise HTTPException(409, "Wait for this item's marketplace action to finish before removing a video.")
    current = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(current.current_revision_id) if current else None
    listing = revision.listing_json if revision else {}
    if any(isinstance(value, dict) and value.get("videoId") == video_id for value in listing.values()):
        raise HTTPException(409, "Clear this video from the listing form before deleting it.")
    path = videos.video_path(row)
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
