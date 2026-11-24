"""Seller-scoped automations and relisting on the web."""
import asyncio
from datetime import timedelta

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.automation import Automation
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import utcnow
from anteroom.models.job import Job
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.services import automation_actions, hosted_automation_scheduler
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use


def item(client, auth, title, price):
    identity = client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']
    response = client.put('/api/conversations/' + identity + '/listing', headers=auth, json={
        'listing': {'title': title, 'price': price}, 'expected_revision_id': ''})
    assert response.status_code == 200, response.text
    return identity


def task(identity, actions, **extra):
    return {'name': 'Markdown', 'timezone': 'UTC', 'schedule': {'kind': 'daily', 'time': '09:00'},
            'filters': {'conversation_ids': [identity]}, 'actions': actions, 'marketplaces': ['ebay'], **extra}


def test_web_automations_run_per_seller_and_only_propose_marketplace_writes(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    coat, bobs = item(client, alice, 'Alice coat', 100), item(client, bob, 'Bob coat', 100)
    price = [{'type': 'price', 'mode': 'percent', 'value': 10, 'floor': 15, 'repeat': 'once'}]
    assert client.get('/api/automations').status_code == 401
    assert 'automations' in client.get('/api/runtime', headers=alice).json()['views']
    preview = client.post('/api/automations/preview', headers=alice, json=task(coat, price))
    assert preview.status_code == 200, preview.text
    assert preview.json()['count'] == 1
    # Another seller's item is not selectable.
    assert client.post('/api/automations/preview', headers=alice, json=task(bobs, price)).json()['count'] == 0
    created = client.post('/api/automations', headers=alice, json=task(coat, price, activate=True))
    assert created.status_code == 200, created.text
    delist = client.post('/api/automations', headers=alice, json=task(coat, [{'type': 'delist'}], activate=True)).json()
    bobs_task = client.post('/api/automations', headers=bob, json=task(bobs, price, activate=True)).json()
    assert [row['id'] for row in client.get('/api/automations', headers=bob).json()] == [bobs_task['id']]
    assert client.get('/api/errors', headers=alice).json() == []

    with Session(engine) as db:
        for identity in (created.json()['id'], delist['id']):
            db.get(Automation, identity).next_run_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert hosted_automation_scheduler.due_owners(engine) == [USER_ID]
    asyncio.run(hosted_automation_scheduler.run_owner(USER_ID))
    assert hosted_automation_scheduler.due_owners(engine) == []
    assert client.get('/api/conversations/' + coat + '/listing', headers=alice).json()['listing']['price'] == 90
    assert client.get('/api/conversations/' + bobs + '/listing', headers=bob).json()['listing']['price'] == 100
    # A draft is not live, so the delist task proposes nothing. Propose one as a run would.
    from anteroom.database import SessionLocal
    from anteroom.services import automations
    with use(for_seller(USER_ID)), SessionLocal() as db:
        automations.enqueue_approval(db, db.get(Automation, delist['id']), None, {'conversation_id': coat, 'title': 'Alice coat'},
                                     None, ['ebay'], 'delist')
        db.commit()
    approvals = client.get('/api/automations/approvals', headers=alice).json()
    assert [row['action'] for row in approvals] == ['delist']
    assert client.get('/api/automations/approvals', headers=bob).json() == []
    row = approvals[0]
    refused = client.post('/api/automations/approvals/' + row['id'] + '/approve', headers=alice, json={
        'confirm': True, 'action': row['action'], 'marketplaces': row['marketplaces'],
        'revision_id': row['revision_id'], 'review_token': row['review_token']})
    assert refused.status_code == 409
    with Session(engine) as db:
        # Only the run's own job-log record, as on the Mac. No marketplace job was started.
        assert [(job.status, job.current_step) for job in db.query(Job)] == [('completed', 'automation')]


def test_web_regenerate_action_queues_the_sellers_draft_task(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    coat = item(client, alice, 'Alice coat', 100)
    from anteroom.repositories.queries import ConversationRepo
    from anteroom.services import listing_provider
    monkeypatch.setattr(listing_provider, 'get_listing_provider', lambda: object())
    monkeypatch.setattr(ConversationRepo, 'get_photos', lambda self, conv_id: [object()])
    monkeypatch.setattr(automation_actions, 'IDLE_TIMEOUT_S', 0)
    from anteroom.services import background_tasks
    monkeypatch.setattr(background_tasks, 'draft_payload', lambda db, conv_id, *a: {'revision_id': '', 'photos': [], 'notes': '', 'text': '', 'attachments': []})
    with use(for_seller(USER_ID)):
        result = asyncio.run(automation_actions._regenerate(coat))
    assert result == {'ok': False, 'error': 'Regenerate was still running when this automation stopped waiting.'}
    with Session(engine) as db:
        assert [(row.conversation_id, row.kind, row.status) for row in db.query(BackgroundTask)] == [(coat, 'generate', 'queued')]


def test_web_run_due_relists_uses_the_signed_in_sellers_opt_in_rules(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    coat = item(client, alice, 'Alice coat', 100)
    with Session(engine) as db:
        db.add(MarketplaceListing(
            user_id=USER_ID, conversation_id=coat, marketplace='poshmark', state='published',
            listing_id='5f0000000000000000000001',
            listing_url='https://poshmark.com/listing/5f0000000000000000000001',
            listed_at=utcnow() - timedelta(days=90),
            listed_price=100, inventory_observation={'has_active_offer': True},
        ))
        db.commit()

    run_path = '/api/automations/auto-relist/run'
    assert client.post(run_path).status_code == 401
    disabled = client.post(run_path, headers=alice)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()['outcomes'] == []

    enabled = client.put('/api/automations/auto-relist/poshmark', headers=alice, json={
        'enabled': True, 'confirm': True,
    })
    assert enabled.status_code == 200, enabled.text
    response = client.post(run_path, headers=alice)
    assert response.status_code == 200, response.text
    (outcome,) = response.json()['outcomes']
    assert outcome['conversation_id'] == coat
    assert outcome['status'] == 'skipped_active_offer'
    (log,) = client.get('/api/automations/auto-relist/logs', headers=alice).json()['logs']
    assert log['conversation_id'] == coat
    assert log['status'] == 'skipped_active_offer'

    other_seller = client.post(run_path, headers=bob)
    assert other_seller.status_code == 200, other_seller.text
    assert other_seller.json()['outcomes'] == []


def test_web_draft_schedule_routes_are_served_and_seller_scoped(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    coat = item(client, alice, 'Alice coat', 100)
    path = '/api/conversations/' + coat + '/schedule'
    assert client.get(path).status_code == 401
    state = client.get(path, headers=alice)
    assert state.status_code == 200, state.text
    assert state.json()['ready_to_list'] is False
    # Another seller cannot see or change it.
    assert client.get(path, headers=bob).status_code == 404
    assert client.put(path + '/ready', headers=bob, json={'ready_to_list': True}).status_code == 404
    # A draft without photos is refused with the reason, not a missing route.
    refused = client.put(path + '/ready', headers=alice, json={'ready_to_list': True})
    assert refused.status_code == 400, refused.text
    assert 'not valid yet' in refused.json()['detail']
    unconfirmed = client.post(path, headers=alice, json={'publish_at': '2099-01-01T00:00:00Z', 'marketplaces': ['ebay']})
    assert unconfirmed.status_code == 400, unconfirmed.text
    assert client.delete(path, headers=alice).status_code == 200
