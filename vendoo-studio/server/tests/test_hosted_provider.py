import asyncio
import traceback
from dataclasses import replace
from unittest.mock import Mock

import pytest

from test_service_context import runtime
from vendoo_studio.services import keychain, listing_provider, service_context
from vendoo_studio.services.hosted_provider import HostedProvider, ProviderUnavailable, provider_for


def test_hosted_selection_never_reads_desktop_signins(monkeypatch):
    for name in ('chatgpt_signed_in', 'claude_signed_in'):
        monkeypatch.setattr(listing_provider, name, Mock(side_effect=AssertionError('Desktop sign-in')))
    alice = replace(runtime('alice'), provider_factory=provider_for)
    bob = replace(runtime('bob'), provider_factory=provider_for)
    with service_context.use(alice):
        assert listing_provider.get_listing_provider() is None
        assert not listing_provider.provider_is_configured()
        keychain.set_ai_key('openai', 'fake-alice-key')
        assert listing_provider.get_listing_provider().name == 'openai'
        assert listing_provider.provider_is_configured()
    with service_context.use(bob):
        assert listing_provider.get_listing_provider() is None


class FailingProvider:
    name, vision_model, listing_model = 'fake', 'vision', 'listing'
    api_key = 'fake-private-key'

    async def analyze_photos(self, *args, **kwargs):
        raise RuntimeError(self.api_key)

    async def chat(self, *args, **kwargs):
        yield 'first chunk'
        raise RuntimeError(self.api_key)


def test_stream_and_request_errors_do_not_expose_credentials():
    provider = HostedProvider(FailingProvider())
    assert not hasattr(provider, 'api_key')
    assert not hasattr(provider, 'list_models')

    async def run():
        with pytest.raises(ProviderUnavailable) as request_error:
            await provider.analyze_photos([])
        assert 'fake-private-key' not in ''.join(traceback.format_exception(request_error.value))
        chunks = []
        with pytest.raises(ProviderUnavailable) as stream_error:
            async for chunk in provider.chat([]):
                chunks.append(chunk)
        assert chunks == ['first chunk']
        assert 'fake-private-key' not in ''.join(traceback.format_exception(stream_error.value))
    asyncio.run(run())


def test_hosted_settings_http_uses_shared_handlers_without_local_signins(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services import hosted_context

    contexts = {owner: replace(runtime(owner), provider_factory=provider_for) for owner in ('alice', 'bob')}
    monkeypatch.setattr(hosted_context, 'for_seller', lambda owner, _token: contexts[owner])
    for name in ('chatgpt_signed_in', 'claude_signed_in'):
        monkeypatch.setattr(listing_provider, name, Mock(side_effect=AssertionError('Desktop sign-in')))
    with TestClient(create_app('https://project.supabase.co')) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda token: SimpleNamespace(user_id=token))
        alice, bob = {'Authorization': 'Bearer alice'}, {'Authorization': 'Bearer bob'}
        root = '/api/settings/ai-keys'
        assert client.get(root).status_code == 401
        assert client.put(root + '/openai', headers=alice, json={'api_key': 'fake-private-key'}).status_code == 200
        saved = client.get(root, headers=alice)
        assert 'fake-private-key' not in saved.text
        assert next(p for p in saved.json()['providers'] if p['id'] == 'openai')['masked_key'] == 'Configured'
        assert all(p['masked_key'] is None for p in client.get(root, headers=bob).json()['providers'])
        status = client.get('/api/settings/provider', headers=alice)
        assert status.status_code == 200, status.text
        assert status.json()['provider'] == 'openai'
        assert status.json()['configured'] is True
        assert not client.get('/api/settings/provider', headers=bob).json()['configured']
        # Rejected input is never reflected by FastAPI's validation response.
        bad = client.put(root + '/openai', headers=alice, json={'api_key': {'secret': 'rejected-private-key'}})
        assert bad.status_code == 422
        assert 'rejected-private-key' not in bad.text
        assert client.post(root + '/openrouter/sign-in', headers=alice).status_code == 404
        assert client.delete(root + '/openai', headers=alice).status_code == 200
        assert not client.get('/api/settings/provider', headers=alice).json()['configured']
