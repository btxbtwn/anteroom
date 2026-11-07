"""Account deletion for the signed-in seller. Hosted only; the Mac app has no account to delete."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services import account_deletion
from vendoo_studio.services.seller_sessions import SellerIdentity

router = APIRouter()


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirm: Literal[True]


@router.delete('/api/account')
async def delete_account(_body: DeleteAccountRequest, request: Request):
    identity = getattr(request.state, 'seller', None)
    if not isinstance(identity, SellerIdentity):
        raise HTTPException(401, 'A verified seller session is required')
    # The session middleware verified this exact header for this request.
    token = request.headers['authorization'].partition(' ')[2]
    try:
        await account_deletion.delete_account(identity.user_id, token)
    except account_deletion.AccountDeletionRefused as refusal:
        return JSONResponse({'detail': refusal.message}, status_code=refusal.status, headers={'Cache-Control': 'no-store'})
    except SQLAlchemyError:
        # Every step can run again: a retry finishes whatever was left.
        raise HTTPException(503, 'Could not finish deleting your account. Try again.', headers={'Retry-After': '5'}) from None
    return JSONResponse({'deleted': True}, headers={'Cache-Control': 'no-store'})
