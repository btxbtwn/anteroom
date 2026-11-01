"""Moving a Mac's listings, photos, settings and keys into the signed-in account."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from vendoo_studio.services import account_import, hosted_photos
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/account-import')


def get_import_db(db=Depends(get_seller_db)):
    db.info['quiet_changes'] = True
    return db


class Rows(BaseModel):
    model_config = ConfigDict(extra='forbid')
    table: str
    rows: list[dict]


@router.get('')
def summary(db=Depends(get_seller_db)):
    from vendoo_studio.models.conversation import Conversation
    return {'tables': [table.name for table in account_import.imported_tables()],
            'listings': db.scalar(select(func.count()).select_from(Conversation))}


@router.post('/rows')
def rows(body: Rows, db=Depends(get_import_db)):
    return account_import.import_rows(db, body.table, body.rows)


@router.get('/photos')
def pending_photos(db=Depends(get_import_db)):
    return account_import.pending_photos(db)


@router.post('/photos/{photo_id}/{field}')
async def photo(photo_id: str, field: str, request: Request, db=Depends(get_import_db)):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > hosted_photos.MAX_BYTES:
            raise HTTPException(413, f'Choose a photo of {hosted_photos.MAX_BYTES // (1024 * 1024)} MB or smaller')
        data.extend(chunk)
    token = request.headers['authorization'].partition(' ')[2]
    return await run_in_threadpool(account_import.import_photo, db, photo_id, field, bytes(data), token)


@router.post('/finish')
def finish(db=Depends(get_seller_db)):
    """Tell the seller's other devices, once, that everything may have changed."""
    account_import.announce_all(db)
    return {'ok': True}


@router.put('/settings')
def settings(values: dict = Body(...)):
    return {'added': account_import.import_settings(values)}


@router.put('/secrets')
def secrets(values: dict = Body(...)):
    # Report slot names only; values never leave the server again.
    return {'saved': account_import.import_secrets(values)}
