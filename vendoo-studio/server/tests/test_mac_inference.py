import asyncio
import base64
from types import SimpleNamespace

import pytest

from vendoo_studio.providers.sandbox import LISTING, SandboxProvider
from vendoo_studio.services.mac_ai_worker import MacAIAPI, execute
from vendoo_studio.services.mac_inference import MacChatGPTProvider, MacInference, MacUnavailable, relay_for

MODELS = {'vision_model': 'local-vision', 'listing_model': 'local-listing'}


def test_seller_calls_round_trip_through_the_mac_without_crossing_accounts():
    alice, bob = relay_for('test-alice'), relay_for('test-bob')
    alice.poll('alice-mac', MODELS, timeout=0)
    bob.poll('bob-mac', MODELS, timeout=0)

    async def run():
        async def collect():
            return [chunk async for chunk in MacChatGPTProvider(alice).chat([{'role': 'user', 'content': 'Return JSON'}])]
        work = asyncio.create_task(collect())
        await asyncio.sleep(0)
        assert bob.poll('bob-mac', MODELS, timeout=0) is None
        command = alice.poll('alice-mac', MODELS, timeout=0)
        result = await execute(command, SandboxProvider())
        assert not bob.reply('bob-mac', command['id'], result, False)
        assert not alice.reply('another-mac', command['id'], result, False)
        assert alice.reply('alice-mac', command['id'], result, False)
        assert LISTING['title'] in ''.join(chunk.text for chunk in await work)
        assert not alice.reply('alice-mac', command['id'], result, False)
    asyncio.run(run())


def test_offline_mac_is_refused_and_inflight_calls_are_not_replayed():
    relay = MacInference()
    with pytest.raises(MacUnavailable):
        asyncio.run(relay.invoke('chat', [], {}))
    relay.poll('mac', MODELS, timeout=0)
    with pytest.raises(MacUnavailable):
        relay.poll('other-mac', MODELS, timeout=0)

    async def run():
        work = asyncio.create_task(relay.invoke('chat', [], {}))
        await asyncio.sleep(0)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert relay.poll('mac', MODELS, timeout=0) is None
        assert not relay.pending
    asyncio.run(run())


def test_photo_paths_stay_in_a_temporary_mac_directory():
    paths = []
    class Reader:
        async def analyze_photos(self, images):
            from pathlib import Path
            paths.extend(Path(image) for image in images)
            assert paths[0].read_bytes() == b'photo'
            return {'evidence': 'read'}
    command = {'operation': 'analyze_photos', 'args': [['data:image/jpeg;base64,' + base64.b64encode(b'photo').decode()]]}
    assert asyncio.run(execute(command, Reader())) == {'evidence': 'read'}
    assert not paths[0].exists()
    with pytest.raises(ValueError, match='Unsupported'):
        asyncio.run(execute({'operation': 'run_shell'}, Reader()))


def test_only_the_trusted_account_window_can_connect_the_native_worker(monkeypatch):
    from vendoo_studio.config import HOSTED_URL
    url = HOSTED_URL + '.evil.test'
    bridge = MacAIAPI(lambda: SimpleNamespace(get_current_url=lambda: url))
    received = []
    monkeypatch.setattr(bridge._worker, 'session', lambda token, owner: received.append((token, owner)))
    assert bridge.connect_chatgpt_worker('test-session') == {'ok': False}
    assert received == []
    url = HOSTED_URL + '/'
    owner = 'c7a5ee16-9850-49ba-b5bd-83b8be54df46'
    assert bridge.connect_chatgpt_worker('test-session', owner) == {'ok': True}
    assert received == [('test-session', owner)]


def test_mac_worker_uses_the_outbound_account_channel(monkeypatch):
    import httpx
    from vendoo_studio.services.mac_ai_worker import MacAIWorker

    worker = MacAIWorker()
    worker._token = 'account-session'
    calls = []
    class Finished(Exception):
        pass

    async def handle(request):
        import json
        assert request.headers['Authorization'] == 'Bearer account-session'
        body = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path.endswith('/reply'):
            assert body['result'] == [{'text': 'Sandbox reply: no model was called.', 'kind': 'content'}]
            assert body['failed'] is False
            raise Finished()
        if len(calls) == 1:
            return httpx.Response(200, json={'id': 'call', 'operation': 'chat', 'args': [[{'role': 'user', 'content': 'hi'}]], 'kwargs': {}})
        await asyncio.sleep(.01)
        return httpx.Response(204)

    original = httpx.AsyncClient
    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', lambda: True)
    monkeypatch.setattr('vendoo_studio.providers.chatgpt_codex.ChatGPTCodexProvider', SandboxProvider)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    # Stop the polling loop once its answer task has reached the mock server.
    with pytest.raises(Finished):
        asyncio.run(asyncio.wait_for(worker._run(), 5))
    assert any(path.endswith('/reply') for path in calls)


def test_http_worker_channel_is_authenticated_and_account_scoped(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from test_service_context import runtime
    from vendoo_studio.hosted import create_app

    contexts = {owner: runtime(owner) for owner in ('http-alice', 'http-bob')}
    monkeypatch.setattr('vendoo_studio.services.hosted_context.for_seller', lambda owner, _token: contexts[owner])
    alice = {'Authorization': 'Bearer http-alice'}
    bob = {'Authorization': 'Bearer http-bob'}
    worker = 'a' * 32
    connection = relay_for('http-alice')
    with TestClient(create_app('https://example.supabase.co')) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda token: SimpleNamespace(user_id=token))
        body = {'worker': worker, **MODELS, 'available': False}
        assert client.post('/api/mac-ai/next', json=body).status_code == 401
        assert client.post('/api/mac-ai/next', headers=alice, json=body).status_code == 204
        assert client.get('/api/mac-ai/status', headers=alice).json()['connected']
        assert not client.get('/api/mac-ai/status', headers=bob).json()['connected']
        with ThreadPoolExecutor(max_workers=1) as executor:
            call = executor.submit(lambda: asyncio.run(connection.invoke('chat', [[{'role': 'user', 'content': 'hi'}]], {})))
            with connection.condition:
                assert connection.condition.wait_for(lambda: bool(connection.outbox), 2)
            command = client.post('/api/mac-ai/next', headers=alice, json={**body, 'available': True}).json()
            result = asyncio.run(execute(command, SandboxProvider()))
            reply = {'worker': worker, 'identity': command['id'], 'result': result}
            assert client.post('/api/mac-ai/reply', headers=bob, json=reply).status_code == 404
            assert client.post('/api/mac-ai/reply', headers=alice, json=reply).status_code == 200
            assert call.result(timeout=2) == result
