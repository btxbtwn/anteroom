"""Seller-owned cloud marketplace sessions.

Encrypted session cookies live exclusively in Supabase Vault under
``cloud-session-<marketplace>``. ``cloud_marketplace_sessions`` holds only
non-secret status metadata so ``web`` Machines can report status and mint
single-use extension upload tokens without ever decrypting credentials.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, UTC
import hashlib
import json
import logging
import os
import re
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from anteroom.database import SessionLocal
from anteroom.models.cloud_session import (
    CloudMarketplaceSession,
    CloudOperationJournal,
    CloudSessionAudit,
)
from anteroom.models.conversation import utcnow
from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.services.service_context import current

log = logging.getLogger("anteroom.cloud_sessions")

SUPPORTED_CLOUD_MARKETPLACES = frozenset({"poshmark", "mercari", "depop"})
_COOKIE_NAMES = {"poshmark": "jwt", "depop": "access_token"}
UPLOAD_TOKEN_TTL_S = 180
SESSION_EXPIRING_S = 24 * 60 * 60
MAX_COOKIES = 100
MAX_COOKIE_VALUE_LEN = 16384
MAX_USER_AGENT_LEN = 300
MAX_LABEL_LEN = 80
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_HEX24 = re.compile(r"^[a-f0-9]{24}$")
_SAFE_PROXY_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class CloudSessionError(ValueError):
    """Invalid cloud session input or state."""


class CloudSessionDecryptForbidden(RuntimeError):
    """Raised if a ``web`` process attempts to decrypt cloud marketplace credentials."""


def _process_role() -> str:
    return (os.environ.get("ANTEROOM_PROCESS_ROLE") or "all").strip().lower()


def _configured_marketplaces() -> frozenset[str]:
    raw = os.environ.get("ANTEROOM_CLOUD_MARKETPLACES", "")
    parts = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return frozenset(parts & SUPPORTED_CLOUD_MARKETPLACES)


def is_hosted_mode() -> bool:
    return current() is not None


def enabled_marketplaces() -> frozenset[str]:
    """Cloud marketplaces enabled for this hosted seller execution."""
    if not is_hosted_mode():
        return frozenset()
    return _configured_marketplaces()


def is_marketplace_configured(marketplace: str) -> bool:
    return str(marketplace or "").lower() in _configured_marketplaces()


def is_cloud_enabled(marketplace: str) -> bool:
    return str(marketplace or "").lower() in enabled_marketplaces()


def vault_account(marketplace: str) -> str:
    market = str(marketplace or "").lower()
    if market not in SUPPORTED_CLOUD_MARKETPLACES:
        raise CloudSessionError("Unsupported cloud marketplace.")
    return f"cloud-session-{market}"


def _open_session():
    ctx = current()
    return ctx.session_factory() if ctx is not None else SessionLocal()


def _seller_id() -> str:
    ctx = current()
    if ctx is None or not ctx.seller_id or ctx.seller_id == LOCAL_USER_ID:
        raise CloudSessionError("Cloud marketplace sessions require a hosted seller account.")
    return ctx.seller_id


def _marketplace_domain(marketplace: str, domain: str) -> bool:
    clean = domain.strip().lower().removeprefix(".")
    root = f"{marketplace}.com"
    return clean == root or clean.endswith(f".{root}")


def _jwt_claims(token: str) -> dict[str, Any] | None:
    parts = str(token or "").split(".")
    if len(parts) < 2 or not parts[1]:
        return None
    segment = parts[1].replace("-", "+").replace("_", "/")
    padded = segment + "=" * ((4 - len(segment) % 4) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _sanitize_label(claims: dict[str, Any] | None) -> str | None:
    if not isinstance(claims, dict):
        return None
    for key in ("username", "user_name", "name", "handle", "email"):
        val = claims.get(key)
        if not isinstance(val, str):
            continue
        cleaned = _CONTROL_CHARS.sub("", val).strip()
        if cleaned and len(cleaned) <= MAX_LABEL_LEN:
            return cleaned
    return None


def _sanitize_user_agent(raw: str | None) -> str:
    if not isinstance(raw, str):
        return _DEFAULT_USER_AGENT
    cleaned = _CONTROL_CHARS.sub("", raw).strip()
    if not cleaned:
        return _DEFAULT_USER_AGENT
    return cleaned[:MAX_USER_AGENT_LEN]


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def validate_and_normalize_cookies(
    marketplace: str,
    raw_cookies: list[dict[str, Any]],
    user_agent: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, datetime | None, str]:
    """Filter, validate, and normalize uploaded cookies for a cloud marketplace."""
    market = str(marketplace or "").lower()
    if market not in SUPPORTED_CLOUD_MARKETPLACES:
        raise CloudSessionError("Unsupported cloud marketplace.")
    if not isinstance(raw_cookies, list) or not raw_cookies:
        raise CloudSessionError("No marketplace session cookies were provided.")
    if len(raw_cookies) > MAX_COOKIES:
        raise CloudSessionError("Too many cookies in upload.")

    normalized: list[dict[str, Any]] = []
    jwt_cookie: dict[str, Any] | None = None
    now_ts = int(utcnow().timestamp())
    for entry in raw_cookies:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        value = entry.get("value")
        domain = str(entry.get("domain") or "").strip().lower()
        if not name or not isinstance(value, str) or len(name) > 256 or len(value) > MAX_COOKIE_VALUE_LEN:
            continue
        if _CONTROL_CHARS.search(name) or _CONTROL_CHARS.search(value):
            continue
        if not _marketplace_domain(market, domain):
            continue
        path = str(entry.get("path") or "/").strip() or "/"
        if len(path) > 512 or _CONTROL_CHARS.search(path):
            path = "/"
        session_cookie = bool(entry.get("session", False))
        exp_raw = entry.get("expirationDate")
        exp_ts: int | None = None
        if not session_cookie and isinstance(exp_raw, (int, float)) and 0 < exp_raw <= 253402300799:
            exp_ts = int(exp_raw)
            if market == "mercari" and exp_ts <= now_ts:
                continue
        item: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": bool(entry.get("secure", True)),
            "httpOnly": bool(entry.get("httpOnly", False)),
            "session": exp_ts is None,
        }
        if exp_ts is not None:
            item["expirationDate"] = exp_ts
        same_site = entry.get("sameSite")
        if isinstance(same_site, str) and same_site.lower() in {"strict", "lax", "none", "no_restriction", "unspecified"}:
            item["sameSite"] = same_site.lower()
        normalized.append(item)
        if name == _COOKIE_NAMES.get(market) and value:
            jwt_cookie = item

    # Mercari starts its authenticated session via /v1/initialize rather than a JWT.
    if market == "mercari" and normalized:
        return normalized, None, None, _sanitize_user_agent(user_agent)
    if jwt_cookie is None:
        raise CloudSessionError(f"Sign in to {market.title()} in Chrome first, then try again.")

    raw_jwt = str(jwt_cookie["value"])
    try:
        from urllib.parse import unquote
        decoded_jwt = unquote(raw_jwt)
    except Exception:
        decoded_jwt = raw_jwt

    claims = _jwt_claims(decoded_jwt)
    if market == "poshmark" and (not claims or not _HEX24.match(str(claims.get("user_id") or ""))):
        raise CloudSessionError("Poshmark session cookie is missing a valid account token.")

    exp_candidates: list[int] = []
    if isinstance(jwt_cookie.get("expirationDate"), int):
        exp_candidates.append(int(jwt_cookie["expirationDate"]))
    claimed_exp = (claims or {}).get("exp")
    if isinstance(claimed_exp, (int, float)) and 0 < claimed_exp <= 253402300799:
        exp_candidates.append(int(claimed_exp))
    expires_ts = min(exp_candidates) if exp_candidates else None
    if expires_ts is not None and expires_ts <= now_ts:
        raise CloudSessionError(f"Your {market.title()} sign-in has expired. Sign in in Chrome, then reconnect.")

    expires_at = datetime.fromtimestamp(expires_ts, tz=UTC) if expires_ts is not None else None
    label = _sanitize_label(claims)
    clean_ua = _sanitize_user_agent(user_agent)
    return normalized, label, expires_at, clean_ua


def _audit(db: Session, marketplace: str, event: str, detail: str | None = None) -> None:
    db.add(CloudSessionAudit(
        user_id=_seller_id(),
        marketplace=marketplace,
        event=event,
        detail=(detail[:200] if detail else None),
        created_at=utcnow(),
    ))


def _row(db: Session, marketplace: str, *, lock: bool = False) -> CloudMarketplaceSession | None:
    query = select(CloudMarketplaceSession).where(CloudMarketplaceSession.marketplace == marketplace)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.execute(query).scalar_one_or_none()


def serialize_session_row(marketplace: str, row: CloudMarketplaceSession | None) -> dict[str, Any]:
    now = utcnow()
    connected_at = _as_utc(row.connected_at) if row else None
    if row is None or row.state in {"pending", "revoked"}:
        return {
            "marketplace": marketplace,
            "state": "signed_out",
            "mode": "cloud",
            "cloud_connected": False,
            "label": None,
            "expires_at": None,
            "expiring": False,
            "connected_at": connected_at.isoformat() if connected_at and row and row.state == "pending" else None,
            "last_used_at": None,
            "last_error": None,
        }
    expires_at = _as_utc(row.expires_at)
    effective_state = row.state
    if effective_state == "connected" and expires_at is not None and expires_at <= now:
        effective_state = "expired"
    public_state = "signed_in" if effective_state == "connected" else (
        "expired" if effective_state == "expired" else "signed_out"
    )
    expires_ts = int(expires_at.timestamp()) if expires_at is not None else None
    expiring = bool(
        public_state == "signed_in"
        and expires_at is not None
        and (expires_at - now).total_seconds() <= SESSION_EXPIRING_S
    )
    last_used_at = _as_utc(row.last_used_at)
    return {
        "marketplace": marketplace,
        "state": public_state,
        "mode": "cloud",
        "cloud_connected": public_state == "signed_in",
        "label": row.account_label if public_state != "signed_out" else None,
        "expires_at": expires_ts if public_state != "signed_out" else None,
        "expiring": expiring,
        "connected_at": connected_at.isoformat() if connected_at is not None else None,
        "last_used_at": last_used_at.isoformat() if last_used_at is not None else None,
        "last_error": row.last_error if public_state != "signed_in" else None,
    }


def mint_upload_token(db: Session, marketplace: str) -> dict[str, Any]:
    market = str(marketplace or "").lower()
    if not is_cloud_enabled(market):
        raise CloudSessionError("Cloud sessions are not enabled for this marketplace.")
    seller = _seller_id()
    secret_part = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(secret_part.encode("utf-8")).hexdigest()
    now = utcnow()
    expires_at = now + timedelta(seconds=UPLOAD_TOKEN_TTL_S)

    row = _row(db, market)
    if row is None:
        row = CloudMarketplaceSession(
            user_id=seller,
            marketplace=market,
            state="pending",
            upload_token_hash=token_hash,
            upload_token_expires_at=expires_at,
            updated_at=now,
        )
        db.add(row)
    else:
        row.upload_token_hash = token_hash
        row.upload_token_expires_at = expires_at
        row.updated_at = now
    _audit(db, market, "token_minted")
    db.commit()
    return {
        "marketplace": market,
        "token": f"{seller}.{secret_part}",
        "expires_at": expires_at.isoformat(),
        "expires_in": UPLOAD_TOKEN_TTL_S,
    }


def consume_upload_token(engine, marketplace: str, raw_token: str) -> str | None:
    """Atomically verify and consume a single-use upload token, returning the seller UUID."""
    market = str(marketplace or "").lower()
    if market not in _configured_marketplaces():
        return None
    owner_part, dot, secret_part = str(raw_token or "").strip().partition(".")
    if not dot or len(secret_part) < 20 or len(secret_part) > 200:
        return None
    try:
        owner = str(UUID(owner_part))
    except ValueError:
        return None
    if owner == LOCAL_USER_ID:
        return None

    expected_hash = hashlib.sha256(secret_part.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            ok = bool(conn.scalar(
                text("SELECT anteroom_private.consume_cloud_upload_token(:owner, :market, :hash)"),
                {"owner": owner, "market": market, "hash": expected_hash},
            ))
        else:
            now = utcnow()
            result = conn.execute(
                text(
                    "UPDATE cloud_marketplace_sessions "
                    "SET upload_token_hash = NULL, upload_token_expires_at = NULL, updated_at = :now "
                    "WHERE user_id = :owner AND marketplace = :market "
                    "AND upload_token_hash = :hash "
                    "AND upload_token_expires_at IS NOT NULL AND upload_token_expires_at > :now"
                ),
                {"owner": owner, "market": market, "hash": expected_hash, "now": now},
            )
            ok = (result.rowcount or 0) == 1
    return owner if ok else None


def save_uploaded_session(
    db: Session,
    marketplace: str,
    raw_cookies: list[dict[str, Any]],
    user_agent: str | None = None,
    *,
    verify_first: bool = False,
) -> dict[str, Any]:
    """Validate uploaded cookies, encrypt them in Vault, and mark session connected."""
    market = str(marketplace or "").lower()
    if not is_cloud_enabled(market):
        raise CloudSessionError("Cloud sessions are not enabled for this marketplace.")
    cookies, label, expires_at, clean_ua = validate_and_normalize_cookies(market, raw_cookies, user_agent)
    now = utcnow()
    payload = json.dumps({
        "version": 1,
        "marketplace": market,
        "cookies": cookies,
        "user_agent": clean_ua,
        "captured_at": now.isoformat(),
    }, separators=(",", ":"))

    ctx = current()
    assert ctx is not None
    row = _row(db, market, lock=True)
    if not ctx.secrets.set(vault_account(market), payload):
        raise CloudSessionError("Could not store encrypted session.")

    state = "pending" if verify_first else "connected"
    if row is None:
        row = CloudMarketplaceSession(
            user_id=_seller_id(),
            marketplace=market,
            state=state,
            account_label=label,
            expires_at=expires_at,
            connected_at=now,
            last_used_at=None,
            last_error=None,
            proxy_session_id=secrets.token_hex(12),
            upload_token_hash=None,
            upload_token_expires_at=None,
            updated_at=now,
        )
        db.add(row)
    else:
        row.state = state
        row.account_label = label
        row.expires_at = expires_at
        row.connected_at = now
        row.last_error = None
        if not row.proxy_session_id:
            row.proxy_session_id = secrets.token_hex(12)
        row.upload_token_hash = None
        row.upload_token_expires_at = None
        row.updated_at = now
    _audit(db, market, "connect", detail=f"cookies={len(cookies)}")
    db.commit()
    return serialize_session_row(market, row)


def activate_verified_session(
    db: Session, marketplace: str, generation: str | None, label: str | None,
) -> dict[str, Any] | None:
    from anteroom.services.promotion_automations import resume_after_cloud_reconnect

    row = _row(db, marketplace, lock=True)
    connected_at = _as_utc(row.connected_at) if row else None
    if row is None or connected_at is None or connected_at.isoformat() != generation or row.state not in {"pending", "connected"}:
        return None
    expires_at = _as_utc(row.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        mark_session_expired(db, marketplace, f"Your {marketplace.title()} cloud session expired.", row.connected_at)
        return serialize_session_row(marketplace, row)
    row.state = "connected"
    if label:
        row.account_label = _sanitize_label({"name": label})
    count = resume_after_cloud_reconnect(db, _seller_id(), marketplace)
    if count:
        _audit(db, marketplace, "automations_resumed", detail=f"count={count}")
        log.info("%s cloud reconnect resumed %s automation(s)", marketplace, count)
    db.commit()
    return serialize_session_row(marketplace, row)


def needs_automation_resume(db: Session, marketplace: str) -> bool:
    from anteroom.models.promotion_automation import PromotionAutomation
    from anteroom.services.promotion_automations import CLOUD_DISCONNECTED

    return db.scalar(select(PromotionAutomation.id).where(
        PromotionAutomation.user_id == _seller_id(),
        PromotionAutomation.marketplace == marketplace,
        PromotionAutomation.paused_reason == CLOUD_DISCONNECTED,
    ).limit(1)) is not None


def revoke_session(db: Session, marketplace: str) -> dict[str, Any]:
    """Delete encrypted cookies from Vault and mark the cloud session revoked."""
    from anteroom.services.promotion_automations import pause_for_cloud_disconnect

    market = str(marketplace or "").lower()
    if market not in SUPPORTED_CLOUD_MARKETPLACES:
        raise CloudSessionError("Unsupported cloud marketplace.")
    row = _row(db, market, lock=True)
    ctx = current()
    if ctx is not None:
        ctx.secrets.delete(vault_account(market))
    now = utcnow()
    if row is not None:
        if row.state == "connected" or (row.state == "pending" and row.connected_at is not None):
            pause_for_cloud_disconnect(db, _seller_id(), market)
        row.state = "revoked"
        row.account_label = None
        row.expires_at = None
        row.last_error = None
        row.upload_token_hash = None
        row.upload_token_expires_at = None
        row.updated_at = now
    _audit(db, market, "revoke")
    db.commit()
    return serialize_session_row(market, row)


def mark_session_expired(db: Session, marketplace: str, error: str, generation: datetime | None) -> None:
    market = str(marketplace or "").lower()
    from anteroom.services.promotion_automations import pause_for_cloud_disconnect

    row = _row(db, market, lock=True)
    if row is None or row.state not in {"connected", "pending"} or row.connected_at != generation:
        return
    # Minting a first upload token also creates a pending row, without credentials.
    if row.state == "connected" or row.connected_at is not None:
        pause_for_cloud_disconnect(db, _seller_id(), market)
    row.state = "expired"
    row.last_error = (error or f"{market.title()} asked to sign in again.")[:300]
    row.updated_at = utcnow()
    _audit(db, market, "expired", detail=(error or "signed_out")[:200])
    db.commit()


def _assert_can_decrypt() -> None:
    if _process_role() == "web":
        raise CloudSessionDecryptForbidden(
            "Cloud marketplace credentials must never be decrypted in a web process."
        )


def read_credentials_fresh(
    db: Session,
    marketplace: str,
    operation: str | None = None,
) -> tuple[dict[str, Any], str | None, datetime | None] | None:
    """Read freshly decrypted credentials from Vault (bypassing in-memory cache).

    Refuses to run inside a ``web`` process. Returns ``(payload, proxy_session_id, connected_at)``
    or ``None`` when no active cloud session exists.
    """
    _assert_can_decrypt()
    market = str(marketplace or "").lower()
    if not is_cloud_enabled(market):
        return None
    row = _row(db, market, lock=True)
    states = {"connected", "pending"} if operation == "marketplace.verify_session" else {"connected"}
    if row is None or row.state not in states:
        return None
    expires_at = _as_utc(row.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        mark_session_expired(db, market, f"Your {market.title()} cloud session expired.", row.connected_at)
        return None

    ctx = current()
    assert ctx is not None
    get_fresh = getattr(ctx.secrets, "get_fresh", None)
    raw = get_fresh(vault_account(market)) if callable(get_fresh) else ctx.secrets.get(vault_account(market))
    if not isinstance(raw, str) or not raw:
        mark_session_expired(db, market, f"{market.title()} cloud credentials are missing. Reconnect in Settings → Connections.", row.connected_at)
        return None
    try:
        data = json.loads(raw)
    except Exception:
        mark_session_expired(db, market, f"Stored {market.title()} credentials could not be read. Reconnect in Settings → Connections.", row.connected_at)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        mark_session_expired(db, market, f"Stored {market.title()} credentials are invalid. Reconnect in Settings → Connections.", row.connected_at)
        return None

    now = utcnow()
    row.last_used_at = now
    row.updated_at = now
    _audit(db, market, "decrypt", detail=operation)
    credentials = data, row.proxy_session_id, row.connected_at
    db.commit()
    return credentials


def refresh_cookies(
    db: Session,
    marketplace: str,
    updated_cookies: list[dict[str, Any]],
    user_agent: str | None,
    generation: datetime | None,
) -> None:
    """Persist rotated cookies back to Vault once at the end of an operation."""
    _assert_can_decrypt()
    market = str(marketplace or "").lower()
    row = _row(db, market, lock=True)
    if row is None or row.state not in {"connected", "pending"} or row.connected_at != generation:
        return
    try:
        cookies, label, expires_at, clean_ua = validate_and_normalize_cookies(market, updated_cookies, user_agent)
    except CloudSessionError:
        return
    now = utcnow()
    payload = json.dumps({
        "version": 1,
        "marketplace": market,
        "cookies": cookies,
        "user_agent": clean_ua,
        "captured_at": now.isoformat(),
    }, separators=(",", ":"))
    ctx = current()
    assert ctx is not None
    if ctx.secrets.set(vault_account(market), payload):
        if label:
            row.account_label = label
        if expires_at is not None:
            row.expires_at = expires_at
        row.last_used_at = now
        row.updated_at = now
        _audit(db, market, "refresh_cookies", detail=f"cookies={len(cookies)}")
        db.commit()


def has_active_session(marketplace: str, db: Session | None = None) -> bool:
    market = str(marketplace or "").lower()
    if not is_cloud_enabled(market):
        return False
    owns = db is None
    session = _open_session() if owns else db
    try:
        row = _row(session, market)
        if row is None or row.state != "connected":
            return False
        expires_at = _as_utc(row.expires_at)
        return expires_at is None or expires_at > utcnow()
    finally:
        if owns:
            session.close()


def public_session(marketplace: str, db: Session | None = None) -> dict[str, Any] | None:
    market = str(marketplace or "").lower()
    if not is_cloud_enabled(market):
        return None
    owns = db is None
    session = _open_session() if owns else db
    try:
        return serialize_session_row(market, _row(session, market))
    finally:
        if owns:
            session.close()


def public_sessions(db: Session | None = None) -> dict[str, dict[str, Any]]:
    markets = enabled_marketplaces()
    if not markets:
        return {}
    owns = db is None
    session = _open_session() if owns else db
    try:
        rows = {
            row.marketplace: row
            for row in session.execute(
                select(CloudMarketplaceSession).where(CloudMarketplaceSession.marketplace.in_(markets))
            ).scalars().all()
        }
        return {market: serialize_session_row(market, rows.get(market)) for market in sorted(markets)}
    finally:
        if owns:
            session.close()


def active_owners(engine, marketplace: str) -> list[str]:
    """Return seller UUIDs with an active cloud session for worker-side schedulers."""
    market = str(marketplace or "").lower()
    if market not in _configured_marketplaces():
        return []
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            rows = conn.execute(
                text("SELECT owner_id FROM anteroom_private.cloud_session_owners(:market)"),
                {"market": market},
            ).scalars().all()
        else:
            rows = conn.execute(
                text(
                    "SELECT user_id FROM cloud_marketplace_sessions "
                    "WHERE marketplace = :market AND state = 'connected' "
                    "AND user_id <> :local"
                ),
                {"market": market, "local": LOCAL_USER_ID},
            ).scalars().all()
    return [str(owner) for owner in rows if owner]


def proxy_url_for(proxy_session_id: str | None) -> str | None:
    template = (os.environ.get("ANTEROOM_CLOUD_PROXY_URL") or "").strip()
    if not template:
        return None
    if "{session}" not in template:
        return template
    safe_id = proxy_session_id if proxy_session_id and _SAFE_PROXY_SESSION.match(proxy_session_id) else "default"
    return template.replace("{session}", safe_id)


def load_journals(db: Session, marketplace: str) -> dict[str, Any]:
    rows = db.execute(
        select(CloudOperationJournal).where(CloudOperationJournal.marketplace == marketplace)
    ).scalars().all()
    return {row.journal_key: row.payload for row in rows}


def upsert_journals(db: Session, marketplace: str, entries: dict[str, Any]) -> None:
    if not entries:
        return
    seller = _seller_id()
    now = utcnow()
    existing = {
        row.journal_key: row
        for row in db.execute(
            select(CloudOperationJournal).where(CloudOperationJournal.journal_key.in_(list(entries.keys())))
        ).scalars().all()
    }
    for key, payload in entries.items():
        if not isinstance(key, str) or len(key) > 160:
            continue
        row = existing.get(key)
        if row is None:
            db.add(CloudOperationJournal(
                user_id=seller,
                marketplace=marketplace,
                journal_key=key,
                payload=payload,
                updated_at=now,
            ))
        else:
            row.payload = payload
            row.updated_at = now
    db.commit()


def delete_journals(db: Session, marketplace: str, keys: list[str]) -> None:
    valid = [k for k in keys if isinstance(k, str) and len(k) <= 160]
    if not valid:
        return
    rows = db.execute(
        select(CloudOperationJournal).where(
            CloudOperationJournal.marketplace == marketplace,
            CloudOperationJournal.journal_key.in_(valid),
        )
    ).scalars().all()
    for row in rows:
        db.delete(row)
    db.commit()
