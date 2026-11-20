"""Request admission and total receive deadlines must precede multipart parsing."""
import asyncio
from types import SimpleNamespace
from contextlib import AsyncExitStack

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request

from anteroom.routes import measurements, scout
from anteroom.services import hosted_limits, redaction
from anteroom.services.hosted_limits import UploadSlots, bounded_route_class
from anteroom.services.service_context import use


@pytest.mark.parametrize('router,path', [
    (measurements._hosted_upload_router, '/api/measurements/read'),
    (measurements._hosted_upload_router, '/api/size-tags/read'),
    (scout._upload_router, '/api/scout'),
])
def test_admission_rejects_before_receiving_multipart(router, path):
    async def run():
        app = FastAPI()
        app.include_router(router)
        app.state.upload_slots = slots = UploadSlots(total=1, wait=0.01)
        route = next(r for r in router.routes if getattr(r, 'path', None) == path and 'POST' in r.methods)

        async def receive():
            pytest.fail('multipart parsing ran before admission')

        request = Request({'type': 'http', 'method': 'POST', 'path': path, 'app': app,
                           'headers': [(b'content-type', b'multipart/form-data; boundary=x')],
                           'query_string': b''}, receive)
        with use(SimpleNamespace(seller_id='seller')):
            async with slots.hold('seller'):
                with pytest.raises(HTTPException) as error:
                    await route.get_route_handler()(request)
                assert error.value.status_code == 429
    asyncio.run(run())


def test_receive_deadline_is_total_releases_slot_and_stops_after_body(monkeypatch):
    monkeypatch.setattr(hosted_limits, 'BODY_RECEIVE_SECONDS', 0.04)
    deadlines = []
    original_timeout_at = asyncio.timeout_at

    def timeout_at(deadline):
        deadlines.append(deadline)
        return original_timeout_at(deadline)

    monkeypatch.setattr(asyncio, 'timeout_at', timeout_at)

    async def run():
        app = FastAPI()
        app.state.upload_slots = slots = UploadSlots(total=1, wait=0.01)
        router = APIRouter(route_class=bounded_route_class(100, use_upload_slots=True))

        @router.post('/body')
        async def body(request: Request):
            await request.body()
            # Work and disconnect reads after the body must not inherit its deadline.
            await asyncio.sleep(0.05)
            assert (await request.receive())['type'] == 'http.disconnect'
            return {'ok': True}

        app.include_router(router)
        route = next(r for r in router.routes if getattr(r, 'path', None) == '/body')
        reads = 0

        async def slow_receive():
            nonlocal reads
            reads += 1
            if reads == 1:
                return {'type': 'http.request', 'body': b'a', 'more_body': True}
            await asyncio.Event().wait()
            return {'type': 'http.request', 'body': b'a', 'more_body': True}

        scope = {'type': 'http', 'method': 'POST', 'path': '/body', 'app': app,
                 'headers': [], 'query_string': b'',
                 'fastapi_middleware_astack': AsyncExitStack(),
                 'fastapi_inner_astack': AsyncExitStack(),
                 'fastapi_function_astack': AsyncExitStack()}
        with use(SimpleNamespace(seller_id='seller')):
            with pytest.raises(HTTPException) as error:
                await route.get_route_handler()(Request(scope, slow_receive))
            assert error.value.status_code == 408
            assert reads == 2
            assert deadlines[-1] == deadlines[-2]
            assert slots._sellers == {}
            events = iter([{'type': 'http.request', 'body': b'ok', 'more_body': False},
                           {'type': 'http.disconnect'}])

            async def fast_receive():
                return next(events)

            response = await route.get_route_handler()(Request(scope, fast_receive))
            assert response.status_code == 200
            assert slots._sellers == {}
    asyncio.run(run())


@pytest.mark.parametrize('raw,expected', [
    ('{"Cookie": "sid=first\'second", "status": 500}', '{"Cookie": "…(masked)", "status": 500}'),
    ("{'Cookie': 'sid=first\"second', 'status': 500}", "{'Cookie': '…(masked)', 'status': 500}"),
    ('Cookie="sid=first"', 'Cookie="…(masked)"'),
    ('Cookie: sid=first, Authorization: Bearer token123', 'Cookie: …(masked)'),
    ('Cookie: sid=first&second\nstatus=500', 'Cookie: …(masked)\nstatus=500'),
    ('{"Cookie": "sid=first&second", "status": 500}', '{"Cookie": "…(masked)", "status": 500}'),
    ('postgresql://alice:first@second@db.example/app', 'postgresql://alice:…(masked)@db.example/app'),
    ('Cookie: sid=first&second, status=500', 'Cookie: …(masked)'),
    ('Cookie: sid=first,second}third', 'Cookie: …(masked)'),
    ('postgres://alice:pw@db.example?email=a@b', 'postgres://alice:…(masked)@db.example?email=a@b'),
    ('postgres://alice:pw@db.example/app', 'postgres://alice:…(masked)@db.example/app'),
])
def test_complete_credential_redaction(raw, expected):
    assert redaction.redact(raw) == expected
    assert redaction.redact(expected) == expected


@pytest.mark.parametrize('router,method,path,body', [
    (measurements.hosted_router, 'POST', '/api/conversations/item/measurements/read', {}),
    (scout.router, 'PATCH', '/api/scout/item', {'asking_price': 'invalid'}),
])
def test_non_upload_requests_do_not_wait_for_upload_slots(router, method, path, body):
    import httpx
    from anteroom.database import get_db

    async def run():
        app = FastAPI()
        app.include_router(router)
        app.state.upload_slots = slots = UploadSlots(total=1, wait=0.01)
        app.dependency_overrides[get_db] = lambda: None
        with use(SimpleNamespace(seller_id='seller')):
            async with slots.hold('seller'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    response = await client.request(method, path, json=body)
                # Body validation runs without waiting on the occupied upload slot.
                assert response.status_code == 422, response.text
    asyncio.run(run())
