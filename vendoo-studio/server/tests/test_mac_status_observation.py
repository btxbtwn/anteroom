"""Reading status must not establish even an empty relay or a session."""
import asyncio
from types import SimpleNamespace
from dataclasses import replace

import pytest

from vendoo_studio.routes import mac_inference as routes
from vendoo_studio.services import device_routes, mac_inference


def test_account_destination_preserves_configured_environment_without_import(monkeypatch):
    from vendoo_studio import config
    from vendoo_studio.routes import account_move
    monkeypatch.setattr(config, 'HOSTED_URL', 'https://staging.example.test')
    monkeypatch.setattr(account_move.account_move, 'status', lambda: pytest.fail('Listing-copy status was read'))
    assert account_move.account_destination() == {'hosted_url': 'https://staging.example.test/?sign-in'}


def test_status_of_absent_mac_does_not_create_relay(monkeypatch):
    owner = 'status-observation-only'
    monkeypatch.setattr(routes, 'current', lambda: SimpleNamespace(seller_id=owner))
    monkeypatch.setattr(routes, '_pinned', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(device_routes, 'remote_fresh', lambda *_args: False)
    monkeypatch.setattr(mac_inference, '_relays', {})
    assert asyncio.run(routes.status()) == {'connected': False, 'providers': {}}
    assert mac_inference._relays == {}


def test_status_preserves_existing_heartbeat_models_and_pending_work(monkeypatch):
    owner = 'existing-status-only'
    connection = mac_inference.MacInference(owner)
    models = {'chatgpt': {'listing_model': 'model', 'vision_model': 'vision'}}
    connection.poll('worker', models, timeout=0)
    before = (connection.worker, connection.seen, dict(connection.models))
    monkeypatch.setattr(mac_inference, '_relays', {owner: connection})
    monkeypatch.setattr(routes, 'current', lambda: SimpleNamespace(seller_id=owner))
    monkeypatch.setattr(routes, '_pinned', lambda *_args, **_kwargs: None)
    assert asyncio.run(routes.status()) == {'connected': True, 'providers': models}
    assert (connection.worker, connection.seen, connection.models) == before
    assert not connection.outbox and not connection.pending


def test_provider_settings_and_selection_do_not_establish_account_relay(monkeypatch):
    from test_service_context import runtime
    from vendoo_studio.routes import settings
    from vendoo_studio.services import service_context, user_settings
    from vendoo_studio.services.hosted_provider import provider_for

    owner = 'provider-status-only'
    monkeypatch.setattr(mac_inference, '_relays', {})
    monkeypatch.setattr(routes, '_pinned', lambda *_args, **_kwargs: None)
    seller = replace(runtime(owner), provider_factory=provider_for)
    with service_context.use(seller):
        user_settings.set_listing_provider_order(primary='chatgpt', fallback='none')
        status = settings.get_provider()
    assert status.provider == 'chatgpt'
    assert not status.chatgpt.signed_in and not status.claude.signed_in
    assert status.listing_model == ''
    assert mac_inference._relays == {}


def test_inspected_provider_uses_the_relay_that_connects_before_its_operation(monkeypatch):
    owner = 'late-connection'
    monkeypatch.setattr(mac_inference, '_relays', {})
    provider = mac_inference.MacSubscriptionProvider(None, 'chatgpt', owner=owner)
    assert mac_inference._relays == {}
    connection = mac_inference.relay_for(owner)
    connection.poll('worker', {'chatgpt': {'listing_model': 'model', 'vision_model': 'vision'}}, timeout=0)
    assert provider.relay is connection
    assert connection.ready('chatgpt')


@pytest.mark.parametrize('failed_step', ['lookup', 'forward'])
def test_forward_failure_does_not_claim_mac_is_signed_out_or_asleep(monkeypatch, failed_step):
    async def target(*_args):
        if failed_step == 'lookup':
            raise device_routes.RoutingUnavailable()
        return 'http://127.0.0.1:8080'

    async def post(*_args, **_kwargs):
        raise device_routes.RoutingUnavailable()

    monkeypatch.setattr(device_routes, 'forward_target', target)
    monkeypatch.setattr(device_routes, 'post', post)
    connection = mac_inference.MacInference('seller')
    with pytest.raises(mac_inference.MacUnavailable, match='could not reach the Mac connection') as failure:
        asyncio.run(connection.call('chat', [], {}, 'chatgpt'))
    assert 'signed out' not in str(failure.value).lower()
    assert 'copy or import' in str(failure.value)
    assert not connection.outbox and not connection.pending
