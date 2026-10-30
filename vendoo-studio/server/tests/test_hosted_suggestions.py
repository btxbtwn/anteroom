import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing


def test_suggestions_use_owned_inventory_preferences_and_durable_task_state(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)

    def item(auth, title):
        identity = client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']
        saved = client.put('/api/conversations/' + identity + '/listing', headers=auth, json={
            'listing': {'title': title, 'price': 40}, 'expected_revision_id': '',
        })
        assert saved.status_code == 200
        return identity

    first, second = item(alice, 'Alice private coat'), item(bob, 'Bob private coat')
    assert client.get('/api/suggestions').status_code == 401
    alice_feed = client.get('/api/suggestions', headers=alice)
    assert alice_feed.status_code == 200, alice_feed.text
    assert {card['conversation_id'] for card in alice_feed.json()['suggestions']} == {first}
    assert 'Bob private coat' not in alice_feed.text
    bob_feed = client.get('/api/suggestions', headers=bob)
    assert {card['conversation_id'] for card in bob_feed.json()['suggestions']} == {second}
    assert 'Alice private coat' not in bob_feed.text
    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'suggestions' in runtime['features']
    assert 'assistant' not in runtime['features']
    assert client.get('/api/settings/provider', headers=alice).json()['configured'] is False
    with Session(engine) as db:
        task = BackgroundTask(user_id=USER_ID, conversation_id=first, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    assert client.get('/api/suggestions', headers=alice).json()['suggestions'] == []
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'running'
        db.commit()
    assert client.get('/api/suggestions', headers=alice).json()['suggestions'] == []
    assert len(client.get('/api/suggestions', headers=bob).json()['suggestions']) == 1
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    assert len(client.get('/api/suggestions', headers=alice).json()['suggestions']) == 1
    listed = (datetime.now(UTC) - timedelta(days=70)).isoformat()
    with Session(engine) as db:
        for identity in (first, second):
            conv = db.get(Conversation, identity)
            conv.status = 'active'
            conv.notes = json.dumps({'vendooMarketplaces': ['ebay'], 'vendooDates': {'listed': listed}})
            db.get(Listing, identity).validation_status = 'valid'
        db.commit()
    assert client.put('/api/settings/ui', headers=alice, json={'stale_listing_days': 45}).status_code == 200
    assert client.put('/api/settings/ui', headers=bob, json={'stale_listing_days': 120}).status_code == 200
    stale = client.get('/api/suggestions', headers=alice).json()['suggestions']
    assert len(stale) == 1 and stale[0]['kind'] == 'stale_active'
    assert stale[0]['conversation_id'] == first and stale[0]['age_days'] == 70
    assert client.get('/api/suggestions', headers=bob).json()['suggestions'] == []
    assert client.post('/api/suggestions', headers=alice, json={}).status_code == 405
    with Session(engine) as db:
        assert db.query(Job).count() == 0
