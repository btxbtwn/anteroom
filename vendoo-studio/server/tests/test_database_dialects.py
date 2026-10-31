"""The app's database works the same on the Mac's SQLite and on hosted Postgres.

Run on whichever database the suite targets; set ANTEROOM_TEST_DATABASE_URL to
exercise Postgres.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from sqlalchemy.exc import TimeoutError
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from vendoo_studio.database import Base, SessionLocal, database_url, engine, json_text, json_value, load_models
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.listing_evidence import SaleSnapshot
from vendoo_studio.services.listing_evidence import capture_sale_snapshot


def test_postgres_bursts_wait_for_a_connection_instead_of_exceeding_session_limit():
    from vendoo_studio.database import make_engine

    if engine.dialect.name != 'postgresql':
        pytest.skip('Exercises the real Postgres connection pool')
    isolated = make_engine(engine.url)
    isolated.pool._timeout = 0.05
    try:
        with ExitStack() as stack:
            for _ in range(5):
                stack.enter_context(isolated.connect())
            with pytest.raises(TimeoutError):
                isolated.connect()
        # Returning a checkout makes the next request succeed again.
        with isolated.connect() as connection:
            assert connection.exec_driver_sql('SELECT 1').scalar_one() == 1
    finally:
        isolated.dispose()


def test_migrations_leave_the_schema_the_models_describe():
    load_models()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_hosted_urls_use_the_psycopg_driver():
    assert database_url("postgres://u:p%40ss@db.example:5432/app") == "postgresql+psycopg://u:p%40ss@db.example:5432/app"
    assert database_url("postgresql://u@h/app").startswith("postgresql+psycopg://")
    assert database_url("sqlite:////tmp/x.db") == "sqlite:////tmp/x.db"


def test_json_values_come_back_typed_and_comparable():
    doc = {"brand": " Levi's ", "price": 12.5, "quantity": 1, "etsy_specifics": {"who": "I did"},
           "category_path": ["Men", "Jeans"], "ebay_specifics": {"season": "Fall"}}
    with SessionLocal() as db:
        conv = Conversation(title="dialect check")
        db.add(conv)
        db.flush()
        revision = ListingRevision(conversation_id=conv.id, listing_json=doc, source="test")
        db.add(revision)
        db.flush()
        db.add(Listing(conversation_id=conv.id, current_revision_id=revision.id))
        db.flush()
        column = ListingRevision.listing_json
        row = db.query(
            json_value(column, "price"), json_value(column, "quantity"), json_value(column, "etsy_specifics"),
            json_value(column, "category_path"), json_value(column, "ebay_specifics.season"),
            json_value(column, "missing"),
        ).filter(ListingRevision.id == revision.id).one()
        assert tuple(row) == (12.5, 1, {"who": "I did"}, ["Men", "Jeans"], "Fall", None)
        matched = db.query(ListingRevision.id).filter(
            json_text(column, "brand").isnot(None),
            ListingRevision.id == revision.id,
        ).scalar()
        assert matched == revision.id
        db.rollback()


def test_a_sale_is_snapshotted_once_even_when_recorded_twice(monkeypatch):
    from vendoo_studio.services import vendoo_import

    monkeypatch.setattr(vendoo_import, "vendoo_item_sold", lambda item, form: True)
    monkeypatch.setattr(vendoo_import, "vendoo_sale", lambda item, form: {"marketplace": "ebay"})
    monkeypatch.setattr(vendoo_import, "vendoo_dates", lambda item, form: {"sold": "2026-10-01T12:00:00Z"})
    monkeypatch.setattr(vendoo_import, "listing_from_vendoo", lambda item, form: {"title": "Tee"})
    with SessionLocal() as db:
        conv = Conversation(title="snapshot check")
        db.add(conv)
        db.flush()
        capture_sale_snapshot(db, conv.id, {"id": "x"})
        capture_sale_snapshot(db, conv.id, {"id": "x"})
        assert db.query(SaleSnapshot).filter_by(conversation_id=conv.id).count() == 1
        db.rollback()
