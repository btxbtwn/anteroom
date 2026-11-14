"""Synthetic read-volume regressions; payload sizes are not billed wire bytes."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from time import perf_counter

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import item_status, schema_migrations


def test_quantity_history_returns_one_row_per_requested_item(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    with Session(engine) as db:
        # Two synthetic sellers, with an unrelated inventory much larger than
        # the selected page. Explicit IDs must scope even a privileged session.
        for seller in range(2):
            owner = f'00000000-0000-0000-0000-{seller + 1:012d}'
            for number in range(600):
                identity = f'{seller}-{number}'
                db.add(Conversation(id=identity, user_id=owner, title=identity))
        db.flush()
        db.add_all([ListingRevision(id=f'{seller}-{number}-{revision}',
            user_id=f'00000000-0000-0000-0000-{seller + 1:012d}',
            conversation_id=f'{seller}-{number}', source='test',
            listing_json={}, inventory_quantity=[10, 3, 2, 5, 1][revision % 5],
            created_at=now + timedelta(minutes=revision))
            for seller in range(2) for number in range(600) for revision in range(30)])
        for seller in range(2):
            owner = f'00000000-0000-0000-0000-{seller + 1:012d}'
            for number in range(600):
                identity = f'{seller}-{number}'
                db.add(MarketplaceSale(user_id=owner, conversation_id=identity, marketplace='ebay',
                    order_id=identity, line_id=f'line-{identity}', listing_id=identity,
                    quantity=1, received_at=now + timedelta(minutes=26)))
                db.add(MarketplaceListing(user_id=owner, conversation_id=identity,
                    marketplace='ebay', state='published', listing_url=f'https://example.com/{identity}'))
        db.commit()
        ids = {f'0-{number}' for number in range(501)}
        cutoff = {identity: now + timedelta(minutes=26) for identity in ids}
        reads = []
        sqlite_steps = 0

        def count_steps():
            nonlocal sqlite_steps
            sqlite_steps += 1000
            return 0

        sqlite_connection = db.connection().connection.driver_connection if engine.dialect.name == 'sqlite' else None
        if sqlite_connection is not None:
            sqlite_connection.set_progress_handler(count_steps, 1000)

        def measure(state):
            result = state.invoke_statement()
            frozen = result.freeze()
            reads.append((len(frozen.data), sum(len(str(row).encode()) for row in frozen.data)))
            return frozen()

        event.listen(db, 'do_orm_execute', measure, retval=True)
        try:
            started = perf_counter()
            quantities = item_status._quantities(db, ids, cutoff)
            quantity_seconds = perf_counter() - started
        finally:
            event.remove(db, 'do_orm_execute', measure)
            if sqlite_connection is not None:
                sqlite_connection.set_progress_handler(None, 0)
        # Last correction is 3; later restock 5 survives the final reduction 1.
        assert quantities == dict.fromkeys(ids, 5)
        print(f'quantity reads: queries={len(reads)} rows={sum(r for r, _ in reads)} '
              f'python_repr_bytes={sum(b for _, b in reads)} elapsed_ms={quantity_seconds * 1000:.1f} '
              f'sqlite_vm_steps={sqlite_steps}')
        assert sum(rows for rows, _ in reads) <= len(ids)
        assert len(reads) <= 2
        # Bound database work independently of shared-host timing. A left join
        # that rescans all 501 cutoffs per revision takes >30 million steps.
        if sqlite_connection is not None:
            assert sqlite_steps < 5_000_000
        reads.clear()
        event.listen(db, 'do_orm_execute', measure, retval=True)
        try:
            started = perf_counter()
            results = item_status.statuses(db, [(identity, {}) for identity in sorted(ids)])
            status_seconds = perf_counter() - started
        finally:
            event.remove(db, 'do_orm_execute', measure)
        assert set(results) == ids
        assert all((value.status, value.quantity, value.units_sold) == ('active', 5, 1)
                   for value in results.values())
        print(f'status reads: queries={len(reads)} rows={sum(r for r, _ in reads)} '
              f'python_repr_bytes={sum(b for _, b in reads)} elapsed_ms={status_seconds * 1000:.1f}')
        assert sum(rows for rows, _ in reads) == 3 * len(ids)
        assert len(reads) == 8
        # Hosted RLS must survive nested window queries, even if a caller passes
        # another seller's ID. SQLite's explicit-ID scoping was checked above.
        if engine.dialect.name == 'postgresql':
            from vendoo_studio.services.hosted_database import SellerDatabaseSession

            with SellerDatabaseSession(bind=engine, user_id='00000000-0000-0000-0000-000000000001') as seller_db:
                assert item_status._quantities(seller_db, {'0-0', '1-0'}, cutoff) == {'0-0': 5}
                assert item_status.statuses(seller_db, [('1-0', {})])['1-0'].quantity == 1
                reads.clear()
                event.listen(seller_db, 'do_orm_execute', measure, retval=True)
                try:
                    results = item_status.statuses(seller_db, [(identity, {}) for identity in sorted(ids)])
                finally:
                    event.remove(seller_db, 'do_orm_execute', measure)
                assert results == item_status.statuses(db, [(identity, {}) for identity in sorted(ids)])
                print(f'hosted seller status reads: queries={len(reads)} rows={sum(r for r, _ in reads)}')
                assert sum(rows for rows, _ in reads) == 3 * len(ids)


def test_status_batches_never_drop_requested_scope():
    requested = [(f'item-{number}', {}) for number in range(1001)]
    scopes = []

    def sales(_db, ids):
        scopes.append(ids)
        return {}

    class EmptyQuery:
        def filter(self, *args):
            return self

        def all(self):
            return []

    class Database:
        def query(self, *args):
            return EmptyQuery()

    with patch.object(item_status, 'paid_lines', sales), patch.object(item_status, '_quantities', return_value={}):
        result = item_status.statuses(Database(), requested)
    assert set(result) == {identity for identity, _ in requested}
    assert scopes and all(scope is not None and len(scope) <= 500 for scope in scopes)
    assert set.union(*scopes) == set(result)


def test_sql_quantity_summary_matches_historical_replay(migration_engine):  # noqa: F811
    """Corrections, restocks, missing/invalid quantities and undated history."""
    import random

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    randomizer = random.Random(71)
    with Session(engine) as db:
        ids = {f'history-{number}' for number in range(60)}
        db.add_all([Conversation(id=identity, title=identity) for identity in ids])
        db.flush()
        for identity in sorted(ids):
            for number in range(40):
                db.add(ListingRevision(id=f'{identity}-{number}', conversation_id=identity,
                    source='test', listing_json={}, inventory_quantity=randomizer.choice([None, -1, 0, 1, 2, 3, 8, 20]),
                    created_at=now + timedelta(minutes=number)))
        db.flush()
        # Set NULL explicitly: the model's default fills it on INSERT.
        db.query(ListingRevision).filter(ListingRevision.id.in_(
            [f'{identity}-0' for identity in ids])).update({'created_at': None}, synchronize_session=False)
        db.commit()
        if engine.dialect.name == 'postgresql':
            # Timestamps are stored as UTC-naive; an aware cutoff must not be
            # implicitly converted through the connection's local timezone.
            db.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
        for cutoff_minute in [None, -1, 0, 20, 39, 50]:
            first_sales = {identity: now + timedelta(minutes=cutoff_minute)
                           for identity in ids if int(identity.split('-')[1]) % 2} if cutoff_minute is not None else {}
            expected = {}
            for identity, quantity, created in db.query(ListingRevision.conversation_id,
                    ListingRevision.inventory_quantity, ListingRevision.created_at).order_by(ListingRevision.created_at):
                if quantity is None or quantity < 1:
                    continue
                cutoff = first_sales.get(identity)
                moment = item_status._time(created)
                if cutoff is None or (moment is not None and moment <= cutoff) or quantity > expected.get(identity, 0):
                    expected[identity] = quantity
            assert item_status._quantities(db, ids, first_sales) == expected
        assert item_status._quantities(db, set()) == {}


def test_sold_units_never_reads_other_items_sales():
    conversations = [SimpleNamespace(id=f'item-{number}', notes='{}') for number in range(1001)]
    scopes = []

    def sales(_db, ids):
        scopes.append(ids)
        return {}

    with patch.object(item_status, 'paid_lines', sales):
        assert item_status.sold_units(None, conversations) == {}
    assert scopes and all(scope is not None and len(scope) <= 500 for scope in scopes)
    assert set.union(*scopes) == {conversation.id for conversation in conversations}
