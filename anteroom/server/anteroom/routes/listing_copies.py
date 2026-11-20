"""Review selected source items; each copy uses the existing atomic copy route."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import listing_copy

router = APIRouter(prefix="/api/listings/copies", tags=["listings"])


@router.post("/preview")
def preview(body: listing_copy.BatchPreviewRequest, db: Session = Depends(get_db, scope="function")):
    return listing_copy.batch_preview(db, body)
