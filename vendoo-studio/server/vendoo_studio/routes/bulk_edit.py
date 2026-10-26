"""Seller-reviewed bulk edits to local inventory."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import bulk_edit

router = APIRouter(prefix="/api/listings/bulk-edit", tags=["listings"])


@router.post("/preview")
def preview(body: bulk_edit.PreviewRequest, db: Session = Depends(get_db)):
    return bulk_edit.preview(db, body)


@router.post("/apply")
def apply(body: bulk_edit.ApplyRequest, db: Session = Depends(get_db)):
    return bulk_edit.apply(db, body)
