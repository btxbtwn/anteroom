"""Hosted sales use a seller's own connected Chrome and independent schedule."""
import asyncio
from unittest.mock import AsyncMock

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_browser_relay import _bridge_accepts
from test_hosted_vinted import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import chrome_sales, hosted_browser_relay, order_history
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

CAPABLE = 'marketplace.actions,marketplace.sales_history'


def test_each_seller_gets_their_own_due_check(monkeypatch):
    chrome_sales._next.clear()
    read = AsyncMock(return_value=True)
    monkeypatch.setattr(chrome_sales, 'sale_detection_enabled', lambda _: True)
    monkeypatch.setattr(chrome_sales, 'check_marketplace', read)
    try:
        for seller in (USER_ID, BOB):
            with use(for_seller(seller)):
                asyncio.run(chrome_sales.check_due())
        assert [call.args[0] for call in read.call_args_list] == ['poshmark', 'poshmark']
    finally:
        chrome_sales._next.clear()


def test_history_read_uses_the_hosted_contract_and_stays_private(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.1)
    monkeypatch.setattr(order_history, 'READ_TIMEOUT_S', 3)
    hosted_browser_relay._relays.clear()
    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)

    def check():
        with use(for_seller(USER_ID)):
            return asyncio.run(order_history.sync('poshmark'))

    thread, result = _in_background(check)
    command = _answer(client, alice, {'ok': True, 'orders': [{
        'order_id': 'receipt-1', 'status': 'paid', 'currency': 'USD', 'sold_at': '2020-01-01T00:00:00Z',
        'totals': {'item_revenue': '45.00'},
        'lines': [{'line_id': 'line-1', 'listing_id': 'b' * 24}],
    }]}, CAPABLE)
    thread.join(10)
    assert _bridge_accepts(command), command
    assert command['payload'] == {'marketplace': 'poshmark', 'settled': [], 'request_id': command['message_id']}
    assert result['response']['ok'] is True
    assert result['response']['recorded'] == 1
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.marketplace_sale import MarketplaceSale
    for seller, count in ((USER_ID, 1), (BOB, 0)):
        with use(for_seller(seller)), SessionLocal() as db:
            assert db.query(MarketplaceSale).count() == count
    with use(for_seller(BOB)):
        assert asyncio.run(order_history.sync('poshmark'))['ok'] is False
    assert client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=bob).status_code == 204


def test_scheduler_skips_disconnected_and_old_bridges_and_scopes_context(monkeypatch):
    import time
    from vendoo_studio.services import hosted_sale_checks
    from vendoo_studio.services.service_context import current

    hosted_browser_relay._relays.clear()
    for seller, capabilities, seen in (
        (USER_ID, frozenset({'marketplace.actions', order_history.CAPABILITY}), time.monotonic()),
        (BOB, frozenset({'marketplace.actions'}), time.monotonic()),
        ('00000000-0000-0000-0000-000000000001', frozenset({order_history.CAPABILITY}), 0),
    ):
        relay = hosted_browser_relay.relay_for(seller)
        relay._capabilities, relay._seen_at = capabilities, seen
    seen = []

    async def check():
        seen.append(current().seller_id)

    monkeypatch.setattr(chrome_sales, 'check_due', check)
    asyncio.run(hosted_sale_checks.check_ready_sellers())
    assert seen == [USER_ID]
    hosted_browser_relay._relays.clear()


def test_sale_detection_settings_are_owned(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    assert client.get('/api/settings/sale-detection').status_code == 401
    result = client.put('/api/settings/sale-detection', headers=alice,
                        json={'marketplace': 'poshmark', 'enabled': False})
    assert result.status_code == 200
    assert result.json()['enabled']['poshmark'] is False
    assert client.get('/api/settings/sale-detection', headers=bob).json()['enabled']['poshmark'] is True
