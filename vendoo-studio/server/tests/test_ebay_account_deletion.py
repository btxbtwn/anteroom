"""eBay's account deletion notice deletes what Anteroom holds about that eBay account, and nothing else."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest import mock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.ebay_action import EbayAction
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferPolicy, EbayOfferRule
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import keychain, relay_sales, saved_choices

TABLES = (EbayAction, EbayOfferPolicy, EbayOfferRule, EbayOfferAttempt, MarketplaceListing, MarketplaceSale)


def deletion(user_id):
    return {"seq": 1, "source": "ebay", "eventType": "MARKETPLACE_ACCOUNT_DELETION", "ids": {"userId": [user_id]}}


def handle(event, session_factory):
    return asyncio.run(relay_sales.handle_event(event, session_factory=session_factory, read_etsy_receipt=mock.AsyncMock()))


def seed(db, account="seller-a"):
    """One item listed on eBay and Poshmark, sold on eBay and Etsy, with eBay offers and receipts."""
    item = ConversationRepo(db).create(title="Wool coat")
    expires = utcnow() + timedelta(minutes=15)
    rule = EbayOfferRule(account_id=account, listing_id="123456789012", conversation_id=item.id,
                         discount_percent=10, minimum_price_cents=3000, enabled=True)
    db.add_all([
        MarketplaceListing(conversation_id=item.id, marketplace="ebay", state="published",
                           listing_id="123456789012", ebay_sku="SKU-1", ebay_offer_id="o-1"),
        MarketplaceListing(conversation_id=item.id, marketplace="poshmark", state="published", listing_id="p-1"),
        MarketplaceSale(marketplace="ebay", order_id=f"12-{account}", line_id="li-1", listing_id="123456789012",
                        conversation_id=item.id, financials={"currency": "USD", "item_revenue": "40.00"}),
        MarketplaceSale(marketplace="etsy", order_id=f"9001-{account}", line_id="31", listing_id="555"),
        EbayAction(account_id=account, kind="refund", resource_id="12-345", fingerprint="f", state="sent",
                   receipt_id="r-1", expires_at=expires),
        EbayOfferPolicy(account_id=account, discount_percent=10, minimum_price_cents=3000, enabled=True),
        rule,
    ])
    db.flush()
    db.add(EbayOfferAttempt(rule_id=rule.id, current_price_cents=5000, offer_price_cents=4500, discount_percent=10,
                            minimum_price_cents=3000, state="sent", recipient_count=3, expires_at=expires))
    db.commit()
    return item.id


def counts(session_factory):
    with session_factory() as db:
        return {table.__tablename__: db.scalar(select(func.count()).select_from(table)) for table in TABLES}


@pytest.fixture
def desk():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        item = seed(db)
    keychain.set_marketplace_account("ebay", {"accountId": "seller-a", "label": "closet_a", "accessToken": "t"})
    keychain.set_marketplace_account("etsy", {"accountId": "shop-1", "accessToken": "e"})
    saved_choices._save("ebay-policies", "seller-a", {"locations": [{"id": "home", "name": "12 Elm St"}]})
    yield session_factory, item
    for marketplace in ("ebay", "etsy"):
        keychain.delete_marketplace_account(marketplace)
    saved_choices.forget("ebay-policies")


def test_the_connected_accounts_ebay_data_is_deleted_and_the_sellers_own_records_kept(desk, caplog):
    session_factory, item = desk
    with caplog.at_level(logging.INFO):
        assert handle(deletion("seller-a"), session_factory) == []
    assert counts(session_factory) == {"ebay_actions": 0, "ebay_offer_policies": 0, "ebay_offer_rules": 0,
                                       "ebay_offer_attempts": 0, "marketplace_listings": 1, "marketplace_sales": 2}
    with session_factory() as db:
        assert db.scalar(select(MarketplaceListing.marketplace)) == "poshmark"
        assert ConversationRepo(db).get(item).title == "Wool coat"
    accounts = keychain.get_marketplace_accounts()
    assert "ebay" not in accounts and accounts["etsy"]["accountId"] == "shop-1"
    assert saved_choices.saved("ebay-policies", "seller-a") is None
    # The log says what went, never whose account it was.
    assert "deleted" in caplog.text
    for personal in ("seller-a", "closet_a", "12 Elm St"):
        assert personal not in caplog.text


def test_another_ebay_account_closing_leaves_everything(desk):
    session_factory, _ = desk
    before = counts(session_factory)
    handle(deletion("someone-else"), session_factory)
    handle({**deletion("x"), "ids": {}}, session_factory)
    assert counts(session_factory) == before
    assert keychain.get_marketplace_accounts()["ebay"]["accountId"] == "seller-a"
    assert saved_choices.saved("ebay-policies", "seller-a") is not None


def test_a_replayed_notice_is_harmless(desk):
    session_factory, _ = desk
    handle(deletion("seller-a"), session_factory)
    after = counts(session_factory)
    handle(deletion("seller-a"), session_factory)
    assert counts(session_factory) == after
    # The seller connects a new eBay account and lists again; the old notice comes back.
    with session_factory() as db:
        seed(db, account="seller-b")
    keychain.set_marketplace_account("ebay", {"accountId": "seller-b", "accessToken": "t2"})
    before = counts(session_factory)
    handle(deletion("seller-a"), session_factory)
    assert counts(session_factory) == before
    assert keychain.get_marketplace_accounts()["ebay"]["accountId"] == "seller-b"


def test_a_failed_purge_keeps_the_sign_in_so_the_retry_runs_again(desk):
    session_factory, _ = desk
    with mock.patch.object(saved_choices, "forget", side_effect=OSError("disk full")), pytest.raises(OSError):
        handle(deletion("seller-a"), session_factory)
    assert keychain.get_marketplace_accounts()["ebay"]["accountId"] == "seller-a"
    handle(deletion("seller-a"), session_factory)
    assert "ebay" not in keychain.get_marketplace_accounts()
    assert counts(session_factory)["ebay_actions"] == 0


def test_the_purge_runs_off_the_event_loop(desk):
    import threading

    from vendoo_studio.services import ebay_account_deletion

    threads = []
    with mock.patch.object(ebay_account_deletion, "purge", lambda user_id, factory: threads.append(threading.current_thread())):
        handle(deletion("seller-a"), desk[0])
    assert len(threads) == 1 and threads[0] is not threading.main_thread()
