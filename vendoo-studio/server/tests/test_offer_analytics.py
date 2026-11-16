from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from vendoo_studio.database import Base
from vendoo_studio.models.listing_promotion import ListingPromotion
from vendoo_studio.models.ownership import LOCAL_USER_ID
from vendoo_studio.services.offer_analytics import offer_analytics


def test_receipts_not_runs_and_no_recipient_inflation():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 16, 12)
    with Session(engine) as db:
        for market, state, evidence, count in [
            ('mercari', 'done', 'recipients_created', 3),
            ('mercari', 'skipped', None, 99),
            ('mercari', 'unknown', None, None),
            ('mercari', 'failed', None, None),
            ('poshmark', 'done', 'request_accepted', 99),
            ('depop', 'done', 'job_completed', 99),
            ('depop', 'done', 'legacy_unverified', None),
        ]:
            db.add(ListingPromotion(batch_id='same-batch', marketplace=market, action='offer',
                listing_id='same-listing', state=state, confirmation=evidence, recipient_count=count,
                created_at=now, sent_at=now, expires_at=now + timedelta(days=1)))
        db.add(ListingPromotion(user_id='other', batch_id='private', marketplace='mercari', action='offer',
            listing_id='private', state='done', confirmation='recipients_created', recipient_count=100,
            created_at=now, sent_at=now, expires_at=now))
        db.commit()
        statements = []
        event.listen(engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
        result = offer_analytics(db, start=date(2026, 11, 16), end=date(2026, 11, 16), timezone='UTC')
        rows = {row['marketplace']: row for row in result['marketplaces']}
        assert rows['mercari']['confirmed_recipients'] == 3
        assert rows['mercari']['unknown'] == 1
        assert rows['mercari']['failed'] == 1
        assert rows['poshmark']['confirmed_recipients'] is None
        assert rows['poshmark']['accepted_requests'] == 1
        assert rows['depop']['completed_jobs'] == 1
        assert rows['depop']['unverified'] == 1
        assert result['confirmed_conversions'] is None
        assert len(statements) <= 4
        assert LOCAL_USER_ID not in str(result)


def test_calendar_bounds_include_dst_and_exclude_next_day():
    from vendoo_studio.services.offer_analytics import period_bounds
    start, end = period_bounds(date(2026, 11, 1), date(2026, 11, 1), 'America/New_York')
    assert start == datetime(2026, 11, 1, 4)
    assert end - start == timedelta(hours=25)
    assert start.tzinfo is None
    assert datetime.now(UTC).tzinfo is not None


def test_ebay_send_date_not_preview_date_and_repeated_reads():
    from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 16, 12)
    with Session(engine) as db:
        rule = EbayOfferRule(account_id='account', listing_id='listing', discount_percent=10, minimum_price_cents=100)
        db.add(rule)
        db.flush()
        for created, sent, state, recipients in [
            (now - timedelta(days=1), now, 'sent', 2),
            (now, now + timedelta(hours=12), 'sent', 100),
            (now, None, 'sent', 100),  # historical preview time is not a send date
            (now, now, 'unknown', 100),
            (now, now, 'checked', 100),
        ]:
            db.add(EbayOfferAttempt(rule_id=rule.id, current_price_cents=1000, offer_price_cents=900,
                discount_percent=10, minimum_price_cents=100, state=state, recipient_count=recipients,
                created_at=created, sent_at=sent, expires_at=now))
        db.commit()
        args = dict(start=date(2026, 11, 16), end=date(2026, 11, 16), timezone='UTC')
        result = offer_analytics(db, **args)
        assert result == offer_analytics(db, **args)
        row = result['marketplaces'][0]
        assert row['confirmed_recipients'] == 2
        assert row['confirmed_sends'] == 1
        assert row['unknown'] == 2


def test_rejects_unbounded_or_invalid_ranges():
    import pytest
    from zoneinfo import ZoneInfoNotFoundError
    from vendoo_studio.services.offer_analytics import period_bounds
    for start, end in [(date(2026, 1, 2), date(2026, 1, 1)), (date(2025, 1, 1), date(2026, 2, 1))]:
        with pytest.raises(ValueError):
            period_bounds(start, end, 'UTC')
    with pytest.raises(ZoneInfoNotFoundError):
        period_bounds(date(2026, 1, 1), date(2026, 1, 1), 'Invalid/Timezone')


def test_upgrade_previous_release_keeps_historical_dates_unknown(tmp_path):
    from alembic import command
    from sqlalchemy import inspect
    from vendoo_studio.services.schema_migrations import alembic_config
    url = f'sqlite:///{tmp_path / "upgrade.db"}'
    config = alembic_config(url)
    command.upgrade(config, 'f64a29c108de')
    command.upgrade(config, 'fd7c3a5d05ea')
    engine = create_engine(url)
    assert 'sent_at' in {column['name'] for column in inspect(engine).get_columns('ebay_offer_attempts')}
    assert 'ix_listing_promotions_seller_sent' in {index['name'] for index in inspect(engine).get_indexes('listing_promotions')}


def test_endpoint_validates_dates_and_returns_no_store():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vendoo_studio.database import get_db
    from vendoo_studio.routes.analytics import router
    from sqlalchemy.pool import StaticPool
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        response = client.get('/api/analytics/offers?start=2026-11-16&end=2026-11-16&timezone=UTC')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert all(row['confirmed_recipients'] is None for row in response.json()['marketplaces'])
        for query in ['start=2026-11-17&end=2026-11-16', 'start=2026-11-16&end=2026-11-16&timezone=invalid',
                      'start=2024-01-01&end=2026-11-16']:
            assert client.get('/api/analytics/offers?' + query).status_code == 400


def test_mixed_receipts_are_classified_per_send_before_aggregation():
    from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 16, 12)
    with Session(engine) as db:
        rule = EbayOfferRule(account_id='account', listing_id='listing', discount_percent=10, minimum_price_cents=100)
        db.add(rule)
        db.flush()
        for recipients in (2, 5, 0, -100):
            db.add(EbayOfferAttempt(rule_id=rule.id, current_price_cents=1000, offer_price_cents=900,
                discount_percent=10, minimum_price_cents=100, state='sent', recipient_count=recipients,
                created_at=now, sent_at=now, expires_at=now))
        # eBay's recipient column is NOT NULL; Mercari also permits absent counts.
        for recipients in (2, 5, None, 0, -100):
            db.add(ListingPromotion(batch_id='batch', marketplace='mercari', action='offer',
                listing_id='listing', state='done', confirmation='recipients_created', recipient_count=recipients,
                created_at=now, sent_at=now, expires_at=now))
        db.commit()
        statements = []
        event.listen(engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
        result = offer_analytics(db, start=now.date(), end=now.date(), timezone='UTC')
        rows = {row['marketplace']: row for row in result['marketplaces']}
        for market, unverified in [('ebay', 2), ('mercari', 3)]:
            assert rows[market]['confirmed_recipients'] == 7
            assert rows[market]['confirmed_sends'] == 2
            assert rows[market]['unverified'] == unverified
        assert len(statements) == 2


def test_missing_receipt_does_not_inherit_positive_receipt_confirmation():
    from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 16, 12)
    with Session(engine) as db:
        rule = EbayOfferRule(account_id='account', listing_id='listing', discount_percent=10, minimum_price_cents=100)
        db.add(rule)
        db.flush()
        for recipients in (2, 0):
            db.add(EbayOfferAttempt(rule_id=rule.id, current_price_cents=1000, offer_price_cents=900,
                discount_percent=10, minimum_price_cents=100, state='sent', recipient_count=recipients,
                created_at=now, sent_at=now, expires_at=now))
        for recipients in (2, None, 0):
            db.add(ListingPromotion(batch_id='batch', marketplace='mercari', action='offer',
                listing_id='listing', state='done', confirmation='recipients_created', recipient_count=recipients,
                created_at=now, sent_at=now, expires_at=now))
        db.commit()
        rows = {row['marketplace']: row for row in offer_analytics(db, start=now.date(), end=now.date(), timezone='UTC')['marketplaces']}
        for market, unverified in [('ebay', 1), ('mercari', 2)]:
            assert rows[market]['confirmed_recipients'] == 2
            assert rows[market]['confirmed_sends'] == 1
            assert rows[market]['unverified'] == unverified
