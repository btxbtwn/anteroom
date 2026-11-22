"""AI Studio and listing videos on the web, under two seller identities.

Photoroom is never reached (sandbox mode draws its stand-in) and Storage is a
fake that checks every key stays in the calling seller's own folder.
"""
import asyncio
import io
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.conversation import Photo
from anteroom.models.listing_video import ListingVideo
from anteroom.services import hosted_photos, hosted_videos

CLIP = (Path(__file__).parent / 'fixtures' / 'listing-video.mp4').read_bytes()


def _jpeg(size=(200, 100), color='red') -> bytes:
    image = io.BytesIO()
    Image.new('RGB', size, color).save(image, 'JPEG')
    return image.getvalue()


def test_hosted_ai_studio_keeps_the_private_original_and_resets(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    monkeypatch.setenv('ANTEROOM_SANDBOX', '1')
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Studio'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        assert key.startswith(f'{USER_ID}/{identity}/')
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    photo_id = client.post(f'/api/drafts/{identity}/photos', headers=alice, content=_jpeg()).json()['id']
    original_key = next(iter(stored))
    path = f'/api/conversations/{identity}/photos/studio'

    # Another seller cannot run AI Studio on this photo.
    refused = client.post(path, headers=bob, json={'photo_ids': [photo_id], 'options': {'remove_background': True}})
    assert refused.status_code in (200, 404)
    if refused.status_code == 200:
        assert refused.json()['photos'] == [] and refused.json()['errors'][0]['detail'] in ('Photo not found', 'Listing not found')
    assert len(stored) == 1

    response = client.post(path, headers=alice, json={'photo_ids': [photo_id],
                                                      'options': {'flat_lay': True, 'background': '#F5F0E6'}})
    assert response.status_code == 200, response.text
    saved = response.json()['photos'][0]
    studio_key = saved['edits']['studio']['key']
    assert studio_key.startswith(f'{USER_ID}/{identity}/')
    assert saved['edits']['studio']['options']['remove_background'] is True
    # The upload, the AI result and the photo rendered from it.
    assert len(stored) == 3 and original_key in stored and studio_key in stored

    reset = client.delete(f'/api/conversations/{identity}/photos/{photo_id}/edits', headers=alice)
    assert reset.status_code == 200, reset.text
    assert list(stored) == [original_key]
    with Session(engine) as db:
        assert db.get(Photo, photo_id).stored_filename == original_key


def test_hosted_ai_studio_result_is_deleted_with_its_photo(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    monkeypatch.setenv('ANTEROOM_SANDBOX', '1')
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Studio delete'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    photo_id = client.post(f'/api/drafts/{identity}/photos', headers=alice, content=_jpeg()).json()['id']
    assert client.post(f'/api/conversations/{identity}/photos/studio', headers=alice,
                       json={'photo_ids': [photo_id], 'options': {'remove_background': True}}).status_code == 200
    assert len(stored) == 3
    assert client.delete(f'/api/conversations/{identity}/photos/{photo_id}', headers=alice).status_code == 200
    assert stored == {}


class FakeStorage:
    """Supabase Storage's object, sign and delete calls, kept in memory."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        prefix = f'/storage/v1/object/{hosted_photos.BUCKET}'
        if request.method == 'POST' and path.startswith('/storage/v1/object/sign/'):
            key = path.removeprefix(f'/storage/v1/object/sign/{hosted_photos.BUCKET}/')
            if key not in self.objects:
                return httpx.Response(404, json={'error': 'not found'})
            return httpx.Response(200, json={'signedURL': f'/object/sign/{hosted_photos.BUCKET}/{key}?token=t'})
        if request.method == 'POST':
            self.objects[path.removeprefix(prefix + '/')] = (request.read(), request.headers['content-type'])
            return httpx.Response(200, json={'Key': path})
        if request.method == 'GET':
            found = self.objects.get(path.removeprefix(prefix + '/'))
            return httpx.Response(200, content=found[0]) if found else httpx.Response(404)
        if request.method == 'DELETE':
            import json
            for key in json.loads(request.read())['prefixes']:
                self.objects.pop(key, None)
            return httpx.Response(200, json=[])
        return httpx.Response(405)


def test_hosted_listing_videos_upload_play_select_and_delete(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'publishable')
    fake = FakeStorage()
    monkeypatch.setattr(hosted_videos, '_storage', httpx.Client(transport=httpx.MockTransport(fake)))
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Video'}).json()['id']
    base = f'/api/conversations/{identity}/videos'

    assert client.get('/api/runtime', headers=alice).json()['features'].count('listing_videos') == 1
    assert client.post(base, headers=bob, files={'file': ('clip.mp4', CLIP, 'video/mp4')}).status_code == 404
    not_video = client.post(base, headers=alice, files={'file': ('notes.mp4', b'not a video', 'video/mp4')})
    assert not_video.status_code == 400
    uploaded = client.post(base, headers=alice, files={'file': ('clip.mp4', CLIP, 'video/mp4')})
    assert uploaded.status_code == 200, uploaded.text
    video = uploaded.json()
    assert video['duration_seconds'] > 0 and video['size_bytes'] == len(CLIP)
    [(key, (data, mime))] = fake.objects.items()
    assert key.startswith(f'{USER_ID}/{identity}/') and key.endswith('.video')
    assert data == CLIP and mime.startswith('video/')
    with Session(engine) as db:
        assert db.get(ListingVideo, video['id']).user_id == USER_ID

    library = client.get(base, headers=alice).json()
    assert [row['id'] for row in library['videos']] == [video['id']]
    assert library['max_bytes'] == hosted_videos.MAX_BYTES
    assert client.get(base, headers=bob).status_code == 404

    link = client.get(f"/api/videos/{video['id']}/link", headers=alice)
    assert link.status_code == 200, link.text
    assert link.json()['url'].startswith('https://project.supabase.co/storage/v1/object/sign/anteroom-photos/')
    assert client.get(f"/api/videos/{video['id']}/link", headers=bob).status_code == 404

    # A listing may select the video for a marketplace that takes one, never another listing's.
    save = client.put(f'/api/conversations/{identity}/listing', headers=alice, json={
        'listing': {'title': 'Video', 'poshmark_specifics': {'videoId': video['id']}}, 'expected_revision_id': ''})
    assert save.status_code == 200, save.text
    revision = save.json()['revision_id']
    foreign = client.put(f'/api/conversations/{identity}/listing', headers=alice, json={
        'listing': {'title': 'Video', 'poshmark_specifics': {'videoId': 'someone-else'}}, 'expected_revision_id': revision})
    assert foreign.status_code == 422
    mercari = client.put(f'/api/conversations/{identity}/listing', headers=alice, json={
        'listing': {'title': 'Video', 'mercari_specifics': {'videoId': video['id']}}, 'expected_revision_id': revision})
    assert mercari.status_code == 422

    # Still selected on Poshmark: it cannot be deleted yet.
    assert client.delete(f"{base}/{video['id']}", headers=alice).status_code == 409
    cleared = client.put(f'/api/conversations/{identity}/listing', headers=alice, json={
        'listing': {'title': 'Video', 'poshmark_specifics': {'videoId': ''}}, 'expected_revision_id': revision})
    assert cleared.status_code == 200, cleared.text
    assert client.delete(f"{base}/{video['id']}", headers=bob).status_code == 404
    assert client.delete(f"{base}/{video['id']}", headers=alice).json() == {'ok': True}
    assert fake.objects == {}


def test_video_keys_stay_in_the_sellers_folder():
    key = hosted_videos.new_key(USER_ID, 'listing1')
    assert hosted_videos.check_key(key, USER_ID, 'listing1') == key
    for bad in (key.replace(USER_ID, BOB), key.replace('.video', '.jpg'), f'{USER_ID}/../x/{"0" * 32}.video'):
        with pytest.raises(HTTPException):
            hosted_videos.check_key(bad, USER_ID)
    with pytest.raises(HTTPException):
        hosted_videos.check_key(key, USER_ID, 'listing2')


def test_relayed_poshmark_command_carries_a_signed_video_link(monkeypatch):
    from anteroom.services import hosted_browser_relay

    monkeypatch.setattr(hosted_videos, 'signed_url', lambda key: f'https://p.supabase.co/storage/v1/object/sign/anteroom-photos/{key}?token=t')
    command = {'type': 'marketplace.execute', 'job_id': 'j1',
               'payload': {'marketplace': 'poshmark', 'video': {'id': 'v1', 'stored_filename': 'u/l/k.video'}}}
    linked = asyncio.run(hosted_browser_relay.with_video_link(command))
    assert linked['payload']['video']['url'].endswith('u/l/k.video?token=t')
    assert 'url' not in command['payload']['video']
    other = {'type': 'marketplace.delete', 'payload': {'video': {'stored_filename': 'x'}}}
    assert asyncio.run(hosted_browser_relay.with_video_link(other)) is other

    def broken(_key):
        raise RuntimeError('storage down')

    monkeypatch.setattr(hosted_videos, 'signed_url', broken)
    assert asyncio.run(hosted_browser_relay.with_video_link(command)) is command
