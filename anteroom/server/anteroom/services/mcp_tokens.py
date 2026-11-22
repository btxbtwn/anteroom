"""Personal access token management and authentication for the HTTP MCP server.

Tokens use the ``arm_`` prefix so middleware can distinguish them from Supabase
JWTs. Only SHA-256 hashes are persisted; raw tokens are returned once on create.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from anteroom.models.mcp_token import McpAccessToken

TOKEN_PREFIX = "arm_"
MAX_ACTIVE_TOKENS = 25
MAX_NAME_LENGTH = 80
MAX_EXPIRY_DAYS = 365


class McpTokenError(ValueError):
    """Invalid MCP token operation or limit exceeded."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def is_mcp_token(raw_token: str) -> bool:
    return (
        isinstance(raw_token, str)
        and raw_token.startswith(TOKEN_PREFIX)
        and 20 <= len(raw_token) <= 128
    )


def _serialize(row: McpAccessToken, *, now: datetime | None = None) -> dict[str, Any]:
    clock = _aware(now) or _now()
    expires = _aware(row.expires_at)
    expired = expires is not None and expires <= clock
    active = row.revoked_at is None and not expired
    return {
        "id": row.id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
        "revoked_at": _iso(row.revoked_at),
        "expires_at": _iso(row.expires_at),
        "active": active,
    }


def list_tokens(db: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    rows = (
        db.query(McpAccessToken)
        .order_by(McpAccessToken.created_at.desc(), McpAccessToken.id.desc())
        .all()
    )
    clock = _aware(now) or _now()
    return [_serialize(row, now=clock) for row in rows]


def create_token(
    db: Session,
    *,
    name: str,
    expires_in_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cleaned = " ".join((name or "").split()).strip()
    if not cleaned:
        raise McpTokenError("Give this token a name so you know which app uses it.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise McpTokenError(f"Token name must be {MAX_NAME_LENGTH} characters or fewer.")
    if expires_in_days is not None and (expires_in_days < 1 or expires_in_days > MAX_EXPIRY_DAYS):
        raise McpTokenError(f"Expiration must be between 1 and {MAX_EXPIRY_DAYS} days.")

    clock = _aware(now) or _now()
    existing = db.query(McpAccessToken).filter(McpAccessToken.revoked_at.is_(None)).all()
    active_count = sum(
        1 for row in existing if row.expires_at is None or (_aware(row.expires_at) or clock) > clock
    )
    if active_count >= MAX_ACTIVE_TOKENS:
        raise McpTokenError(
            f"You already have {MAX_ACTIVE_TOKENS} active MCP tokens. Revoke an unused token first."
        )

    raw_token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    digest = hash_token(raw_token)
    expires_at = clock + timedelta(days=expires_in_days) if expires_in_days is not None else None
    row = McpAccessToken(
        name=cleaned,
        token_prefix=raw_token[:12],
        token_hash=digest,
        created_at=clock,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    payload = _serialize(row, now=clock)
    payload["token"] = raw_token
    return payload


def revoke_token(db: Session, token_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    row = db.get(McpAccessToken, token_id)
    if row is None:
        return None
    clock = _aware(now) or _now()
    if row.revoked_at is None:
        row.revoked_at = clock
        db.commit()
        db.refresh(row)
    return _serialize(row, now=clock)


@dataclass(frozen=True)
class VerifiedMcpToken:
    token_id: str
    owner_id: str


def authenticate_token(
    engine: Engine,
    raw_token: str,
    *,
    hosted: bool = False,
    now: datetime | None = None,
) -> VerifiedMcpToken | None:
    """Validate a raw ``arm_`` token and return a ``VerifiedMcpToken`` or ``None``."""
    if not is_mcp_token(raw_token):
        return None
    digest = hash_token(raw_token)
    if hosted:
        if engine.dialect.name != "postgresql":
            return None
        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT token_id, owner_id FROM anteroom_private.authenticate_mcp_token(:hash)"),
                {"hash": digest},
            ).first()
            if row is None:
                return None
            return VerifiedMcpToken(token_id=str(row.token_id), owner_id=str(row.owner_id))

    clock = _aware(now) or _now()
    with Session(bind=engine) as db:
        row = (
            db.query(McpAccessToken)
            .filter(
                McpAccessToken.token_hash == digest,
                McpAccessToken.revoked_at.is_(None),
            )
            .first()
        )
        if row is None:
            return None
        expires = _aware(row.expires_at)
        if expires is not None and expires <= clock:
            return None
        row.last_used_at = clock
        token_id, owner_id = row.id, row.user_id
        db.commit()
        return VerifiedMcpToken(token_id=token_id, owner_id=owner_id)
