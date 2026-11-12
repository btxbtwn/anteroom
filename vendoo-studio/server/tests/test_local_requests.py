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
    monkeypatch.setattr(local_requests, "_private_owner", None)


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
    {"Origin": "chrome-extension://empinkdlhndpfoigoipbiiofaeepcheo"},
])
def test_local_app_cli_and_extension_can_read_health(headers):
    assert TestClient(app).get("/api/health", headers=headers).status_code == 200


PRIVATE = "https://mac.tailnet.ts.net:9444"


def _serve_on(owner: str | None = "seller@example.com"):
    with patch.object(tailscale_serve, "_which_tailscale", return_value="tailscale"), \
         patch.object(tailscale_serve, "identity", return_value=("mac.tailnet.ts.net", owner)), \
         patch.object(tailscale_serve, "_serve_status", return_value={}), \
         patch.object(tailscale_serve, "_classify_route", return_value="configured"):
        tailscale_serve.status()


def test_only_the_configured_private_proxy_host_is_trusted():
    _serve_on()
    owner = {"Origin": PRIVATE, "Tailscale-User-Login": "seller@example.com"}
    assert TestClient(app, base_url=PRIVATE).get("/api/health", headers=owner).status_code == 200
    assert TestClient(app, base_url="https://another.tailnet.ts.net:9444").get("/api/health", headers=owner).status_code == 403
    local_requests.set_private_origin(None)
    assert TestClient(app, base_url=PRIVATE).get("/api/health", headers=owner).status_code == 403


@pytest.mark.parametrize("headers", [
    {},  # a tagged device: Serve sends no identity
    {"Tailscale-User-Login": "someone-else@example.com"},
    {"Tailscale-User-Login": ""},
])
def test_private_proxy_admits_only_the_macs_own_tailnet_user(headers):
    _serve_on()
    reply = TestClient(app, base_url=PRIVATE).get("/api/health", headers={"Origin": PRIVATE, **headers})
    assert reply.status_code == 403


def test_another_tailnet_user_cannot_reach_loopback_by_naming_it():
    _serve_on()
    reply = TestClient(app).get("/api/health", headers={"Tailscale-User-Login": "someone-else@example.com"})
    assert reply.status_code == 403


def test_a_tagged_mac_admits_nobody_over_the_private_proxy():
    _serve_on(owner=None)
    reply = TestClient(app, base_url=PRIVATE).get("/api/health", headers={"Origin": PRIVATE, "Tailscale-User-Login": "seller@example.com"})
    assert reply.status_code == 403


def test_owner_login_comes_from_this_macs_tailscale_user():
    status = {"Self": {"DNSName": "mac.tailnet.ts.net.", "UserID": 7},
              "User": {"7": {"LoginName": "seller@example.com"}, "8": {"LoginName": "other@example.com"}}}
    with patch.object(tailscale_serve, "_run_json", return_value=status):
        assert tailscale_serve.identity() == ("mac.tailnet.ts.net", "seller@example.com")
    tagged = {"Self": {"DNSName": "mac.tailnet.ts.net.", "UserID": 9, "Tags": ["tag:server"]},
              "User": {"9": {"LoginName": "tagged-devices"}}}
    with patch.object(tailscale_serve, "_run_json", return_value=tagged):
        assert tailscale_serve.identity() == ("mac.tailnet.ts.net", None)


def test_only_anteroom_bridge_extensions_are_trusted(monkeypatch):
    monkeypatch.delenv("ANTEROOM_BRIDGE_EXTENSION_IDS", raising=False)
    local_requests.trusted_extension_ids.cache_clear()
    bridge = "chrome-extension://" + local_requests.STORE_BRIDGE_ID
    other = "chrome-extension://" + "a" * 32
    assert TestClient(app).get("/api/health", headers={"Origin": bridge}).status_code == 200
    assert TestClient(app).get("/api/health", headers={"Origin": other}).status_code == 403
    assert TestClient(app).get("/api/extension/pairing-token", headers={"Origin": other}).status_code == 403


def test_the_bridge_this_mac_loads_unpacked_is_trusted(monkeypatch):
    from vendoo_studio.services.chrome_bridge import installed_extension_dir

    local_requests.trusted_extension_ids.cache_clear()
    unpacked = local_requests.unpacked_extension_id(str(installed_extension_dir().resolve()))
    try:
        assert TestClient(app).get("/api/health", headers={"Origin": "chrome-extension://" + unpacked}).status_code == 200
        monkeypatch.setenv("ANTEROOM_BRIDGE_EXTENSION_IDS", " " + "b" * 32 + ",")
        local_requests.trusted_extension_ids.cache_clear()
        assert TestClient(app).get("/api/health", headers={"Origin": "chrome-extension://" + "b" * 32}).status_code == 200
    finally:
        local_requests.trusted_extension_ids.cache_clear()


def test_unpacked_extension_id_matches_chrome():
    # Chromium's id_util_unittest: GenerateId("test").
    assert local_requests.unpacked_extension_id("test") == "jpignaibiiemhngfjkcpokkamffknabf"


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


def test_a_browser_returning_from_marketplace_sign_in_reaches_only_the_finish_page():
    from vendoo_studio.services import marketplace_accounts

    async def finish(marketplace, state, claim):
        assert (marketplace, state, claim) == ("ebay", "s1", "c1")
        return {"marketplace": "ebay", "label": "<cris>"}

    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    with patch.object(marketplace_accounts, "finish_sign_in", finish):
        page = TestClient(app).get("/api/marketplace-accounts/ebay/finish?marketplace=ebay&state=s1&claim=c1", headers=navigation)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "&lt;cris&gt; is connected" in page.text and "<cris>" not in page.text
    # The exception covers that one page, for GET only, from Anteroom's own address.
    assert TestClient(app).post("/api/marketplace-accounts/ebay/finish", headers=navigation, json={}).status_code == 403
    assert TestClient(app).get("/api/marketplace-accounts", headers=navigation).status_code == 403
    assert TestClient(app).get("/api/marketplace-accounts/ebay/finish?state=s1&claim=c1",
                               headers={**navigation, "Host": "foreign.example"}).status_code == 403


def test_a_sign_in_this_mac_did_not_start_is_not_finished():
    from vendoo_studio.services import marketplace_accounts

    async def finish(marketplace, state, claim):
        raise marketplace_accounts.SignInNotFound("This sign-in was not started in this Anteroom, or it expired.")

    with patch.object(marketplace_accounts, "finish_sign_in", finish):
        page = TestClient(app).get("/api/marketplace-accounts/etsy/finish?state=s1&claim=c1", headers={"Sec-Fetch-Site": "cross-site"})
    assert page.status_code == 409
    assert "Etsy was not connected" in page.text
