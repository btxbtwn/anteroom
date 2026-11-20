"""Archive-marker checks reuse their caller's transaction, without pool saturation."""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.sourcing import SourceBox
from anteroom.models.user_preference import UserPreference
from anteroom.services import boxes, schema_migrations
from anteroom.services.hosted_context import HostedSettingsStore
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.service_context import ServiceContext, current, use
from anteroom.services.vendoo_sales_archive import ARCHIVED_KEY, ensure_archived


@contextmanager
def seller_checkouts(engine):
    """Count real simultaneous checkouts; leave capacity and timeout unchanged."""
    active = set()
    counts = {'peak': 0, 'settings_reads': 0}

    def checkout(connection, record, proxy):
        try:
            context = current()
        except RuntimeError:
            context = None  # A hosted lifespan worker has no request context.
        if context is not None:
            active.add(id(record))
            counts['peak'] = max(counts['peak'], len(active))

    def checkin(connection, record):
        active.discard(id(record))

    def statement(connection, cursor, sql, parameters, execution, many):
        if sql.lstrip().upper().startswith('SELECT') and 'user_preferences' in sql:
            counts['settings_reads'] += 1

    listeners = [('checkout', checkout), ('checkin', checkin), ('before_cursor_execute', statement)]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        yield counts
        assert not active, 'The caller must release its connection when its session ends'
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)


@pytest.fixture(params=[True, False], ids=['autoflush', 'no-autoflush'])
def archived_boxes(migration_engine, request):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(UserPreference(user_id=USER_ID, key='settings', value={ARCHIVED_KEY: 'already archived'}))
        db.add(SourceBox(id='kept-box', user_id=USER_ID, store='Synthetic', title='Kept box', price=30, pieces=3))
        db.commit()

    def sessions():
        if engine.dialect.name == 'postgresql':
            return SellerDatabaseSession(bind=engine, user_id=USER_ID, autoflush=request.param)
        return Session(engine, autoflush=request.param)

    context = ServiceContext(USER_ID, sessions, HostedSettingsStore(sessions), MagicMock(), MagicMock())
    return engine, sessions, context


def test_boxes_marker_read_does_not_checkout_beside_its_existing_read(archived_boxes):
    engine, sessions, context = archived_boxes
    with seller_checkouts(engine) as counts:
        with use(context), sessions() as db:
            # Real boxes.results first reads SourceBox, THEN checks the archive
            # marker through load_rows. No injected waits or small pool needed.
            first = boxes.results(db)
            second = boxes.results(db)
            assert first == second
            assert [row['id'] for row in first['boxes']] == ['kept-box']
        assert counts['settings_reads'] == 1, 'Keep the execution settings snapshot memoized'
        assert counts['peak'] == 1, 'An archive marker must not require a second simultaneous connection'


def test_archived_marker_check_preserves_the_callers_pending_changes(archived_boxes):
    engine, sessions, context = archived_boxes
    with use(context), sessions() as db:
        box = db.scalars(select(SourceBox).where(SourceBox.id == 'kept-box')).one()
        transaction = db.get_transaction()
        box.title = 'Uncommitted edit'
        pending = SourceBox(id='pending-box', user_id=USER_ID, store='Synthetic', title='Pending', price=5)
        db.add(pending)
        ensure_archived(db)
        assert db.get_transaction() is transaction
        assert box in db.dirty and pending in db.new
        assert box.title == 'Uncommitted edit'
        db.rollback()
    with Session(engine) as db:
        assert db.scalars(select(SourceBox.title).where(SourceBox.id == 'kept-box')).one() == 'Kept box'
        assert db.get(SourceBox, 'pending-box') is None


@pytest.mark.parametrize('already_archived', [True, False], ids=['saved-marker', 'first-archive'])
def test_hosted_boxes_marker_check_is_seller_scoped_without_nested_checkout(
    cloud, signing_keys, already_archived,  # noqa: F811
):
    client, engine = cloud
    with Session(engine) as db:
        for owner, name in [(USER_ID, 'Alice'), (BOB, 'Bob')]:
            db.add(UserPreference(user_id=owner, key='settings',
                                  value={ARCHIVED_KEY: 'already archived'} if already_archived else {}))
            db.add(SourceBox(id=name, user_id=owner, store=name, title=name, price=30, pieces=3))
        db.commit()
    with seller_checkouts(engine) as counts:
        for owner, name in [(USER_ID, 'Alice'), (BOB, 'Bob')]:
            response = client.get('/api/boxes', headers=headers(signing_keys, owner))
            assert response.status_code == 200, response.text
            assert [row['id'] for row in response.json()['boxes']] == [name]
        assert counts['peak'] == 1, 'A boxes read must not reserve another connection for its marker'
    # First-run archival must still durably mark each seller independently.
    with Session(engine) as db:
        settings = dict(db.execute(select(UserPreference.user_id, UserPreference.value)).all())
    assert settings[USER_ID][ARCHIVED_KEY]
    assert settings[BOB][ARCHIVED_KEY]
