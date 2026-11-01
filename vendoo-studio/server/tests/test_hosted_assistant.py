"""The business assistant on the web: one answer at a time per seller, private threads and images."""
import base64
import io
from urllib.parse import quote

from PIL import Image

from test_business_assistant import Provider
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.services import box_scout


def test_assistant_answers_privately_per_seller_without_reading_local_sourcing(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    objects = {}

    def storage(method, key, token=None, data=None):
        assert key.startswith(USER_ID + '/')
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
    monkeypatch.setattr(box_scout, 'state_path', lambda: (_ for _ in ()).throw(AssertionError('read the Mac sourcing file')))
    monkeypatch.setattr(assistant_route, 'get_listing_provider', lambda: Provider(('You sold ', 'one item.')))
    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'assistant' in runtime['views'] and 'assistant' in runtime['features']
    assert client.get('/api/assistant/threads').status_code == 401

    assert client.put('/api/assistant/note', headers=alice, json={'note': 'Alice sells coats'}).status_code == 200
    assert client.get('/api/assistant/note', headers=bob).json()['note'] != 'Alice sells coats'
    thread = client.post('/api/assistant/threads', headers=alice).json()['id']
    image = io.BytesIO()
    Image.new('RGB', (8, 6), 'blue').save(image, 'PNG')
    encoded = base64.b64encode(image.getvalue()).decode()
    answer = client.post('/api/assistant/messages', headers=alice, json={
        'text': 'How did I do?', 'thread_id': thread,
        'attachments': [{'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + encoded}]})
    assert answer.status_code == 200, answer.text
    assert 'one item.' in answer.text
    messages = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
    assert [row['role'] for row in messages] == ['user', 'assistant']
    key = messages[0]['attachments'][0]['id']
    assert key.startswith(f'{USER_ID}/{thread}/') and key in objects
    assert client.get('/api/chat-attachments/' + quote(key), headers=alice).status_code == 200
    assert client.get('/api/chat-attachments/' + quote(key), headers=bob).status_code in (403, 404)

    # Bob never sees Alice's conversation, and his own answer is not blocked by hers.
    assert client.get('/api/assistant/threads', headers=bob).json() == []
    assert client.get('/api/assistant/messages?thread_id=' + thread, headers=bob).status_code == 404
    assert client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=bob).text.strip().endswith('[DONE]')
    assert client.delete('/api/assistant/threads/' + thread, headers=bob).status_code == 404
    assert client.delete('/api/assistant/threads/' + thread, headers=alice).status_code == 200
    assert key not in objects
