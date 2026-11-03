"""The signed-in Mac polls outbound; ChatGPT credentials never leave that Mac."""
import asyncio

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services.device_routes import claim, lookup, replay_instance
from vendoo_studio.services.mac_inference import MacUnavailable, relay_for
from vendoo_studio.services.service_context import current

router = APIRouter(prefix='/api/mac-ai')


def relay():
    context = current()
    if context is None:
        raise HTTPException(404, 'Not found')
    return relay_for(context.seller_id)


def _pinned(device: str, *, claim_route: bool):
    context = current()
    if context is None:
        return None
    try:
        route = claim(context.seller_id, device) if claim_route else lookup(context.seller_id, device)
    except SQLAlchemyError:
        return None
    instance = replay_instance(route)
    if instance is None:
        return None
    return Response(status_code=409, headers={'fly-replay': f'instance={instance}'})


class Poll(BaseModel):
    model_config = ConfigDict(extra='forbid')
    worker: str = Field(pattern=r'^[0-9a-f]{32}$')
    vision_model: str = Field(min_length=1, max_length=100)
    listing_model: str = Field(min_length=1, max_length=100)
    available: bool = True
    active_call: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


@router.post('/next')
async def next_call(body: Poll):
    if pinned := _pinned('mac', claim_route=True):
        return pinned
    deadline = asyncio.get_running_loop().time() + 20
    connection = relay()
    while True:
        try:
            command = connection.poll(body.worker, {'vision_model': body.vision_model, 'listing_model': body.listing_model}, body.available, timeout=0)
        except MacUnavailable:
            raise HTTPException(409, 'Another Mac is already connected to this account') from None
        if command is not None:
            return command
        if not body.available or asyncio.get_running_loop().time() >= deadline:
            break
        # Long polls must not occupy the request thread pool needed by replies.
        await asyncio.sleep(.25)
    with connection.condition:
        active = body.active_call in connection.pending
    return Response(status_code=204, headers={'X-Anteroom-Call-Active': '1' if active else '0'})


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    worker: str = Field(pattern=r'^[0-9a-f]{32}$')
    identity: str = Field(pattern=r'^[0-9a-f]{32}$')
    result: dict | list | None = None
    failed: bool = False


@router.post('/reply')
async def reply(body: Reply):
    if pinned := _pinned('mac', claim_route=False):
        return pinned
    if not relay().reply(body.worker, body.identity, body.result, body.failed):
        raise HTTPException(404, 'This inference call is no longer active')
    return {'ok': True}


@router.get('/status')
async def status():
    if pinned := _pinned('mac', claim_route=False):
        return pinned
    connection = relay()
    return {'connected': connection.connected, **connection.models}
