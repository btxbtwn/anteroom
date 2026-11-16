"""Rolling old/new mail workers, migrations and operator-only storage.

Legacy fixtures are frozen from main 2a257afb (before per-group storage). Only
its store's table declaration is isolated from today's ORM model.
"""
import logging
import runpy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from vendoo_studio.repositories.error_email import EmailStore
from vendoo_studio.services.error_alerts import build_report
from vendoo_studio.services.error_email_delivery import EmailDelivery
from vendoo_studio.services.schema_migrations import alembic_config

LEGACY = Path(__file__).parent / 'fixtures' / 'error_email_legacy'
LegacyStore = runpy.run_path(str(LEGACY / 'store.py'))['EmailStore']
LegacyDelivery = runpy.run_path(str(LEGACY / 'delivery.py'))['EmailDelivery']


def report(line):
    return build_report(logging.LogRecord('vendoo_studio.jobs', logging.ERROR, '/jobs.py', line, 'failed', (), None))


@pytest.fixture
def previous_engine(tmp_path):
    from vendoo_studio.database import engine as configured

    if configured.dialect.name == 'sqlite':
        engine = create_engine(f"sqlite:///{tmp_path / 'mail.db'}")
        command.upgrade(alembic_config(str(engine.url)), 'c3f1a9d27e64')
        yield engine
        engine.dispose()
        return
    name = 'mail_migration_' + uuid.uuid4().hex
    admin = configured.execution_options(isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(configured.url.set(database=name))
    try:
        command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), 'c3f1a9d27e64')
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def migrate(engine):
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), 'head')


def test_migration_preserves_backlog_budget_pause_and_inflight_lease(previous_engine):
    old = LegacyStore(previous_engine)
    delivery = LegacyDelivery(old, lambda *args: None, daily_limit=0, clock=lambda: 100)
    for line in range(80):
        delivery.add(report(line))
        delivery.add(report(line))
    with old.transaction() as state:
        state.update(attempts=[1, 2], lease='keep-token', lease_until=220, paused_until=500,
                     failure='delivery_failed', last_sent={'ERROR:sent-only': 90})
    expected = old.read()
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    inserts = []

    def record_insert(connection, cursor, statement, parameters, context, executemany):
        if connection.engine.url.database == previous_engine.url.database and statement.startswith('INSERT INTO error_email_groups'):
            inserts.append(statement)

    event.listen(Engine, 'before_cursor_execute', record_insert)
    try:
        migrate(previous_engine)
    finally:
        event.remove(Engine, 'before_cursor_execute', record_insert)
    assert len(inserts) <= 2, 'backfill must batch pending groups and sent-only history'
    new = EmailStore(previous_engine)
    assert new.read() == expected
    assert new.summary() == ({key: value for key, value in expected.items() if key not in ('groups', 'last_sent')}, 160)
    with previous_engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM error_email_groups')) == 81
    assert EmailDelivery(new, lambda *args: pytest.fail('paused'), daily_limit=10, clock=lambda: 100).status()['state'] == 'delivery_failed'
    EmailDelivery(new, lambda *args: None, daily_limit=0).add(report(0))
    assert 'ERROR:sent-only' not in old.read()['groups']
    assert new.summary()[1] == 161


@pytest.mark.parametrize('old_sender', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_old_and_new_workers_preserve_adds_during_send(previous_engine, old_sender, failure):
    old = LegacyStore(previous_engine)
    for line in range(50):
        LegacyDelivery(old, lambda *args: None, daily_limit=0, clock=lambda: 100).add(report(line))
    migrate(previous_engine)
    new = EmailStore(previous_engine)
    writer = EmailDelivery(new, lambda *args: None, daily_limit=0, clock=lambda: 100) if old_sender else LegacyDelivery(
        old, lambda *args: None, daily_limit=0, clock=lambda: 100)

    def send(*args):
        for line in range(50, 70):
            writer.add(report(line))
        writer.add(report(0))
        if failure:
            raise OSError('fake failure')

    sender = (LegacyDelivery if old_sender else EmailDelivery)(old if old_sender else new, send, daily_limit=10, clock=lambda: 100)
    sender.tick()
    assert sender.status()['pending'] == (71 if failure else 51)
    assert EmailDelivery(new, send, daily_limit=10, clock=lambda: 100).status()['pending'] == sender.status()['pending']
    assert old.read()['lease_until'] == 0
    assert old.read()['attempts'] == [100]
    with previous_engine.connect() as connection:
        assert connection.scalar(text("SELECT sum(CAST(payload->>'count' AS BIGINT)) FROM error_email_groups")) == (71 if failure else 51)


def test_migration_while_an_old_worker_is_outside_transaction_in_smtp(previous_engine):
    old = LegacyStore(previous_engine)

    def send(*args):
        migrate(previous_engine)
        new = EmailDelivery(EmailStore(previous_engine), lambda *args: None, daily_limit=10, clock=lambda: 100)
        new.add(report(1))
        new.add(report(2))
        # The old worker's already-reserved lease prevents a second send.
        new.tick()
        assert new.status()['used'] == 1

    delivery = LegacyDelivery(old, send, daily_limit=10, clock=lambda: 100)
    delivery.add(report(1))
    delivery.tick()
    assert delivery.status()['pending'] == 2
    assert EmailStore(previous_engine).summary()[1] == 2


def test_mixed_workers_increment_atomically_and_rollback_together(previous_engine):
    migrate(previous_engine)
    new, old = EmailStore(previous_engine), LegacyStore(previous_engine)
    workers = [factory(store, lambda *args: None, daily_limit=0) for factory, store in
               [(EmailDelivery, new), (LegacyDelivery, old)] * 4]

    def add(worker):
        for _ in range(20):
            worker.add(report(1))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, workers))
    assert new.summary()[1] == 160
    before = old.read()
    with pytest.raises(RuntimeError, match='abort'):
        with new.transaction() as transaction:
            transaction.put_group('ERROR:rollback', {'report': report(2), 'count': 5, 'first': 'first', 'due': 100}, None)
            raise RuntimeError('abort')
    assert old.read() == before
    assert new.summary()[1] == 160


def test_group_storage_denies_sellers_and_supports_old_api_role_writes(previous_engine):
    if previous_engine.dialect.name != 'postgresql':
        pytest.skip('Postgres role enforcement')
    migrate(previous_engine)
    from sqlalchemy.exc import DBAPIError

    with previous_engine.begin() as connection:
        connection.execute(text('SET LOCAL ROLE anteroom_seller'))
        with pytest.raises(DBAPIError, match='permission denied'):
            connection.execute(text('SELECT * FROM error_email_groups'))
    with previous_engine.begin() as connection:
        assert connection.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid='error_email_groups'::regclass"))
        if connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
            connection.execute(text('SET LOCAL ROLE anteroom_api'))
            # An old writer changes only payload; its invoker trigger needs the
            # same explicit group-table/sequence grants as the new repository.
            import json
            connection.execute(text('UPDATE error_email_state SET payload = CAST(:payload AS JSON)'),
                               {'payload': json.dumps({'groups': {'ERROR:test': {
                                   'report': report(3), 'count': 3, 'first': 'first', 'due': 100}}})})
            assert connection.scalar(text('SELECT count(*) FROM error_email_groups')) == 1

    from sqlalchemy import event

    def api_role(connection):
        connection.exec_driver_sql('SET LOCAL ROLE anteroom_api')

    event.listen(previous_engine, 'begin', api_role)
    try:
        new = EmailDelivery(EmailStore(previous_engine), lambda *args: None, daily_limit=10, clock=lambda: 100)
        legacy = LegacyDelivery(LegacyStore(previous_engine), lambda *args: None, daily_limit=10, clock=lambda: 100)
        new.add(report(1))
        legacy.add(report(2))
        assert new.status()['pending'] == 5
        new.tick()
        assert new.status()['pending'] == 0
    finally:
        event.remove(previous_engine, 'begin', api_role)


class ResultBytes:
    """Count returned scalar encodings, excluding wire framing, TLS and billing."""

    def __init__(self):
        self.bytes = 0
        self.queries = 0

    def observe(self, conn, cursor, statement, parameters, context, executemany):
        if not statement.lstrip().upper().startswith('SELECT'):
            return
        self.queries += 1
        counter = self

        class Cursor:
            def __getattr__(self, name):
                return getattr(cursor, name)

            def count(self, row):
                if row is not None:
                    import json
                    counter.bytes += sum(len(value.encode()) if isinstance(value, str) else
                                         len(json.dumps(value, default=str).encode()) for value in row)
                return row

            def fetchone(self):
                return self.count(cursor.fetchone())

            def fetchmany(self, *args):
                return [self.count(row) for row in cursor.fetchmany(*args)]

            def fetchall(self):
                return [self.count(row) for row in cursor.fetchall()]

        context.cursor = Cursor()


@pytest.mark.parametrize("group_count", [100, 1000])
def test_idle_read_payload_is_independent_of_unrelated_backlog(previous_engine, group_count):
    import copy
    import statistics
    import time
    from sqlalchemy import event

    old = LegacyStore(previous_engine)
    reports = []
    for line in range(group_count):
        item = report(line)
        item['severity'] = 'WARNING'
        item['message'] = 'synthetic diagnostic ' + ('x' * 1024)
        reports.append(item)
    with old.transaction() as state:
        state['digest_after'] = 10000
        state['groups'] = {'WARNING:' + item['fingerprint']: {
            'report': item, 'count': 1, 'first': item['occurred_at'], 'due': 10000} for item in reports}
    results = {}
    for name, factory, store in [('legacy', LegacyDelivery, old), ('groups', EmailDelivery, EmailStore(previous_engine))]:
        if name == 'groups':
            migrate(previous_engine)
        delivery = factory(store, lambda *args: pytest.fail('nothing is due'), daily_limit=10, clock=lambda: 100)
        counter, timings = ResultBytes(), []
        event.listen(previous_engine, 'after_cursor_execute', counter.observe)
        try:
            for _ in range(20):
                started = time.perf_counter()
                delivery.add(copy.deepcopy(reports[0]))
                assert delivery.status()['pending'] >= group_count
                delivery.tick()
                timings.append((time.perf_counter() - started) * 1000)
        finally:
            event.remove(previous_engine, 'after_cursor_execute', counter.observe)
        results[name] = {'returned_value_bytes': counter.bytes, 'selects': counter.queries,
                         'cycles': len(timings), 'p50_ms': statistics.median(timings),
                         'p95_ms': sorted(timings)[18]}
    print('\nOUTBOX_SYNTHETIC_READS', previous_engine.dialect.name, group_count, results)
    assert results['legacy']['returned_value_bytes'] > 20 * group_count * 1024 * 2
    assert results['groups']['returned_value_bytes'] < results['legacy']['returned_value_bytes'] / 100
    assert results['groups']['returned_value_bytes'] < 50000
    assert EmailStore(previous_engine).summary()[1] == group_count + 40


def test_sqlite_backfill_fences_old_adds_until_trigger_install(previous_engine):
    import threading
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    if previous_engine.dialect.name != 'sqlite':
        pytest.skip('SQLite legacy transaction mode')
    old = LegacyStore(previous_engine)
    delivery = LegacyDelivery(old, lambda *args: None, daily_limit=0)
    delivery.add(report(1))
    main_thread = threading.get_ident()
    attempted = threading.Event()
    tasks = []
    saw_snapshot = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        def checkpoint(connection, cursor, statement, parameters, context, executemany):
            if connection.engine.url.database != previous_engine.url.database:
                return
            if threading.get_ident() != main_thread:
                if statement == 'BEGIN IMMEDIATE':
                    attempted.set()
                return
            if statement.startswith('SELECT error_email_state.payload'):
                # Python sqlite3 legacy mode does not BEGIN on DDL or SELECT.
                # Without the migration's early write, this snapshot can race
                # an old add before the projection trigger has been installed.
                assert connection.connection.driver_connection.in_transaction
                saw_snapshot.append(True)
            if statement.startswith('INSERT INTO error_email_groups') and not tasks:
                tasks.append(executor.submit(delivery.add, report(99)))
                assert attempted.wait(5)
                assert not tasks[0].done()

        event.listen(Engine, 'before_cursor_execute', checkpoint)
        try:
            migrate(previous_engine)
        finally:
            event.remove(Engine, 'before_cursor_execute', checkpoint)
        assert saw_snapshot and len(tasks) == 1
        tasks[0].result(timeout=5)
    new = EmailDelivery(EmailStore(previous_engine), lambda *args: None, daily_limit=0)
    assert new.status()['pending'] == delivery.status()['pending'] == 2
    new.add(report(1))
    assert new.status()['pending'] == delivery.status()['pending'] == 3


def test_rolling_mirror_and_old_reenqueue_preserve_fifo(previous_engine):
    old = LegacyStore(previous_engine)
    clock = [100.0]
    legacy = LegacyDelivery(old, lambda *args: None, daily_limit=0, clock=lambda: clock[0])
    for line in range(50):
        legacy.add(report(line))
    original_order = list(old.read()['groups'])
    migrate(previous_engine)
    current = EmailDelivery(EmailStore(previous_engine), lambda *args: None, daily_limit=10, clock=lambda: clock[0])
    current.add(report(49))
    assert list(old.read()['groups']) == original_order
    current.tick()
    legacy.add(report(0))
    assert list(old.read()['groups']) == [*original_order[20:], original_order[0]]
    clock[0] += 900
    current.tick()
    assert list(old.read()['groups']) == [*original_order[40:], original_order[0]]
