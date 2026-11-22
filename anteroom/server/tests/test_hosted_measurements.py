"""Bulk listing and a saved listing read measurements from photos on the hosted API."""
import io

from PIL import Image

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from anteroom.services import hosted_photos
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from anteroom.services.service_context import current


def _photo() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (64, 64), (40, 40, 45)).save(out, 'JPEG')
    return out.getvalue()


def test_unsaved_photos_are_read_with_the_signed_in_sellers_provider(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    sellers = []

    class Provider:
        async def vision_chat(self, messages):
            yield ''

    def provider():
        sellers.append(current().seller_id)
        return Provider()

    async def read(_provider, paths, garment):
        assert len(paths) == 2 and garment == 'top'
        return {'photos_read': 2, 'measurements': {'pitToPit': {'status': 'accepted', 'value': 21, 'reads': [21, 21]}}}

    monkeypatch.setattr('anteroom.routes.measurements.get_photo_provider', provider)
    monkeypatch.setattr('anteroom.routes.measurements.measure_read.read_measurements', read)
    files = [('files', ('a.jpg', _photo(), 'image/jpeg')), ('files', ('b.jpg', _photo(), 'image/jpeg'))]

    assert client.post('/api/measurements/read', files=files, data={'garment': 'top'}).status_code == 401
    answer = client.post('/api/measurements/read', files=files, data={'garment': 'top'},
                         headers=headers(signing_keys, BOB))
    assert answer.status_code == 200, answer.text
    assert answer.json()['measurements']['pitToPit']['value'] == 21
    assert sellers == [BOB]


def test_a_saved_listing_is_read_from_its_own_photos_in_order(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')

    class Provider:
        async def vision_chat(self, messages):
            yield ''

    read_bytes = []

    async def read(_provider, paths, garment):
        assert garment == 'pants'
        read_bytes.extend(path.read_bytes() for path in paths)
        return {'photos_read': len(paths), 'measurements': {'waist': {'status': 'accepted', 'value': 16, 'reads': [16, 16]}}}

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    monkeypatch.setattr('anteroom.routes.measurements.get_photo_provider', Provider)
    monkeypatch.setattr('anteroom.routes.measurements.measure_read.read_measurements', read)
    photos = [_photo(), _photo()]
    for data in photos:
        assert client.post(f'/api/drafts/{identity}/photos', headers=alice, content=data).status_code == 201
    path = f'/api/conversations/{identity}/measurements/read'

    assert client.post(path, headers=bob, json={'garment': 'pants'}).status_code == 404
    answer = client.post(path, headers=alice, json={'garment': 'pants'})
    assert answer.status_code == 200, answer.text
    assert answer.json()['measurements']['waist']['value'] == 16
    assert read_bytes == list(stored.values())


def test_a_saved_listing_reads_its_size_tag_from_its_own_photos_in_order(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')

    class Provider:
        async def vision_chat(self, messages):
            yield ''

    read_bytes = []

    async def read(_provider, paths):
        read_bytes.extend(path.read_bytes() for path in paths)
        return {'photos_read': len(paths), 'status': 'accepted', 'size': 'M', 'sizes': ['M']}

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    for data in (_photo(), _photo()):
        assert client.post(f'/api/drafts/{identity}/photos', headers=alice, content=data).status_code == 201
    path = f'/api/conversations/{identity}/size-tags/read'

    # Unmocked provider lookup reads HostedSettingsStore from Postgres; must stay off the event loop.
    assert client.post(path, headers=alice).status_code == 503

    monkeypatch.setattr('anteroom.routes.measurements.get_photo_provider', Provider)
    monkeypatch.setattr('anteroom.routes.measurements.size_tag_read.read_size_tag', read)

    assert client.post(path).status_code == 401
    assert client.post(path, headers=bob).status_code == 404
    answer = client.post(path, headers=alice)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {'photos_read': 2, 'status': 'accepted', 'size': 'M', 'sizes': ['M']}
    assert read_bytes == list(stored.values())


def test_managed_ai_routes_measurements_to_measurement_model_and_size_tags_to_default(cloud, signing_keys, monkeypatch):  # noqa: F811
    from anteroom.services.hosted_provider import DEFAULT_MANAGED_MEASUREMENT_MODEL, DEFAULT_MANAGED_MODEL

    client, _engine = cloud
    alice = headers(signing_keys)
    monkeypatch.setenv('ANTEROOM_MANAGED_OPENROUTER_KEY', 'sk-or-managed-secret')
    monkeypatch.delenv('ANTEROOM_MANAGED_MODEL', raising=False)
    monkeypatch.delenv('ANTEROOM_MANAGED_MEASUREMENT_MODEL', raising=False)

    seen_models: dict[str, str] = {}

    async def read_measure(provider, _paths, _garment):
        seen_models['measurement'] = provider.vision_model
        return {'photos_read': 1, 'measurements': {}}

    async def read_tag(provider, _paths):
        seen_models['size_tag'] = provider.vision_model
        return {'photos_read': 1, 'status': 'accepted', 'size': 'L', 'sizes': ['L']}

    monkeypatch.setattr('anteroom.routes.measurements.measure_read.read_measurements', read_measure)
    monkeypatch.setattr('anteroom.routes.measurements.size_tag_read.read_size_tag', read_tag)

    files = [('files', ('a.jpg', _photo(), 'image/jpeg'))]
    res_m = client.post('/api/measurements/read', files=files, data={'garment': 'top'}, headers=alice)
    assert res_m.status_code == 200, res_m.text

    res_t = client.post('/api/size-tags/read', files=files, headers=alice)
    assert res_t.status_code == 200, res_t.text

    assert seen_models == {
        'measurement': DEFAULT_MANAGED_MEASUREMENT_MODEL,
        'size_tag': DEFAULT_MANAGED_MODEL,
    }
