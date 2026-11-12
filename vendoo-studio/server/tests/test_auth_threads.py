"""Signing in a request must not queue behind database steps and photo copies.

Every ``asyncio.to_thread`` on the request loop shares one pool: database steps
waiting for a pooled connection (up to the pool timeout each), photo copies to
Storage, snapshot rebuilds. Session verification ran there too, so a busy pool
stalled authentication for every async request, the Mac's and Chrome's
heartbeats included. It has threads of its own.
"""
import asyncio
import threading

from fastapi.testclient import TestClient

from test_seller_sessions import PROJECT_URL, get_account, jwks, session_token, signing_keys  # noqa: F401
from vendoo_studio.hosted import create_app
from vendoo_studio.services.background_loop import LOOP_THREADS


def test_a_seller_signs_in_while_every_loop_thread_is_busy(jwks, signing_keys):  # noqa: F811
    app = create_app(PROJECT_URL)
    release = threading.Event()
    busy = []

    async def saturate():
        # Every thread of the request loop's pool, and a queue behind them.
        for _ in range(LOOP_THREADS + 5):
            busy.append(asyncio.ensure_future(asyncio.to_thread(release.wait, 10)))
        await asyncio.sleep(0.2)
        return {'busy': len(busy)}

    app.add_api_route('/internal/saturate', saturate)
    replies = []
    with TestClient(app) as client:
        try:
            assert client.get('/internal/saturate').json() == {'busy': LOOP_THREADS + 5}
            asking = threading.Thread(target=lambda: replies.append(get_account(client, session_token(signing_keys))))
            asking.start()
            asking.join(timeout=5)
            answered_while_busy = list(replies)
        finally:
            release.set()
        asking.join(timeout=30)
    assert [reply.status_code for reply in answered_while_busy] == [200]
