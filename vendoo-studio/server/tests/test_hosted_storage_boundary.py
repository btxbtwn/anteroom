"""The privileged Storage credential may touch only the execution's namespace."""
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from vendoo_studio.services import hosted_storage, service_context
from test_service_context import runtime

OWNER = 'a7a5ee16-9850-49ba-b5bd-83b8be54df46'
KEY = f'{OWNER}/draft_1/{"b" * 32}.jpg'


@pytest.mark.parametrize('key', [
    KEY.replace(OWNER, 'another-seller'), KEY.replace('draft_1', '..'),
    KEY.replace('draft_1', '%2e%2e'), KEY + '/extra', KEY.replace('.jpg', '.png'),
    KEY.replace('draft_1', 'draft?x=1'), KEY.replace('draft_1', 'draft#fragment'),
])
def test_foreign_and_malformed_keys_never_reach_storage(monkeypatch, key):
    request = Mock(side_effect=AssertionError('No privileged call'))
    monkeypatch.setattr(httpx, 'request', request)
    with service_context.use(runtime(OWNER)), pytest.raises(HTTPException) as error:
        hosted_storage.photo_call('GET', key)
    assert error.value.status_code == 404
    request.assert_not_called()


def test_background_storage_requires_verified_context(monkeypatch):
    request = Mock()
    monkeypatch.setattr(httpx, 'request', request)
    with pytest.raises(RuntimeError, match='verified seller'):
        hosted_storage.photo_call('GET', KEY)
    request.assert_not_called()


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_background_storage_uses_server_apikey_without_a_user_bearer(monkeypatch, method):
    monkeypatch.setenv('SUPABASE_SERVER_SECRET_KEY', 'fake-server-credential')
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    request = Mock(return_value=httpx.Response(200, content=b'photo'))
    monkeypatch.setattr(httpx, 'request', request)
    with service_context.use(runtime(OWNER)):
        assert hosted_storage.photo_call(method, KEY, b'input') == b'photo'
    args, options = request.call_args
    assert args[0] == method
    assert options['headers']['apikey'] == 'fake-server-credential'
    assert 'Authorization' not in options['headers']
    assert options['follow_redirects'] is False
    if method == 'DELETE':
        assert options['json'] == {'prefixes': [KEY]}
    else:
        assert args[1].endswith('/' + KEY)


def test_storage_failure_does_not_reflect_provider_details(monkeypatch):
    monkeypatch.setenv('SUPABASE_SERVER_SECRET_KEY', 'fake-server-credential')
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setattr(httpx, 'request', Mock(side_effect=httpx.ConnectError('sensitive diagnostic')))
    with service_context.use(runtime(OWNER)), pytest.raises(HTTPException) as error:
        hosted_storage.photo_call('GET', KEY)
    assert error.value.status_code == 503
    assert 'sensitive' not in str(error.value)
