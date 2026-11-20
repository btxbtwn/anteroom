import pytest
from anteroom.services import assistant_changes, listing_persistence
from test_business_assistant import workspace, seed  # noqa: F401


@pytest.mark.parametrize('status', ['proposed', 'dismissed'])
def test_partial_history_identifies_completed_prefix(status):
    note = assistant_changes.history_note({'status': status, 'completed': 2, 'items': [
        {'listing_id': 'a', 'set': {'asking': 20}},
        {'listing_id': 'b', 'set': {'asking': 30}, 'skipped': 'busy'},
        {'listing_id': 'c', 'set': {'asking': 40}},
    ]})
    assert '1 listings changed and 1 were skipped' in note
    assert 'remaining 1 items' in note
    assert ('dismissed' if status == 'dismissed' else 'awaiting application') in note
    assert 'nothing changed' not in note and 'nothing has changed' not in note


@pytest.mark.parametrize('commit', [False, True])
def test_deferred_fill_only_after_outer_commit(workspace, monkeypatch, commit):  # noqa: F811
    db, _client = workspace
    _, _, coat = seed(db)
    calls = []
    monkeypatch.setattr('anteroom.services.native_field_fill.category_saved', lambda *args: calls.append(args))
    listing_persistence.save_revision(db, coat.id, {'title': 'Changed'}, 'user_form', commit=False)
    assert calls == []
    if commit:
        db.commit()
        assert len(calls) == 1
    else:
        db.rollback()
        db.commit()
        assert calls == []


def test_rolled_back_savepoint_does_not_queue_fill(workspace, monkeypatch):  # noqa: F811
    db, _client = workspace
    _, _, coat = seed(db)
    calls = []
    monkeypatch.setattr('anteroom.services.native_field_fill.category_saved', lambda *args: calls.append(args))
    nested = db.begin_nested()
    listing_persistence.save_revision(db, coat.id, {'title': 'Changed'}, 'user_form', commit=False)
    nested.rollback()
    db.commit()
    assert calls == []
