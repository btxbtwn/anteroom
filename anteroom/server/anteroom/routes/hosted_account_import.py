"""Moving a Mac's listings, photos, settings and keys into the signed-in account."""
import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from anteroom.services import account_import, hosted_photos
from anteroom.services.hosted_database import get_seller_db, in_seller_session
from anteroom.services.hosted_limits import bounded_route_class, hold_upload_slot, read_bounded_body

# Up to MAX_ROWS records of listing JSON per batch, and bounded config payloads.
MAX_ROWS_BYTES = 16 * 1024 * 1024
MAX_CONFIG_BYTES = 1 * 1024 * 1024

router = APIRouter(prefix='/api/account-import')
_rows_router = APIRouter(
    route_class=bounded_route_class(
        lambda: MAX_ROWS_BYTES,
        too_large_detail='Import batch is too large',
        use_upload_slots=True,
    ),
)
_config_router = APIRouter(
    route_class=bounded_route_class(
        lambda: MAX_CONFIG_BYTES,
        too_large_detail='Import payload is too large',
    ),
)


def get_import_db(db=Depends(get_seller_db)):
    return _quiet(db)


def _quiet(db):
    db.info['quiet_changes'] = True
    return db


def _save_photo(db, photo_id, field, key, data, width, height):
    return account_import.save_imported_photo(_quiet(db), photo_id, field, key, data, width, height)


class Rows(BaseModel):
    model_config = ConfigDict(extra='forbid')
    table: str = Field(min_length=1, max_length=100)
    rows: list[dict] = Field(max_length=account_import.MAX_ROWS)


@router.get('')
def summary(db=Depends(get_seller_db)):
    from anteroom.models.conversation import Conversation
    return {'tables': [table.name for table in account_import.imported_tables()],
            'listings': db.scalar(select(func.count()).select_from(Conversation))}


@_rows_router.post('/rows')
def rows(body: Rows, db=Depends(get_import_db)):
    return account_import.import_rows(db, body.table, body.rows)


@router.get('/photos')
def pending_photos(db=Depends(get_import_db)):
    return account_import.pending_photos(db)


@router.post('/photos/{photo_id}/{field}')
async def photo(photo_id: str, field: str, request: Request):
    # Guard memory with UploadSlots and stream-cap raw bytes before decoding.
    async with hold_upload_slot(request):
        data = await read_bounded_body(
            request,
            hosted_photos.MAX_BYTES,
            too_large_detail=f'Choose a photo of {hosted_photos.MAX_BYTES // (1024 * 1024)} MB or smaller',
        )
        # Convert and upload with no session open, then lock the row only to save
        # its key: Storage can take seconds, and a held lock or connection would
        # stall the seller's other requests meanwhile (as in ``hosted_photos``).
        draft_id = await in_seller_session(request, account_import.photo_to_import, photo_id, field)
        if draft_id is None:
            return {'copied': False}
        image, width, height = await asyncio.to_thread(hosted_photos.normalize, bytes(data))
        del data
        token = request.headers['authorization'].partition(' ')[2]
        key = hosted_photos.new_key(request.state.seller.user_id, draft_id)
        await asyncio.to_thread(hosted_photos.storage_call, 'POST', key, token, image)
    try:
        saved = await in_seller_session(request, _save_photo, photo_id, field, key, image, width, height)
    except BaseException:
        await _forget(key, token)
        raise
    if not saved:
        # Another upload of the same file saved first; keep only its object.
        await _forget(key, token)
    return {'copied': saved}


async def _forget(key, token):
    """Delete an uploaded object no row points at."""
    try:
        await asyncio.to_thread(hosted_photos.storage_call, 'DELETE', key, token)
    except HTTPException:
        pass


@router.post('/finish')
def finish(db=Depends(get_seller_db)):
    """Tell the seller's other devices, once, that everything may have changed."""
    account_import.announce_all(db)
    return {'ok': True}


@_config_router.put('/settings')
def settings(values: dict = Body(...)):
    return {'added': account_import.import_settings(values)}


@_config_router.put('/secrets')
def secrets(values: dict = Body(...)):
    # Report slot names only; values never leave the server again.
    return {'saved': account_import.import_secrets(values)}


router.include_router(_rows_router)
router.include_router(_config_router)
