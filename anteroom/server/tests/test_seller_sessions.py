from __future__ import annotations

import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import urllib.error
import uuid

import jwt
from jwt.exceptions import PyJWKClientConnectionError
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from anteroom.hosted import create_app
from anteroom.services.seller_sessions import SessionVerifier

PROJECT_URL = "https://seller-project.supabase.co"
USER_ID = str(uuid.uuid4())


@pytest.fixture
def signing_keys():
    private = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    public.update(kid="current", alg="ES256", use="sig")
    return private, public


@pytest.fixture
def jwks(monkeypatch, signing_keys):
    state = {"keys": [signing_keys[1]], "requests": []}

    def open_keys(_opener, request, **kwargs):
        state["requests"].append(request.full_url)
        if state.get("unavailable"):
            raise urllib.error.URLError("Private diagnostic that must never reach the client")
        return io.BytesIO(json.dumps({"keys": state["keys"]}).encode())

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_keys)
    return state


@pytest.fixture
def client(jwks):
    with TestClient(create_app(PROJECT_URL)) as client:
        yield client


def session_token(signing_keys, **changes):
    claims = {
        "sub": USER_ID,
        "iss": PROJECT_URL + "/auth/v1",
        "aud": "authenticated",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()) - 1,
        "role": "authenticated",
        "session_id": str(uuid.uuid4()),
        "is_anonymous": False,
    }
    claims.update(changes)
    return jwt.encode(claims, signing_keys[0], algorithm="ES256", headers={"kid": "current"})


def get_account(client, token):
    return client.get("/api/account", headers={"Authorization": f"Bearer {token}"})


def test_verified_identity_and_key_cache(client, signing_keys, jwks):
    token = session_token(signing_keys, email="private@example.com")
    for _ in range(2):
        response = get_account(client, token)
        assert response.status_code == 200
        assert response.json() == {"user_id": USER_ID}
        assert response.headers["cache-control"] == "no-store"
        assert token not in response.text
    assert jwks["requests"] == [PROJECT_URL + "/auth/v1/.well-known/jwks.json"]



def test_seller_verification_runs_while_mcp_token_workers_are_blocked(client, signing_keys, monkeypatch):
    from anteroom.services import mcp_tokens

    waiting = Event()
    release = Event()

    def blocked_auth(*_args, **_kwargs):
        waiting.set()
        if not release.wait(timeout=10):
            pytest.fail("MCP authentication was never released")
        return None

    monkeypatch.setattr(mcp_tokens, "authenticate_token", blocked_auth)
    with ThreadPoolExecutor(max_workers=5) as requests:
        checks = [requests.submit(
            client.post, "/api/mcp", headers={"Authorization": "Bearer arm_fixture"},
        ) for _ in range(4)]
        try:
            assert waiting.wait(timeout=5)
            account = requests.submit(get_account, client, session_token(signing_keys))
            assert account.result(timeout=5).status_code == 200
            assert all(not check.done() for check in checks)
        finally:
            release.set()
        assert [check.result(timeout=5).status_code for check in checks] == [401] * 4


def test_mcp_auth_threads_shut_down_on_lifespan_exit(jwks):
    app = create_app(PROJECT_URL)
    with TestClient(app):
        assert app.state.mcp_auth_threads is not app.state.auth_threads
    with pytest.raises(RuntimeError):
        app.state.mcp_auth_threads.submit(lambda: None)


def test_seller_identity_is_per_request(client, signing_keys):
    other_seller = str(uuid.uuid4())
    assert get_account(client, session_token(signing_keys, sub=other_seller)).json() == {"user_id": other_seller}
    assert get_account(client, session_token(signing_keys)).json() == {"user_id": USER_ID}
    assert client.get("/api/account").status_code == 401


@pytest.mark.parametrize("changes", [
    {"exp": -60},
    {"iat": 300},
    {"nbf": 300},
    {"iss": "https://another-project.supabase.co/auth/v1"},
    {"aud": "anon"},
    {"role": "service_role"},
    {"role": "anon"},
    {"sub": "not-a-uuid"},
    {"sub": 42},
    {"session_id": "not-a-session"},
    {"session_id": None},
    {"is_anonymous": True},
    {"is_anonymous": "false"},
    {"is_anonymous": None},
])
def test_invalid_claims_rejected(client, signing_keys, changes):
    # Compute relative dates when the test runs, not during collection: a
    # long backend suite can outlast a token's future-date offset.
    changes = {
        key: int(time.time()) + value if key in {"exp", "iat", "nbf"} else value
        for key, value in changes.items()
    }
    response = get_account(client, session_token(signing_keys, **changes))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "sub", "role", "session_id"])
def test_required_claims(client, signing_keys, missing):
    claims = jwt.decode(session_token(signing_keys), options={"verify_signature": False})
    del claims[missing]
    token = jwt.encode(claims, signing_keys[0], algorithm="ES256", headers={"kid": "current"})
    assert get_account(client, token).status_code == 401


@pytest.mark.parametrize("authorization", [None, "", "Basic password", "Bearer", "Bearer ", "Bearer invalid", "Bearer " + "x" * 16385])
def test_invalid_credentials_rejected_without_key_fetch(client, jwks, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get("/api/account", headers=headers)
    assert response.status_code == 401
    assert jwks["requests"] == []


def test_duplicate_credentials_rejected(client, signing_keys, jwks):
    value = "Bearer " + session_token(signing_keys)
    response = client.get("/api/account", headers=[("Authorization", value), ("Authorization", value)])
    assert response.status_code == 401
    assert jwks["requests"] == []


def test_forged_signature(client, signing_keys):
    forged = ec.generate_private_key(ec.SECP256R1())
    token = session_token((forged, signing_keys[1]))
    assert get_account(client, token).status_code == 401


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_untrusted_algorithms_never_fetch_keys(client, jwks, algorithm):
    token = jwt.encode({"sub": USER_ID}, "" if algorithm == "none" else "x" * 32,
                       algorithm=algorithm, headers={"kid": "current"})
    assert get_account(client, token).status_code == 401
    assert jwks["requests"] == []


def test_rsa_project(client, jwks):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="rsa-key", alg="RS256", use="sig")
    jwks["keys"] = [public]
    claims = jwt.decode(session_token((ec.generate_private_key(ec.SECP256R1()), {})),
                        options={"verify_signature": False})
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "rsa-key"})
    assert get_account(client, token).json() == {"user_id": USER_ID}


def test_key_rotation_and_untrusted_key_url(client, signing_keys, jwks):
    assert get_account(client, session_token(signing_keys)).status_code == 200
    # Advance past PyJWT's refresh cooldown without waiting in real time.
    client.app.state.session_verifier.keys._last_successful_fetch -= 31
    private = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    public.update(kid="rotated", alg="ES256", use="sig")
    jwks["keys"] = [public]
    claims = jwt.decode(session_token(signing_keys), options={"verify_signature": False})
    token = jwt.encode(claims, private, algorithm="ES256",
                       headers={"kid": "rotated", "jku": "https://attacker.example/keys"})
    assert get_account(client, token).status_code == 200
    assert jwks["requests"] == [PROJECT_URL + "/auth/v1/.well-known/jwks.json"] * 2


def test_unknown_key_rejected(client, signing_keys):
    claims = jwt.decode(session_token(signing_keys), options={"verify_signature": False})
    token = jwt.encode(claims, signing_keys[0], algorithm="ES256", headers={"kid": "unknown"})
    assert get_account(client, token).status_code == 401


def test_outage_fails_closed_without_exposing_diagnostics(client, signing_keys, jwks, caplog):
    jwks["unavailable"] = True
    token = session_token(signing_keys)
    with caplog.at_level(logging.ERROR, logger="anteroom.http"):
        response = get_account(client, token)
    assert response.status_code == 503
    # The operator's report names the key fetch that failed; the seller's answer does not.
    [record] = [r for r in caplog.records if r.name == "anteroom.http"]
    assert record.exc_info and record.exc_info[0] is PyJWKClientConnectionError
    assert response.headers["retry-after"] == "5"
    assert "Private diagnostic" not in response.text
    assert token not in response.text
    # A transient outage must not poison the cache after recovery.
    jwks["unavailable"] = False
    assert get_account(client, token).status_code == 200


def test_only_health_is_public_and_desktop_routes_are_absent(client, signing_keys, jwks):
    assert client.get("/api/health").json()["status"] == "ok"
    assert jwks["requests"] == []
    for path in ["/api/desktop/chrome", "/api/extension/pairing-token", "/api/listings", "/docs", "/openapi.json"]:
        assert client.get(path, follow_redirects=False).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + session_token(signing_keys)},
                          follow_redirects=False).status_code == 404
    assert client.post("/api/health").status_code == 401
    assert client.get("/api/health/", follow_redirects=False).status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/extension"):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("url", ["", "http://seller-project.supabase.co", "https://user:password@example.com",
                                  "https://example.com/auth/v1", "https://example.com?q=1", "https://example.com/#x"])
def test_invalid_configuration_refuses_startup(url):
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        with TestClient(create_app(url)):
            pass


def test_project_origin_has_no_shared_secret():
    verifier = SessionVerifier(PROJECT_URL + "/")
    assert verifier.issuer == PROJECT_URL + "/auth/v1"


def test_large_json_is_compressed_for_phones(client, signing_keys):
    """Depop's brand list is about 300 KB of JSON; a phone downloads it gzipped."""
    auth = {"Authorization": f"Bearer {session_token(signing_keys)}", "Accept-Encoding": "gzip"}
    response = client.get("/api/catalog/choice-lists/depop-brands", headers=auth)
    assert response.status_code == 200, response.text
    assert response.headers["content-encoding"] == "gzip"
    assert int(response.headers["content-length"]) < len(response.content) / 2


def test_web_page_carries_security_headers(jwks, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "cloud.html").write_text("<!doctype html><title>Anteroom</title>")
    with TestClient(create_app(PROJECT_URL, web_dir=tmp_path)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_browser_leaving_before_the_page_loads_is_not_a_server_error(jwks, tmp_path, caplog):
    """A visitor who closes the tab mid-load gets nothing sent and no error alert."""
    import anyio

    (tmp_path / "assets").mkdir()
    (tmp_path / "cloud.html").write_text("<!doctype html><title>Anteroom</title>")
    app = create_app(PROJECT_URL, web_dir=tmp_path)
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "GET", "scheme": "https", "path": "/", "raw_path": b"/", "root_path": "",
             "query_string": b"", "headers": [(b"host", b"myanteroom.com")], "app": app,
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 443)}
    anyio.run(app, scope, receive, send)
    assert not [r for r in caplog.records if r.name == "anteroom.http" and r.levelname == "ERROR"]


# Tokens Supabase's OAuth server issues to Claude, ChatGPT or Gemini carry a
# ``client_id`` claim. They reach the MCP endpoint and nothing else.
OAUTH_CLIENT_ID = str(uuid.uuid4())


def test_connected_app_tokens_are_refused_outside_mcp(client, signing_keys):
    token = session_token(signing_keys, client_id=OAUTH_CLIENT_ID)
    for method, path in [
        ("GET", "/api/account"),
        ("GET", "/api/listings"),
        ("GET", "/api/settings/mcp-tokens"),
        ("GET", "/api/settings/mcp-connections"),
        ("POST", "/api/marketplace-listings/delist"),
    ]:
        response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, path


def test_connected_app_token_reaches_mcp_with_its_client(client, signing_keys, monkeypatch):
    from anteroom.routes import mcp as mcp_route

    seen = {}

    def fake_call(db, payload, **kwargs):
        seen.update(kwargs)
        return {"jsonrpc": "2.0", "id": 1, "result": {}}, 200, {}

    async def fake_worker_session(step):
        return step(None)

    monkeypatch.setattr(mcp_route, "_run_mcp_call", fake_call)
    monkeypatch.setattr(mcp_route, "in_worker_session", fake_worker_session)
    token = session_token(signing_keys, client_id=OAUTH_CLIENT_ID)
    response = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert seen["oauth_client_id"] == OAUTH_CLIENT_ID
    assert seen["hosted_token_id"] is None


def test_mcp_access_tokens_are_refused_outside_mcp(client, monkeypatch):
    from anteroom.services import mcp_tokens

    auth_calls = []

    def unexpected_auth(*args, **kwargs):
        auth_calls.append((args, kwargs))
        return mcp_tokens.VerifiedMcpToken(token_id="tok-1", owner_id=USER_ID)

    monkeypatch.setattr(mcp_tokens, "authenticate_token", unexpected_auth)
    token = "arm_" + "a" * 32
    for method, path in [
        ("GET", "/api/account"),
        ("GET", "/api/listings"),
        ("GET", "/api/settings/mcp-tokens"),
        ("GET", "/api/settings/mcp-connections"),
        ("POST", "/api/marketplace-listings/delist"),
        ("POST", "/api/mcp/"),
    ]:
        response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, path
    assert auth_calls == []


def test_mcp_access_token_reaches_mcp_with_its_token_id(client, monkeypatch):
    from anteroom.routes import mcp as mcp_route
    from anteroom.services import mcp_tokens

    seen = {}
    auth_calls = []

    def fake_auth(engine, raw_token, *, hosted=False):
        auth_calls.append((raw_token, hosted))
        return mcp_tokens.VerifiedMcpToken(token_id="tok-123", owner_id=USER_ID)

    def fake_call(db, payload, **kwargs):
        seen.update(kwargs)
        return {"jsonrpc": "2.0", "id": 1, "result": {}}, 200, {}

    async def fake_worker_session(step):
        return step(None)

    monkeypatch.setattr(mcp_tokens, "authenticate_token", fake_auth)
    monkeypatch.setattr(mcp_route, "_run_mcp_call", fake_call)
    monkeypatch.setattr(mcp_route, "in_worker_session", fake_worker_session)
    token = "arm_" + "b" * 32
    response = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert auth_calls == [(token, True)]
    assert seen["hosted_token_id"] == "tok-123"
    assert seen["oauth_client_id"] is None


def test_mcp_access_token_auth_is_rate_limited_in_hosted_middleware(client, monkeypatch):
    from anteroom.services import mcp_tokens

    mcp_tokens.clear_auth_failures()
    monkeypatch.setattr(mcp_tokens, "authenticate_token", lambda *_a, **_kw: None)
    auth_headers = {"Authorization": "Bearer arm_" + "c" * 32, "Fly-Client-IP": "198.51.100.42"}
    try:
        for _ in range(mcp_tokens.AUTH_FAIL_MAX):
            resp = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=auth_headers)
            assert resp.status_code == 401
        throttled = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=auth_headers)
        assert throttled.status_code == 429
        assert throttled.headers["retry-after"] == "60"
    finally:
        mcp_tokens.clear_auth_failures()


@pytest.mark.parametrize("client_id", ["", 7, "x" * 129])
def test_malformed_client_claim_is_refused(client, signing_keys, client_id):
    token = session_token(signing_keys, client_id=client_id)
    assert client.post("/api/mcp", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert get_account(client, token).status_code == 401


def test_mcp_challenge_points_apps_at_the_sign_in_metadata(client):
    response = client.post("/api/mcp", json={}, headers={"Host": "myanteroom.com"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://myanteroom.com/.well-known/oauth-protected-resource/api/mcp"'
    )
    # Other routes keep the plain challenge.
    assert client.get("/api/account").headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource",
                                  "/.well-known/oauth-protected-resource/api/mcp"])
def test_protected_resource_metadata_is_public(client, jwks, path):
    response = client.get(path, headers={"Host": "myanteroom.com"})
    assert response.status_code == 200
    assert response.json() == {
        "resource": "https://myanteroom.com/api/mcp",
        "authorization_servers": [PROJECT_URL + "/auth/v1"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Anteroom",
    }
    assert jwks["requests"] == []


def test_app_approval_page_opens_without_a_session(jwks, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "cloud.html").write_text("<!doctype html><title>Anteroom</title>")
    with TestClient(create_app(PROJECT_URL, web_dir=tmp_path)) as client:
        response = client.get("/oauth/consent?authorization_id=abc")
    assert response.status_code == 200
    assert "<title>Anteroom</title>" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
