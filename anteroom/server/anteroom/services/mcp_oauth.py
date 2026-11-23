"""Sign-in for apps that connect to the hosted MCP endpoint (Claude, ChatGPT, Gemini).

Supabase Auth's OAuth 2.1 server signs the seller in and issues the app a token.
Anteroom is the protected resource: it tells apps where to sign in
(``/.well-known/oauth-protected-resource``) and, per connected app, keeps what
that app may reach and whether the seller has turned it off
(``mcp_oauth_connections``). The token itself carries the app's ``client_id``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anteroom.models.mcp_token import McpOAuthConnection
from anteroom.services import mcp_tokens

MCP_PATH = "/api/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource"
METADATA_PATHS = frozenset({METADATA_PATH, METADATA_PATH + MCP_PATH})

_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")
_LOOPBACK = {"127.0.0.1", "localhost", "testserver"}


log = logging.getLogger(__name__)
GRANT_REVOKE_TIMEOUT_SECONDS = 10
# "Last used" in Settings is to the minute; an app's calls do not each write it.
LAST_USED_RESOLUTION = timedelta(minutes=1)


class McpConnectionError(ValueError):
    """Invalid connected-app operation."""


def public_origin(host: str | None) -> str | None:
    """The origin an app used to reach this server, from its ``Host`` header."""
    if not host or not _HOST.match(host):
        return None
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    scheme = "http" if name in _LOOPBACK else "https"
    return f"{scheme}://{host}"


def metadata_url(origin: str) -> str:
    return origin + METADATA_PATH + MCP_PATH


def challenge(host: str | None) -> str:
    """``WWW-Authenticate`` for an unauthenticated MCP request (RFC 9728)."""
    origin = public_origin(host)
    return f'Bearer resource_metadata="{metadata_url(origin)}"' if origin else "Bearer"


def resource_metadata(origin: str, issuer: str) -> dict[str, Any]:
    return {
        "resource": origin + MCP_PATH,
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "resource_name": "Anteroom",
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _clean_client_id(client_id: Any) -> str:
    if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
        raise McpConnectionError("Unknown app.")
    return client_id.strip()


def _serialize(row: McpOAuthConnection) -> dict[str, Any]:
    return {
        "client_id": row.client_id,
        "client_name": row.client_name,
        "permissions": mcp_tokens.stored_permissions(row.permissions),
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
        "revoked_at": _iso(row.revoked_at),
        "signed_out_at": _iso(row.signed_out_at),
    }


def _find(db: Session, client_id: str) -> McpOAuthConnection | None:
    return db.query(McpOAuthConnection).filter(McpOAuthConnection.client_id == client_id).first()


def list_connections(db: Session) -> list[dict[str, Any]]:
    rows = db.query(McpOAuthConnection).order_by(McpOAuthConnection.created_at.desc()).all()
    return [_serialize(row) for row in rows]


def approve(db: Session, client_id: Any, *, client_name: Any, permissions: Any, enable: bool = False) -> dict[str, Any]:
    """Record what an app may reach as the seller approves it.

    An app the seller turned off stays off until ``enable``, which the approval
    page sends only after Supabase has accepted the seller's approval.
    """
    cid = _clean_client_id(client_id)
    name = str(client_name).strip()[:200] if isinstance(client_name, str) and client_name.strip() else None
    try:
        granted = mcp_tokens.normalize_permissions(permissions if permissions is not None else {})
    except mcp_tokens.McpTokenError as exc:
        raise McpConnectionError(str(exc)) from exc
    row = _find(db, cid)
    if row is None:
        row = McpOAuthConnection(client_id=cid, created_at=_now())
        db.add(row)
    row.client_name = name or row.client_name
    row.permissions = granted
    if enable:
        row.revoked_at = None
        row.signed_out_at = None
    db.commit()
    return _serialize(row)


def update_permissions(db: Session, client_id: Any, permissions: Any) -> dict[str, Any] | None:
    row = _find(db, _clean_client_id(client_id))
    if row is None:
        return None
    try:
        row.permissions = mcp_tokens.normalize_permissions(
            permissions, base=mcp_tokens.stored_permissions(row.permissions),
        )
    except mcp_tokens.McpTokenError as exc:
        raise McpConnectionError(str(exc)) from exc
    db.commit()
    return _serialize(row)


def revoke(db: Session, client_id: Any) -> dict[str, Any] | None:
    row = _find(db, _clean_client_id(client_id))
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()
    return _serialize(row)


def mark_signed_out(db: Session, client_id: Any) -> dict[str, Any] | None:
    """Supabase confirmed the turned-off app's sign-in is over."""
    row = _find(db, _clean_client_id(client_id))
    if row is None:
        return None
    if row.revoked_at is not None and row.signed_out_at is None:
        row.signed_out_at = _now()
        db.commit()
    return _serialize(row)


def permissions_for(db: Session, client_id: str) -> dict[str, str] | None:
    """What a connected app may reach on this call, or None once the seller turned it off.

    An app the seller approved before this record existed, or whose approval
    Supabase repeated without showing Anteroom's page, starts read-only.
    """
    now = _now()
    row = _find(db, client_id)
    if row is None:
        db.add(McpOAuthConnection(
            client_id=client_id, permissions=dict(mcp_tokens.DEFAULT_PERMISSIONS), created_at=now,
        ))
        try:
            db.commit()
        except IntegrityError:
            # The app's first calls arrived together; another one made the row.
            db.rollback()
        row = _find(db, client_id)
        if row is None:
            return None
    if row.revoked_at is not None:
        return None
    last = row.last_used_at
    if last is None or now - (last if last.tzinfo else last.replace(tzinfo=UTC)) > LAST_USED_RESOLUTION:
        row.last_used_at = now
        # Not a Settings change other devices need to reload for on every call.
        db.info["quiet_changes"] = True
        try:
            db.commit()
        finally:
            db.info.pop("quiet_changes", None)
    return mcp_tokens.stored_permissions(row.permissions)


def _delete_grant(authorization: str, client_id: str) -> bool:
    import httpx

    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not base or not key:
        return False
    response = httpx.delete(
        f"{base}/auth/v1/user/oauth/grants",
        params={"client_id": client_id},
        headers={"Authorization": authorization, "apikey": key},
        timeout=GRANT_REVOKE_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    # Nothing to revoke means the app is already signed out there.
    return response.status_code in (200, 204, 404)


async def end_supabase_grant(authorization: str | None, client_id: str) -> bool:
    """Revoke the seller's Supabase grant for this app, with the seller's own session.

    False when Supabase could not confirm it in time. The app stays off in
    Anteroom either way; Settings tells the seller to retry.
    """
    if not authorization:
        return False
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_delete_grant, authorization, client_id),
            timeout=GRANT_REVOKE_TIMEOUT_SECONDS + 2,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the seller as not signed out
        log.warning("Supabase grant revocation failed for a connected app: %s", type(exc).__name__)
        return False
