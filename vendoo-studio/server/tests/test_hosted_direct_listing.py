"""Listing on eBay from the web: the seller's own items, approved photos and the job watchdog."""
import asyncio
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

from sqlalchemy import update

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import approved_photos, hosted_job_watchdog
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.service_context import use


def _fake_storage(monkeypatch) -> dict:
    objects: dict = {}

    def storage(method, key, token=None, data=None):
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        else:
            return objects[key]
        return b''

    monkeypatch.setattr('vendoo_studio.services.hosted_photos.storage_call', storage)
    monkeypatch.setattr('vendoo_studio.services.hosted_storage.photo_call',
                        lambda method, key, data=None: storage(method, key, data=data))
    return objects


def test_listing_actions_are_the_sellers_own_and_ebay_only(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Vintage coat'}).json()
    base = f"/api/conversations/{draft['id']}"

    # The test app runs no job watchdog, so the web offers no marketplace actions.
    assert client.get('/api/runtime', headers=alice).json()['action_marketplaces'] == []
    assert client.get(base + '/native-marketplaces', headers=alice).json() == {}
    assert client.get(base + '/marketplace-listings', headers=alice).status_code == 200
    assert client.get(base + '/native-marketplaces', headers=bob).status_code == 404
    assert client.post(base + '/native-marketplaces/ebay/list', headers=bob,
                       json={'revision_id': 'r1', 'confirm': True}).status_code == 404
    # Chrome-only marketplaces are not reachable from the web.
    assert client.post(base + '/native-marketplaces/poshmark/list', headers=alice,
                       json={'revision_id': 'r1', 'confirm': True}).status_code == 422
    refused = client.post(base + '/marketplace-listings/delist', headers=alice,
                          json={'marketplaces': ['poshmark'], 'confirm': True})
    assert refused.status_code == 409 and 'Mac app' in refused.json()['detail']
    # List needs the seller's confirmation in the same call.
    assert client.post(base + '/native-marketplaces/ebay/list', headers=alice,
                       json={'revision_id': 'r1'}).status_code == 400


def test_approved_photos_are_copied_into_the_sellers_storage(cloud, monkeypatch):  # noqa: F811
    objects = _fake_storage(monkeypatch)
    with use(for_seller(USER_ID)):
        store = photo_store()
        source = store.new_key('conv1', '.jpg')
        store.put(source, b'Front')
        rows = approved_photos.capture([SimpleNamespace(id='p1', stored_filename=source, mime_type='image/jpeg',
                                                        checksum=sha256(b'Front').hexdigest())])
        copy = rows[0]['stored_filename']
        assert copy != source and copy.startswith(f'{USER_ID}/conv1/') and objects[copy] == b'Front'
        approved_photos.release({approved_photos.KEY: rows})
    assert copy not in objects and objects[source] == b'Front'


def test_web_job_watchdog_frees_a_restarted_sellers_marketplace_slot(cloud):  # noqa: F811
    _, engine = cloud
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.conversation import Conversation, utcnow
    from vendoo_studio.models.job import Job
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
    from vendoo_studio.repositories.queries import JobRepo

    with use(for_seller(USER_ID)), SessionLocal() as db:
        conv = Conversation(title='Vintage coat', status='draft')
        db.add(conv)
        db.commit()
        # A List this process is not running: the API restarted mid-send.
        job = JobRepo(db).create(conv.id, 'r1', {}, status='dispatched', current_step='native_ebay_publish')
        MarketplaceListingRepo(db).save(conv.id, 'ebay', {'state': 'publishing', 'action': 'publish', 'job_id': job.id})
        # Last heard from a minute ago, past the watchdog's grace for a job no one runs.
        db.execute(update(Job).where(Job.id == job.id).values(updated_at=utcnow() - timedelta(minutes=1)))
        db.commit()
        conv_id, job_id = conv.id, job.id

    assert hosted_job_watchdog.unsettled_owners(engine) == [USER_ID]
    assert asyncio.run(hosted_job_watchdog.sweep_owner(USER_ID, reconcile_all=True)) == [job_id]

    with use(for_seller(USER_ID)), SessionLocal() as db:
        assert JobRepo(db).get(job_id).status == 'failed'
        # The watchdog read the result: this List never saved its eBay SKU, so nothing reached eBay.
        record = MarketplaceListingRepo(db).get(conv_id, 'ebay')
        assert record['state'] == 'not_saved' and 'Nothing reached eBay' in record['error']
    with use(for_seller(BOB)), SessionLocal() as db:
        assert JobRepo(db).get(job_id) is None
