import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vendoo_studio.services.request_timing import RequestTimingMiddleware


def test_slow_response_logs_only_route_template(caplog):
    async def route(scope, receive, send):
        scope["route"] = type("Route", (), {"path": "/api/conversations/{id}/price-drop"})()
        await send({"type": "http.response.start", "status": 200})

    async def send(message):
        pass

    scope = {"type": "http", "method": "POST", "path": "/api/conversations/private/price-drop",
             "query_string": b"token=secret"}
    with patch("vendoo_studio.services.request_timing.time.perf_counter", side_effect=[0, 31]):
        asyncio.run(RequestTimingMiddleware(route)(scope, None, send))
    assert "POST /api/conversations/{id}/price-drop 200 31000ms" in caplog.text
    assert "private" not in caplog.text
    assert "secret" not in caplog.text


def test_fast_response_is_quiet(caplog):
    async def route(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})

    async def send(message):
        pass

    with patch("vendoo_studio.services.request_timing.time.perf_counter", side_effect=[0, 1]):
        asyncio.run(RequestTimingMiddleware(route)({"type": "http", "method": "GET"}, None, send))
    assert not caplog.records


def test_slow_save_reaches_persistent_logs(tmp_path, monkeypatch):
    from vendoo_studio.services import error_log, request_timing

    monkeypatch.setattr(error_log, "_disk_path", lambda: tmp_path / "app.jsonl")
    monkeypatch.setattr(request_timing, "SLOW_SECONDS", 0)
    error_log.install()
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.post("/api/conversations/{listing_id}/price-drop")
    def save(listing_id: str):
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/api/conversations/private/price-drop?token=secret").json() == {"ok": True}
    entries = error_log.query_entries(grep="Slow response:")
    assert len(entries) == 1
    assert "/api/conversations/{listing_id}/price-drop 200" in entries[0]["message"]
    assert "private" not in entries[0]["message"]
    assert "secret" not in entries[0]["message"]


def test_relay_poll_allows_the_route_wait_but_not_excess_delay(caplog):
    from vendoo_studio.services.hosted_browser_relay import WAIT_S

    async def send(message):
        pass

    for method, path, status, duration, warns in [
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 0.079, False),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 4.999, False),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 5, True),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 6, True),
        ('GET', '/api/browser-relay/next', 503, 6, True),
        ('GET', '/api/browser-relay/next', 200, 12.223, False),
        ('GET', '/api/browser-relay/next', 200, WAIT_S + 4.999, False),
        ('GET', '/api/browser-relay/next', 200, WAIT_S + 5, True),
        ('GET', '/api/browser-relay/next', 200, WAIT_S + 6, True),
        ('GET', '/api/browser-relay/next', 401, 6, True),
        ('GET', '/api/browser-relay/next', 409, 6, True),
        ('POST', '/api/browser-relay/next', 200, 6, True),
        ('GET', '/api/other', 200, 6, True),
        ('POST', '/api/browser-relay/next', 204, 6, True),
        ('GET', '/api/other', 204, 6, True),
        ('GET', '/api/browser-relay/next/other', 204, 6, True),
    ]:
        caplog.clear()

        async def route(scope, receive, send, path=path, status=status):
            scope['route'] = type('Route', (), {'path': path})()
            await send({'type': 'http.response.start', 'status': status})

        with patch('vendoo_studio.services.request_timing.time.perf_counter', side_effect=[0, duration]):
            asyncio.run(RequestTimingMiddleware(route)({'type': 'http', 'method': method}, None, send))
        assert bool(caplog.records) is warns, (method, path, status, duration)


@pytest.mark.parametrize("has_command", [True, False])
def test_poll_response_after_wait_keeps_its_shape_without_warning(monkeypatch, caplog, has_command):
    from vendoo_studio.routes import hosted_browser_relay as routes
    from vendoo_studio.services import hosted_browser_relay, request_timing

    relay = hosted_browser_relay.BrowserRelay()
    clock = [0.0]
    command = {"type": "marketplace.sessions", "job_id": None, "message_id": "a" * 32, "payload": {}}
    waited = []

    async def wait_for_command():
        # Exercise next_message's empty-outbox wait without a wall-clock sleep.
        waited.append(True)
        clock[0] = 12.223 if has_command else hosted_browser_relay.WAIT_S
        if has_command:
            assert await relay.deliver_local(command)
        else:
            raise TimeoutError

    monkeypatch.setattr(relay._ready, "wait", wait_for_command)
    monkeypatch.setattr(routes, "_pinned", lambda **kwargs: None)
    monkeypatch.setattr(routes, "_relay", lambda: relay)
    monkeypatch.setattr(request_timing, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get("/api/browser-relay/next?capabilities=marketplace.sessions")
    assert waited == [True]
    if has_command:
        assert response.status_code == 200
        assert response.json() == command
    else:
        assert response.status_code == 204
        assert response.content == b""
    assert not [record for record in caplog.records if record.name == request_timing.__name__]


def test_route_timing_fields_join_only_their_own_slow_line(caplog):
    from vendoo_studio.services import request_timing

    async def noted(scope, receive, send):
        scope["route"] = type("Route", (), {"path": "/api/conversations"})()
        request_timing.note(snapshot="inventory", builds=1, wait_ms=12)
        await send({"type": "http.response.start", "status": 200})

    async def plain(scope, receive, send):
        scope["route"] = type("Route", (), {"path": "/api/status"})()
        await send({"type": "http.response.start", "status": 200})

    async def send(message):
        pass

    async def run():
        await RequestTimingMiddleware(noted)({"type": "http", "method": "GET"}, None, send)
        await RequestTimingMiddleware(plain)({"type": "http", "method": "GET"}, None, send)

    with patch("vendoo_studio.services.request_timing.time.perf_counter", side_effect=[0, 16.463, 0, 6]):
        asyncio.run(run())
    first, second = [record.getMessage() for record in caplog.records]
    assert first == "Slow response: GET /api/conversations 200 16463ms snapshot=inventory builds=1 wait_ms=12"
    assert second == "Slow response: GET /api/status 200 6000ms"
    # Outside a request there is nowhere to attach fields, and nothing fails.
    request_timing.note(builds=1)
