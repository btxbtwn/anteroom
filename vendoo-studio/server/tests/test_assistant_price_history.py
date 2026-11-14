"""Price history stays complete without decompressing archived marketplace forms."""
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.database import json_value
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.services import business_assistant, schema_migrations

NOW = datetime(2026, 11, 14, tzinfo=UTC)
OWNERS = ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002')


def test_price_history_uses_compact_inputs_and_keeps_every_change(migration_engine, monkeypatch):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add_all([Conversation(id=name, title=name) for name in ('coat', 'tee', 'unchanged')])
        db.flush()
        prices = [None, 0, -1, 'bad', 'NaN', False, 40, '40.00', None, 30, 0, 30, 25]
        for index, price in enumerate(prices):
            db.add(ListingRevision(id=f'coat-{index:02}', conversation_id='coat', source='test',
                listing_json={'price': price, 'marketplace_forms': {'unused': 'large form ' * 10000}},
                created_at=NOW + timedelta(seconds=index)))
        # Thirty revisions, no current-listing pointer: history is not just the
        # latest revision or the short summary shown in the assistant prompt.
        for index in range(30):
            db.add(ListingRevision(id=f'tee-{index:02}', conversation_id='tee', source='test',
                listing_json={'price': 100 - index}, created_at=NOW + timedelta(seconds=index)))
        for index, price in enumerate([None, 'bad', 50, '50.00', None, 0, False, 50]):
            db.add(ListingRevision(id=f'unchanged-{index}', conversation_id='unchanged', source='test',
                listing_json={'price': price}, created_at=NOW + timedelta(seconds=index)))
        db.commit()
        # Execute the former query through the same chronological calculation:
        # changed and unchanged histories must retain exactly their old meaning.
        with monkeypatch.context() as patch:
            patch.setattr(business_assistant, 'json_value', lambda _column, key: json_value(ListingRevision.listing_json, key))
            previous = business_assistant._price_changes(db)
        reads = []

        def capture(state):
            if state.is_select:
                reads.append(str(state.statement))

        event.listen(db, 'do_orm_execute', capture)
        try:
            changes = business_assistant._price_changes(db)
        finally:
            event.remove(db, 'do_orm_execute', capture)
        assert changes == previous
        assert not any(row['listing_id'] == 'unchanged' for row in changes)
        assert len(changes) == 31
        assert [(r['old_price'], r['new_price']) for r in changes if r['listing_id'] == 'coat'] == [(30, 25), (40, 30)]
        assert [(r['old_price'], r['new_price']) for r in changes if r['listing_id'] == 'tee'] == [
            (101 - index, 100 - index) for index in reversed(range(1, 30))]
        assert changes[0]['changed_on'] == NOW.date().isoformat()
        assert not db.in_transaction()
        assert len(reads) == 1
        assert 'listing_json' not in reads[0], 'History must not decompress archived marketplace forms to read price'


def _seed_large_history(engine):
    """Two sellers, 80 items and 20 revisions each, with ~1.5 MB form snapshots."""
    import hashlib
    import json

    options = [{'label': f'Option {i}', 'value': hashlib.sha256(str(i).encode()).hexdigest()} for i in range(24)]
    payload = json.dumps({'marketplace_forms': {'fields': [
        {'id': f'field-{i}', 'options': options} for i in range(600)
    ]}})
    with engine.begin() as connection:
        for seller, owner in enumerate(OWNERS):
            connection.execute(text("""
                INSERT INTO conversations (id, user_id, title, status)
                SELECT :prefix || n, :owner, 'Synthetic coat', 'draft' FROM generate_series(1, 80) n
            """), {'prefix': f'{seller}-', 'owner': owner})
            connection.execute(text("""
                INSERT INTO listing_revisions (id, conversation_id, user_id, source, listing_json, created_at)
                SELECT :prefix || n || '-' || r, :prefix || n, :owner, 'test',
                    jsonb_set(CAST(:payload AS jsonb), '{price}', CASE r % 5
                        WHEN 1 THEN '"bad"'::jsonb WHEN 2 THEN to_jsonb((40 + r / 5)::text)
                        WHEN 3 THEN 'null'::jsonb WHEN 4 THEN 'false'::jsonb
                        ELSE to_jsonb(40 + r / 5) END),
                    timestamp '2026-11-14' + r * interval '1 second'
                FROM generate_series(1, 80) n CROSS JOIN generate_series(0, 19) r
            """), {'prefix': f'{seller}-', 'owner': owner, 'payload': payload})
        connection.execute(text('ANALYZE listing_revisions'))
    return len(payload.encode())


def _plan(db, statement):
    sql = str(statement.compile(dialect=db.get_bind().dialect, compile_kwargs={'literal_binds': True}))
    report = db.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql)).scalar_one()[0]
    db.rollback()
    plan = report['Plan']
    return report['Execution Time'], plan.get('Shared Hit Blocks', 0) + plan.get('Shared Read Blocks', 0)


def test_postgres_price_history_avoids_toast_work_and_remains_seller_scoped(migration_engine, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from vendoo_studio.services.hosted_database import SellerDatabaseSession

    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('TOAST and hosted RLS require Postgres')
    schema_migrations.ensure_schema(engine)
    payload_bytes = _seed_large_history(engine)
    old_read = select(ListingRevision.conversation_id, json_value(ListingRevision.listing_json, 'price'),
                      ListingRevision.created_at).order_by(ListingRevision.created_at, ListingRevision.id)
    statements = []

    def capture(state):
        if state.is_select:
            statements.append(state.statement)

    with SellerDatabaseSession(bind=engine, user_id=OWNERS[0]) as db:
        role, bypass = db.execute(text('SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        assert role == 'anteroom_seller' and not bypass
        db.rollback()
        event.listen(db, 'do_orm_execute', capture)
        try:
            started = perf_counter()
            changes = business_assistant._price_changes(db)
            first_read_ms = (perf_counter() - started) * 1000
        finally:
            event.remove(db, 'do_orm_execute', capture)
        assert len(statements) == 1
        expected = [
            {'listing_id': f'0-{n}', 'changed_on': '2026-11-14', 'old_price': old, 'new_price': old + 1}
            for old in (42, 41, 40) for n in sorted(range(1, 81), key=lambda n: f'0-{n}-{(old - 39) * 5}', reverse=True)
        ]
        assert changes == expected
        with monkeypatch.context() as patch:
            patch.setattr(business_assistant, 'json_value', lambda _column, key: json_value(ListingRevision.listing_json, key))
            assert business_assistant._price_changes(db) == changes
        before_ms, before_blocks = _plan(db, old_read)
        after_ms, after_blocks = _plan(db, statements[0])
        print(f'price history: form_bytes={payload_bytes}, revisions=3200 (1600 owned), '
              f'first_read_ms={first_read_ms:.1f} baseline_ms={before_ms:.1f} current_ms={after_ms:.1f} '
              f'baseline_buffers={before_blocks} compact_buffers={after_blocks}')
        # Bound physical work, not wall-clock time on shared CI hardware. The
        # old scalar extraction must fetch/decompress TOAST for every revision.
        assert after_blocks * 4 < before_blocks

    barrier = Barrier(5)

    def histories(seller):
        samples = []
        with SellerDatabaseSession(bind=engine, user_id=OWNERS[seller]) as db:
            barrier.wait(timeout=15)
            for _ in range(5):
                started = perf_counter()
                result = business_assistant._price_changes(db)
                samples.append((perf_counter() - started) * 1000)
                assert len(result) == 240 and all(row['listing_id'].startswith(f'{seller}-') for row in result)
                assert not db.in_transaction()
        return samples

    def normal_reads_and_writes():
        samples = []
        with SellerDatabaseSession(bind=engine, user_id=OWNERS[0]) as db:
            barrier.wait(timeout=15)
            for index in range(20):
                started = perf_counter()
                row = db.get(Conversation, '0-1')
                assert row is not None and db.get(Conversation, '1-1') is None
                row.title = f'Synthetic edit {index}'
                db.commit()
                samples.append((perf_counter() - started) * 1000)
        return samples

    with ThreadPoolExecutor(max_workers=5) as pool:
        work = [pool.submit(histories, seller) for seller in (0, 0, 1, 1)]
        normal = pool.submit(normal_reads_and_writes)
        history_times = sorted(value for task in work for value in task.result())
        normal_times = sorted(normal.result())
    for name, values in [('history', history_times), ('normal read/write', normal_times)]:
        print(f'four histories / two sellers: {name} samples={len(values)} '
              f'p50_ms={values[len(values) // 2]:.1f} p95_ms={values[int(len(values) * .95) - 1]:.1f} errors=0')
