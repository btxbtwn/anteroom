import asyncio
import traceback
from dataclasses import replace
from unittest.mock import Mock

import pytest

from test_service_context import runtime
from anteroom.services import keychain, listing_provider, service_context
from anteroom.services.hosted_provider import HostedProvider, ProviderUnavailable, provider_for


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


@pytest.mark.parametrize('subscription', ['chatgpt', 'claude'])
def test_selected_offline_subscription_does_not_charge_an_api_key_fallback(subscription):
    from anteroom.services.mac_inference import MacUnavailable
    from anteroom.services.user_settings import set_listing_provider_order

    seller = replace(runtime('offline-' + subscription), provider_factory=provider_for)
    with service_context.use(seller):
        keychain.set_ai_key('openai', 'fake-fallback-key')
        set_listing_provider_order(primary=subscription, fallback='openai')
        provider = listing_provider.get_listing_provider()
        assert provider.name == subscription
        with pytest.raises(MacUnavailable):
            asyncio.run(provider.test_connection())


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


def test_hosted_provider_preserves_signature_without_exposing_wrapped():
    import inspect

    class SearchProvider:
        name, vision_model, listing_model = 'openrouter', 'v', 'l'
        api_key = 'fake-private-key'

        async def web_search(self, messages, *, max_calls=None, allowed_domains=None):
            return {'answer': '', 'sources': []}

    hosted = HostedProvider(SearchProvider())
    search = hosted.web_search
    assert not hasattr(search, '__wrapped__')
    assert set(inspect.signature(search).parameters) == {'messages', 'max_calls', 'allowed_domains'}


def test_hosted_settings_http_uses_shared_handlers_without_local_signins(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from anteroom.hosted import create_app
    from anteroom.services import hosted_context

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


def test_managed_openrouter_pins_gemini_flash_for_hosted_accounts(monkeypatch):
    from anteroom.services.hosted_provider import (
        DEFAULT_MANAGED_MODEL,
        managed_ai_enabled,
        managed_model,
        managed_reasoning,
    )
    from anteroom.services.user_settings import set_listing_provider_order, set_photo_provider_choice

    monkeypatch.setenv('ANTEROOM_MANAGED_OPENROUTER_KEY', 'sk-or-managed-secret')
    monkeypatch.delenv('ANTEROOM_MANAGED_MODEL', raising=False)
    monkeypatch.delenv('ANTEROOM_MANAGED_REASONING', raising=False)
    for name in ('chatgpt_signed_in', 'claude_signed_in'):
        monkeypatch.setattr(listing_provider, name, Mock(side_effect=AssertionError('Desktop sign-in')))

    assert managed_ai_enabled() is True
    assert managed_model() == DEFAULT_MANAGED_MODEL
    assert managed_reasoning() is None

    seller = replace(runtime('managed-seller'), provider_factory=provider_for)
    with service_context.use(seller):
        # Even if an account previously preferred ChatGPT or a Vault key, managed AI overrides it.
        keychain.set_ai_key('openai', 'fake-seller-openai-key')
        set_listing_provider_order(primary='chatgpt', fallback='openai')
        set_photo_provider_choice('openai')

        assert listing_provider.provider_is_configured() is True
        listing = listing_provider.get_listing_provider()
        photo = listing_provider.get_photo_provider()
        assert listing is not None and photo is not None
        assert listing.name == 'openrouter'
        assert photo.name == 'openrouter'
        assert listing.vision_model == DEFAULT_MANAGED_MODEL
        assert listing.listing_model == DEFAULT_MANAGED_MODEL
        assert hasattr(listing, 'web_search')
        assert not hasattr(listing, 'api_key')

    monkeypatch.setenv('ANTEROOM_MANAGED_MODEL', 'google/gemini-3.8-flash-custom')
    monkeypatch.setenv('ANTEROOM_MANAGED_REASONING', 'low')
    assert managed_model() == 'google/gemini-3.8-flash-custom'
    assert managed_reasoning() == 'low'
    with service_context.use(seller):
        custom = listing_provider.get_listing_provider()
        assert custom is not None
        assert custom.vision_model == 'google/gemini-3.8-flash-custom'
        assert custom.listing_model == 'google/gemini-3.8-flash-custom'

    monkeypatch.setenv('ANTEROOM_MANAGED_REASONING', 'invalid-level')
    assert managed_reasoning() is None


def test_desktop_providers_unaffected_when_managed_key_is_set(monkeypatch):
    from anteroom.routes import ai_keys

    monkeypatch.setenv('ANTEROOM_MANAGED_OPENROUTER_KEY', 'sk-or-managed-secret')
    assert service_context.current() is None
    monkeypatch.setattr(listing_provider, 'chatgpt_signed_in', lambda: False)
    monkeypatch.setattr(listing_provider, 'claude_signed_in', lambda: False)
    monkeypatch.setattr(listing_provider, 'get_api_key', lambda: None)
    monkeypatch.setattr(listing_provider, 'get_cursor_api_key', lambda: None)
    monkeypatch.setattr(listing_provider, 'get_ai_keys', lambda: {})
    assert listing_provider.provider_is_configured() is False
    # Desktop AI-keys listing still returns the catalog instead of managed-AI empty list.
    monkeypatch.setattr(keychain, 'get_ai_keys', lambda: {})
    assert len(ai_keys.list_ai_keys()['providers']) > 0


def test_hosted_managed_ai_http_routes_and_runtime(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from anteroom.hosted import create_app
    from anteroom.services import category_tree, hosted_context
    from anteroom.services.hosted_database import get_seller_db
    from anteroom.services.hosted_provider import DEFAULT_MANAGED_MODEL

    monkeypatch.setenv('ANTEROOM_MANAGED_OPENROUTER_KEY', 'sk-or-managed-secret')
    monkeypatch.delenv('ANTEROOM_MANAGED_MODEL', raising=False)
    monkeypatch.setattr(category_tree, 'categories_ready', lambda _db: False)
    contexts = {'alice': replace(runtime('alice'), provider_factory=provider_for)}
    monkeypatch.setattr(hosted_context, 'for_seller', lambda owner, _token: contexts[owner])
    for name in ('chatgpt_signed_in', 'claude_signed_in'):
        monkeypatch.setattr(listing_provider, name, Mock(side_effect=AssertionError('Desktop sign-in')))

    app = create_app('https://project.supabase.co')
    app.dependency_overrides[get_seller_db] = lambda: None
    with TestClient(app) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda token: SimpleNamespace(user_id=token))
        alice = {'Authorization': 'Bearer alice'}

        rt = client.get('/api/runtime', headers=alice)
        assert rt.status_code == 200, rt.text
        assert 'managed_ai' in rt.json()['features']

        status = client.get('/api/settings/provider', headers=alice)
        assert status.status_code == 200, status.text
        body = status.json()
        assert body['provider'] == 'openrouter'
        assert body['primary'] == 'openrouter'
        assert body['configured'] is True
        assert body['masked_key'] is None
        assert body['vision_model'] == DEFAULT_MANAGED_MODEL
        assert body['listing_model'] == DEFAULT_MANAGED_MODEL
        assert 'sk-or-managed-secret' not in status.text

        keys = client.get('/api/settings/ai-keys', headers=alice)
        assert keys.status_code == 200
        assert keys.json() == {'providers': []}
        assert 'sk-or-managed-secret' not in keys.text

        limits = client.get('/api/settings/usage-limits', headers=alice)
        assert limits.status_code == 200
        assert limits.json() == {'ok': True, 'providers': []}

        # All seller provider & key writes/probes are locked down with 403.
        assert client.put('/api/settings/provider', headers=alice, json={'api_key': 'k'}).status_code == 403
        assert client.put('/api/settings/provider/preferred', headers=alice, json={'primary': 'openai', 'fallback': 'none'}).status_code == 403
        assert client.put('/api/settings/provider/photos', headers=alice, json={'photo_provider': 'same'}).status_code == 403
        assert client.delete('/api/settings/provider/key', headers=alice).status_code == 403
        assert client.post('/api/settings/provider/test', headers=alice).status_code == 403
        assert client.post('/api/settings/provider/mimo/test', headers=alice).status_code == 403
        assert client.get('/api/settings/chatgpt/models', headers=alice).status_code == 403
        assert client.put('/api/settings/ai-keys/openai', headers=alice, json={'api_key': 'k'}).status_code == 403
        assert client.delete('/api/settings/ai-keys/openai', headers=alice).status_code == 403
        assert client.post('/api/settings/ai-keys/openai/test', headers=alice).status_code == 403
        assert client.get('/api/settings/ai-keys/openai/models', headers=alice).status_code == 403
        assert client.put('/api/settings/ai-keys/openai/models', headers=alice, json={'vision_model': 'm', 'listing_model': 'm'}).status_code == 403

