import asyncio
import logging
import uuid

import httpx

from test_seller_sessions import PROJECT_URL, jwks, session_token, signing_keys  # noqa: F401
from vendoo_studio.hosted import access_log, create_app
from vendoo_studio.services.mac_inference import relay_for
from vendoo_studio.services.seller_sessions import SessionVerifier


def _post_invoke(app, owner, extra_headers):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://internal') as client:
            return await client.post('/internal/mac-inference/invoke', headers={
                'Authorization': 'Bearer process-token', **extra_headers,
            }, json={'user_id': owner, 'provider': 'chatgpt', 'operation': 'quick_chat', 'args': [1], 'kwargs': {}})
    return asyncio.run(run())


def test_internal_routes_refuse_requests_from_the_public_edge(monkeypatch):
    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    relay = relay_for(owner)
    relay.poll('a' * 32, {'chatgpt': {'vision_model': 'v', 'listing_model': 'l'}}, timeout=0)
    # Fly's proxy adds Fly-Client-IP; even the right token does not get through.
    refused = _post_invoke(create_app(), owner, {'Fly-Client-IP': '203.0.113.9'})
    assert refused.status_code == 404
    assert relay.poll('a' * 32, {'chatgpt': {'vision_model': 'v', 'listing_model': 'l'}}, timeout=0) is None


def test_internal_routes_still_answer_on_the_private_network(monkeypatch):
    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    # No Mac is polling, so the call reaches the route and reports it unavailable.
    monkeypatch.setattr('vendoo_studio.services.mac_inference.MacInference.call',
                        lambda *args, **kwargs: asyncio.sleep(0, ({'answer': 7}, 'mac-listing')))
    allowed = _post_invoke(create_app(), owner, {})
    assert allowed.status_code == 200
    assert allowed.json() == {'ok': True, 'result': {'answer': 7}, 'model': 'mac-listing'}


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_access_log_names_the_route_but_never_the_url_query_or_headers(jwks, signing_keys, request):  # noqa: F811
    # As in a fresh process: a handler attached before the first request must
    # still receive the INFO lines, whichever tests ran earlier.
    request.addfinalizer(lambda level=access_log.level: access_log.setLevel(level))
    access_log.setLevel(logging.NOTSET)
    app = create_app()
    app.state.session_verifier = SessionVerifier(PROJECT_URL)
    lines = Lines()
    access_log.addHandler(lines)
    draft = str(uuid.uuid4())
    token = session_token(signing_keys)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://hosted') as client:
            await client.get('/api/health?code=one-time-secret')
            await client.get(f'/api/conversations/{draft}/screen?code=one-time-secret',
                             headers={'Authorization': 'Bearer ' + token})
            await client.get('/api/drafts?code=one-time-secret')
    try:
        asyncio.run(run())
    finally:
        access_log.removeHandler(lines)
    assert lines.lines[0].startswith('GET /api/health 200 ')
    assert lines.lines[0].endswith('ms')
    assert lines.lines[1].startswith('GET /api/conversations/{identity}/screen ')
    assert lines.lines[2].startswith('GET - 401 ')
    logged = '\n'.join(lines.lines)
    for private in ('one-time-secret', 'code=', draft, token, 'Bearer'):
        assert private not in logged
