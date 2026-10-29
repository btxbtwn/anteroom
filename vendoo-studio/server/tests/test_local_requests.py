"""Browser request rejection happens before a privileged handler runs."""
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vendoo_studio.main import app
from vendoo_studio.services import local_requests, tailscale_serve


@pytest.fixture(autouse=True)
def trusted_private_origin(monkeypatch):
    monkeypatch.setattr(local_requests, "_private_origin", None)


@pytest.mark.parametrize("headers", [
    {"Origin": "https://foreign.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://127.0.0.1:9999", "Sec-Fetch-Site": "same-site"},
])
def test_foreign_simple_post_never_reaches_update_handler(headers):
    from vendoo_studio.routes import updates

    with patch.object(updates, "_cancel_leftover_jobs") as cancel, patch.object(updates.update_service, "apply_update") as apply:
        reply = TestClient(app).post("/api/updates/apply", headers=headers, content="")
    assert reply.status_code == 403
    cancel.assert_not_called()
    apply.assert_not_called()


@pytest.mark.parametrize("host", ["foreign.example", "127.0.0.1.foreign.example", "user@127.0.0.1", "127.0.0.1:bad", "127.0.0.1/path"])
def test_untrusted_host_cannot_read_pairing_token(host):
    reply = TestClient(app).get("/api/extension/pairing-token", headers={"Host": host})
    assert reply.status_code == 403
    assert "token" not in reply.json()


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "http://127.0.0.1:4318", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "same-site"},
    {"Origin": "chrome-extension://" + "a" * 32},
])
def test_local_app_cli_and_extension_can_read_health(headers):
    assert TestClient(app).get("/api/health", headers=headers).status_code == 200


def test_only_the_configured_private_proxy_host_is_trusted():
    private = "https://mac.tailnet.ts.net:9444"
    with patch.object(tailscale_serve, "_which_tailscale", return_value="tailscale"), \
         patch.object(tailscale_serve, "dns_name", return_value="mac.tailnet.ts.net"), \
         patch.object(tailscale_serve, "_serve_status", return_value={}), \
         patch.object(tailscale_serve, "_classify_route", return_value="configured"):
        tailscale_serve.status()
    assert TestClient(app, base_url=private).get("/api/health", headers={"Origin": private}).status_code == 200
    assert TestClient(app, base_url="https://another.tailnet.ts.net:9444").get("/api/health").status_code == 403
    local_requests.set_private_origin(None)
    assert TestClient(app, base_url=private).get("/api/health").status_code == 403


def test_foreign_websocket_is_rejected_before_accepting():
    test_app = FastAPI()
    test_app.add_middleware(local_requests.LocalRequestMiddleware)

    @test_app.websocket("/socket")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("trusted")
        await ws.close()

    client = TestClient(test_app)
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/socket", headers={"Origin": "https://foreign.example"}):
        pass
    assert refused.value.code == 1008
    with client.websocket_connect("/socket", headers={"Origin": "http://127.0.0.1:4318"}) as ws:
        assert ws.receive_text() == "trusted"


def test_a_non_loopback_bind_is_refused():
    env = {**os.environ, "VENDOO_STUDIO_HOST": "0.0.0.0"}
    result = subprocess.run([sys.executable, "-c", "import vendoo_studio.config"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "must bind to 127.0.0.1" in result.stderr
