"""The Marketplaces page on the web: each seller's own eBay, Etsy and sale alerts."""
import json
import time

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services.ebay_client import EbayClient
from vendoo_studio.services.hosted_context import VaultSecretStore, for_seller
from vendoo_studio.services.service_context import use


def _fake_vault(monkeypatch) -> dict:
    # CI's Postgres has no Supabase Vault; keep each seller's secrets apart in memory.
    vault: dict = {}
    monkeypatch.setattr(VaultSecretStore, 'get', lambda self, account: vault.get((str(self.owner), account)))
    monkeypatch.setattr(VaultSecretStore, 'set', lambda self, account, value: vault.__setitem__((str(self.owner), account), value) or True)
    monkeypatch.setattr(VaultSecretStore, 'delete', lambda self, account: vault.pop((str(self.owner), account), None))
    monkeypatch.setattr(VaultSecretStore, 'update', lambda self, account, mutator: vault.__setitem__(
        (str(self.owner), account), mutator(vault.get((str(self.owner), account)))))
    return vault


def test_marketplaces_page_reads_only_the_sellers_own_ebay(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    vault = _fake_vault(monkeypatch)
    vault[(USER_ID, 'marketplace-accounts')] = json.dumps({'ebay': {
        'accountId': 'alice-ebay', 'username': 'alice_closet', 'accessToken': 'alice-token',
        'refreshToken': 'alice-refresh', 'expiresAt': time.time() + 7200, 'connectedAt': '2026-11-01T00:00:00Z'}})
    tokens = []

    async def request(self, method, path, **kwargs):
        tokens.append(self.token)
        return {'total': 1, 'conversations': [{'conversationId': 'c1', 'conversationTitle': 'Is this still available?',
                                               'conversationStatus': 'ACTIVE', 'unreadCount': 1}]}

    monkeypatch.setattr(EbayClient, 'request', request)

    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'marketplaces' in runtime['views']
    assert 'ebay_offers' not in runtime['features']

    inbox = client.get('/api/ebay/read/inbox', headers=alice)
    assert inbox.status_code == 200, inbox.text
    assert 'Is this still available?' in inbox.text
    assert tokens == ['alice-token']
    # Bob never reaches eBay with Alice's connection.
    assert client.get('/api/ebay/read/inbox', headers=bob).status_code == 403
    assert client.get('/api/etsy/shop', headers=bob).status_code == 403
    assert tokens == ['alice-token']
    assert client.get('/api/ebay/read/activity', headers=bob).status_code == 403

    offers = client.get('/api/ebay-offers', headers=alice)
    assert offers.status_code == 200, offers.text
    assert offers.json()['lastCheck'] is None and offers.json()['lastError'] is None


def test_sale_alerts_are_private_to_each_seller(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    with use(for_seller(USER_ID)), SessionLocal() as db:
        sale = MarketplaceSale(marketplace='etsy', order_id='o1', line_id='l1', listing_id='123', state='new')
        db.add(sale)
        db.commit()
        sale_id = sale.id

    assert [row['id'] for row in client.get('/api/marketplace-sales', headers=alice).json()['sales']] == [sale_id]
    assert client.get('/api/marketplace-sales', headers=bob).json()['sales'] == []
    assert client.post('/api/marketplace-sales/reconcile?marketplace=etsy', headers=bob).json() == {'matched': 0, 'unmatched': 0}
    assert client.post(f'/api/marketplace-sales/{sale_id}/dismiss', headers=bob).status_code == 404
    assert client.post(f'/api/marketplace-sales/{sale_id}/dismiss', headers=alice).status_code == 200
    assert client.get('/api/marketplace-sales', headers=alice).json()['sales'] == []
