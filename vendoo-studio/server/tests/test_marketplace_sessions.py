from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services.extension_connection import _public_connect, _public_session
from vendoo_studio.services.browser_bridge import BrowserBridgeError


def test_session_summary_drops_cookies_and_tokens():
    summary = _public_session({
        "state": "signed_in",
        "expires_at": 1_900_000_000,
        "label": "  ada  ",
        "expiring": False,
        "access_token": "secret-token",
        "headers": {"authorization": "Bearer secret"},
    })
    assert summary == {"state": "signed_in", "expires_at": 1900000000, "label": "ada", "expiring": False}
    assert "secret" not in str(summary)
    assert _public_session({"state": "signed_out", "label": "ada", "expires_at": 10, "access_token": "secret"}) == {
        "state": "signed_out", "expires_at": None, "label": None, "expiring": False,
    }
    assert _public_connect({"ok": True, "state": "expired", "signed_in": True, "access_token": "secret"})["signed_in"] is False


def test_session_status_asks_to_connect_chrome():
    with patch("vendoo_studio.services.browser_bridge.request", new=AsyncMock(side_effect=BrowserBridgeError("Connect Chrome to use the Vendoo browser."))):
        response = TestClient(app).get("/api/marketplaces/sessions")
    assert response.status_code == 409


def test_connect_response_cannot_carry_a_token():
    def request(job, message_type, payload=None, timeout=40):
        assert message_type == "marketplace.connect"
        assert payload["marketplace"] == "depop"
        return {"ok": True, "state": "signed_in", "signed_in": True, "label": "ada", "access_token": "secret-token", "page_read_status": 200}

    with patch("vendoo_studio.services.browser_bridge.request", new=AsyncMock(side_effect=request)):
        response = TestClient(app).post("/api/marketplaces/depop/connect")
    assert response.status_code == 200
    assert response.json()["signed_in"] is True
    assert "secret" not in response.text
    assert "page_read_status" not in response.json()


def test_marketplace_request_recording_is_not_offered():
    # The recorder needed Chrome's debugger permission, which the Bridge no longer asks for.
    with patch("vendoo_studio.services.browser_bridge.request", new=AsyncMock()) as request, TestClient(app) as client:
        assert client.post("/api/marketplaces/mercari/record?goal=offers").status_code == 404
        assert client.get("/api/marketplaces/mercari/record?goal=offers").status_code == 404
    request.assert_not_awaited()


def test_unknown_marketplace_connect_is_rejected():
    with patch("vendoo_studio.services.browser_bridge.request", new=AsyncMock()) as request:
        response = TestClient(app).post("/api/marketplaces/ebay/connect")
    assert response.status_code == 404
    request.assert_not_awaited()
