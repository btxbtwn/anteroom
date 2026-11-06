"""Server-signed approvals for the marketplace writes the hosted API relays to Chrome.

The seller's Anteroom page carries each command to the Bridge, so the Bridge
cannot tell a command the hosted API accepted from one a script on the page
made up. Every relayed write therefore carries a short-lived ES256 token (a
compact JWS) that binds the seller, the command, its marketplace, action and
listing, and a hash of the whole command. The Bridge checks it with the public
key it ships with, refuses a token it has already used, and runs nothing
without one. See docs/cloud-extension.md, **Signed approvals**.

The private key comes only from ``ANTEROOM_BRIDGE_SIGNING_KEY``. Without it,
commands go out unsigned and a current Bridge refuses every write.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import logging
import os
import time
import uuid
from decimal import Decimal

import jwt
from fastapi.encoders import jsonable_encoder
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

logger = logging.getLogger(__name__)

KEY_ENV = "ANTEROOM_BRIDGE_SIGNING_KEY"
# The capability a Bridge declares when it checks signed approvals. Only such a
# Bridge gets them: an older one rejects the extra payload field on some commands.
SIGNED_APPROVALS = "marketplace.signed_approvals"
# Commands that change something: a marketplace listing, a Vendoo draft, or a
# message to a buyer. ``marketplace.result`` is here because it may resume an
# interrupted List. Reads and Cancel are not signed.
SIGNED_COMMANDS = frozenset({"job.vendoo_api", "marketplace.execute", "marketplace.result",
                             "marketplace.delete", "marketplace.promote"})
# The token is minted when the page takes the command, so this covers loading
# its photos and handing it to Chrome, not the marketplace call itself.
APPROVAL_TTL_S = 300
ISSUER = "anteroom"
AUDIENCE = "anteroom-bridge"


@functools.lru_cache(maxsize=4)
def _key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError(f"{KEY_ENV} must be an ECDSA P-256 private key.")
    return key


def signing_key() -> ec.EllipticCurvePrivateKey | None:
    pem = os.environ.get(KEY_ENV, "").strip()
    return _key(pem) if pem else None


def _js_number(value: float) -> str:
    """``value`` as JavaScript's JSON.stringify writes it, the form the Bridge hashes."""
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    sign, digits, exponent = Decimal(repr(value)).normalize().as_tuple()
    assert isinstance(exponent, int)
    s = "".join(map(str, digits))
    k, n = len(s), exponent + len(s)
    text = (s + "0" * (n - k) if k <= n <= 21 else s[:n] + "." + s[n:] if 0 < n <= 21
            else "0." + "0" * -n + s if -6 < n <= 0
            else (s if k == 1 else s[0] + "." + s[1:]) + "e" + ("+" if n - 1 >= 0 else "-") + str(abs(n - 1)))
    return ("-" if sign else "") + text


def canonical(value) -> str:
    """Sorted-key JSON, written the way the Bridge rebuilds it from the parsed command."""
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(str(key), ensure_ascii=False) + ":" + canonical(value[key])
                              for key in sorted(value, key=str)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if value is None or isinstance(value, (bool, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _js_number(value)
    raise TypeError(f"Cannot sign a {type(value).__name__} value.")


def command_hash(command: dict) -> str:
    """The base64url SHA-256 of the command without its approval."""
    payload = {key: item for key, item in (command.get("payload") or {}).items() if key != "approval"}
    body = canonical({"type": command.get("type"), "job_id": command.get("job_id"),
                      "message_id": command.get("message_id"), "payload": payload})
    return base64.urlsafe_b64encode(hashlib.sha256(body.encode()).digest()).rstrip(b"=").decode()


def approval(owner: str, command: dict) -> str | None:
    """A signed approval for one relayed write by ``owner``, or None when no key is set."""
    key = signing_key()
    if key is None:
        logger.warning("%s is not set; Chrome will refuse %s.", KEY_ENV, command.get("type"))
        return None
    payload = command.get("payload") or {}
    issued = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": str(owner).lower(),
        "cmd": command.get("type"), "mkt": payload.get("marketplace"), "act": payload.get("action"),
        "lid": payload.get("listing_id"), "job": command.get("job_id"), "mid": command.get("message_id"),
        "sha": command_hash(command), "jti": uuid.uuid4().hex, "iat": issued, "exp": issued + APPROVAL_TTL_S,
    }
    return jwt.encode(claims, key, algorithm="ES256")


def signed(owner: str | None, command: dict, capabilities: set[str] | frozenset[str]) -> dict:
    """``command`` as the page should carry it: with an approval when it is a write and the Bridge checks them."""
    if (owner is None or command.get("type") not in SIGNED_COMMANDS or SIGNED_APPROVALS not in capabilities
            or not isinstance(command.get("payload"), dict)):
        return command
    # Hash the command exactly as the page will receive it: FastAPI encodes the route's dict this way.
    command = jsonable_encoder(command)
    token = approval(owner, command)
    return command if token is None else {**command, "payload": {**command["payload"], "approval": token}}
