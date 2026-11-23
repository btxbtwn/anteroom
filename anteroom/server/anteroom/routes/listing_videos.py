"""Video upload, preview and job-scoped delivery; never marketplace writes."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.models.conversation import Conversation
from anteroom.models.job import Job
from anteroom.models.listing_video import ListingVideo
from anteroom.services import listing_videos as videos

router = APIRouter(tags=["listing-videos"])


def _conversation(db: Session, conv_id: str):
    if db.get(Conversation, conv_id) is None:
        raise HTTPException(404, "Conversation not found")


@router.get("/api/conversations/{conv_id}/videos")
def library(conv_id: str, db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    rows = db.query(ListingVideo).filter_by(conversation_id=conv_id).order_by(ListingVideo.created_at, ListingVideo.id).all()
    return {"videos": [videos.video_view(row) for row in rows], "max_bytes": videos.MAX_VIDEO_BYTES,
            "rules": videos.VIDEO_RULES}


@router.post("/api/conversations/{conv_id}/videos")
def upload(conv_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    try:
        row = videos.store_upload(db, conv_id, file)
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
    if (not job or not row or not expected or expected.get("id") != video_id or row.conversation_id != job.conversation_id
            or row.checksum != expected.get("checksum")):
        raise HTTPException(404, "Video not found for this job revision")
    return _serve(row)


@router.delete("/api/conversations/{conv_id}/videos/{video_id}")
def delete(conv_id: str, video_id: str, db: Session = Depends(get_db)):
    _conversation(db, conv_id)
    row = db.query(ListingVideo).filter_by(id=video_id, conversation_id=conv_id).first()
    if not row:
        raise HTTPException(404, "Video not found")
    if refusal := videos.deletion_refusal(db, conv_id, video_id):
        raise HTTPException(409, refusal)
    path = videos.video_path(row)
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
