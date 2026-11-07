"""Import active listings from the marketplaces Anteroom reaches through Chrome. Read-only there."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from vendoo_studio.services import chrome_import
from vendoo_studio.services.chrome_import import ChromeImportError

router = APIRouter(prefix="/api/chrome-import", tags=["chrome-import"])


class ImportRequest(BaseModel):
    listing: str = Field(min_length=1, max_length=2048)
    # Link the listing to this existing item instead of creating a new one.
    link_conversation_id: str | None = Field(None, min_length=1, max_length=64)


@router.get("/{marketplace}/active")
async def import_candidates(marketplace: str, cursor: str | None = Query(None, max_length=300)):
    try:
        return await chrome_import.active_listings(marketplace, cursor)
    except ChromeImportError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{marketplace}")
async def import_item(marketplace: str, body: ImportRequest):
    """Save one listing as a local item, or link it to the item the seller chose. Never changes the marketplace."""
    try:
        if body.link_conversation_id:
            return await chrome_import.link_listing(marketplace, body.listing, body.link_conversation_id)
        return await chrome_import.import_listing(marketplace, body.listing)
    except ChromeImportError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
