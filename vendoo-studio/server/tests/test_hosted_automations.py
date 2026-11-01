"""Automations on the web: owned tasks, per-seller scheduled runs, and no marketplace writes."""
import asyncio
from datetime import timedelta

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.automation import Automation
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.job import Job
from vendoo_studio.services import automation_actions, hosted_automation_scheduler
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


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
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services import automations
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
    from vendoo_studio.repositories.queries import ConversationRepo
    from vendoo_studio.services import listing_provider
    monkeypatch.setattr(listing_provider, 'get_listing_provider', lambda: object())
    monkeypatch.setattr(ConversationRepo, 'get_photos', lambda self, conv_id: [object()])
    monkeypatch.setattr(automation_actions, 'IDLE_TIMEOUT_S', 0)
    from vendoo_studio.services import background_tasks
    monkeypatch.setattr(background_tasks, 'draft_payload', lambda db, conv_id, *a: {'revision_id': '', 'photos': [], 'notes': '', 'text': '', 'attachments': []})
    with use(for_seller(USER_ID)):
        result = asyncio.run(automation_actions._regenerate(coat))
    assert result == {'ok': False, 'error': 'Regenerate was still running when this automation stopped waiting.'}
    with Session(engine) as db:
        assert [(row.conversation_id, row.kind, row.status) for row in db.query(BackgroundTask)] == [(coat, 'generate', 'queued')]
