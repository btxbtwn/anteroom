"""Seller-reviewed List, Delist and Relist for selected items, one item per confirmed request."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import bulk_marketplace

router = APIRouter(prefix="/api/listings/marketplace-actions", tags=["listings"])


@router.post("/preview")
def preview(body: bulk_marketplace.PreviewRequest, db: Session = Depends(get_db)):
    """What each selected item would do. Nothing is sent."""
    return bulk_marketplace.preview(db, body)


@router.post("/perform")
async def perform(body: bulk_marketplace.PerformRequest, db: Session = Depends(get_db)):
    """Perform one reviewed item's action on its named marketplaces. Requires confirm."""
    return await bulk_marketplace.perform(db, body)
