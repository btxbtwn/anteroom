"""The seller's Anteroom tab relays marketplace commands to their Chrome and posts back replies."""
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from vendoo_studio.routes import jobs
from vendoo_studio.services.hosted_browser_relay import relay_for
from vendoo_studio.services.service_context import current

router = APIRouter(prefix='/api/browser-relay')
WAIT_S = 20.0


def _relay():
    context = current()
    if context is None:
        raise HTTPException(404, 'Not found')
    return relay_for(context.seller_id)


@router.get('/next')
async def next_command(capabilities: str = Query('', max_length=200)):
    """The next command for this seller's Chrome. Waits up to 20 seconds; 204 when there is none."""
    message = await _relay().next_message({item for item in capabilities.split(',') if item}, WAIT_S)
    return message if message is not None else Response(status_code=204)


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    payload: dict


@router.post('/replies')
async def post_reply(body: Reply):
    """Chrome's answer to one command this seller's relay sent. Resolved on the event
    loop that awaits it, so the waiting action wakes at once."""
    relay = _relay()
    if not relay.is_waiting(body.request_id):
        raise HTTPException(404, 'Nothing is waiting for this reply.')
    relay.resolve_wait(body.request_id, body.payload)
    return {'ok': True}


# The Bridge uploads a job's approved photos, read here with the seller's session.
router.add_api_route('/jobs/{job_id}/photos/{photo_id}', jobs.serve_job_photo, methods=['GET'])
