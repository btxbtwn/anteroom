"""Process-to-process Mac and Chrome calls. Browsers do not call these routes."""
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from vendoo_studio.services.device_routes import token_matches
from vendoo_studio.services.hosted_browser_relay import ChromeReadError, relay_for as chrome_for
from vendoo_studio.services.mac_inference import NO_REPLY, NOT_TAKEN, OPERATIONS, MacCallFailed, MacUnavailable, relay_for as mac_for
from vendoo_studio.services.provider_deadlines import ProviderStalled

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
        result = await mac_for(_owner(body.user_id)).invoke(body.operation, body.args, body.kwargs, body.provider)
    except MacCallFailed as failure:
        return {'ok': False, 'error': failure.kind}
    except ProviderStalled:
        return {'ok': False, 'error': NO_REPLY}
    except MacUnavailable:
        return {'ok': False, 'error': NOT_TAKEN}
    return {'ok': True, 'result': result}


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
