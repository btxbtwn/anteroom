from types import SimpleNamespace

import pytest

from vendoo_studio.config import HOSTED_URL


class Worker:
    def __init__(self):
        self.paused = False
        self.closed = False
        self.sessions = []

    def session(self, token, owner):
        self.sessions.append((token, owner))

    def set_paused(self, paused):
        self.paused = paused

    def status(self):
        return {'connection': 'connected', 'paused': self.paused, 'active_calls': 0}

    def close(self):
        self.closed = True


@pytest.fixture
def connector():
    from vendoo_studio.services.connector import ConnectorAPI
    location = SimpleNamespace(url=HOSTED_URL + '/?connector')
    worker = Worker()
    api = ConnectorAPI(lambda: SimpleNamespace(get_current_url=lambda: location.url), worker=worker)
    yield api, worker, location
    api._shutdown()
    assert worker.closed


def test_connector_rejects_untrusted_pages_and_never_returns_tokens(connector, monkeypatch):
    api, worker, location = connector
    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in', lambda: False)
    monkeypatch.setattr('vendoo_studio.services.claude_auth.status', lambda: {'installed': False, 'signed_in': False})
    status = api.connector_status()
    assert status['chatgpt']['signed_in'] is False
    assert status['claude']['signed_in'] is False
    assert 'token' not in repr(status)
    location.url = HOSTED_URL + '.evil.test/?connector'
    with pytest.raises(PermissionError):
        api.connector_pause(True)
    with pytest.raises(PermissionError):
        api.connector_status()
    assert not worker.paused


def test_pause_preserves_the_account_and_resumes_the_same_worker(connector):
    api, worker, _location = connector
    owner = 'c7a5ee16-9850-49ba-b5bd-83b8be54df46'
    assert api.connect_chatgpt_worker('private-session', owner) == {'ok': True}
    assert api.connector_pause(True) == {'ok': True}
    assert worker.paused
    assert worker.sessions == [('private-session', owner)]
    assert api.connector_pause(False) == {'ok': True}
    assert not worker.paused
    assert api.connect_chatgpt_worker(None, None) == {'ok': True}
    assert worker.sessions[-1] == (None, None)


def test_subscription_models_use_existing_preferences_without_api_keys(connector):
    from vendoo_studio.services.keychain import get_chatgpt_models
    from vendoo_studio.services.user_settings import get_claude_models

    api, _worker, _location = connector
    assert api.connector_set_model('chatgpt', 'gpt-test') == {'ok': True}
    assert get_chatgpt_models()['listing_model'] == 'gpt-test'
    assert get_chatgpt_models()['vision_model'] == 'gpt-test'
    assert api.connector_set_model('claude', 'sonnet') == {'ok': True}
    assert get_claude_models()['listing_model'] == 'sonnet'
    with pytest.raises(ValueError):
        api.connector_set_model('openrouter', 'any')


def test_chatgpt_signin_runs_on_a_persistent_local_loop(connector, monkeypatch):
    import asyncio
    api, _worker, _location = connector
    loops = []

    async def start():
        loops.append(asyncio.get_running_loop())
        return {'authorize_url': 'https://auth.openai.com/authorize'}

    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.start_login', start)
    assert api.connector_chatgpt_login()['authorize_url'].startswith('https://auth.openai.com/')
    api.connector_chatgpt_login()
    assert loops[0] is loops[1]


def test_claude_terminal_is_scoped_to_one_signin_and_closed_on_exit(connector, monkeypatch):
    api, _worker, _location = connector
    terminals = []

    class Terminal:
        def __init__(self, cols, rows):
            self.closed = False
            self.inputs = []
            self.dimensions = (cols, rows)
            self.output = iter([b'Claude sign-in', None])
            terminals.append(self)

        async def read(self):
            return next(self.output)

        def write(self, data):
            self.inputs.append(data)

        def resize(self, cols, rows):
            self.dimensions = (cols, rows)

        async def close(self):
            self.closed = True
            return 0

    monkeypatch.setattr('vendoo_studio.services.claude_auth.LoginTerminal', Terminal)
    identity = api.connector_claude_start(80, 24)['id']
    api.connector_claude_send(identity, {'type': 'input', 'data': 'test-code\n'})
    api.connector_claude_send(identity, {'type': 'resize', 'cols': 60, 'rows': 20})
    assert terminals[0].inputs == [b'test-code\n']
    assert terminals[0].dimensions == (60, 20)
    with pytest.raises(ValueError):
        api.connector_claude_send(identity, {'type': 'run_shell', 'data': 'anything'})
    assert api.connector_claude_read(identity)['data'] == list(b'Claude sign-in')
    assert api.connector_claude_read(identity) == {'code': 0}
    assert terminals[0].closed
    with pytest.raises(ValueError):
        api.connector_claude_read(identity)
    another = api.connector_claude_start(80, 24)['id']
    assert another != identity
    api.connector_claude_close(identity)
    assert not terminals[1].closed
    api.connector_claude_close(another)
    assert terminals[1].closed
    with pytest.raises(ValueError):
        api.connector_claude_start(1000, 24)


def test_connector_signout_setup_and_model_catalog_use_existing_services(connector, monkeypatch):
    api, _worker, _location = connector
    events = []

    async def logout():
        events.append('logout')
        return True

    async def gpt_models():
        return ['gpt-model']

    async def claude_models():
        return [{'value': 'sonnet', 'label': 'Sonnet'}]

    monkeypatch.setattr('vendoo_studio.services.chatgpt_oauth.logout', logout)
    monkeypatch.setattr('vendoo_studio.providers.chatgpt_codex.fetch_chatgpt_models', gpt_models)
    monkeypatch.setattr('vendoo_studio.providers.claude_agent.list_models', claude_models)
    monkeypatch.setattr('webbrowser.open', lambda url: events.append(url))
    api.connector_chatgpt_logout()
    api.connector_setup_claude()
    assert events == ['logout', 'https://code.claude.com/docs/en/setup']
    assert api.connector_models('chatgpt') == [{'value': 'gpt-model', 'label': 'gpt-model'}]
    assert api.connector_models('claude') == [{'value': 'sonnet', 'label': 'Sonnet'}]
    with pytest.raises(ValueError):
        api.connector_models('openai')


def test_worker_pause_removes_subscriptions_from_advertisement_and_shutdown_cancels_poll(monkeypatch):
    import asyncio
    import httpx
    import json
    from vendoo_studio.providers.sandbox import SandboxProvider
    from vendoo_studio.services.mac_ai_worker import MacAIWorker

    async def providers():
        return {'chatgpt': SandboxProvider()}

    async def run():
        worker = MacAIWorker()
        worker._token, worker._owner = 'test-session', 'test-owner'
        worker.set_paused(True)
        resumed = asyncio.Event()
        calls = []

        async def handle(request):
            body = json.loads(request.content)
            calls.append(body)
            if len(calls) == 1:
                assert body['providers'] == {}
                assert body['available'] is False
                worker.set_paused(False)
                return httpx.Response(204)
            assert body['available'] is True
            assert 'chatgpt' in body['providers']
            resumed.set()
            await asyncio.Event().wait()

        original = httpx.AsyncClient
        monkeypatch.setattr('vendoo_studio.services.mac_ai_worker.subscription_providers', providers)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
        task = asyncio.create_task(worker._run())
        await asyncio.wait_for(resumed.wait(), 3)
        assert worker.status()['connection'] == 'connected'
        worker.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.status()['active_calls'] == 0

    asyncio.run(run())
