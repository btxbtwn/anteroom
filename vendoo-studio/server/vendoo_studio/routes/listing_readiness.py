"""The fields each listing still needs per marketplace, for the inventory's rows on screen."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import listing_readiness

router = APIRouter(tags=["listing-readiness"])


@router.get("/api/listing-readiness")
def readiness(ids: list[str] = Query(default=[], max_length=listing_readiness.MAX_LISTINGS),
              db: Session = Depends(get_db, scope="function")):
    """Read-only. A plain function, so FastAPI runs its form work in the threadpool."""
    return {"listings": listing_readiness.for_listings(db, ids)}
