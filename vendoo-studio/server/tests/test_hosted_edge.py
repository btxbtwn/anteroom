import asyncio
import logging
import uuid

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from test_seller_sessions import PROJECT_URL, jwks, session_token, signing_keys  # noqa: F401
from vendoo_studio.hosted import (
    HOSTED_CONTENT_SECURITY_POLICY,
    AccessLogMiddleware,
    ResponseHeadersMiddleware,
    access_log,
    create_app,
    http_log,
)
from vendoo_studio.services.mac_inference import relay_for
from vendoo_studio.services.seller_sessions import SessionVerifier


def _post_invoke(app, owner, extra_headers):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://internal') as client:
            return await client.post('/internal/mac-inference/invoke', headers={
                'Authorization': 'Bearer process-token', **extra_headers,
            }, json={'user_id': owner, 'operation': 'quick_chat', 'args': [1], 'kwargs': {}})
    return asyncio.run(run())


def test_internal_routes_refuse_requests_from_the_public_edge(monkeypatch):
    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    relay = relay_for(owner)
    relay.poll('a' * 32, {'vision_model': 'v', 'listing_model': 'l'}, timeout=0)
    # Fly's proxy adds Fly-Client-IP; even the right token does not get through.
    refused = _post_invoke(create_app(), owner, {'Fly-Client-IP': '203.0.113.9'})
    assert refused.status_code == 404
    assert relay.poll('a' * 32, {'vision_model': 'v', 'listing_model': 'l'}, timeout=0) is None


def test_internal_routes_still_answer_on_the_private_network(monkeypatch):
    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    # No Mac is polling, so the call reaches the route and reports it unavailable.
    monkeypatch.setattr('vendoo_studio.services.mac_inference.MacInference.invoke',
                        lambda *args, **kwargs: asyncio.sleep(0, {'answer': 7}))
    allowed = _post_invoke(create_app(), owner, {})
    assert allowed.status_code == 200
    assert allowed.json() == {'ok': True, 'result': {'answer': 7}}


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_access_log_names_the_route_but_never_the_url_query_or_headers(jwks, signing_keys):  # noqa: F811
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


class _SilentApp:
    """ASGI app that finishes an HTTP request without sending a response."""

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            await receive()


class _Errors(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


async def _get(app, path='/'):
    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'GET', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
        'query_string': b'', 'headers': [], 'client': ('127.0.0.1', 1), 'server': ('test', 80),
    }
    queue = asyncio.Queue()
    await queue.put({'type': 'http.request', 'body': b'', 'more_body': False})

    async def receive():
        return await queue.get()

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_base_http_middleware_turns_a_missing_response_into_runtime_error():
    """Pin the Starlette failure mode this PR removes from the hosted stack."""

    class Headers(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await call_next(request)

    stack = AccessLogMiddleware(Headers(GZipMiddleware(_SilentApp())))
    errors = _Errors()
    http_log.addHandler(errors)
    try:
        try:
            asyncio.run(_get(stack))
        except RuntimeError as exc:
            assert str(exc) == 'No response returned.'
        else:
            raise AssertionError('BaseHTTPMiddleware should raise when no response is sent')
    finally:
        http_log.removeHandler(errors)
    assert any(
        record.exc_info and record.exc_info[0] is RuntimeError
        and 'Unhandled exception in GET -' in record.getMessage()
        for record in errors.records
    )


def test_response_headers_middleware_does_not_raise_when_no_response_is_sent():
    """Client disconnect / empty inner completion must not alert as an unhandled GET."""
    stack = AccessLogMiddleware(ResponseHeadersMiddleware(GZipMiddleware(_SilentApp())))
    errors = _Errors()
    http_log.addHandler(errors)
    try:
        assert asyncio.run(_get(stack)) == []
    finally:
        http_log.removeHandler(errors)
    assert errors.records == []


def test_hosted_responses_keep_browser_security_headers():
    from fastapi.testclient import TestClient

    with TestClient(create_app(PROJECT_URL)) as client:
        response = client.get('/api/health')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['content-security-policy'] == HOSTED_CONTENT_SECURITY_POLICY
