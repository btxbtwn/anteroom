from __future__ import annotations

import io
import json
import logging
import time
import urllib.error
import uuid

import jwt
from jwt.exceptions import PyJWKClientConnectionError
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vendoo_studio.hosted import create_app
from vendoo_studio.services.seller_sessions import SessionVerifier

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
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.http"):
        response = get_account(client, token)
    assert response.status_code == 503
    # The operator's report names the key fetch that failed; the seller's answer does not.
    [record] = [r for r in caplog.records if r.name == "vendoo_studio.http"]
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
    assert not [r for r in caplog.records if r.name == "vendoo_studio.http" and r.levelname == "ERROR"]
