import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.seller_change import SellerChange, TOPIC_BY_TABLE
from anteroom.services import hosted_read_snapshots, suggestions
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401


def test_unrelated_feed_write_skips_whole_shop_queries(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    auth = headers(signing_keys)
    paths = ('/api/conversations', '/api/suggestions')
    with Session(engine) as db:
        db.add_all(Conversation(user_id=USER_ID, id=f'i{i}', title='Sample', notes='x' * 1440)
                   for i in range(1400))
        db.add_all(MarketplaceSale(user_id=USER_ID, marketplace='ebay', order_id=str(i),
                                   line_id='1', state='unmatched') for i in range(730))
        db.commit()

    def poll():
        responses = [client.get(path, headers=auth) for path in paths]
        assert all(response.status_code == 200 for response in responses)
        return [response.json() for response in responses]

    rows = []

    def count(_conn, cursor, statement, *_args):
        if ('conversations.notes AS conversations_notes' in statement
                or ('FROM marketplace_sales LEFT OUTER JOIN conversations' in statement)
                or ('conversations.notes,' in statement and 'FROM conversations' in statement)):
            rows.append(cursor.rowcount)

    event.listen(engine, 'after_cursor_execute', count)
    try:
        # Prove the observer recognizes each full read before checking the warm path.
        baseline = poll()
        assert 1400 in rows and 730 in rows
        assert poll() == baseline
        with Session(engine) as db:
            db.add(SellerChange(user_id=USER_ID, topic='assistant', changed_at=datetime.now(UTC)))
            db.commit()
        rows.clear()
        assert poll() == baseline
    finally:
        event.remove(engine, 'after_cursor_execute', count)
    assert rows == []


def test_snapshot_topics_cover_observed_seller_reads(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    assert client.post('/api/drafts', headers=alice, json={'title': 'Probe tee'}).status_code == 201
    statements = []

    def listener(_conn, _cursor, statement, *_args):
        statements.append(statement)

    for name, path in [('inventory', '/api/conversations'), ('suggestions', '/api/suggestions')]:
        client.get(path, headers=alice)
        client.app.state.seller_reads.completed.clear()
        statements.clear()
        event.listen(engine, 'before_cursor_execute', listener)
        try:
            assert client.get(path, headers=alice).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', listener)
        tables = {table for statement in statements
                  for table in re.findall(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+"?(\w+)', statement, re.IGNORECASE)}
        read_topics = {TOPIC_BY_TABLE[table] for table in tables if table in TOPIC_BY_TABLE}
        assert read_topics - {None} == set(hosted_read_snapshots._SIDEBAR_TOPICS[name])


def test_cached_suggestions_age_and_remain_private(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    start = datetime(2026, 11, 26, tzinfo=UTC)
    clock = [start]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(suggestions, 'datetime', Clock)
    monkeypatch.setattr('anteroom.services.user_settings.get_stale_listing_days', lambda: 60)
    with Session(engine) as db:
        db.add(Conversation(user_id=USER_ID, title='Coat', status='active',
                            notes=json.dumps({'vendooDates': {'listed': (start - timedelta(days=59)).isoformat()}})))
        db.commit()

    def cards(owner):
        response = client.get('/api/suggestions', headers=headers(signing_keys, owner))
        assert response.status_code == 200
        return response.json()['suggestions']

    assert cards(USER_ID) == cards(BOB) == []
    assert client.get('/api/suggestions').status_code == 401

    def unexpected_read(_db):
        raise AssertionError('A clock change must use cached inputs')

    monkeypatch.setattr(suggestions, 'read_suggestion_inputs', unexpected_read)
    clock[0] += timedelta(days=1)
    assert [(card['title'], card['age_days']) for card in cards(USER_ID)] == [('Coat', 60)]
    assert cards(BOB) == []
