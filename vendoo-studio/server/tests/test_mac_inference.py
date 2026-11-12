import asyncio
import base64
from types import SimpleNamespace

import pytest

from vendoo_studio.providers.sandbox import LISTING, SandboxProvider
from vendoo_studio.services.mac_ai_worker import MacAIAPI, execute
from vendoo_studio.services.mac_inference import MacSubscriptionProvider, MacInference, MacUnavailable, relay_for

MODELS = {'chatgpt': {'vision_model': 'local-vision', 'listing_model': 'local-listing'}}


def test_claude_subscription_is_routed_independently():
    from vendoo_studio.services.mac_inference import MacSubscriptionProvider

    relay = MacInference()
    models = {'claude': {'vision_model': 'claude-vision', 'listing_model': 'claude-listing'}}
    relay.poll('mac', models, timeout=0)
    assert relay.ready('claude')
    assert not relay.ready('chatgpt')

    async def run():
        provider = MacSubscriptionProvider(relay, 'claude')
        assert provider.listing_model == 'claude-listing'
        work = asyncio.create_task(provider.web_search([]))
        await asyncio.sleep(0)
        command = relay.poll('mac', models, timeout=0)
        assert command['provider'] == 'claude'
        relay.reply('mac', command['id'], {'answer': 'done'}, False)
        assert await work == {'answer': 'done'}

    asyncio.run(run())


def test_claude_quick_requests_use_the_real_providers_nonstream_chat(monkeypatch):
    from vendoo_studio.providers.claude_agent import ClaudeProvider
    from vendoo_studio.providers.xiaomi_mimo import StreamChunk

    async def answer(self, messages, **kwargs):
        yield StreamChunk('Claude reply', 'content')

    monkeypatch.setattr(ClaudeProvider, '_run', answer)
    local = ClaudeProvider()
    relay = MacInference()
    models = {'claude': {'vision_model': local.vision_model, 'listing_model': local.listing_model}}
    relay.poll('mac', models, timeout=0)

    async def run():
        provider = MacSubscriptionProvider(relay, 'claude')
        async def collect():
            return [item.text async for item in provider.quick_chat([{'role': 'user', 'content': 'hi'}])]
        for action in (collect, provider.test_connection):
            work = asyncio.create_task(action())
            await asyncio.sleep(0)
            command = relay.poll('mac', models, timeout=0)
            assert command['operation'] == 'chat'
            assert command['kwargs'] == {'stream': False}
            result = await execute(command, local)
            relay.reply('mac', command['id'], result, False)
            assert await work in (True, ['Claude reply'])

    asyncio.run(run())


def test_seller_calls_round_trip_through_the_mac_without_crossing_accounts():
    alice, bob = relay_for('test-alice'), relay_for('test-bob')
    alice.poll('alice-mac', MODELS, timeout=0)
    bob.poll('bob-mac', MODELS, timeout=0)

    async def run():
        async def collect():
            return [chunk async for chunk in MacSubscriptionProvider(alice, 'chatgpt').chat([{'role': 'user', 'content': 'Return JSON'}])]
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
        asyncio.run(relay.invoke('chat', [], {}, 'chatgpt'))
    relay.poll('mac', MODELS, timeout=0)
    with pytest.raises(MacUnavailable):
        relay.poll('other-mac', MODELS, timeout=0)

    async def run():
        work = asyncio.create_task(relay.invoke('chat', [], {}, 'chatgpt'))
        await asyncio.sleep(0)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert relay.poll('mac', MODELS, timeout=0) is None
        assert not relay.pending
    asyncio.run(run())


def test_more_than_sixteen_calls_can_overlap_without_crossing_accounts():
    relay = MacInference()
    relay.poll('mac', MODELS, timeout=0)

    async def run():
        calls = [asyncio.create_task(relay.invoke('quick_chat', [number], {}, 'chatgpt')) for number in range(24)]
        try:
            await asyncio.sleep(0)
            commands = [relay.poll('mac', MODELS, timeout=0) for _ in calls]
            assert all(commands)
            for command in reversed(commands):
                assert relay.reply('mac', command['id'], command['args'][0], False)
            assert await asyncio.gather(*calls) == list(range(24))
        finally:
            for call in calls:
                call.cancel()
            await asyncio.gather(*calls, return_exceptions=True)
        assert not relay.pending

    asyncio.run(run())


@pytest.mark.parametrize('cancel_first', [False, True])
@pytest.mark.parametrize('subscription', ['chatgpt', 'claude'])
def test_mac_worker_starts_a_second_call_while_the_first_model_is_waiting(monkeypatch, cancel_first, subscription):
    import json
    import httpx
    from vendoo_studio.services.mac_ai_worker import MacAIWorker

    async def run():
        first, second, release, cancelled = (asyncio.Event() for _ in range(4))
        replies = set()
        commands = iter(['first', 'second'])

        class Provider:
            vision_model = listing_model = 'test'

            async def quick_chat(self, messages):
                if messages == 'first':
                    first.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                yield messages

        async def handle(request):
            body = json.loads(request.content)
            if request.url.path.endswith('/next'):
                assert set(body['providers']) == {subscription}
            if body.get('available') is False:
                active = not (cancel_first and body['active_call'] == 'first')
                return httpx.Response(204, headers={'X-Anteroom-Call-Active': '1' if active else '0'})
            if request.url.path.endswith('/reply'):
                replies.add(body['identity'])
                if body['identity'] == 'second':
                    second.set()
                return httpx.Response(200, json={'ok': True})
            command = next(commands, None)
            if command:
                return httpx.Response(200, json={'id': command, 'provider': subscription, 'operation': 'quick_chat',
                                                 'args': [command], 'kwargs': {}})
            await asyncio.sleep(.01)
            return httpx.Response(204, headers={'X-Anteroom-Call-Active': '1'})

        original = httpx.AsyncClient
        monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', lambda: subscription == 'chatgpt')
        monkeypatch.setattr('vendoo_studio.services.claude_auth.claude_signed_in', lambda: subscription == 'claude')
        monkeypatch.setattr('vendoo_studio.providers.chatgpt_codex.ChatGPTCodexProvider', Provider)
        monkeypatch.setattr('vendoo_studio.providers.claude_agent.ClaudeProvider', Provider)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
        worker = MacAIWorker()
        worker._token, worker._owner = 'session', 'alice'
        task = asyncio.create_task(worker._run())
        try:
            await asyncio.wait_for(first.wait(), 2)
            await asyncio.wait_for(second.wait(), 2)
            assert replies == {'second'}
            if cancel_first:
                await asyncio.wait_for(cancelled.wait(), 5)
                assert replies == {'second'}
            else:
                release.set()
                for _ in range(100):
                    if len(replies) == 2:
                        break
                    await asyncio.sleep(.01)
                assert replies == {'first', 'second'}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
            return httpx.Response(200, json={'id': 'call', 'provider': 'chatgpt', 'operation': 'chat', 'args': [[{'role': 'user', 'content': 'hi'}]], 'kwargs': {}})
        await asyncio.sleep(.01)
        return httpx.Response(204)

    original = httpx.AsyncClient
    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', lambda: True)
    monkeypatch.setattr('vendoo_studio.services.claude_auth.claude_signed_in', lambda: False)
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
        body = {'worker': worker, 'providers': MODELS, 'available': False}
        assert client.post('/api/mac-ai/next', json=body).status_code == 401
        assert client.post('/api/mac-ai/next', headers=alice, json=body).status_code == 204
        assert client.get('/api/mac-ai/status', headers=alice).json()['connected']
        assert not client.get('/api/mac-ai/status', headers=bob).json()['connected']
        with ThreadPoolExecutor(max_workers=1) as executor:
            call = executor.submit(lambda: asyncio.run(connection.invoke('chat', [[{'role': 'user', 'content': 'hi'}]], {}, 'chatgpt')))
            with connection.condition:
                assert connection.condition.wait_for(lambda: bool(connection.outbox), 2)
            command = client.post('/api/mac-ai/next', headers=alice, json={**body, 'available': True}).json()
            result = asyncio.run(execute(command, SandboxProvider()))
            reply = {'worker': worker, 'identity': command['id'], 'result': result}
            assert client.post('/api/mac-ai/reply', headers=bob, json=reply).status_code == 404
            assert client.post('/api/mac-ai/reply', headers=alice, json=reply).status_code == 200
            assert call.result(timeout=2) == result
        # An older call's heartbeat cannot undo a newer subscription sign-out.
        assert client.post('/api/mac-ai/next', headers=alice, json={**body, 'providers': {}}).status_code == 204
        assert client.post('/api/mac-ai/next', headers=alice,
                           json={**body, 'active_call': command['id']}).status_code == 204
        assert client.get('/api/mac-ai/status', headers=alice).json()['providers'] == {}
        invalid = client.post('/api/mac-ai/next', headers=alice, json={
            **body, 'providers': {'mimo': MODELS['chatgpt']},
        })
        assert invalid.status_code == 422


def test_hosted_clients_see_each_subscription_and_signout_without_local_credentials(monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from test_service_context import runtime
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services.hosted_provider import provider_for

    owner = 'subscriptions-http-owner'
    context = replace(runtime(owner), provider_factory=provider_for)
    monkeypatch.setattr('vendoo_studio.services.hosted_context.for_seller', lambda _owner, _token: context)
    def forbidden():
        raise AssertionError('Hosted requests must not inspect local subscriptions')
    monkeypatch.setattr('vendoo_studio.services.claude_auth.claude_signed_in', forbidden)
    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', forbidden)
    relay = relay_for(owner)
    relay.poll('mac', {'claude': {'vision_model': 'claude-photo', 'listing_model': 'claude-listing'}}, timeout=0)
    headers = {'Authorization': 'Bearer account'}
    with TestClient(create_app('https://example.supabase.co')) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda _token: SimpleNamespace(user_id=owner))
        status = client.get('/api/settings/provider', headers=headers).json()
        assert status['claude']['signed_in'] is True
        assert status['chatgpt']['signed_in'] is False
        assert status['provider'] == 'claude'
        assert status['listing_model'] == 'claude-listing'
        models = client.get('/api/settings/claude/models', headers=headers).json()
        assert models['models'] == [{'value': 'claude-listing', 'label': 'claude-listing', 'efforts': []}]
        assert client.post('/api/settings/chatgpt/login', headers=headers).status_code == 404
        relay.poll('mac', {}, timeout=0)
        status = client.get('/api/settings/provider', headers=headers).json()
        assert not status['claude']['signed_in']
        assert not status['chatgpt']['signed_in']
        relay.poll('mac', MODELS, timeout=0)
        relay.seen -= 46
        status = client.get('/api/settings/provider', headers=headers).json()
        assert not status['chatgpt']['signed_in']


def test_two_sellers_can_read_and_save_settings_during_48_subscription_calls(monkeypatch):
    import time
    from dataclasses import replace
    from statistics import median, quantiles
    from fastapi.testclient import TestClient
    from test_service_context import runtime
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services.hosted_provider import provider_for

    owners = ('load-alice', 'load-bob')
    contexts = {owner: replace(runtime(owner), provider_factory=provider_for) for owner in owners}
    models = {name: {'vision_model': name, 'listing_model': name} for name in ('chatgpt', 'claude')}
    relays = {owner: relay_for(owner) for owner in owners}
    for owner, relay in relays.items():
        relay.poll(owner, models, timeout=0)
    monkeypatch.setattr('vendoo_studio.services.hosted_context.for_seller', lambda owner, _token: contexts[owner])
    with TestClient(create_app('https://example.supabase.co')) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda owner: SimpleNamespace(user_id=owner))

        def requests(owner):
            timings = {'read': [], 'write': []}
            headers = {'Authorization': 'Bearer ' + owner}
            for _ in range(10):
                started = time.perf_counter()
                assert client.get('/api/settings/provider', headers=headers).status_code == 200
                timings['read'].append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                assert client.put('/api/settings/provider/photos', headers=headers,
                                  json={'choice': 'claude' if owner == owners[0] else 'chatgpt'}).status_code == 200
                timings['write'].append((time.perf_counter() - started) * 1000)
            return timings

        async def run():
            calls = [asyncio.create_task(relays[owner].invoke('quick_chat', [owner, number], {},
                       'claude' if number % 2 else 'chatgpt')) for owner in owners for number in range(24)]
            try:
                await asyncio.sleep(0)
                measured = await asyncio.gather(*(asyncio.to_thread(requests, owner) for owner in owners))
                assert all(not call.done() for call in calls)
                assert contexts[owners[0]].settings.read()['photo_provider'] == 'claude'
                assert contexts[owners[1]].settings.read()['photo_provider'] == 'chatgpt'
                for owner, relay in relays.items():
                    for _ in range(24):
                        command = relay.poll(owner, models, timeout=0)
                        assert command['args'][0] == owner
                        relay.reply(owner, command['id'], command['args'], False)
                assert await asyncio.gather(*calls) == [[owner, number] for owner in owners for number in range(24)]
                for operation in ('read', 'write'):
                    samples = [value for result in measured for value in result[operation]]
                    print(f'{operation}: n={len(samples)}, p50={median(samples):.1f}ms, p95={quantiles(samples, n=20)[18]:.1f}ms; errors=0')
            finally:
                for call in calls:
                    call.cancel()
                await asyncio.gather(*calls, return_exceptions=True)

        asyncio.run(run())


@pytest.mark.parametrize(('error', 'kind'), [
    (RuntimeError('ChatGPT HTTP 429: You have hit your usage limit'), 'rate_limited'),
    (RuntimeError('Claude error: rate limit reached for your plan'), 'rate_limited'),
    (TimeoutError(), 'stalled'),
    (RuntimeError('Claude is signed out. Sign in with Claude in Settings.'), 'signed_out'),
    (RuntimeError('ChatGPT HTTP 401: token expired'), 'signed_out'),
    (RuntimeError('ChatGPT HTTP 400: bad schema'), 'rejected'),
    (RuntimeError('ChatGPT HTTP 503: upstream'), 'unavailable'),
    (RuntimeError('Claude returned an empty listing response'), 'failed'),
])
def test_the_mac_reports_why_a_call_failed_in_one_word(error, kind):
    from vendoo_studio.services.mac_ai_worker import error_kind
    assert error_kind(error) == kind


def test_a_failed_mac_call_names_the_provider_and_the_reason():
    from vendoo_studio.services.mac_inference import MacCallFailed

    relay = MacInference()
    relay.poll('mac', MODELS, timeout=0)

    async def run():
        work = asyncio.create_task(MacSubscriptionProvider(relay, 'chatgpt').web_search([]))
        await asyncio.sleep(0)
        command = relay.poll('mac', MODELS, timeout=0)
        assert relay.reply('mac', command['id'], None, True, 'rate_limited')
        with pytest.raises(MacCallFailed, match='ChatGPT on your computer hit its usage limit') as failure:
            await work
        assert failure.value.kind == 'rate_limited'
        assert 'keep the computer awake' not in str(failure.value).lower()

    asyncio.run(run())


def test_a_mac_from_before_error_kinds_still_reads_as_unavailable():
    # Removal note in services/mac_inference.py: drop with the fallback after 2027-02-01.
    relay = MacInference()
    relay.poll('mac', MODELS, timeout=0)

    async def run():
        work = asyncio.create_task(MacSubscriptionProvider(relay, 'chatgpt').web_search([]))
        await asyncio.sleep(0)
        command = relay.poll('mac', MODELS, timeout=0)
        assert relay.reply('mac', command['id'], None, True)
        with pytest.raises(MacUnavailable):
            await work

    asyncio.run(run())


def test_a_call_the_mac_took_and_never_answered_is_a_stall_not_an_offline_mac(monkeypatch):
    from vendoo_studio.services import mac_inference
    from vendoo_studio.services.provider_deadlines import ProviderStalled

    monkeypatch.setattr(mac_inference, 'INVOKE_DEADLINE_SEC', 0.05)
    relay = MacInference()
    relay.poll('mac', MODELS, timeout=0)

    async def run():
        work = asyncio.create_task(MacSubscriptionProvider(relay, 'chatgpt').web_search([]))
        await asyncio.sleep(0)
        assert relay.poll('mac', MODELS, timeout=0) is not None
        with pytest.raises(ProviderStalled, match='ChatGPT on your computer stopped responding while searching the web'):
            await work
        # A call the Mac never picked up still says to open the Mac.
        with pytest.raises(MacUnavailable):
            await MacSubscriptionProvider(relay, 'chatgpt').web_search([])

    asyncio.run(run())


def test_mac_calls_wait_as_long_as_the_provider_deadline():
    from vendoo_studio.services.mac_inference import INVOKE_DEADLINE_SEC
    from vendoo_studio.services.provider_deadlines import CALL_DEADLINE_SEC
    assert INVOKE_DEADLINE_SEC > CALL_DEADLINE_SEC


def test_mac_worker_sends_the_failure_kind(monkeypatch):
    import httpx
    from vendoo_studio.services.mac_ai_worker import MacAIWorker

    worker = MacAIWorker()
    worker._token = 'account-session'
    replies = []

    class Finished(Exception):
        pass

    class LimitedProvider(SandboxProvider):
        async def web_search(self, messages):
            raise RuntimeError('ChatGPT HTTP 429: secret account detail')

    async def handle(request):
        import json
        body = json.loads(request.content)
        if request.url.path.endswith('/reply'):
            replies.append(body)
            raise Finished()
        if not replies and 'active_call' not in body:
            return httpx.Response(200, json={'id': 'c' * 32, 'provider': 'chatgpt', 'operation': 'web_search', 'args': [[]], 'kwargs': {}})
        await asyncio.sleep(.01)
        return httpx.Response(204)

    original = httpx.AsyncClient
    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', lambda: True)
    monkeypatch.setattr('vendoo_studio.services.claude_auth.claude_signed_in', lambda: False)
    monkeypatch.setattr('vendoo_studio.providers.chatgpt_codex.ChatGPTCodexProvider', LimitedProvider)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(Finished):
        asyncio.run(asyncio.wait_for(worker._run(), 5))
    assert replies[0]['failed'] is True
    assert replies[0]['error'] == 'rate_limited'
    assert 'secret' not in str(replies[0])


def test_account_window_signs_this_mac_into_chatgpt_through_the_local_server(monkeypatch):
    import httpx
    from vendoo_studio.config import HOSTED_URL, PORT

    url = HOSTED_URL + '/'
    bridge = MacAIAPI(lambda: SimpleNamespace(get_current_url=lambda: url))
    calls = []
    pending = {'authorize_url': 'https://auth.openai.com/authorize?state=x'}

    def request(method, target, timeout):
        calls.append((method, target))
        if method == 'POST':
            return httpx.Response(200, json=pending)
        if method == 'DELETE':
            return httpx.Response(200, json={'ok': True})
        return httpx.Response(200, json={'chatgpt': {'signed_in': False, 'pending': pending, 'error': None}})

    monkeypatch.setattr(httpx, 'request', request)
    assert bridge.chatgpt_sign_in() == {'ok': True}
    assert bridge.chatgpt_sign_in_status() == {'ok': True, 'signed_in': False, 'pending': True, 'error': None}
    assert bridge.chatgpt_sign_in_cancel() == {'ok': True}
    base = f'http://127.0.0.1:{PORT}/api/settings'
    assert calls == [('POST', base + '/chatgpt/login'), ('GET', base + '/provider'), ('DELETE', base + '/chatgpt/login')]

    url = HOSTED_URL + '.evil.test'
    calls.clear()
    assert bridge.chatgpt_sign_in() == {'ok': False}
    assert bridge.chatgpt_sign_in_status() == {'ok': False}
    assert bridge.chatgpt_sign_in_cancel() == {'ok': False}
    assert calls == []


def test_account_window_sees_why_chatgpt_sign_in_could_not_start(monkeypatch):
    import httpx
    from vendoo_studio.config import HOSTED_URL

    bridge = MacAIAPI(lambda: SimpleNamespace(get_current_url=lambda: HOSTED_URL + '/'))
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(400, json={'detail': 'ChatGPT sign-in is unavailable.'}))
    assert bridge.chatgpt_sign_in() == {'ok': False, 'error': 'ChatGPT sign-in is unavailable.'}

    def offline(*a, **k):
        raise httpx.ConnectError('refused')
    monkeypatch.setattr(httpx, 'request', offline)
    assert bridge.chatgpt_sign_in() == {'ok': False, 'error': 'Anteroom on this computer did not answer. Quit and reopen Anteroom, then try again.'}


def test_relayed_calls_are_recorded_with_their_model_time_and_outcome():
    """Generation timings list every model call; subscription calls on the Mac were missing."""
    from vendoo_studio.services import model_usage
    from vendoo_studio.services.mac_inference import MacCallFailed

    relay = MacInference()
    relay.poll('mac', MODELS, timeout=0)
    provider = MacSubscriptionProvider(relay, 'chatgpt')

    async def answer(work, result=None, error=None):
        await asyncio.sleep(0)
        command = relay.poll('mac', MODELS, timeout=0)
        relay.reply('mac', command['id'], result, error is not None, error)
        return await work

    async def run():
        with model_usage.capture() as calls:
            chunks = await answer(asyncio.create_task(
                _collect(provider.chat([{'role': 'user', 'content': 'hi'}]))), [{'text': 'ok', 'kind': 'content'}])
            assert [chunk.text for chunk in chunks] == ['ok']
            await answer(asyncio.create_task(_collect(provider.vision_chat([]))), [])
            with pytest.raises(MacCallFailed):
                await answer(asyncio.create_task(provider.web_search([])), error='rate_limited')
        return calls

    calls = asyncio.run(run())
    assert [(call['provider'], call['model'], call['outcome']) for call in calls] == [
        ('chatgpt', 'local-listing', 'done'), ('chatgpt', 'local-vision', 'done'), ('chatgpt', 'local-listing', 'failed')]
    assert all(call['latency_ms'] is not None for call in calls)


async def _collect(stream):
    return [chunk async for chunk in stream]
