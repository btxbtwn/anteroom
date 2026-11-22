"""Seller-reviewed bulk edits to local inventory."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import bulk_edit
from anteroom.services.marketplace_text import TextRequest, prepare

router = APIRouter(prefix="/api/listings/bulk-edit", tags=["listings"])


@router.post("/preview")
def preview(body: bulk_edit.PreviewRequest, db: Session = Depends(get_db)):
    return bulk_edit.preview(db, body)


@router.post("/apply")
def apply(body: bulk_edit.ApplyRequest, db: Session = Depends(get_db)):
    return bulk_edit.apply(db, body)


@router.post("/marketplace-text/{conv_id}")
async def marketplace_text(conv_id: str, body: TextRequest):
    """Keep, shorten or write one item's marketplace titles and descriptions, saved locally."""
    from anteroom.database import in_worker_session

    return await prepare(in_worker_session, conv_id, body)
