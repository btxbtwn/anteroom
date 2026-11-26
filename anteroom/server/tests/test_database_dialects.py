"""The app's database works the same on the Mac's SQLite and on hosted Postgres.

Run on whichever database the suite targets; set ANTEROOM_TEST_DATABASE_URL to
exercise Postgres.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from anteroom.database import Base, SessionLocal, database_url, engine, json_text, json_value, load_models
from anteroom.models.conversation import Conversation
from anteroom.models.listing import Listing, ListingRevision


def test_postgres_bursts_can_hold_more_than_five_connections_and_release_them():
    from anteroom.database import make_engine

    if engine.dialect.name != 'postgresql':
        pytest.skip('Exercises the real Postgres connection pool')
    isolated = make_engine(engine.url.render_as_string(hide_password=False))
    try:
        with ExitStack() as stack:
            connections = [stack.enter_context(isolated.connect()) for _ in range(8)]
            drivers = [connection.connection.driver_connection for connection in connections]
            backends = {connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
                        for connection in connections}
            assert len(backends) == 8
        # Closing a request must release its client connection to the external
        # pooler, rather than leave idle connections reserved by this process.
        assert all(driver.closed for driver in drivers)
        with isolated.connect() as connection:
            assert connection.exec_driver_sql('SELECT 1').scalar_one() == 1
    finally:
        isolated.dispose()


def test_migrations_leave_the_schema_the_models_describe():
    load_models()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_postgres_repeated_queries_do_not_require_session_prepared_statements():
    from anteroom.database import make_engine

    if engine.dialect.name != 'postgresql':
        pytest.skip('Exercises psycopg automatic statement preparation')
    isolated = make_engine(engine.url.render_as_string(hide_password=False))
    try:
        with isolated.connect() as connection:
            for value in range(12):
                assert connection.exec_driver_sql('SELECT %s::integer', (value,)).scalar_one() == value
                connection.commit()
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM pg_prepared_statements WHERE left(name, 5) = '_pg3_'"
            ).scalar_one() == 0
    finally:
        isolated.dispose()


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


class _Captured(Exception):
    pass


def _driver_arguments(url: str) -> dict:
    """The keyword arguments a new connection hands psycopg, without connecting."""
    from sqlalchemy import event

    from anteroom.database import make_engine

    isolated = make_engine(url)
    captured: dict = {}

    @event.listens_for(isolated, 'do_connect')
    def capture(_dialect, _record, _cargs, cparams):
        captured.update(cparams)
        raise _Captured

    try:
        with pytest.raises(_Captured):
            isolated.connect()
    finally:
        isolated.dispose()
    return captured


@pytest.mark.parametrize('hosted', ['1', ''])
def test_postgres_connections_fail_fast_when_the_network_stops_answering(monkeypatch, hosted):
    # A connection to Supabase that stops delivering packets held request and
    # automation threads until Postgres ended the idle transaction (30 s), the
    # connect gave up (~2 min) or the kernel abandoned the socket (~15 min) (#2266).
    from anteroom.services.hosted_database import IDLE_IN_TRANSACTION_TIMEOUT

    monkeypatch.setenv('ANTEROOM_HOSTED', hosted)
    for url in ('postgresql+psycopg://u:p@pooler.example:6543/postgres',
                'postgresql+psycopg://u:p@pooler.example:5432/postgres'):
        arguments = _driver_arguments(url)
        assert arguments['connect_timeout'] <= 10
        assert arguments['keepalives'] == 1
        assert arguments['keepalives_idle'] + arguments['keepalives_interval'] * arguments['keepalives_count'] <= 30
        # Unacknowledged data must fail the connection before Postgres ends the
        # transaction as idle, so the client sees why instead of a dead session.
        assert arguments['tcp_user_timeout'] < int(IDLE_IN_TRANSACTION_TIMEOUT.removesuffix('s')) * 1000


def test_task_signals_listener_carries_the_network_deadlines(monkeypatch):
    import psycopg

    from anteroom.database import POSTGRES_NETWORK_DEADLINES
    from anteroom.services.task_signals import TaskSignals

    captured: dict = {}

    def capture(_url, **kwargs):
        captured.update(kwargs)
        raise _Captured

    monkeypatch.setattr(psycopg, 'connect', capture)
    with pytest.raises(_Captured):
        TaskSignals('postgresql+psycopg://u:p@pooler.example:6543/postgres')._listen()
    for key, expected in POSTGRES_NETWORK_DEADLINES.items():
        assert captured[key] == expected


def test_postgres_tcp_sockets_carry_the_network_deadlines():
    import socket
    import sys

    from anteroom.database import make_engine

    if engine.dialect.name != 'postgresql' or not sys.platform.startswith('linux'):
        pytest.skip('Reads Linux TCP socket options on a real Postgres connection')
    isolated = make_engine(engine.url.render_as_string(hide_password=False))
    try:
        with isolated.connect() as connection:
            driver = connection.connection.driver_connection
            sock = socket.socket(fileno=driver.fileno())
            try:
                if sock.family == socket.AF_UNIX:
                    pytest.skip('The test database is reached over a Unix socket')
                assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
                assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 10
                assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 20_000
            finally:
                sock.detach()
    finally:
        isolated.dispose()
