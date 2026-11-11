"""Server-signed approvals for the marketplace commands the hosted API relays to Chrome.

The seller's Anteroom page carries each command to the Bridge, so the Bridge
cannot tell a command the hosted API accepted from one a script on the page
made up. Every relayed command therefore carries a short-lived ES256 token (a
compact JWS) that binds the seller, the command, its marketplace, action and
listing, a hash of the whole command and the SHA-256 of each photo the page
relays beside it. The Bridge checks it with the public key it ships with,
refuses a token it has already used, and runs nothing without one. See
docs/cloud-extension.md, **Signed approvals**.

The private key comes only from ``ANTEROOM_BRIDGE_SIGNING_KEY``. Without it,
commands go out unsigned and a current Bridge refuses every one.
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
# The capability a Bridge declares when it checks the approval on every relayed
# command, reads and Cancel included, and each relayed photo's SHA-256 against
# the approval's ``pho`` claim. Only such a Bridge gets approvals on reads: an
# older one rejects the extra payload field on them.
SIGNED_COMMANDS_CAPABILITY = "marketplace.signed_commands"
# Removal note: Bridges 0.2.56 to 0.2.67 declare only this name, check approvals
# on the writes in SIGNED_COMMANDS, and do not hash photos. SIGNED_COMMANDS_CAPABILITY
# replaces it; remove this name and SIGNED_COMMANDS after 2027-02-01.
SIGNED_APPROVALS = "marketplace.signed_approvals"
# Commands that change something: a marketplace listing, a Vendoo draft, or a
# message to a buyer. ``marketplace.result`` is here because it may resume an
# interrupted List.
SIGNED_COMMANDS = frozenset({"job.vendoo_api", "marketplace.execute", "marketplace.result",
                             "marketplace.delete", "marketplace.promote"})
# The commands whose photos the page reads and relays beside them (src/cloud/chromeRelay.ts).
PHOTO_COMMANDS = frozenset({"job.vendoo_api", "marketplace.execute"})
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


def photo_hashes(db, job_id: str) -> list[dict]:
    """``{id, sha256}`` of each photo approved for ``job_id``: the bytes the page relays for it.

    They come from the job's pinned manifest (``approved_photos``), the copies the
    relay's photo route serves. A job without one gets none, so the Bridge refuses
    any photo relayed with its command."""
    from vendoo_studio.repositories.queries import JobRepo

    source = JobRepo(db).photo_source(job_id)
    rows = (source.approved_photos if source is not None and source.has_approved_photos else None) or []
    return [{"id": str(row["id"]), "sha256": str(row["checksum"]).lower()} for row in rows
            if isinstance(row, dict) and row.get("id") and row.get("checksum")]


def approval(owner: str, command: dict, photos: list[dict] | None = None) -> str | None:
    """A signed approval for one relayed command by ``owner``, or None when no key is set.

    ``photos`` (``{id, sha256}``) are the only photos the Bridge may run it with;
    None leaves the claim out, for a Bridge that does not check photos."""
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
        **({"pho": photos} if photos is not None else {}),
    }
    return jwt.encode(claims, key, algorithm="ES256")


def signs_every_command(capabilities: set[str] | frozenset[str]) -> bool:
    return SIGNED_COMMANDS_CAPABILITY in capabilities


def signed(owner: str | None, command: dict, capabilities: set[str] | frozenset[str],
           photos: list[dict] | None = None) -> dict:
    """``command`` as the page should carry it, with an approval when the Bridge checks one for it.

    A Bridge that declares ``SIGNED_COMMANDS_CAPABILITY`` gets one on every command,
    binding ``photos`` (none when not given). An older one that declares
    ``SIGNED_APPROVALS`` gets one on writes only."""
    every = signs_every_command(capabilities)
    if (owner is None or not isinstance(command.get("payload"), dict)
            or not (every or (command.get("type") in SIGNED_COMMANDS and SIGNED_APPROVALS in capabilities))):
        return command
    # Hash the command exactly as the page will receive it: FastAPI encodes the route's dict this way.
    command = jsonable_encoder(command)
    token = approval(owner, command, (photos or []) if every else None)
    return command if token is None else {**command, "payload": {**command["payload"], "approval": token}}
