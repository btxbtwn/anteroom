"""Lists of sales read in a fixed number of statements, so they never hold a hosted connection per sale.

The Sales Tracker and Today's still-listed alerts once read each sale's stock,
listings and SKU one at a time: about 14 statements per sale on the seller's
connection, which held it for tens of seconds and starved every other request
of the shared pool (#2088).
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

URLS = {'ebay': 'https://www.ebay.com/itm/{}', 'poshmark': 'https://poshmark.com/listing/Tee-{:024x}',
        'mercari': 'https://www.mercari.com/us/item/m{}/', 'depop': 'https://www.depop.com/products/seller-tee-{}/'}


def _sell(count: int, start: int) -> None:
    """``count`` single-unit items, each sold on eBay in the last few days and still listed on three other marketplaces."""
    from anteroom.database import SessionLocal
    from anteroom.models.conversation import Conversation
    from anteroom.models.marketplace_listing import MarketplaceListing
    from anteroom.models.marketplace_sale import MarketplaceSale
    from anteroom.services import listing_persistence
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.marketplace_identity import listing_id
    from anteroom.services.service_context import use

    now = datetime.now(UTC).replace(tzinfo=None)
    with use(for_seller(USER_ID)), SessionLocal() as db:
        for i in range(start, start + count):
            conv = Conversation(title=f'Tee {i}', status='listed')
            db.add(conv)
            db.flush()
            listing_persistence.save_revision(db, conv.id, {'title': f'Tee {i}', 'price': 20, 'quantity': 1,
                                                            'sku': f'SKU-{i}'}, 'test', commit=False)
            for market, pattern in URLS.items():
                url = pattern.format(10_000_000_000 + i)
                db.add(MarketplaceListing(conversation_id=conv.id, marketplace=market, state='published',
                                          listing_id=listing_id(market, url), listing_url=url,
                                          listed_at=now - timedelta(days=30)))
            sold = now - timedelta(hours=1 + i)
            db.add(MarketplaceSale(marketplace='ebay', order_id=f'order-{i}', line_id=f'line-{i}',
                                   listing_id=str(10_000_000_000 + i), conversation_id=conv.id, quantity=1,
                                   order_status='paid', state='new', source='marketplace', sold_at=sold, received_at=sold))
        db.commit()


def _statements(client, alice, path: str, texts: list[str] | None = None) -> tuple[int, dict]:
    from anteroom import database

    seen = []

    def note(_conn, _cursor, statement, *_args, **_kwargs):
        seen.append(1)
        if texts is not None:
            texts.append(statement)

    event.listen(database.engine, 'before_cursor_execute', note)
    try:
        response = client.get(path, headers=alice)
    finally:
        event.remove(database.engine, 'before_cursor_execute', note)
    assert response.status_code == 200, response.text
    return len(seen), response.json()


def _still_listed(body: dict) -> int:
    if 'needs_attention' in body:
        return sum(item['kind'] == 'still_listed' for item in body['needs_attention'])
    return len(body['entries'])


@pytest.mark.parametrize('path', ['/api/marketplace-sales/tracker', '/api/home'], ids=['sales-tracker', 'today'])
def test_more_sales_do_not_add_statements_to_the_held_connection(cloud, signing_keys, path):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    _sell(3, 0)
    few, body = _statements(client, alice, path)
    assert _still_listed(body) == 3
    _sell(27, 3)
    many, body = _statements(client, alice, path)
    assert _still_listed(body) == 30, 'every sold item still listed elsewhere is shown'
    assert many <= few, f'{path} sent {few} statements for 3 sales but {many} for 30'


def test_sales_tracker_reads_small_fields_not_whole_listing_documents(cloud, signing_keys):  # noqa: F811
    """The tracker needs each item's SKU and quantity, not its marketplace forms.

    Production revisions average 83 kB of ``listing_json``; loading one per sold
    item held the seller's pooled connection for up to 28 seconds (#2104).
    """
    client, _engine = cloud
    alice = headers(signing_keys)
    _sell(30, 0)
    texts: list[str] = []
    count, body = _statements(client, alice, '/api/marketplace-sales/tracker', texts)
    assert len(body['entries']) == 30
    assert {entry['sku'] for entry in body['entries']} == {f'SKU-{i}' for i in range(30)}
    assert all(entry['stockStatus'] == 'sold_out' for entry in body['entries'])
    whole = [text for text in texts if 'listing_revisions.listing_json AS' in text]
    assert not whole, f'the tracker loaded whole listing documents: {whole}'
    assert count <= 22, f'the tracker sent {count} statements'
