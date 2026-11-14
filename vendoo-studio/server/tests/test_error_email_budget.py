"""Durable mail accounting, batching and relay backoff on disposable databases."""
import logging
import smtplib
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine

from vendoo_studio.repositories.error_email import EmailStore
from vendoo_studio.services.error_alerts import build_report
from vendoo_studio.services.error_email_delivery import EmailDelivery


@pytest.fixture
def store(tmp_path):
    from vendoo_studio.database import engine as configured
    if configured.dialect.name == 'postgresql':
        store = EmailStore(configured)
        with configured.begin() as connection:
            connection.execute(store.table.update().values(payload={}))
        yield store
        return
    engine = create_engine(f"sqlite:///{tmp_path / 'mail.db'}", connect_args={'timeout': 30})
    table = EmailStore(engine).table
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, payload={}))
    yield EmailStore(engine)
    engine.dispose()


def report(level=logging.ERROR, line=1):
    return build_report(logging.LogRecord('vendoo_studio.jobs', level, '/jobs.py', line, 'failed', (), None))


def test_unconfigured_budget_retains_reports_without_sending(store):
    sent = []
    delivery = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=0)
    delivery.add(report())
    delivery.tick()
    assert sent == []
    assert delivery.status()['state'] == 'needs_budget'
    assert delivery.status()['pending'] == 1


def test_budget_survives_restart_and_is_shared_across_concurrent_workers(store):
    sent = []
    workers = [EmailDelivery(store, lambda *args: sent.append(args), daily_limit=4) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda item: (item[1].add(report(line=item[0])), item[1].tick()), enumerate(workers)))
    assert len(sent) <= 4
    restarted = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=4)
    for i in range(20):
        restarted.add(report(line=100+i))
        restarted.tick()
    assert len(sent) == 4
    assert restarted.status()['used'] == 4
    assert restarted.status()['pending'] > 0


def test_warning_digest_waits_and_leaves_budget_for_errors(store):
    sent, now = [], [100.0]
    delivery = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=4, clock=lambda: now[0])
    for i in range(10):
        delivery.add(report(logging.WARNING, i))
    delivery.tick()
    assert not sent
    now[0] += 3600
    delivery.tick()
    assert len(sent) == 1 and '10 occurrences' in sent[0][1]
    for _ in range(4):
        delivery.add(report(logging.WARNING))
        now[0] += 3601
        delivery.tick()
    assert len(sent) == 3
    delivery.add(report())
    delivery.tick()
    assert len(sent) == 4


def test_quota_rejection_pauses_every_worker_without_discarding_reports(store):
    now, attempts = [100.0], []

    def reject(*args):
        attempts.append(args)
        raise smtplib.SMTPDataError(550, b'5.7.1 Daily SMTP relay limit exceeded for this customer')

    delivery = EmailDelivery(store, reject, daily_limit=20, clock=lambda: now[0])
    delivery.add(report())
    delivery.tick()
    restarted = EmailDelivery(store, reject, daily_limit=20, clock=lambda: now[0])
    restarted.add(report(line=2))
    restarted.tick()
    assert len(attempts) == 1
    assert restarted.status()['state'] == 'quota_paused'
    assert restarted.status()['pending'] == 2
    now[0] += 86401
    restarted.tick()
    assert len(attempts) == 2


def test_rolling_budget_expires_and_failed_sends_count_conservatively(store):
    now = [100.0]
    delivery = EmailDelivery(store, lambda *args: None, daily_limit=1, clock=lambda: now[0])
    delivery.add(report())
    delivery.tick()
    delivery.add(report(line=2))
    delivery.tick()
    assert delivery.status()['used'] == 1
    now[0] += 86401
    delivery.tick()
    assert delivery.status()['pending'] == 0
    assert delivery.status()['used'] == 1


def test_slow_send_does_not_hold_a_connection_or_lose_new_occurrences(store):
    import threading

    entered, finish = threading.Event(), threading.Event()

    def send(*args):
        entered.set()
        assert finish.wait(5)

    delivery = EmailDelivery(store, send, daily_limit=10)
    delivery.add(report())
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(delivery.tick)
        assert entered.wait(5)
        try:
            delivery.add(report())
            delivery.add(report(line=2))
            assert delivery.status()['pending'] == 3
        finally:
            finish.set()
        task.result()
    assert delivery.status()['pending'] == 2


def test_errors_do_not_wait_for_a_warning_with_the_same_fingerprint(store):
    sent = []
    delivery = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=10)
    delivery.add(report(logging.WARNING))
    delivery.add(report(logging.ERROR))
    delivery.tick()
    assert len(sent) == 1
    assert 'ERROR' in sent[0][0]
    assert delivery.status()['pending'] == 1


def test_operator_storage_is_inaccessible_to_seller_role(store):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    if store.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres role enforcement')
    with store.engine.begin() as connection:
        connection.execute(text('SET LOCAL ROLE anteroom_seller'))
        with pytest.raises(DBAPIError, match='permission denied'):
            connection.execute(text('SELECT payload FROM error_email_state'))
    with store.engine.begin() as connection:
        assert connection.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid='error_email_state'::regclass"))
        if connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
            connection.execute(text('SET LOCAL ROLE anteroom_api'))
            assert connection.execute(text('SELECT id FROM error_email_state')).scalar_one() == 1
            assert connection.execute(text('UPDATE error_email_state SET payload=payload WHERE id=1')).rowcount == 1


def test_warning_backlog_is_bounded_per_message_and_waits_for_next_digest(store):
    now, sent = [100.0], []
    delivery = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=20, clock=lambda: now[0])
    for line in range(25):
        delivery.add(report(logging.WARNING, line=line))
    now[0] += 3600
    delivery.tick()
    delivery.tick()
    assert len(sent) == 1
    assert '20 occurrences' in sent[0][1]
    assert delivery.status()['pending'] == 5
    now[0] += 3600
    delivery.tick()
    assert len(sent) == 2
    assert delivery.status()['pending'] == 0


def test_crash_reservation_survives_restart_and_is_counted_again_on_retry(store):
    class ProcessStopped(BaseException):
        pass

    now, sent = [100.0], []

    def crash(*args):
        raise ProcessStopped()

    delivery = EmailDelivery(store, crash, daily_limit=4, clock=lambda: now[0])
    delivery.add(report())
    with pytest.raises(ProcessStopped):
        delivery.tick()
    restarted = EmailDelivery(store, lambda *args: sent.append(args), daily_limit=4, clock=lambda: now[0])
    restarted.tick()
    assert not sent
    assert restarted.status()['used'] == 1
    now[0] += 121
    restarted.tick()
    assert len(sent) == 1
    assert restarted.status()['used'] == 2
    assert restarted.status()['pending'] == 0


def test_transient_delivery_recovers_without_another_error_arriving(store):
    now, attempts = [100.0], []

    def send(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise OSError('temporary network failure')

    delivery = EmailDelivery(store, send, daily_limit=4, clock=lambda: now[0])
    delivery.add(report())
    delivery.tick()
    now[0] += 299
    delivery.tick()
    assert len(attempts) == 1
    now[0] += 2
    delivery.tick()
    assert len(attempts) == 2
    assert delivery.status()['state'] == 'ready'
    assert delivery.status()['used'] == 2
    assert delivery.status()['pending'] == 0


@pytest.mark.parametrize('level', [logging.ERROR, logging.WARNING])
def test_unconfigured_budget_preserves_every_unsent_group(store, level):
    delivery = EmailDelivery(store, lambda *args: None, daily_limit=0)
    reports = [report(level, line=line) for line in range(80)]
    reports.append(report(logging.WARNING if level == logging.ERROR else logging.ERROR, line=9999))
    for item in reports:
        delivery.add(item)
    delivery.tick()
    groups = store.read()['groups']
    assert set(groups) == {item['severity'] + ':' + item['fingerprint'] for item in reports}
    assert delivery.status()['pending'] == len(reports)
    assert delivery.status()['state'] == 'needs_budget'


@pytest.mark.parametrize('fail_send', [False, True])
def test_add_during_send_preserves_backlog_and_occurrence_counts(store, fail_send):
    sent = []
    initial = [report(line=line) for line in range(50)]
    added = [report(line=line) for line in range(50, 70)]

    def send(*args):
        sent.append(args)
        for item in added:
            delivery.add(item)
        delivery.add(initial[0])
        if fail_send:
            raise OSError('temporary network failure')

    delivery = EmailDelivery(store, send, daily_limit=10, clock=lambda: 100.0)
    for item in initial:
        delivery.add(item)
    delivery.tick()
    groups = store.read()['groups']
    expected = initial + added if fail_send else [initial[0], *initial[20:], *added]
    assert set(groups) == {item['severity'] + ':' + item['fingerprint'] for item in expected}
    first_key = initial[0]['severity'] + ':' + initial[0]['fingerprint']
    assert groups[first_key]['count'] == (2 if fail_send else 1)
    assert delivery.status()['pending'] == (71 if fail_send else 51)
    assert len(sent) == 1
    assert store.read()['lease_until'] == 0
