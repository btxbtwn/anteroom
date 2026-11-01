"""Signed commands the web app hands to the seller's Chrome extension.

The web app cannot reach the extension's loopback socket, so a Save draft it
starts travels through the page. The page only relays: the server signs each
command (ECDSA P-256, SHA-256) and the extension verifies it with the public
key it ships with, so a page script cannot invent or alter one. A command is
for one seller, one operation and ``save_draft`` only, and it expires.

Each operation carries a random token. Only its SHA-256 is stored, on the job.
The extension presents the token to read that job's approved photos and to
report the result; the result clears it, so it works once.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

KEY_ENV = "ANTEROOM_COMMAND_SIGNING_KEY"
# The extension must start a command within this long of its signing.
COMMAND_TTL = timedelta(minutes=5)
# Photos and the result are accepted until then. A save that started late still
# finishes inside it (Chrome gets three minutes per step). Past it, the job
# watchdog leaves the record unconfirmed for the seller to check.
TOKEN_TTL = timedelta(minutes=15)
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


class OperationUnauthorized(Exception):
    """The token is missing, malformed, used, expired or for another operation."""


def _private_key() -> ec.EllipticCurvePrivateKey | None:
    """The signing key: base64 PKCS#8 DER (one line, for Fly secrets) or PEM."""
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        key = (serialization.load_pem_private_key(raw.encode(), password=None) if raw.startswith("-----")
               else serialization.load_der_private_key(base64.b64decode(raw), password=None))
    except (ValueError, TypeError, binascii.Error):
        return None
    return key if isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1) else None


def throwaway_key() -> str:
    """A fresh signing key in the secret's format, for sandboxes. Never a production key."""
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())).decode()


def configured() -> bool:
    return _private_key() is not None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(command: dict) -> dict:
    """``body`` is the command's JSON, base64url; ``signature`` is r‖s over the body's ASCII bytes.

    Signing the transported text, not re-serialized JSON, spares both ends a
    canonical encoding.
    """
    key = _private_key()
    if key is None:
        raise RuntimeError("Command signing is not configured")
    body = _b64url(json.dumps(command, separators=(",", ":"), ensure_ascii=False).encode())
    r, s = decode_dss_signature(key.sign(body.encode(), ec.ECDSA(hashes.SHA256())))
    return {"body": body, "signature": _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(job) -> tuple[str, int]:
    """Give the job a fresh token. Returns the token and the command's expiry (epoch seconds)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    job.operation_token_hash = token_hash(token)
    # Naive UTC, like the column: an aware value would be shifted into the database session's time zone.
    job.operation_expires_at = (now + TOKEN_TTL).replace(tzinfo=None)
    return token, int((now + COMMAND_TTL).timestamp())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def live(job) -> bool:
    """A Chrome operation the web issued that may still report. Nothing in this process runs it."""
    expires = _as_utc(getattr(job, "operation_expires_at", None))
    return bool(job is not None and getattr(job, "operation_token_hash", None) and job.status == "dispatched"
                and expires and expires > datetime.now(UTC))


def bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not TOKEN.fullmatch(token):
        raise OperationUnauthorized
    return token


def owner(operation_id: str, token: str) -> str:
    """The seller whose running operation this token belongs to.

    Runs as the trusted process login: without a seller session, row security
    hides every job. The function returns only the row whose token hash matches.
    """
    from sqlalchemy import text

    from vendoo_studio import database

    with database.engine.begin() as connection:
        rows = connection.execute(text("SELECT * FROM anteroom_private.browser_operation_owner(:hash)"),
                                  {"hash": token_hash(token)}).all()
    if len(rows) != 1 or rows[0].job_id != operation_id:
        raise OperationUnauthorized
    return rows[0].owner_id


def check(job, token: str) -> None:
    """The token still opens this job: same hash, not used, not expired."""
    if not live(job) or not secrets.compare_digest(job.operation_token_hash, token_hash(token)):
        raise OperationUnauthorized
