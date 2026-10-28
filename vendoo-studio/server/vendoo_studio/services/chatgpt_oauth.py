"""Sign in with ChatGPT: OpenAI's token sharing for open-source apps.

The seller approves Anteroom in the browser and OpenAI hands back a token that
runs requests on their own ChatGPT plan against the public API
(``https://api.openai.com/v1``), the same Responses API an API key reaches.
https://developers.openai.com/siwc/token-sharing-open-source/sign-in

Ported from T3 Code (apps/server/src/provider/CodexChatGptAuth.ts,
https://github.com/pingdotgg/t3code, MIT License, (c) 2026 T3 Tools Inc.),
reduced to the one connection Anteroom keeps:

- The first sign-in registers with ``client_id=dynamic_agent_client``; OpenAI
  returns an ``oaiapp_...`` client id, saved and reused for every later
  sign-in and refresh.
- The redirect is a short-lived listener on ``127.0.0.1`` (never
  ``localhost``); only its port may change between attempts.
- The id token is verified against OpenAI's published keys before anything
  is saved, and a sign-in without the token-sharing scope is refused.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import secrets
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import jwt

from vendoo_studio.services.keychain import (
    delete_chatgpt_tokens,
    get_chatgpt_registration,
    get_chatgpt_tokens,
    set_chatgpt_registration,
    set_chatgpt_tokens,
)

log = logging.getLogger("vendoo_studio.chatgpt_oauth")

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
SCOPE = f"openid profile email offline_access resource.invoke {REQUIRED_SCOPE}"
FIRST_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "Anteroom"
CALLBACK_PATH = "/auth/callback"
LOGIN_TIMEOUT_S = 15 * 60
# A token this close to expiry is renewed before use.
REFRESH_MARGIN_S = 60
# The seller's browser waits on the callback page while the code is exchanged.
CALLBACK_PAGE_WAIT_S = 60
REVOKED_ERRORS = frozenset({
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
})
SIGN_IN_AGAIN = "Your ChatGPT connection expired or was disconnected. Sign in again."


@dataclass
class PendingLogin:
    authorize_url: str
    redirect_uri: str
    state: str
    nonce: str
    verifier: str
    client_id: str | None
    server: HTTPServer | None = None
    callback: asyncio.Future | None = None
    task: asyncio.Task | None = None
    error: str | None = None
    done: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    callback_used: bool = False


_pending: PendingLogin | None = None
_pending_guard = threading.Lock()
_refresh_lock = asyncio.Lock()
_discovery: dict | None = None


# ---------------------------------------------------------------------------
# Saved connection


def _valid_record(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    client_id = record.get("client_id")
    if not (isinstance(client_id, str) and client_id.startswith("oaiapp_")):
        return None
    if not isinstance(record.get("access_token"), str) or not record["access_token"]:
        return None
    if REQUIRED_SCOPE not in (record.get("scopes") or []):
        return None
    return record


def _saved_record() -> dict | None:
    """The saved connection, or None. A record from before this sign-in existed is dropped."""
    record = get_chatgpt_tokens()
    if record is None:
        return None
    valid = _valid_record(record)
    if valid is None:
        # The old Codex sign-in stored a different shape that no longer works.
        delete_chatgpt_tokens()
    return valid


def chatgpt_signed_in() -> bool:
    return _saved_record() is not None


def _b64url_json(segment: str) -> dict[str, Any]:
    padded = segment + "=" * ((4 - len(segment) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def jwt_claims(token: str | None) -> dict[str, Any]:
    """Claims of a token already verified at sign-in, read for display only."""
    if not token or token.count(".") != 2:
        return {}
    return _b64url_json(token.split(".")[1])


def profile_from_tokens(record: dict | None) -> dict[str, str | None]:
    if not record:
        return {"email": None, "plan": None}
    claims = jwt_claims(record.get("id_token"))
    auth = claims.get("https://api.openai.com/auth")
    plan = auth.get("chatgpt_plan_type") if isinstance(auth, dict) else None
    email = record.get("email") or claims.get("email")
    return {
        "email": email if isinstance(email, str) else None,
        "plan": plan if isinstance(plan, str) else None,
    }


# ---------------------------------------------------------------------------
# OpenAI's endpoints


async def _discover() -> dict:
    """OpenAI's OAuth endpoints, checked to all live on auth.openai.com."""
    global _discovery
    if _discovery is not None:
        return _discovery
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(DISCOVERY_URL)
        data = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        data = None
    keys = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in keys):
        raise RuntimeError("Could not reach ChatGPT sign-in. Try again.")
    urls = [data[key] for key in keys[1:]]
    if isinstance(data.get("revocation_endpoint"), str):
        urls.append(data["revocation_endpoint"])
    if data["issuer"] != ISSUER or any(_origin(url) != ISSUER for url in urls):
        raise RuntimeError("ChatGPT sign-in configuration could not be verified.")
    _discovery = data
    return data


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class OAuthError(RuntimeError):
    def __init__(self, code: str | None, status: int):
        super().__init__(code or f"HTTP {status}")
        self.code = code
        self.status = status


async def _token_request(body: dict[str, str]) -> dict:
    endpoints = await _discover()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            endpoints["token_endpoint"],
            headers={"Accept": "application/json"},
            data=body,
        )
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if resp.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            error = error.get("code")
        raise OAuthError(error if isinstance(error, str) else None, resp.status_code)
    if not isinstance(payload, dict):
        raise RuntimeError("ChatGPT returned an invalid token response.")
    return payload


def _token_fields(payload: dict) -> dict:
    access = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    scope = payload.get("scope")
    if (
        not isinstance(access, str)
        or not access
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
        or not isinstance(expires_in, int)
        or expires_in <= 0
        or not isinstance(scope, str)
    ):
        raise RuntimeError("ChatGPT returned an invalid token response.")
    refresh = payload.get("refresh_token")
    return {
        "access_token": access,
        "refresh_token": refresh if isinstance(refresh, str) and refresh else None,
        "expires_at": time.time() + expires_in,
        "earliest_refresh_at": _epoch(payload.get("earliest_refresh_at")),
        "scopes": scope.split(),
    }


def _epoch(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


async def _verified_identity(id_token: str, client_id: str, nonce: str) -> dict:
    """Subject and email from an id token signed by OpenAI for this client."""
    endpoints = await _discover()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(endpoints["jwks_uri"])
        keys = jwt.PyJWKSet.from_dict(resp.json())
        kid = jwt.get_unverified_header(id_token).get("kid")
        key = next(item for item in keys.keys if kid is None or item.key_id == kid)
        claims = jwt.decode(
            id_token,
            key=key.key,
            algorithms=["RS256", "ES256"],
            audience=client_id,
            issuer=endpoints["issuer"],
            leeway=5,
            options={"require": ["exp", "sub"]},
        )
    except Exception as exc:
        raise RuntimeError("ChatGPT sign-in could not be verified. Start again.") from exc
    if claims.get("nonce") != nonce:
        raise RuntimeError("ChatGPT sign-in could not be verified. Start again.")
    email = claims.get("email")
    return {"subject": str(claims["sub"]), "email": email if isinstance(email, str) else None}


# ---------------------------------------------------------------------------
# Refresh


async def refresh_chatgpt_tokens() -> dict | None:
    """The saved connection with a fresh access token, or None when signed out.

    Models, usage and generation can all ask at once; one refresh rotates the
    token and the others use its result. A temporary failure keeps the saved
    connection; only a refresh token OpenAI rejects signs the seller out.
    """
    async with _refresh_lock:
        record = _saved_record()
        if record is None:
            return None
        now = time.time()
        if record["expires_at"] - now > REFRESH_MARGIN_S:
            return record
        earliest = record.get("earliest_refresh_at")
        if isinstance(earliest, (int, float)) and earliest > now:
            if record["expires_at"] > now:
                return record
            raise RuntimeError("ChatGPT cannot renew this connection yet. Try again shortly.")
        if not record.get("refresh_token"):
            delete_chatgpt_tokens()
            raise RuntimeError(SIGN_IN_AGAIN)
        try:
            payload = await _token_request({
                "grant_type": "refresh_token",
                "client_id": record["client_id"],
                "refresh_token": record["refresh_token"],
                "resource": RESOURCE,
            })
        except OAuthError as exc:
            if get_chatgpt_tokens() != record:
                # A sign-out or a new sign-in while the request was in flight wins.
                return _saved_record()
            if exc.code in REVOKED_ERRORS and exc.status in (400, 401):
                delete_chatgpt_tokens()
                raise RuntimeError(SIGN_IN_AGAIN) from exc
            raise RuntimeError(
                "Could not renew the ChatGPT connection. Your saved sign-in has been kept. Try again."
            ) from exc
        if get_chatgpt_tokens() != record:
            return _saved_record()
        fields = _token_fields(payload)
        if not fields["refresh_token"]:
            raise RuntimeError(
                "ChatGPT returned an invalid renewal. Your saved sign-in has been kept. Try again."
            )
        renewed = {**record, **fields}
        set_chatgpt_tokens(renewed)
        if REQUIRED_SCOPE not in fields["scopes"]:
            raise RuntimeError("ChatGPT token sharing is no longer enabled. Sign in again.")
        return renewed


# ---------------------------------------------------------------------------
# Sign-in


def _callback_page(ok: bool, message: str) -> bytes:
    title = "ChatGPT is connected" if ok else "ChatGPT sign-in did not finish"
    return (
        "<!doctype html><meta charset=utf-8><title>Anteroom</title>"
        "<body style='font:15px system-ui;margin:4rem auto;max-width:28rem;text-align:center'>"
        f"<h1 style='font-size:1.25rem'>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>"
    ).encode()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Takes the one redirect back from OpenAI, then shows how sign-in ended."""

    def do_GET(self):  # noqa: N802 - http.server's name
        url = urlsplit(self.path)
        if url.path != CALLBACK_PATH:
            self.send_error(404)
            return
        login = self.server.login  # type: ignore[attr-defined]
        if login.callback_used:
            self.send_error(410, "Sign-in is no longer active.")
            return
        login.callback_used = True
        params = dict(parse_qsl(url.query))
        callback = login.callback

        def deliver() -> None:
            if callback is not None and not callback.done():
                callback.set_result(params)

        self.server.loop.call_soon_threadsafe(deliver)  # type: ignore[attr-defined]
        login.finished.wait(CALLBACK_PAGE_WAIT_S)
        ok = login.finished.is_set() and login.error is None
        message = "You can close this tab and go back to Anteroom." if ok else (
            login.error or "Sign-in is still finishing. Go back to Anteroom."
        )
        body = _callback_page(ok, message)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - http.server's signature
        return


def _start_listener(login: PendingLogin, loop: asyncio.AbstractEventLoop) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.login = login  # type: ignore[attr-defined]
    server.loop = loop  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, name="chatgpt-sign-in", daemon=True).start()
    return server


def _stop_listener(login: PendingLogin) -> None:
    server, login.server = login.server, None
    if server is not None:
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


async def start_login() -> dict:
    """Open ChatGPT's consent page; the sign-in finishes in the background."""
    global _pending
    await cancel_login()
    endpoints = await _discover()
    registration = get_chatgpt_registration()
    host_id = registration.get("host_id")
    if not isinstance(host_id, str) or not host_id:
        host_id = str(uuid.uuid4())
        registration = {**registration, "host_id": host_id}
        set_chatgpt_registration(registration)
    client_id = registration.get("client_id")
    client_id = client_id if isinstance(client_id, str) and client_id.startswith("oaiapp_") else None

    loop = asyncio.get_running_loop()
    login = PendingLogin(
        authorize_url="",
        redirect_uri="",
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
        client_id=client_id,
        callback=loop.create_future(),
    )
    login.server = _start_listener(login, loop)
    # Only the port may vary between attempts; the exchange repeats this exact URI.
    login.redirect_uri = f"http://127.0.0.1:{login.server.server_address[1]}{CALLBACK_PATH}"
    params = {
        "client_id": client_id or FIRST_CLIENT_ID,
        **(
            {"login_hint": registration["email"]}
            if client_id and isinstance(registration.get("email"), str)
            else {}
        ),
        **({} if client_id else {"agent_name_hint": AGENT_NAME}),
        **({"prompt": "consent"} if client_id and registration.get("sharing_enabled") is False else {}),
        "ext_agent_host_id": f"urn:uuid:{host_id}",
        "response_type": "code",
        "redirect_uri": login.redirect_uri,
        "scope": SCOPE,
        "resource": RESOURCE,
        "state": login.state,
        "nonce": login.nonce,
        "code_challenge_method": "S256",
        "code_challenge": _challenge(login.verifier),
    }
    login.authorize_url = f"{endpoints['authorization_endpoint']}?{urlencode(params)}"
    login.task = asyncio.create_task(_complete_login(login))
    with _pending_guard:
        _pending = login
    try:
        webbrowser.open(login.authorize_url)
    except Exception:  # noqa: BLE001 - Settings shows the link too
        log.debug("could not open ChatGPT sign-in", exc_info=True)
    return {"authorize_url": login.authorize_url}


async def _complete_login(login: PendingLogin) -> None:
    try:
        params = await asyncio.wait_for(login.callback, LOGIN_TIMEOUT_S)
        await _finish(login, params)
    except asyncio.CancelledError:
        login.error = "Sign-in cancelled"
        raise
    except TimeoutError:
        login.error = "ChatGPT sign-in timed out after 15 minutes. Start again."
    except Exception as exc:
        login.error = str(exc) or "ChatGPT sign-in could not be completed. Start again."
        log.warning("ChatGPT sign-in failed: %s", login.error)
    finally:
        login.done = True
        login.finished.set()
        _stop_listener(login)


async def _finish(login: PendingLogin, params: dict[str, str]) -> None:
    if params.get("state") != login.state:
        raise RuntimeError("ChatGPT sign-in could not be verified. Start again.")
    if params.get("error"):
        if params["error"] == "access_denied":
            raise RuntimeError("ChatGPT sign-in was declined. Sign in again when you are ready.")
        raise RuntimeError("ChatGPT sign-in could not be completed. Start again.")
    code = params.get("code")
    returned_client = params.get("client_id")
    client_id = login.client_id or returned_client
    if (
        not code
        or not client_id
        or not client_id.startswith("oaiapp_")
        or (login.client_id and returned_client and returned_client != login.client_id)
    ):
        raise RuntimeError("ChatGPT registration is incomplete. Start sign-in again.")

    registration = get_chatgpt_registration()
    try:
        payload = await _token_request({
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": login.verifier,
            "redirect_uri": login.redirect_uri,
            "resource": RESOURCE,
        })
    except OAuthError as exc:
        if exc.code == "invalid_grant":
            if not login.client_id:
                # Keep the new registration so the next attempt reuses it.
                set_chatgpt_registration({**registration, "client_id": client_id, "redirect_uri": login.redirect_uri})
            raise RuntimeError("This sign-in code expired. Start again.") from exc
        if exc.code == "access_denied":
            raise RuntimeError("ChatGPT sign-in was declined. Sign in again when you are ready.") from exc
        raise RuntimeError("ChatGPT could not complete sign-in. Try again.") from exc

    fields = _token_fields(payload)
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise RuntimeError("ChatGPT did not return a verified identity. Sign in again.")
    identity = await _verified_identity(id_token, client_id, login.nonce)
    if registration.get("client_id") == client_id and registration.get("subject") not in (None, identity["subject"]):
        raise RuntimeError(
            "This sign-in returned a different ChatGPT account. Sign out first to switch accounts."
        )
    sharing = REQUIRED_SCOPE in fields["scopes"]
    set_chatgpt_registration({
        **registration,
        "client_id": client_id,
        "redirect_uri": login.redirect_uri,
        "sharing_enabled": sharing,
        **identity,
    })
    if not sharing:
        raise RuntimeError(
            "Signed in with ChatGPT, but token sharing is off. Sign in again and allow it, or use another provider."
        )
    set_chatgpt_tokens({"client_id": client_id, "id_token": id_token, **fields, **identity})


async def cancel_login() -> None:
    global _pending
    with _pending_guard:
        previous, _pending = _pending, None
    if previous is None:
        return
    if previous.task and not previous.task.done():
        previous.task.cancel()
    else:
        _stop_listener(previous)


def pending_login() -> dict | None:
    if not _pending or _pending.done:
        return None
    return {"authorize_url": _pending.authorize_url}


def login_error() -> str | None:
    if _pending and _pending.error and _pending.done:
        return _pending.error
    return None


async def logout() -> bool:
    """Sign out, revoking the refresh token with OpenAI. True when OpenAI confirmed it."""
    record = get_chatgpt_tokens()
    confirmed = False
    refresh = record.get("refresh_token") if isinstance(record, dict) else None
    if isinstance(refresh, str) and refresh and isinstance(record.get("client_id"), str):
        try:
            endpoints = await _discover()
            revoke = endpoints.get("revocation_endpoint")
            if isinstance(revoke, str):
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(revoke, data={
                        "token": refresh,
                        "token_type_hint": "refresh_token",
                        "client_id": record["client_id"],
                    })
                confirmed = resp.status_code == 200
        except Exception:  # noqa: BLE001 - signing out locally must not depend on the network
            log.info("ChatGPT revocation failed", exc_info=True)
    delete_chatgpt_tokens()
    return confirmed
