"""The phone's bulk listing reads measurements from photos on the hosted API."""
import io

from PIL import Image

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.services.service_context import current


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

    monkeypatch.setattr('vendoo_studio.routes.measurements.get_photo_provider', provider)
    monkeypatch.setattr('vendoo_studio.routes.measurements.measure_read.read_measurements', read)
    files = [('files', ('a.jpg', _photo(), 'image/jpeg')), ('files', ('b.jpg', _photo(), 'image/jpeg'))]

    assert client.post('/api/measurements/read', files=files, data={'garment': 'top'}).status_code == 401
    answer = client.post('/api/measurements/read', files=files, data={'garment': 'top'},
                         headers=headers(signing_keys, BOB))
    assert answer.status_code == 200, answer.text
    assert answer.json()['measurements']['pitToPit']['value'] == 21
    assert sellers == [BOB]
