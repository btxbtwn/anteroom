"""An upload answers the photo as the shared contract's ListingPhoto, the
same shape the photo list returns, so the iPhone can show it at once instead
of failing to read the answer and reporting a saved photo as not uploaded."""
from io import BytesIO

from PIL import Image

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from anteroom.routes import api_contract as contract
from anteroom.services import hosted_photos


def test_upload_answers_the_listed_photo(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Wool coat'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = BytesIO()
    Image.new('RGB', (40, 20), 'red').save(image, 'JPEG')
    uploaded = client.post(f'/api/drafts/{identity}/photos', headers=alice, content=image.getvalue())
    assert uploaded.status_code == 201
    photo = contract.ListingPhoto.model_validate(uploaded.json())
    listed = client.get(f'/api/conversations/{identity}/photos', headers=alice).json()
    assert [photo.model_dump(mode='json')] == listed
    assert photo.conversation_id == identity and photo.display_order == 0 and photo.width == 40
