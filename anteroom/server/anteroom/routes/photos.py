from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anteroom.services import listing_persistence
from anteroom.database import get_db
from anteroom.repositories.queries import ConversationRepo
from anteroom.routes.conversations import _photo_response
from anteroom.services import marketplace_media, photo_edits
from anteroom.services.photo_edits import BackgroundRemovalError, PhotoEdits
from anteroom.services.photos import (
    THUMBNAIL_MIME,
    THUMBNAIL_SIZES,
    get_or_create_thumbnail,
    process_upload,
)
from anteroom.services.photo_store import photo_store

router = APIRouter(tags=["photos"])
edit_router = APIRouter(tags=["photos"])


@router.get("/api/marketplace-media")
def marketplace_media_rules():
    """Each marketplace's photo limit and video rules, for its form's photos."""
    return marketplace_media.rules()


@router.post("/api/conversations/{conv_id}/photos")
def upload_photos(
    conv_id: str,
    files: list[UploadFile] = File(...),
    marketplace: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Add item photos, or with ``marketplace`` photos only that marketplace's form sends."""
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    if marketplace is None:
        if len(repo.get_photos(conv_id)) + len(files) > 20:
            raise HTTPException(400, "Maximum 20 photos per listing")
    elif marketplace not in marketplace_media.PHOTO_LIMITS:
        raise HTTPException(400, "Unknown marketplace")
    else:
        limit = marketplace_media.PHOTO_LIMITS[marketplace]
        own = [photo for photo in repo.get_all_photos(conv_id) if photo.marketplace == marketplace]
        if len(own) + len(files) > limit:
            raise HTTPException(400, f"Add at most {limit} photos on this form")

    results = []
    failures: list[str] = []
    for f in files:
        try:
            meta = process_upload(conv_id, f)
        except ValueError as e:
            failures.append(f"{f.filename or 'file'}: {e}")
            continue

        try:
            photo = listing_persistence.add_photo(repo.db,
                conv_id=conv_id,
                original_filename=meta["original_filename"],
                stored_filename=meta["stored_filename"],
                mime_type=meta["mime_type"],
                size_bytes=meta["size_bytes"],
                checksum=meta["checksum"],
                width=meta["width"],
                height=meta["height"],
                marketplace=marketplace,
            )
        except Exception:
            photo_store().delete(meta["stored_filename"])
            failures.append(f"{f.filename or 'file'}: Failed to persist photo record")
            continue

        results.append(_photo_response(photo))

    if failures and not results:
        raise HTTPException(400, failures[0] if len(failures) == 1 else "Some photos could not be uploaded: " + "; ".join(failures))
    if failures:
        return {
            "ok": True,
            "count": len(results),
            "photos": results,
            "errors": failures,
            "message": f"Saved {len(results)} photo(s). {len(failures)} file(s) failed: " + "; ".join(failures),
        }

    return {"ok": True, "count": len(results), "photos": results}


@router.patch("/api/conversations/{conv_id}/photos/order")
def reorder_photos(conv_id: str, ordered_ids: list[str], db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    repo.reorder_photos(conv_id, ordered_ids)
    return {"ok": True}


class RotateRequest(BaseModel):
    # Clockwise; -90 turns the photo left.
    degrees: int


def _apply_edit(repo: ConversationRepo, conv_id: str, photo_id: str, resolve_edits, *, missing_file_detail: str = "Photo file not found"):
    try:
        return _photo_response(photo_edits.apply_photo_edit(repo, conv_id, photo_id, resolve_edits))
    except photo_edits.PhotoNotFoundError:
        raise HTTPException(404, "Photo not found") from None
    except photo_edits.PhotoConflictError as e:
        raise HTTPException(409, str(e)) from None
    except FileNotFoundError:
        raise HTTPException(404, missing_file_detail) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@edit_router.post("/api/conversations/{conv_id}/photos/{photo_id}/rotate")
def rotate_photo(conv_id: str, photo_id: str, body: RotateRequest, db: Session = Depends(get_db)):
    if body.degrees not in (90, -90, 180):
        raise HTTPException(400, "Rotate by 90, -90 or 180 degrees")
    repo = ConversationRepo(db)

    def _turn(current: PhotoEdits) -> PhotoEdits | None:
        turned = current.turned(body.degrees)
        return None if turned.is_original() else turned

    return _apply_edit(repo, conv_id, photo_id, _turn)


@edit_router.put("/api/conversations/{conv_id}/photos/{photo_id}/edits")
def edit_photo(conv_id: str, photo_id: str, body: PhotoEdits, db: Session = Depends(get_db)):
    """Replace the photo's edits; they render from the original upload, which is kept."""
    repo = ConversationRepo(db)
    target = None if body.is_original() else body
    return _apply_edit(repo, conv_id, photo_id, lambda _cur: target)


@edit_router.delete("/api/conversations/{conv_id}/photos/{photo_id}/edits")
def reset_photo(conv_id: str, photo_id: str, db: Session = Depends(get_db)):
    repo = ConversationRepo(db)
    return _apply_edit(
        repo,
        conv_id,
        photo_id,
        lambda _cur: None,
        missing_file_detail="The original photo file is missing",
    )


@edit_router.post("/api/conversations/{conv_id}/photos/{photo_id}/edits/preview")
def preview_photo_edits(conv_id: str, photo_id: str, body: PhotoEdits, crop: bool = True, db: Session = Depends(get_db)):
    """A smaller JPEG of the edits, for the editor; ``crop=false`` leaves the frame whole to draw the crop on."""
    from fastapi.responses import Response

    try:
        source = photo_edits.read_preview_source(db, conv_id, photo_id)
    except photo_edits.PhotoNotFoundError:
        raise HTTPException(404, "Photo not found") from None
    try:
        image = photo_edits.preview_jpeg(source, body, crop=crop)
    except FileNotFoundError:
        raise HTTPException(404, "Photo file not found") from None
    except BackgroundRemovalError as e:
        raise HTTPException(400, str(e)) from None
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/photos/{photo_id}")
def serve_photo(photo_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    from anteroom.models.conversation import Photo

    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404, "Photo not found")

    try:
        filepath = photo_store().local_path(photo.stored_filename)
    except FileNotFoundError:
        raise HTTPException(404, "Photo file not found") from None
    return FileResponse(str(filepath), media_type=photo.mime_type)


@router.get("/api/photos/{photo_id}/thumb")
def serve_photo_thumbnail(photo_id: str, size: int = 96, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    from anteroom.models.conversation import Photo

    if size not in THUMBNAIL_SIZES:
        raise HTTPException(400, f"Unsupported thumbnail size: {size}")

    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404, "Photo not found")

    try:
        thumb = get_or_create_thumbnail(photo.stored_filename, size)
    except FileNotFoundError:
        raise HTTPException(404, "Photo file not found") from None
    except Exception:
        # HEIC and other formats Pillow cannot decode fall back to the original.
        try:
            filepath = photo_store().local_path(photo.stored_filename)
        except FileNotFoundError:
            raise HTTPException(404, "Photo file not found") from None
        return FileResponse(str(filepath), media_type=photo.mime_type)

    return FileResponse(
        str(thumb),
        media_type=THUMBNAIL_MIME,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/api/cover-thumb")
async def cover_thumb(url: str):
    """A small JPEG for a sidebar cover that lives on another host.

    The row is about 40px. The original is what WebKit would decode and keep.
    """
    from fastapi.responses import FileResponse

    from anteroom.services.cover_thumb import remote_cover_thumb
    from anteroom.services.safe_fetch import UnsafeURLError

    try:
        path = await remote_cover_thumb(url)
    except UnsafeURLError:
        raise HTTPException(400, "That image address is not allowed") from None
    except Exception:
        raise HTTPException(404, "Cover image is not available") from None
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


router.include_router(edit_router)
