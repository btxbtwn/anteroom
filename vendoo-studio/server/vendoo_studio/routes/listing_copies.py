"""Review selected source items; each copy uses the existing atomic copy route."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import listing_copy

router = APIRouter(prefix="/api/listings/copies", tags=["listings"])


@router.post("/preview")
def preview(body: listing_copy.BatchPreviewRequest, db: Session = Depends(get_db)):
    return listing_copy.batch_preview(db, body)
