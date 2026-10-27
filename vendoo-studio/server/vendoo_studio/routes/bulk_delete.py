"""Seller-reviewed delete of selected items from Anteroom. Nothing is delisted."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import bulk_delete

router = APIRouter(prefix="/api/listings/bulk-delete", tags=["listings"])


@router.post("/preview")
def preview(body: bulk_delete.PreviewRequest, db: Session = Depends(get_db)):
    """Which selected items can be deleted. Nothing is deleted."""
    return bulk_delete.preview(db, body)


@router.post("/delete")
def delete(body: bulk_delete.DeleteRequest, db: Session = Depends(get_db)):
    """Delete the reviewed items from Anteroom. Requires confirm; never delists."""
    return bulk_delete.delete(db, body)
