"""Import active listings from the marketplaces Anteroom reaches through Chrome. Read-only there."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import chrome_import
from vendoo_studio.services.chrome_import import ChromeImportError

router = APIRouter(prefix="/api/chrome-import", tags=["chrome-import"])


class ImportRequest(BaseModel):
    listing: str = Field(min_length=1, max_length=2048)
    # Link the listing to this existing item instead of creating a new one.
    link_conversation_id: str | None = Field(None, min_length=1, max_length=64)


@router.get("/{marketplace}/active")
async def import_candidates(marketplace: str, cursor: str | None = Query(None, max_length=300), db: Session = Depends(get_db)):
    try:
        return await chrome_import.active_listings(db, marketplace, cursor)
    except ChromeImportError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{marketplace}")
async def import_item(marketplace: str, body: ImportRequest, db: Session = Depends(get_db)):
    """Save one listing as a local item, or link it to the item the seller chose. Never changes the marketplace."""
    try:
        if body.link_conversation_id:
            return await chrome_import.link_listing(db, marketplace, body.listing, body.link_conversation_id)
        return await chrome_import.import_listing(db, marketplace, body.listing)
    except ChromeImportError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
