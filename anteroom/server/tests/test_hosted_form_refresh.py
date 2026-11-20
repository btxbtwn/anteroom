"""Hosted editors expose the same fenced form refresh as desktop editors."""
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from anteroom.routes import hosted_workspace
from anteroom.services import native_editor


def test_hosted_refresh_uses_seller_session_and_revision(monkeypatch):
    app = FastAPI()
    app.include_router(hosted_workspace.router)
    seller_db = object()
    sessions: list[str] = []

    async def fake_in_seller_session(_request, work, *args):
        sessions.append(work.__name__)
        return work(seller_db, *args)

    inputs = native_editor.FormSnapshot(snapshot={"title": "Tee"}, listing_json={"title": "Tee"})
    snapshot = Mock(return_value=inputs)
    snapshot.__name__ = "form_snapshot"
    build = AsyncMock(return_value={"marketplace": "ebay", "fields": [{"path": "price"}]})
    save = Mock(return_value={"revision_id": "rebuilt"})
    save.__name__ = "save_refreshed_form"
    monkeypatch.setattr(hosted_workspace, "in_seller_session", fake_in_seller_session)
    monkeypatch.setattr(native_editor, "form_snapshot", snapshot)
    monkeypatch.setattr(native_editor, "build_form", build)
    monkeypatch.setattr(native_editor, "save_refreshed_form", save)
    with TestClient(app) as client:
        response = client.post('/api/conversations/draft/native-marketplaces/ebay/form/refresh',
                               json={"revision_id": "previous"})
    assert response.status_code == 200
    assert response.json() == {"revision_id": "rebuilt"}
    assert sessions == ["form_snapshot", "save_refreshed_form"]
    snapshot.assert_called_once_with(seller_db, 'draft', 'ebay', 'previous')
    save.assert_called_once_with(seller_db, 'draft', 'ebay', 'previous',
                                 {"marketplace": "ebay", "fields": [{"path": "price"}]})


@pytest.mark.parametrize('status', [404, 409, 502])
def test_hosted_refresh_preserves_refusals(monkeypatch, status):
    from anteroom.services.native_marketplaces import NativeMarketplaceError

    app = FastAPI()
    app.include_router(hosted_workspace.router)

    async def fake_in_seller_session(_request, work, *args):
        return work(object(), *args)

    monkeypatch.setattr(hosted_workspace, "in_seller_session", fake_in_seller_session)
    monkeypatch.setattr(native_editor, 'form_snapshot',
                        Mock(side_effect=NativeMarketplaceError(status, 'Reload refused')))
    with TestClient(app) as client:
        response = client.post('/api/conversations/draft/native-marketplaces/ebay/form/refresh',
                               json={'revision_id': 'previous'})
    assert response.status_code == status
    assert response.json() == {'detail': 'Reload refused'}
