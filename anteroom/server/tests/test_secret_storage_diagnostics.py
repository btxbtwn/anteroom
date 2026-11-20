"""Credential failures retain bounded diagnostics, never the credential exception."""
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError, TimeoutError

from anteroom.services import hosted_context
from anteroom.services.error_alerts import build_report, render_email

PRIVATE = 'private-fixture-do-not-log'
LOGGER = 'anteroom.services.hosted_context'


def failure(kind=OperationalError, sqlstate='57014'):
    original = type('PrivateDriverName_' + PRIVATE, (Exception,), {})(PRIVATE)
    original.sqlstate = sqlstate
    return kind('SELECT ' + PRIVATE, {'secret': PRIVATE}, original)


@contextmanager
def session(db):
    yield db


def records(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


@pytest.mark.parametrize('kind,family,state', [
    (OperationalError, 'operational', '57014'),
    (IntegrityError, 'integrity', '23503'),
    (DBAPIError, 'database', 'unknown'),
])
def test_read_diagnostic_is_allowlisted_and_keeps_safe_public_error(caplog, kind, family, state):
    error = failure(kind, state if state != 'unknown' else PRIVATE)
    error.__cause__ = RuntimeError(PRIVATE)
    db = SimpleNamespace(scalar=Mock(side_effect=error))
    store = hosted_context.VaultSecretStore(lambda: session(db), PRIVATE)
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable) as caught:
        store.get(PRIVATE)
    assert str(caught.value) == 'Secure credential storage is temporarily unavailable'
    assert caught.value.__suppress_context__ is True
    logged = records(caplog)
    assert len(logged) == 1
    assert logged[0].getMessage() == f'Credential storage failed (operation=get; stage=read; family={family}; sqlstate={state})'
    assert logged[0].exc_info is None
    report = build_report(logged[0])
    subject, body = render_email(report, 1, None)
    assert PRIVATE not in json.dumps(report) + subject + body + str(logged[0].__dict__)
    assert report['stack'] is None
    db.scalar.assert_called_once()


@pytest.mark.parametrize('stage', ['session', 'lock', 'read', 'mutate', 'write', 'change', 'commit', 'close'])
def test_update_reports_exact_failed_stage_once_without_retry(monkeypatch, caplog, stage):
    error = TimeoutError(PRIVATE)
    db = Mock()
    db.scalar.return_value = 'old'
    calls = []

    def step(name):
        calls.append(name)
        if name == stage:
            raise error

    @contextmanager
    def sessions():
        step('session')
        yield db
        step('close')

    db.execute.side_effect = lambda statement, params: step('lock' if 'pg_advisory' in str(statement) else 'write')
    db.scalar.side_effect = lambda *args: (step('read'), 'old')[1]
    db.commit.side_effect = lambda: step('commit')
    monkeypatch.setattr(hosted_context, 'note_changes', lambda *args: step('change'))

    def mutate(previous):
        step('mutate')
        return PRIVATE

    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable):
        hosted_context.VaultSecretStore(sessions, PRIVATE).update(PRIVATE, mutate)
    logged = records(caplog)
    assert len(logged) == 1
    assert logged[0].getMessage() == f'Credential storage failed (operation=update; stage={stage}; family=pool_timeout; sqlstate=unknown)'
    assert PRIVATE not in str(logged[0].__dict__)
    assert calls.count(stage) == 1


@pytest.mark.parametrize('sqlstate', [PRIVATE, 'ZZ999', '57014\n' + PRIVATE, 57014, ['57014']])
def test_nonallowlisted_sqlstate_is_not_formatted(caplog, sqlstate):
    db = SimpleNamespace(scalar=Mock(side_effect=failure(sqlstate=sqlstate)))
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable):
        hosted_context.VaultSecretStore(lambda: session(db), PRIVATE).get(PRIVATE)
    assert records(caplog)[0].getMessage().endswith('sqlstate=unknown)')
    assert PRIVATE not in str(records(caplog)[0].__dict__)


def test_unknown_subclass_never_emits_class_name(caplog):
    error = type('PrivateSQL_' + PRIVATE, (SQLAlchemyError,), {})(PRIVATE)
    db = SimpleNamespace(scalar=Mock(side_effect=error))
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable):
        hosted_context.VaultSecretStore(lambda: session(db), PRIVATE).get(PRIVATE)
    assert 'family=sqlalchemy; sqlstate=unknown' in records(caplog)[0].getMessage()
    assert PRIVATE not in str(records(caplog)[0].__dict__)


def test_success_and_unchanged_update_do_not_log_or_add_work(monkeypatch, caplog):
    db = Mock()
    db.scalar.return_value = PRIVATE
    monkeypatch.setattr(hosted_context, 'note_changes', Mock())
    store = hosted_context.VaultSecretStore(lambda: session(db), PRIVATE)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert store.get(PRIVATE) == PRIVATE
        assert store.get(PRIVATE) == PRIVATE
        assert db.scalar.call_count == 1
        store.update(PRIVATE, lambda value: value)
    assert not records(caplog)
    db.commit.assert_not_called()
    assert db.execute.call_count == 1  # existing lock only


def test_hostile_sqlstate_property_preserves_safe_storage_failure(caplog):
    class HostileDriver(Exception):
        @property
        def sqlstate(self):
            raise RuntimeError(PRIVATE)

    error = OperationalError(PRIVATE, {'secret': PRIVATE}, HostileDriver(PRIVATE))
    db = SimpleNamespace(scalar=Mock(side_effect=error))
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable):
        hosted_context.VaultSecretStore(lambda: session(db), PRIVATE).get(PRIVATE)
    assert records(caplog)[0].getMessage().endswith('sqlstate=unknown)')
    assert PRIVATE not in str(records(caplog)[0].__dict__)


@pytest.mark.parametrize('stage', ['session', 'close'])
def test_read_session_and_close_failures_remain_generic(caplog, stage):
    @contextmanager
    def sessions():
        if stage == 'session':
            raise TimeoutError(PRIVATE)
        yield SimpleNamespace(scalar=lambda *args: PRIVATE)
        raise TimeoutError(PRIVATE)

    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(hosted_context.CredentialStorageUnavailable) as caught:
        hosted_context.VaultSecretStore(sessions, PRIVATE).get(PRIVATE)
    assert caught.value.__suppress_context__ is True
    assert records(caplog)[0].getMessage() == f'Credential storage failed (operation=get; stage={stage}; family=pool_timeout; sqlstate=unknown)'


def test_changed_update_commits_once_and_refreshes_cache_without_logging(monkeypatch, caplog):
    db = Mock()
    db.scalar.return_value = 'old'
    changes = Mock()
    monkeypatch.setattr(hosted_context, 'note_changes', changes)
    store = hosted_context.VaultSecretStore(lambda: session(db), PRIVATE)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        store.update(PRIVATE, lambda value: PRIVATE)
        assert store.get(PRIVATE) == PRIVATE
    assert not records(caplog)
    db.commit.assert_called_once()
    changes.assert_called_once()
    assert db.execute.call_count == 2  # existing lock and write
    db.scalar.assert_called_once()
