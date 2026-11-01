"""eBay and Etsy sign-in on the web: per-seller relay sign-ins and Vault tokens, no server browser."""
import time

import httpx

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
        calls.append((method, path))
        if path.endswith('/start'):
            return httpx.Response(200, json={'state': 'state-' + str(len(calls)), 'authorizeUrl': 'https://auth.ebay.com/x'})
        if path == '/studio/oauth/result':
            return httpx.Response(200, json={'accountId': 'alice-ebay', 'username': 'alice_closet',
                                             'tokens': {'accessToken': 'private-access', 'refreshToken': 'private-refresh', 'expiresIn': 7200}})
        return httpx.Response(204)

    monkeypatch.setattr(marketplace_accounts, 'relay_request', relay)
    monkeypatch.setattr(marketplace_accounts, 'POLL_INTERVAL_S', 0)
    monkeypatch.setattr(marketplace_accounts.webbrowser, 'open', lambda url: (_ for _ in ()).throw(AssertionError('server opened a browser')))
    monkeypatch.setattr(marketplace_accounts.relay_client, 'start_stream', lambda handle: (_ for _ in ()).throw(AssertionError('streamed sales')))

    assert client.get('/api/marketplace-accounts').status_code == 401
    started = client.post('/api/marketplace-accounts/ebay/connect', headers=alice)
    assert started.status_code == 200, started.text
    assert started.json() == {'authorizeUrl': 'https://auth.ebay.com/x'}
    deadline = time.time() + 10
    while time.time() < deadline:
        status = client.get('/api/marketplace-accounts', headers=alice).json()
        if status['accounts']['ebay']['connected']:
            break
        time.sleep(0.05)
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
