"""Automatic eBay offers on the web: each seller's check runs in their own context."""
import asyncio
import json
import time
from datetime import timedelta

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_marketplaces import _fake_vault
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from vendoo_studio.services import ebay_api, ebay_offers, hosted_offer_scheduler
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


def _rule(owner, *, enabled, listing='123456789012'):
    from vendoo_studio.database import SessionLocal

    with use(for_seller(owner)), SessionLocal() as db:
        rule = EbayOfferRule(account_id=f'{owner}-ebay', listing_id=listing, discount_percent=10,
                             minimum_price_cents=3000, enabled=enabled)
        db.add(rule)
        db.commit()
        return rule.id


def test_web_offer_timer_checks_each_seller_in_their_own_context(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    vault = _fake_vault(monkeypatch)
    vault[(USER_ID, 'marketplace-accounts')] = json.dumps({'ebay': {
        'accountId': f'{USER_ID}-ebay', 'accessToken': 'alice-token', 'expiresAt': time.time() + 7200}})
    monkeypatch.setattr(ebay_offers, '_checks', {})
    eligible_tokens = []

    async def eligible_items():
        eligible_tokens.append(json.loads(vault[(USER_ID, 'marketplace-accounts')])['ebay']['accessToken'])
        return set()

    monkeypatch.setattr(ebay_api, 'eligible_items', eligible_items)

    _rule(USER_ID, enabled=True)
    # Bob's send was cut off by a restart: its result is unknown.
    bob_rule = _rule(BOB, enabled=True)
    _rule(BOB, enabled=True, listing='223456789012')
    from vendoo_studio.database import SessionLocal
    with use(for_seller(BOB)), SessionLocal() as db:
        db.add(EbayOfferAttempt(rule_id=bob_rule, current_price_cents=5000, offer_price_cents=4500, discount_percent=10,
                                minimum_price_cents=3000, state='sending', expires_at=utcnow() + timedelta(minutes=15)))
        db.commit()

    assert sorted(hosted_offer_scheduler.offer_owners(engine)) == sorted([(USER_ID, False), (BOB, True)])

    monkeypatch.setattr(ebay_offers, 'CHECK_INTERVAL_S', 0)

    async def one_round():
        task = asyncio.create_task(hosted_offer_scheduler.serve(engine))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(ebay_offers._checks) == 2:
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(one_round())

    with use(for_seller(BOB)), SessionLocal() as db:
        attempt = db.query(EbayOfferAttempt).one()
        assert attempt.state == 'unknown'
        assert not db.get(EbayOfferRule, bob_rule).enabled
    assert sorted(hosted_offer_scheduler.offer_owners(engine)) == sorted([(USER_ID, False), (BOB, False)])
    assert eligible_tokens and set(eligible_tokens) == {'alice-token'}

    alice = client.get('/api/ebay-offers', headers=headers(signing_keys)).json()
    bob = client.get('/api/ebay-offers', headers=headers(signing_keys, BOB)).json()
    assert alice['lastCheck'] and alice['lastError'] is None
    assert bob['lastCheck'] and 'Connect eBay' in bob['lastError']
