"""Process-to-process Mac and Chrome calls. Browsers do not call these routes."""
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from anteroom.services.device_routes import token_matches
from anteroom.services.hosted_browser_relay import ChromeReadError, relay_for as chrome_for
from anteroom.services.mac_inference import NO_REPLY, NOT_TAKEN, OPERATIONS, MacCallFailed, MacUnavailable, relay_for as mac_for
from anteroom.services.provider_deadlines import ProviderStalled

router = APIRouter(prefix='/internal')


def _authorized(authorization: str | None) -> None:
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token_matches(token):
        raise HTTPException(404, 'Not found')


def _owner(user_id: str) -> str:
    try:
        return str(UUID(user_id))
    except ValueError:
        raise HTTPException(404, 'Not found') from None


class MacCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    provider: Literal['chatgpt', 'claude']
    operation: str
    args: list = Field(default_factory=list)
    kwargs: dict = Field(default_factory=dict)


@router.post('/mac-inference/invoke')
async def invoke_mac(body: MacCall, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    if body.operation not in OPERATIONS:
        raise HTTPException(404, 'Not found')
    try:
        result, model = await mac_for(_owner(body.user_id)).call(body.operation, body.args, body.kwargs, body.provider)
    except MacCallFailed as failure:
        return {'ok': False, 'error': failure.kind}
    except ProviderStalled:
        return {'ok': False, 'error': NO_REPLY}
    except MacUnavailable:
        return {'ok': False, 'error': NOT_TAKEN}
    return {'ok': True, 'result': result, 'model': model}


class ChromeSend(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    message: dict


@router.post('/browser-relay/send')
async def send_chrome(body: ChromeSend, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    sent = await chrome_for(_owner(body.user_id)).deliver_local(body.message)
    return {'ok': True, 'sent': bool(sent)}


class ChromeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    message: dict
    timeout: float = Field(ge=1, le=120)


@router.post('/browser-relay/request')
async def request_chrome(body: ChromeRequest, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    try:
        reply = await chrome_for(_owner(body.user_id)).request_local(body.message, body.timeout)
    except ChromeReadError as exc:
        text = str(exc)
        return {'ok': False, 'error': text if text and len(text) <= 200 else 'Chrome did not answer in time. Try again.'}
    return {'ok': True, 'reply': reply}


class ChromeRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    command: str = Field(max_length=80)
    payload: dict
    timeout: float = Field(ge=1, le=120)


@router.post('/browser-relay/read')
async def read_chrome(body: ChromeRead, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    try:
        reply = await chrome_for(_owner(body.user_id)).read_local(body.command, body.payload, body.timeout)
    except (ChromeReadError, ValueError) as exc:
        text = str(exc)
        return {'ok': False, 'error': text if text and len(text) <= 200 else 'Chrome did not answer in time. Try again.'}
    return {'ok': True, 'reply': reply}


class CloudMarketplaceExecute(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    marketplace: Literal['poshmark']
    command: dict
    photos: list[dict] = Field(default_factory=list)
    approved_photos: list[dict] = Field(default_factory=list)
    # Sales history uses a 20-minute deadline to page through older orders.
    timeout: float = Field(default=180.0, ge=5.0, le=1200.0)


@router.post('/cloud-marketplace/execute')
async def execute_cloud_marketplace(body: CloudMarketplaceExecute, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    from anteroom.services import cloud_marketplace
    from anteroom.services.cloud_sessions import CloudSessionDecryptForbidden
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    owner = _owner(body.user_id)
    with use(for_seller(owner)):
        try:
            result = await cloud_marketplace.execute_local(
                body.marketplace,
                body.command,
                photos=body.photos or None,
                approved_photos=body.approved_photos or None,
                timeout=body.timeout,
            )
        except CloudSessionDecryptForbidden:
            raise HTTPException(404, 'Not found') from None
    return {'ok': True, 'result': result}


class CloudMarketplaceCancel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    job_id: str = Field(max_length=128)


@router.post('/cloud-marketplace/cancel')
async def cancel_cloud_marketplace(body: CloudMarketplaceCancel, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    from anteroom.services import cloud_marketplace

    cancelled = await cloud_marketplace.cancel_local(_owner(body.user_id), body.job_id)
    return {'ok': True, 'cancelled': bool(cancelled)}

