"""Save draft on Poshmark, Mercari and Depop from the web, through the seller's Chrome.

The seller starts it; the server records it exactly as the Mac does and signs a
``save_draft`` command for that one operation. The page relays the command to
the seller's Chrome extension, which checks the signature, saves and reads back
the draft with the seller's own marketplace session, and reports here.

Chrome reaches the two operation routes with the operation's token instead of
a seller session (the session middleware lets exactly these paths through).
The token opens only that running operation's approved photos and its one
result. Nothing here lists, publishes, updates or delists.
"""
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import browser_commands
from vendoo_studio.services import native_marketplaces as native

ChromeDraft = Literal['poshmark', 'mercari', 'depop']
PHOTO_LIMIT = 20 * 1024 * 1024
RESULT_LIMIT = 64 * 1024
UNAUTHORIZED = {'detail': 'This operation is not running or the token is not valid.'}

router = APIRouter()


class ChromeDraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision_id: str


class ChromeResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    result: dict[str, Any]


@router.post('/api/conversations/{conv_id}/native-marketplaces/{marketplace}/chrome-draft')
async def chrome_draft(conv_id: str, marketplace: ChromeDraft, body: ChromeDraftRequest, db: Session = Depends(get_db)):
    try:
        return await native.save_draft_in_chrome(conv_id, marketplace, body.revision_id, db)
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _seller(request: Request, operation_id: str):
    from vendoo_studio.services.hosted_context import for_seller

    token = browser_commands.bearer(request.headers.get('authorization'))
    return for_seller(browser_commands.owner(operation_id, token)), token


def _refused() -> Response:
    return Response(json.dumps(UNAUTHORIZED), status_code=401, media_type='application/json',
                    headers={'WWW-Authenticate': 'Bearer'})


@router.get('/api/operations/{operation_id}/photos/{photo_id}')
def operation_photo(operation_id: str, photo_id: str, request: Request):
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services import approved_photos
    from vendoo_studio.services.photo_store import photo_store
    from vendoo_studio.services.service_context import use

    try:
        context, token = _seller(request, operation_id)
        with use(context), SessionLocal() as db:
            job, _marketplace = native.chrome_operation(db, operation_id, token)
            row = next((row for row in job.listing_snapshot.get(approved_photos.KEY) or [] if row.get('id') == photo_id), None)
            if row is None:
                raise HTTPException(404, 'Photo not approved for this operation')
            if int(row.get('size_bytes') or 0) > PHOTO_LIMIT:
                raise HTTPException(413, 'Photo is larger than 20 MB')
            data = photo_store().read(row['stored_filename'])
    except browser_commands.OperationUnauthorized:
        return _refused()
    if len(data) > PHOTO_LIMIT:
        raise HTTPException(413, 'Photo is larger than 20 MB')
    return Response(data, media_type=row['mime_type'], headers={'Cache-Control': 'no-store'})


@router.post('/api/operations/{operation_id}/result')
async def operation_result(operation_id: str, request: Request):
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.service_context import use

    raw = await request.body()
    if len(raw) > RESULT_LIMIT:
        raise HTTPException(413, 'Result is too large')
    try:
        body = ChromeResult.model_validate_json(raw)
    except ValueError:
        raise HTTPException(422, 'Check the supplied values and try again') from None
    try:
        context, token = _seller(request, operation_id)
        with use(context), SessionLocal() as db:
            try:
                return native.record_chrome_result(db, operation_id, token, body.result)
            except native.NativeMarketplaceError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
    except browser_commands.OperationUnauthorized:
        return _refused()
