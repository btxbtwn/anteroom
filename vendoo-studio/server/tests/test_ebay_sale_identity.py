"""Concurrent hosted eBay writers reconcile only the same seller's transaction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_hosted_drafts import BOB, cloud  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import relay_sales
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


def test_concurrent_alias_writers_create_one_row_per_seller(cloud):  # noqa: F811
    barrier = Barrier(4)

    def record(args):
        owner, line_id = args
        context = for_seller(owner)
        with use(context), context.session_factory() as db:
            barrier.wait(timeout=10)
            relay_sales.record_sale(db, marketplace='ebay', order_id='12-12345-12345', line_id=line_id,
                                    listing_id='123456789012', quantity=1, sold_at=None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, [(owner, line) for owner in (USER_ID, BOB)
                               for line in ('123456789012-1234567', '1234567')]))
    for owner in (USER_ID, BOB):
        context = for_seller(owner)
        with use(context), context.session_factory() as db:
            assert db.query(MarketplaceSale).count() == 1
