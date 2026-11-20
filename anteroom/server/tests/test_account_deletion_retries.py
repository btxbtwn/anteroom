"""Deletion retries must replay the complete rolled-back database phase."""
from unittest.mock import MagicMock

import pytest
from psycopg.errors import DeadlockDetected, SerializationFailure
from sqlalchemy.exc import OperationalError

from anteroom.services import account_deletion


@pytest.mark.parametrize('phase', ['_delete_records', '_finish_deletion'])
@pytest.mark.parametrize('failures', [1, 2, 3])
def test_deadlock_replays_phase_in_fresh_session(monkeypatch, phase, failures):
    sessions = [MagicMock() for _ in range(3)]
    error = OperationalError('commit', {}, DeadlockDetected('deadlock'))
    for index, db in enumerate(sessions):
        db.__enter__.return_value = db
        db.scalars.return_value = ['owner/photo.jpg']
        if index < failures:
            db.commit.side_effect = error
    factory = MagicMock(side_effect=sessions)
    check = MagicMock()
    monkeypatch.setattr(account_deletion, '_session', factory)
    monkeypatch.setattr(account_deletion, '_check', check)
    monkeypatch.setattr(account_deletion, 'seller_tables', lambda: [])
    operation = getattr(account_deletion, phase)
    if failures == 3:
        with pytest.raises(OperationalError):
            operation('owner')
    else:
        result = operation('owner')
        assert result == (['owner/photo.jpg'] if phase == '_delete_records' else None)
    attempts = min(failures + 1, 3)
    assert factory.call_count == check.call_count == attempts
    for db in sessions[:attempts]:
        db.__exit__.assert_called_once()
        db.commit.assert_called_once()
        statements = [str(call.args[0]) for call in db.execute.call_args_list]
        assert 'pg_advisory_xact_lock' in statements[0]
        if phase == '_delete_records':
            db.scalars.assert_called_once()
            assert 'stage_account_deletion' in str(db.scalars.call_args.args[0])
        else:
            assert 'finish_account_deletion' in statements[-1]


def test_other_database_errors_are_not_retried(monkeypatch):
    db = MagicMock()
    db.__enter__.return_value = db
    db.commit.side_effect = OperationalError('commit', {}, SerializationFailure('serialization'))
    factory = MagicMock(return_value=db)
    monkeypatch.setattr(account_deletion, '_session', factory)
    monkeypatch.setattr(account_deletion, '_check', MagicMock())
    with pytest.raises(OperationalError):
        account_deletion._finish_deletion('owner')
    factory.assert_called_once()
