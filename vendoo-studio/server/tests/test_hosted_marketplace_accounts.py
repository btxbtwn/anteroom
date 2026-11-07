"""eBay and Etsy sign-in on the web: per-seller relay sign-ins and Vault tokens, no server browser."""
import time

import httpx

from vendoo_studio import config

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.services import marketplace_accounts
from vendoo_studio.services.hosted_context import VaultSecretStore


def test_each_seller_signs_in_to_their_own_ebay_through_the_relay(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    calls, vault = [], {}
    # CI's Postgres has no Supabase Vault; keep each seller's secrets apart in memory.
    monkeypatch.setattr(VaultSecretStore, 'get', lambda self, account: vault.get((self.owner, account)))
    monkeypatch.setattr(VaultSecretStore, 'set', lambda self, account, value: vault.__setitem__((self.owner, account), value) or True)
    monkeypatch.setattr(VaultSecretStore, 'delete', lambda self, account: vault.pop((self.owner, account), None))
    monkeypatch.setattr(VaultSecretStore, 'update', lambda self, account, mutator: vault.__setitem__((self.owner, account), mutator(vault.get((self.owner, account)))))

    async def relay(method, path, **kwargs):
        params = kwargs.get('params') or {}
        calls.append((method, path, params))
        if path.endswith('/start'):
            return httpx.Response(200, json={'state': 'alice-state', 'authorizeUrl': 'https://auth.ebay.com/x'})
        if path == '/studio/oauth/result' and params.get('claim') == 'claim-from-browser':
            return httpx.Response(200, json={'accountId': 'alice-ebay', 'username': 'alice_closet',
                                             'tokens': {'accessToken': 'private-access', 'refreshToken': 'private-refresh', 'expiresIn': 7200}})
        if path == '/studio/oauth/result':
            return httpx.Response(202, json={'status': 'pending'})
        return httpx.Response(204)

    monkeypatch.setattr(marketplace_accounts, 'relay_request', relay)
    monkeypatch.setattr(marketplace_accounts, 'POLL_INTERVAL_S', 0)
    monkeypatch.setattr(marketplace_accounts.webbrowser, 'open', lambda url: (_ for _ in ()).throw(AssertionError('server opened a browser')))
    monkeypatch.setattr(marketplace_accounts.relay_client, 'start_stream', lambda handle: (_ for _ in ()).throw(AssertionError('streamed sales')))

    assert client.get('/api/marketplace-accounts').status_code == 401
    started = client.post('/api/marketplace-accounts/ebay/connect', headers=alice)
    assert started.status_code == 200, started.text
    assert started.json() == {'authorizeUrl': 'https://auth.ebay.com/x'}
    start_params = next(params for _method, path, params in calls if path.endswith('/start'))
    assert start_params['returnTo'] == f'{config.HOSTED_URL}/'
    state = 'alice-state'
    time.sleep(0.1)
    assert not client.get('/api/marketplace-accounts', headers=alice).json()['accounts']['ebay']['connected']
    # Bob's browser cannot finish Alice's sign-in, even with the claim.
    finish = {'state': state, 'claim': 'claim-from-browser'}
    assert client.post('/api/marketplace-accounts/ebay/finish', headers=bob, json=finish).status_code == 409
    assert not any(params.get('claim') for _m, path, params in calls if path == '/studio/oauth/result')
    done = client.post('/api/marketplace-accounts/ebay/finish', headers=alice, json=finish)
    assert done.status_code == 200, done.text
    assert done.json() == {'marketplace': 'ebay', 'label': 'alice_closet'}
    status = client.get('/api/marketplace-accounts', headers=alice).json()
    assert status['accounts']['ebay'] == {'connected': True, 'label': 'alice_closet', 'connectedAt': status['accounts']['ebay']['connectedAt'],
                                          'pending': False, 'authorizeUrl': None, 'error': None}
    assert 'private-access' not in client.get('/api/marketplace-accounts', headers=alice).text
    assert {owner for owner, _account in vault} == {next(owner for owner, _ in vault)}
    # Bob has neither Alice's connection nor her pending sign-in.
    bob_status = client.get('/api/marketplace-accounts', headers=bob).json()['accounts']['ebay']
    assert not bob_status['connected'] and not bob_status['pending']
    assert client.delete('/api/marketplace-accounts/ebay', headers=bob).status_code == 200
    assert client.get('/api/marketplace-accounts', headers=alice).json()['accounts']['ebay']['connected']
    assert client.delete('/api/marketplace-accounts/ebay', headers=alice).status_code == 200
    assert not client.get('/api/marketplace-accounts', headers=alice).json()['accounts']['ebay']['connected']
    assert not any('private-access' in str(value) for value in vault.values())


def test_the_iphone_app_asks_the_relay_to_return_to_the_app(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    starts = []

    async def relay(method, path, **kwargs):
        if path.endswith('/start'):
            starts.append(kwargs.get('params'))
            return httpx.Response(200, json={'state': 'state-app', 'authorizeUrl': 'https://auth.ebay.com/x'})
        return httpx.Response(202, json={'status': 'pending'})

    monkeypatch.setattr(marketplace_accounts, 'relay_request', relay)
    reply = client.post('/api/marketplace-accounts/ebay/connect', headers=headers(signing_keys), json={'client': 'app'})
    assert reply.status_code == 200, reply.text
    assert starts[0]['returnTo'] == 'anteroom://marketplace-signin'
