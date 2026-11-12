"""Hosted editors expose the same fenced form refresh as desktop editors."""
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vendoo_studio.routes.hosted_workspace import router
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services import native_editor


def test_hosted_refresh_uses_seller_session_and_revision(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    seller_db = object()
    app.dependency_overrides[get_seller_db] = lambda: seller_db
    refresh = Mock(return_value={"revision_id": "rebuilt"})
    monkeypatch.setattr(native_editor, "refresh_form", refresh)
    with TestClient(app) as client:
        response = client.post('/api/conversations/draft/native-marketplaces/ebay/form/refresh',
                               json={"revision_id": "previous"})
    assert response.status_code == 200
    assert response.json() == {"revision_id": "rebuilt"}
    refresh.assert_called_once_with(seller_db, 'draft', 'ebay', 'previous')


@pytest.mark.parametrize('status', [404, 409, 502])
def test_hosted_refresh_preserves_refusals(monkeypatch, status):
    from vendoo_studio.services.native_marketplaces import NativeMarketplaceError

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_seller_db] = lambda: object()
    monkeypatch.setattr(native_editor, 'refresh_form',
                        Mock(side_effect=NativeMarketplaceError(status, 'Reload refused')))
    with TestClient(app) as client:
        response = client.post('/api/conversations/draft/native-marketplaces/ebay/form/refresh',
                               json={'revision_id': 'previous'})
    assert response.status_code == status
    assert response.json() == {'detail': 'Reload refused'}
