"""Sign in with OpenRouter: its OAuth PKCE flow hands Studio an API key.

https://openrouter.ai/docs/use-cases/oauth-pkce. The seller approves in the
browser, OpenRouter sends the browser back to Studio with a code, and Studio
trades the code for a key that bills the seller's own OpenRouter account. The
callback must use ``localhost``: that is what OpenRouter accepts on any port.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from urllib.parse import urlencode

import httpx

from vendoo_studio.config import PORT
from vendoo_studio.services.keychain import set_ai_key

AUTHORIZE_URL = "https://openrouter.ai/auth"
EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
CALLBACK_PATH = "/api/settings/ai-keys/openrouter/callback"
KEY_LABEL = "Anteroom"

_lock = threading.Lock()
# state -> code_verifier for sign-ins started and not yet finished.
_pending: dict[str, str] = {}
MAX_PENDING = 5


def callback_url() -> str:
    return f"http://localhost:{PORT}{CALLBACK_PATH}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_sign_in() -> str:
    """The URL to open in the seller's browser."""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    with _lock:
        while len(_pending) >= MAX_PENDING:
            _pending.pop(next(iter(_pending)))
        _pending[state] = verifier
    callback = f"{callback_url()}?{urlencode({'state': state})}"
    query = urlencode({
        "callback_url": callback,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "key_label": KEY_LABEL,
    })
    return f"{AUTHORIZE_URL}?{query}"


async def finish_sign_in(code: str, state: str) -> None:
    """Trade the callback's code for a key and save it in Keychain."""
    with _lock:
        verifier = _pending.pop(state, None)
    if not verifier:
        raise RuntimeError("This sign-in expired. Start it again from Anteroom.")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            EXCHANGE_URL,
            json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenRouter refused the sign-in (HTTP {resp.status_code}).")
    key = resp.json().get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("OpenRouter did not return a key.")
    set_ai_key("openrouter", key)
