"""Real Postgres coverage: never substitute an in-memory SQLite engine."""
from datetime import datetime
import uuid
from unittest.mock import Mock

from alembic import command
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing_promotion import ListingPromotion
from anteroom.repositories.offer_analytics import ebay_counts, promotion_counts
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.schema_migrations import alembic_config
from anteroom.services.service_context import ServiceContext, use
from anteroom.services.offer_analytics import offer_analytics

PREVIOUS = 'f64a29c108de'
REVISION = 'fd7c3a5d05ea'


@pytest.fixture
def pg_engine(migration_engine):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Requires real PostgreSQL migration and RLS execution')
    return migration_engine


def config(engine):
    return alembic_config(engine.url.render_as_string(hide_password=False))


def test_concurrent_index_partial_failure_can_be_retried(pg_engine, monkeypatch):
    command.upgrade(config(pg_engine), PREVIOUS)
    original = Operations.create_index
    def interrupted(self, name, *args, **kwargs):
        if name == 'ix_listing_promotions_seller_sent':
            raise RuntimeError('simulated interruption before second concurrent index')
        return original(self, name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Operations, 'create_index', interrupted)
        with pytest.raises(RuntimeError, match='simulated interruption'):
            command.upgrade(config(pg_engine), REVISION)
    assert 'ix_ebay_offer_attempts_seller_sent' in {i['name'] for i in inspect(pg_engine).get_indexes('ebay_offer_attempts')}
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PREVIOUS
    command.upgrade(config(pg_engine), REVISION)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == REVISION


def seed(db, seller, counts):
    now = datetime(2026, 11, 16, 12)
    rule = EbayOfferRule(user_id=seller, account_id='account', listing_id='listing', discount_percent=10, minimum_price_cents=100)
    db.add(rule)
    db.flush()
    for count in counts:
        db.add(EbayOfferAttempt(user_id=seller, rule_id=rule.id, current_price_cents=1000, offer_price_cents=900,
            discount_percent=10, minimum_price_cents=100, state='sent', recipient_count=count,
            created_at=now, sent_at=now, expires_at=now))
        db.add(ListingPromotion(user_id=seller, batch_id='batch', marketplace='mercari', action='offer',
            listing_id='listing', state='done', confirmation='recipients_created', recipient_count=count,
            created_at=now, sent_at=now, expires_at=now))


def test_postgres_aggregates_and_rls_exclude_other_seller_even_with_wrong_predicate(pg_engine):
    # SellerDatabaseSession checks RLS for all current models, not just this migration.
    command.upgrade(config(pg_engine), 'head')
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    with Session(pg_engine) as db:
        seed(db, alice, [2, 5, 0, -100])
        db.add(ListingPromotion(user_id=alice, batch_id='missing', marketplace='mercari', action='offer',
            listing_id='listing', state='done', confirmation='recipients_created', recipient_count=None,
            created_at=datetime(2026, 11, 16, 12), sent_at=datetime(2026, 11, 16, 12), expires_at=datetime(2026, 11, 17)))
        seed(db, bob, [99])
        db.commit()
    for seller, expected, sends, unverified, other in [(alice, 7, 2, 2, bob), (bob, 99, 1, 0, alice)]:
        with SellerDatabaseSession(bind=pg_engine, user_id=seller) as db:
            assert db.execute(text('SELECT current_user')).scalar_one() == 'anteroom_seller'
            # RLS must hold even if the repository's explicit owner predicate is wrong.
            assert ebay_counts(db, other, datetime(2026, 11, 16), datetime(2026, 11, 17)) == []
            assert promotion_counts(db, other, datetime(2026, 11, 16), datetime(2026, 11, 17)) == []
            context = ServiceContext(seller_id=seller, session_factory=Mock(), settings=Mock(), secrets=Mock(), photos=Mock())
            with use(context):
                result = offer_analytics(db, start=datetime(2026, 11, 16).date(), end=datetime(2026, 11, 16).date(), timezone='UTC')
            for row in (result['marketplaces'][0], result['marketplaces'][2]):
                missing = 1 if seller == alice and row['marketplace'] == 'mercari' else 0
                assert (row['confirmed_recipients'], row['confirmed_sends'], row['unverified']) == (expected, sends, unverified + missing)
            assert set(db.scalars(select(EbayOfferAttempt.user_id))) == {seller}


def test_invalid_concurrent_index_is_rebuilt(pg_engine):
    command.upgrade(config(pg_engine), PREVIOUS)
    # A failed unique concurrent build creates a real invalid catalog entry.
    # Duplicate default-owner rows make that failure deterministic.
    with pg_engine.begin() as connection:
        connection.execute(text("INSERT INTO ebay_offer_rules (id, account_id, listing_id, discount_percent, minimum_price_cents, enabled, updated_at) VALUES ('rule', 'a', 'l', 10, 100, false, now())"))
        for key in ('one', 'two'):
            connection.execute(text("INSERT INTO ebay_offer_attempts (id, rule_id, current_price_cents, offer_price_cents, discount_percent, minimum_price_cents, state, recipient_count, created_at, expires_at) VALUES (:id, 'rule', 1000, 900, 10, 100, 'sent', 2, now(), now())"), {'id': key})
    with pg_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text('CREATE UNIQUE INDEX CONCURRENTLY ix_ebay_offer_attempts_seller_sent ON ebay_offer_attempts (user_id)'))
        assert connection.execute(text("SELECT indisvalid FROM pg_index WHERE indexrelid = 'ix_ebay_offer_attempts_seller_sent'::regclass")).scalar_one() is False
    command.upgrade(config(pg_engine), REVISION)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT indisvalid AND NOT indisunique FROM pg_index WHERE indexrelid = 'ix_ebay_offer_attempts_seller_sent'::regclass")).scalar_one() is True
        assert connection.execute(text('SELECT count(*) FROM ebay_offer_attempts WHERE sent_at IS NULL')).scalar_one() == 2
    # A still-running old worker can write the old columns during rollout;
    # the nullable expansion keeps its write valid without inventing dates.
    with pg_engine.begin() as connection:
        connection.execute(text("INSERT INTO ebay_offer_attempts (id, rule_id, current_price_cents, offer_price_cents, discount_percent, minimum_price_cents, state, recipient_count, created_at, expires_at) VALUES ('old-worker', 'rule', 1000, 900, 10, 100, 'sent', 2, now(), now())"))
        assert connection.execute(text("SELECT sent_at FROM ebay_offer_attempts WHERE id = 'old-worker'")).scalar_one() is None


def test_invalid_promotion_recent_index_is_rebuilt(pg_engine):
    command.upgrade(config(pg_engine), 'd7be65e14c2f')
    # A failed unique concurrent build leaves a real INVALID index behind.
    with pg_engine.begin() as connection:
        for key in ('one', 'two'):
            connection.execute(text(
                "INSERT INTO listing_promotions (id, batch_id, marketplace, action, listing_id, state, created_at, expires_at) "
                "VALUES (:id, 'b', 'mercari', 'offer', 'l', 'done', now(), now())"
            ), {'id': key})
    with pg_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                'CREATE UNIQUE INDEX CONCURRENTLY ix_listing_promotions_rule_recent ON listing_promotions (user_id)'
            ))
        assert connection.execute(text(
            "SELECT indisvalid FROM pg_index WHERE indexrelid = 'ix_listing_promotions_rule_recent'::regclass"
        )).scalar_one() is False
    command.upgrade(config(pg_engine), PREVIOUS)
    with pg_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT indisvalid AND NOT indisunique FROM pg_index WHERE indexrelid = 'ix_listing_promotions_rule_recent'::regclass"
        )).scalar_one() is True
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PREVIOUS
