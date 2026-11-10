"""Exhaustion reports retain bounded, data-free checkout evidence at failure time."""
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as PoolTimeout

from vendoo_studio import database
from vendoo_studio.services.error_alerts import build_report, render_email


def pool_engine():
    return create_engine('sqlite://', poolclass=database.DiagnosticQueuePool,
                         pool_size=1, max_overflow=0, pool_timeout=0.001,
                         connect_args={'check_same_thread': False})


def exhaust(engine):
    try:
        with engine.connect():
            pytest.fail('pool should be exhausted')
    except PoolTimeout:
        return logging.LogRecord('vendoo_studio.http', logging.ERROR, __file__, 1,
                                 'Request failed', (), sys.exc_info())


def test_timeout_email_keeps_holder_snapshot_after_connection_is_returned():
    engine = pool_engine()
    with ThreadPoolExecutor(max_workers=1) as executor:
        connection = executor.submit(engine.connect).result()
        try:
            record = exhaust(engine)
        finally:
            connection.close()
    report = build_report(record)
    diagnostics = report['database_pool']
    assert diagnostics['checked_out'] == 1
    assert diagnostics['pool_size'] == 1
    assert diagnostics['tracked_holders'] == 1
    assert diagnostics['holders'][0]['held_seconds'] >= 0
    assert diagnostics['holders'][0]['execution'] == 'thread'
    assert diagnostics['captured_at']
    _, body = render_email(report, 0, None)
    assert 'Database pool at timeout' in body
    assert 'checked_out' in body
    # A later failure must not report the previous owner.
    with engine.connect():
        newer = build_report(exhaust(engine))['database_pool']
        assert newer['tracked_holders'] == 1
    engine.dispose()


def test_checkout_frames_never_include_sql_locals_or_source():
    engine = pool_engine()
    # Simulate an application callsite with private locals and a source line.
    namespace = {'engine': engine}
    exec(compile('def hold():\n secret = "private-token"\n return engine.connect()\n',
                 '/app/vendoo_studio/services/example.py', 'exec'), namespace)
    connection = namespace['hold']()
    try:
        report = build_report(exhaust(engine))
        serialized = json.dumps(report['database_pool'])
        assert 'example.py:3 in hold' in serialized
        assert 'private-token' not in serialized
        assert 'engine.connect' not in serialized
        assert '/app/' not in serialized
    finally:
        connection.close()
        engine.dispose()


def test_non_pool_errors_do_not_have_diagnostics():
    record = logging.LogRecord('vendoo_studio.http', logging.ERROR, __file__, 1, 'other', (), None)
    assert build_report(record).get('database_pool') is None


def test_readiness_email_reports_database_failure_not_only_503(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from vendoo_studio.hosted import create_app

    def unavailable():
        raise PoolTimeout('QueuePool exhausted')

    monkeypatch.setattr(database, 'engine', SimpleNamespace(
        dialect=SimpleNamespace(name='postgresql'), connect=unavailable))
    reports = []
    handler = logging.Handler(logging.ERROR)
    handler.emit = lambda record: reports.append(build_report(record))
    logger = logging.getLogger('vendoo_studio.http')
    logger.addHandler(handler)
    try:
        response = TestClient(create_app()).get('/api/ready')
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 503
    assert response.json() == {'status': 'unavailable'}
    [report] = reports
    assert report['exception']['type'] == 'sqlalchemy.exc.TimeoutError'


def test_invalidation_detach_and_dispose_do_not_retain_old_holders():
    engine = pool_engine()
    connection = engine.connect()
    connection.invalidate()
    with engine.connect():
        assert build_report(exhaust(engine))['database_pool']['tracked_holders'] == 1
    connection.close()
    connection = engine.connect()
    connection.detach()
    with engine.connect():
        assert build_report(exhaust(engine))['database_pool']['tracked_holders'] == 1
    connection.close()
    engine.dispose()
    with engine.connect():
        assert build_report(exhaust(engine))['database_pool']['tracked_holders'] == 1
    engine.dispose()


def test_diagnostic_list_is_bounded_without_changing_fingerprint():
    engine = create_engine('sqlite://', poolclass=database.DiagnosticQueuePool,
                           pool_size=22, max_overflow=0, pool_timeout=0.001)
    connections = [engine.connect() for _ in range(22)]
    try:
        first = build_report(exhaust(engine))
        second = build_report(exhaust(engine))
        assert first['database_pool']['tracked_holders'] == 22
        assert len(first['database_pool']['holders']) == 20
        assert first['database_pool']['omitted_holders'] == 2
        assert first['fingerprint'] == second['fingerprint']
    finally:
        for connection in connections:
            connection.close()
        engine.dispose()


def test_hosted_engine_uses_diagnostics_without_changing_pool_budget(monkeypatch):
    monkeypatch.setenv('ANTEROOM_HOSTED', '1')
    for name in ('ANTEROOM_DB_POOL_SIZE', 'ANTEROOM_DB_MAX_OVERFLOW', 'ANTEROOM_DB_POOL_TIMEOUT'):
        monkeypatch.delenv(name, raising=False)
    engine = database.make_engine('postgresql://unused@localhost/unused')
    assert isinstance(engine.pool, database.DiagnosticQueuePool)
    assert engine.pool.size() == 8
    assert engine.pool.timeout() == 3
    engine.dispose()


def test_pool_fingerprint_matches_pre_diagnostic_pool():
    from sqlalchemy.pool import QueuePool

    fingerprints = []
    for pool_type in (QueuePool, database.DiagnosticQueuePool):
        engine = create_engine('sqlite://', poolclass=pool_type, pool_size=1,
                               max_overflow=0, pool_timeout=0.001)
        with engine.connect():
            fingerprints.append(build_report(exhaust(engine))['fingerprint'])
        engine.dispose()
    assert fingerprints[0] == fingerprints[1]
