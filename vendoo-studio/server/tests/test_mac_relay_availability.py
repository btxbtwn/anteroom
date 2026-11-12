"""Busy synchronous inventory requests cannot disconnect the Mac inference channel."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from anyio.to_thread import current_default_thread_limiter
from fastapi.testclient import TestClient

from vendoo_studio.hosted import create_app
from vendoo_studio.services.seller_sessions import SellerIdentity


def test_mac_heartbeat_status_and_reply_survive_a_full_request_thread_pool(monkeypatch):
    monkeypatch.delenv('ANTEROOM_BACKGROUND_WORKER', raising=False)
    owner = str(uuid.uuid4())
    verifier = SimpleNamespace(verify=lambda _token: SellerIdentity(user_id=owner))
    app = create_app('https://example.supabase.co', session_verifier=verifier)
    blocked, release = Event(), Event()

    @app.get('/api/test/blocked-inventory')
    def inventory():
        blocked.set()
        release.wait(timeout=10)
        return {'ok': True}

    async def pool_size(size=None):
        limiter = current_default_thread_limiter()
        previous = limiter.total_tokens
        if size is not None:
            limiter.total_tokens = size
        return previous

    headers = {'Authorization': 'Bearer test-session'}
    worker = 'a' * 32
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        previous = client.portal.call(pool_size, 1)
        busy = executor.submit(client.get, '/api/test/blocked-inventory', headers=headers)

        def mac_requests():
            heartbeat = client.post('/api/mac-ai/next', headers=headers, json={
                'worker': worker, 'providers': {'chatgpt': {'vision_model': 'vision', 'listing_model': 'text'}}, 'available': False})
            status = client.get('/api/mac-ai/status', headers=headers)
            reply = client.post('/api/mac-ai/reply', headers=headers, json={
                'worker': worker, 'identity': 'b' * 32, 'result': [], 'failed': False})
            return heartbeat, status, reply

        try:
            assert blocked.wait(2)
            heartbeat, status, reply = executor.submit(mac_requests).result(timeout=2)
            assert heartbeat.status_code == 204
            assert status.status_code == 200 and status.json()['connected']
            assert reply.status_code == 404  # No such call; the route still responds promptly.
            assert not busy.done()
        finally:
            release.set()
            client.portal.call(pool_size, previous)
            busy.result(timeout=2)
