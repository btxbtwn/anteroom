import asyncio
from unittest.mock import patch

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


def test_empty_relay_poll_allows_the_route_wait_but_not_excess_delay(caplog):
    from vendoo_studio.services.hosted_browser_relay import WAIT_S

    async def send(message):
        pass

    for method, path, status, duration, warns in [
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 0.079, False),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 4.999, False),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 5, True),
        ('GET', '/api/browser-relay/next', 204, WAIT_S + 6, True),
        ('GET', '/api/browser-relay/next', 503, 6, True),
        ('GET', '/api/browser-relay/next', 200, 6, True),
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
