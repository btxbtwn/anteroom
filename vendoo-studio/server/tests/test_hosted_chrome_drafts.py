"""Save draft on Poshmark, Mercari and Depop from the web, through the seller's Chrome.

The server records the action as the Mac does and signs a save_draft command;
Chrome reads that job's approved photos and reports the result with the
operation's single-use token. Nothing here can list or publish.
"""
import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import update

from test_hosted_direct_listing import _fake_storage
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import browser_bridge, browser_commands, hosted_job_watchdog
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

KEY = ec.generate_private_key(ec.SECP256R1())
DRAFT_URL = 'https://poshmark.com/edit-listing/0123456789abcdef01234567'


@pytest.fixture
def signed(monkeypatch):
    der = KEY.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv(browser_commands.KEY_ENV, base64.b64encode(der).decode())


@pytest.fixture
def never_publishes(monkeypatch):
    """Every path that runs or publishes in this process fails the test if reached."""
    def refuse(*_args, **_kwargs):
        raise AssertionError('A web Save draft must not run or publish in the server')

    for name in ('list_now', 'update_live', 'run_job', '_execute_step', '_launch'):
        monkeypatch.setattr(native, name, refuse)
    monkeypatch.setattr(browser_bridge, 'request', refuse)
    monkeypatch.setattr('vendoo_studio.services.chrome_catalog.live_preview', refuse)


@pytest.fixture
def ready(monkeypatch):
    # The marketplace form's own gaps have their own tests; these start from a complete listing.
    monkeypatch.setattr(native.direct_preflight, 'gaps', lambda *_args, **_kwargs: [])


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verified(command: dict) -> dict:
    raw = _b64(command['signature'])
    signature = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    KEY.public_key().verify(signature, command['body'].encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(_b64(command['body']))


def _listing(client, keys, user=USER_ID, *, photos=(b'Front', b'Back'), video=False):
    """A seller's draft with a saved revision and photos in their own Storage."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.conversation import Photo
    from vendoo_studio.repositories.queries import ListingRepo
    from vendoo_studio.services.photo_store import photo_store

    draft = client.post('/api/drafts', headers=headers(keys, user), json={'title': 'Vintage coat'}).json()
    with use(for_seller(user)), SessionLocal() as db:
        ids = []
        for index, data in enumerate(photos):
            key = photo_store().new_key(draft['id'], '.jpg')
            photo_store().put(key, data)
            photo = Photo(conversation_id=draft['id'], original_filename='photo.jpg', stored_filename=key, mime_type='image/jpeg',
                          size_bytes=len(data), display_order=index, checksum=sha256(data).hexdigest())
            db.add(photo)
            db.flush()
            ids.append(photo.id)
        listing = {'title': 'Vintage wool coat', 'description': 'Warm.', 'price': 40, 'brand': 'Pendleton', 'condition': 'Good',
                   'poshmark_specifics': {'videoId': 'clip-1'} if video else {}}
        revision = ListingRepo(db).save_revision(draft['id'], listing, 'user_form')
        db.commit()
        return draft['id'], revision.id, ids


def _start(client, keys, conv_id, revision_id, marketplace='poshmark', user=USER_ID):
    return client.post(f'/api/conversations/{conv_id}/native-marketplaces/{marketplace}/chrome-draft',
                       headers=headers(keys, user), json={'revision_id': revision_id})


def _job(job_id, user=USER_ID):
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.repositories.queries import JobRepo

    with use(for_seller(user)), SessionLocal() as db:
        job = JobRepo(db).get(job_id)
        if job is not None:
            db.expunge(job)
        return job


def test_save_draft_records_the_action_and_signs_a_save_only_command(cloud, signing_keys, monkeypatch, signed, ready, never_publishes):  # noqa: F811
    client, _ = cloud
    _fake_storage(monkeypatch)
    conv_id, revision_id, photo_ids = _listing(client, signing_keys, video=True)

    started = _start(client, signing_keys, conv_id, revision_id)
    assert started.status_code == 200, started.text
    record, command = started.json()['record'], verified(started.json()['command'])
    assert (record['state'], record['action'], record['running']) == ('saving', 'save_draft', True)
    assert command['kind'] == 'marketplace.save_draft' and command['version'] == 1
    assert command['user_id'] == USER_ID and command['operation_id'] == record['job_id'] == record['operation_id']
    payload = command['payload']
    assert (payload['marketplace'], payload['action'], payload['confirm']) == ('poshmark', 'save_draft', False)
    assert [photo['id'] for photo in payload['photos']] == photo_ids
    assert payload['revision_id'] == revision_id and payload['listing']['title'] == 'Vintage wool coat'
    # The web app carries no video, and Storage keys never leave the server.
    assert 'video' not in payload and 'videoId' not in payload['listing']['poshmark_specifics']
    assert not {'_approved_photos', '_native_video'} & set(payload['listing'])
    assert 'stored_filename' not in started.text

    job = _job(record['job_id'])
    assert (job.status, job.current_step) == ('dispatched', 'native_poshmark_save_draft')
    assert job.operation_token_hash == sha256(command['token'].encode()).hexdigest()
    assert command['token'] not in json.dumps(job.listing_snapshot)
    expires = browser_commands._as_utc(job.operation_expires_at)
    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)
    assert command['expires_at'] < expires.timestamp()

    status = client.get(f'/api/conversations/{conv_id}/native-marketplaces', headers=headers(signing_keys)).json()
    assert status['poshmark']['running'] is True and status['poshmark']['state'] == 'saving'


def test_only_save_draft_on_chrome_marketplaces_and_one_at_a_time(cloud, signing_keys, monkeypatch, signed, ready, never_publishes):  # noqa: F811
    client, _ = cloud
    _fake_storage(monkeypatch)
    conv_id, revision_id, _ = _listing(client, signing_keys)
    base = f'/api/conversations/{conv_id}/native-marketplaces'

    for marketplace in ('ebay', 'etsy', 'sellwild', 'vinted'):
        assert _start(client, signing_keys, conv_id, revision_id, marketplace).status_code == 422
    # The request names only the revision: no confirm, action or publish flag gets in.
    for extra in ({'confirm': True}, {'action': 'list'}, {'publish': True}):
        assert client.post(base + '/poshmark/chrome-draft', headers=headers(signing_keys),
                           json={'revision_id': revision_id, **extra}).status_code == 422
    # Chrome marketplaces still cannot list, update or delist from the web.
    assert client.post(base + '/poshmark/list', headers=headers(signing_keys), json={'revision_id': revision_id, 'confirm': True}).status_code == 422
    assert client.post(base + '/poshmark/draft', headers=headers(signing_keys), json={'revision_id': revision_id}).status_code == 422
    with pytest.raises(native.NativeMarketplaceError):
        with use(for_seller(USER_ID)):
            from vendoo_studio.database import SessionLocal
            with SessionLocal() as db:
                asyncio.run(native._start(conv_id, 'poshmark', native.ListRequest(revision_id=revision_id, confirm=True), 'list', db, browser='cloud'))

    assert _start(client, signing_keys, conv_id, 'older-revision').status_code == 409
    assert _start(client, signing_keys, conv_id, revision_id, user=BOB).status_code == 404
    assert _start(client, signing_keys, conv_id, revision_id).status_code == 200
    busy = _start(client, signing_keys, conv_id, revision_id)
    assert busy.status_code == 409 and 'Poshmark' in busy.json()['detail']
    # Another marketplace runs beside it, as on the Mac.
    assert _start(client, signing_keys, conv_id, revision_id, 'mercari').status_code == 200

    monkeypatch.delenv(browser_commands.KEY_ENV)
    assert _start(client, signing_keys, conv_id, revision_id, 'depop').status_code == 503


def test_photo_route_serves_only_that_operations_approved_photos(cloud, signing_keys, monkeypatch, signed, ready, never_publishes):  # noqa: F811
    client, _ = cloud
    objects = _fake_storage(monkeypatch)
    conv_id, revision_id, photo_ids = _listing(client, signing_keys)
    bob_conv, bob_revision, bob_photos = _listing(client, signing_keys, BOB, photos=(b'Bob',))
    command = verified(_start(client, signing_keys, conv_id, revision_id).json()['command'])
    bob_command = verified(_start(client, signing_keys, bob_conv, bob_revision, user=BOB).json()['command'])
    operation, token = command['operation_id'], command['token']
    url = f'/api/operations/{operation}/photos/'
    auth = {'Authorization': f'Bearer {token}'}

    served = client.get(url + photo_ids[0], headers=auth)
    assert served.status_code == 200 and served.content == b'Front' and served.headers['content-type'] == 'image/jpeg'
    assert client.get(url + photo_ids[1], headers=auth).content == b'Back'
    # No token, a wrong one, a seller session, another operation's token, or another seller's photo.
    assert client.get(url + photo_ids[0]).status_code == 401
    assert client.get(url + photo_ids[0], headers={'Authorization': 'Bearer ' + 'A' * 43}).status_code == 401
    assert client.get(url + photo_ids[0], headers=headers(signing_keys)).status_code == 401
    assert client.get(url + photo_ids[0], headers={'Authorization': f"Bearer {bob_command['token']}"}).status_code == 401
    assert client.get(url + bob_photos[0], headers=auth).status_code == 404
    assert client.get(f"/api/operations/{bob_command['operation_id']}/photos/{bob_photos[0]}", headers=auth).status_code == 401
    # The token reads the approved copy, not the photo as it is now.
    source = next(key for key, data in objects.items() if data == b'Front' and key.endswith('.jpg'))
    objects[source] = b'Edited later'
    assert client.get(url + photo_ids[0], headers=auth).content == b'Front'

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.job import Job
    with use(for_seller(USER_ID)), SessionLocal() as db:
        db.execute(update(Job).where(Job.id == operation).values(operation_expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)))
        db.commit()
    assert client.get(url + photo_ids[0], headers=auth).status_code == 401


def test_result_is_applied_once_and_the_token_stops_working(cloud, signing_keys, monkeypatch, signed, ready, never_publishes):  # noqa: F811
    client, _ = cloud
    _fake_storage(monkeypatch)
    conv_id, revision_id, photo_ids = _listing(client, signing_keys)
    command = verified(_start(client, signing_keys, conv_id, revision_id).json()['command'])
    operation, auth = command['operation_id'], {'Authorization': f"Bearer {command['token']}"}
    url = f'/api/operations/{operation}/result'

    assert client.post(url, json={'result': {'ok': True, 'state': 'draft', 'draft_url': DRAFT_URL}}).status_code == 401
    assert client.post(url, headers=auth, json={'result': {'ok': True}, 'extra': 1}).status_code == 422
    applied = client.post(url, headers=auth, json={'result': {'ok': True, 'state': 'draft', 'draft_url': DRAFT_URL}})
    assert applied.status_code == 200, applied.text
    assert (applied.json()['state'], applied.json()['draft_url'], applied.json()['running']) == ('draft', DRAFT_URL, False)
    job = _job(operation)
    assert (job.status, job.operation_token_hash) == ('completed', None)
    # A second report, or a photo read, no longer opens anything.
    assert client.post(url, headers=auth, json={'result': {'ok': False, 'safe_to_retry': True, 'error': 'x'}}).status_code == 401
    assert client.get(f'/api/operations/{operation}/photos/{photo_ids[0]}', headers=auth).status_code == 401
    status = client.get(f'/api/conversations/{conv_id}/native-marketplaces', headers=headers(signing_keys)).json()
    assert status['poshmark']['state'] == 'draft' and status['poshmark']['running'] is False

    # A refusal leaves the item ready to try again, with Chrome's reason.
    again = verified(_start(client, signing_keys, conv_id, revision_id, 'depop').json()['command'])
    refused = client.post(f"/api/operations/{again['operation_id']}/result", headers={'Authorization': f"Bearer {again['token']}"},
                          json={'result': {'ok': False, 'safe_to_retry': True, 'error': 'Sign in to Depop.', 'error_code': 'signed_out'}})
    assert refused.status_code == 200
    assert (refused.json()['state'], refused.json()['error_code']) == ('not_saved', 'signed_out')
    assert _job(again['operation_id']).status == 'failed'
    # A publish answer is never taken as a saved draft.
    third = verified(_start(client, signing_keys, conv_id, revision_id, 'mercari').json()['command'])
    odd = client.post(f"/api/operations/{third['operation_id']}/result", headers={'Authorization': f"Bearer {third['token']}"},
                      json={'result': {'ok': True, 'state': 'published', 'listing_url': 'https://www.mercari.com/us/item/m123456789/'}})
    assert odd.json()['state'] == 'unknown'


def test_watchdog_keeps_a_live_web_save_and_leaves_an_expired_one_for_review(cloud, signing_keys, monkeypatch, signed, ready, never_publishes):  # noqa: F811
    client, engine = cloud
    _fake_storage(monkeypatch)
    conv_id, revision_id, _ = _listing(client, signing_keys)
    command = verified(_start(client, signing_keys, conv_id, revision_id).json()['command'])
    operation = command['operation_id']

    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.job import Job, JobEvent

    def quiet_for(minutes: int, **values) -> None:
        """The job and its events last heard from ``minutes`` ago (naive UTC, like the columns)."""
        then = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)
        with use(for_seller(USER_ID)), SessionLocal() as db:
            db.execute(update(Job).where(Job.id == operation).values(updated_at=then, **values))
            db.execute(update(JobEvent).where(JobEvent.job_id == operation).values(created_at=then))
            db.commit()

    # Silent past the watchdog's grace, but its token is live: Chrome may still report.
    quiet_for(2)
    assert asyncio.run(hosted_job_watchdog.sweep_owner(USER_ID, reconcile_all=True)) == []
    assert _job(operation).status == 'dispatched'

    quiet_for(16, operation_expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
    # Expired: the slot frees and the record waits for the seller to check. Chrome is never asked.
    assert asyncio.run(hosted_job_watchdog.sweep_owner(USER_ID, reconcile_all=True)) == [operation]
    assert _job(operation).status == 'failed'
    status = client.get(f'/api/conversations/{conv_id}/native-marketplaces', headers=headers(signing_keys)).json()
    assert status['poshmark']['state'] == 'unknown' and status['poshmark']['running'] is False
    assert client.post(f'/api/operations/{operation}/result', headers={'Authorization': f"Bearer {command['token']}"},
                       json={'result': {'ok': True, 'state': 'draft', 'draft_url': DRAFT_URL}}).status_code == 401
    # "I checked it myself" settles it from the web.
    reset = client.post(f'/api/conversations/{conv_id}/native-marketplaces/poshmark/reset', headers=headers(signing_keys),
                        json={'confirm': True})
    assert reset.status_code == 200 and reset.json()['state'] == 'not_saved'


def test_runtime_offers_chrome_drafts_only_with_signing_and_the_watchdog(cloud, signing_keys, signed):  # noqa: F811
    client, _ = cloud
    # The test app runs no job watchdog, so nothing is offered.
    assert client.get('/api/runtime', headers=headers(signing_keys)).json()['draft_marketplaces'] == []
