"""On the web, eBay's account deletion notice deletes only that seller's eBay data."""
import asyncio
import json

from test_ebay_account_deletion import TABLES, seed
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_marketplaces import _fake_vault
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from sqlalchemy import func, select

from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.services import hosted_sales_scheduler, recent_orders, relay_client, saved_choices
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


class Reply:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def counts(owner):
    from vendoo_studio.database import SessionLocal

    with use(for_seller(owner)), SessionLocal() as db:
        return {table.__tablename__: db.scalar(select(func.count()).select_from(table)) for table in TABLES}


def test_a_closed_ebay_account_is_purged_from_its_seller_only(cloud, monkeypatch):  # noqa: F811
    _client, _engine = cloud
    from vendoo_studio.database import SessionLocal

    vault = _fake_vault(monkeypatch)
    # Both sellers name the same eBay ID: only the seller whose inbox held the notice loses anything.
    for owner in (USER_ID, BOB):
        vault[(owner, 'marketplace-accounts')] = json.dumps({
            'ebay': {'accountId': 'closed-ebay', 'accessToken': f'{owner}-token'},
            'etsy': {'accountId': 'shop-1', 'accessToken': 'etsy-token'}})
        with use(for_seller(owner)):
            with SessionLocal() as db:
                seed(db, account='closed-ebay')
            saved_choices._save('ebay-policies', 'closed-ebay', {'locations': [{'id': 'home'}]})
    bob_before = counts(BOB)
    acks = []

    async def relay(method, path, **kwargs):
        if method == 'POST':
            acks.append(kwargs['json_body'])
            return Reply({})
        return Reply({'events': [{'seq': 7, 'source': 'ebay', 'eventType': 'MARKETPLACE_ACCOUNT_DELETION',
                                  'ids': {'userId': ['closed-ebay']}}]})

    async def no_orders():
        pass

    monkeypatch.setattr(relay_client, 'relay_request', relay)
    monkeypatch.setattr(recent_orders, 'refresh', no_orders)

    asyncio.run(hosted_sales_scheduler.check_owner(USER_ID))
    assert acks == [{'through': 7}]
    assert counts(USER_ID) == {'ebay_actions': 0, 'ebay_offer_policies': 0, 'ebay_offer_rules': 0,
                               'ebay_offer_attempts': 0, 'marketplace_listings': 1, 'marketplace_sales': 2}
    with use(for_seller(USER_ID)), SessionLocal() as db:
        assert db.scalar(select(MarketplaceListing.marketplace)) == 'poshmark'
    with use(for_seller(USER_ID)):
        assert saved_choices.saved('ebay-policies', 'closed-ebay') is None
    assert list(json.loads(vault[(USER_ID, 'marketplace-accounts')])) == ['etsy']

    # Bob's rows, saved policies and sign-in are untouched.
    assert counts(BOB) == bob_before
    with use(for_seller(BOB)):
        assert saved_choices.saved('ebay-policies', 'closed-ebay') is not None
    assert json.loads(vault[(BOB, 'marketplace-accounts')])['ebay']['accessToken'] == f'{BOB}-token'

    # The relay hands the same notice back: nothing more happens.
    after = counts(USER_ID)
    asyncio.run(hosted_sales_scheduler.check_owner(USER_ID))
    assert counts(USER_ID) == after
    assert counts(BOB) == bob_before
