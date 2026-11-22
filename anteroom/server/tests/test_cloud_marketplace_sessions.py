"""Tests for opt-in Poshmark Cloud marketplace sessions and isolated execution."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, UTC
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.models.cloud_session import (
    CloudMarketplaceSession,
    CloudOperationJournal,
    CloudSessionAudit,
)
from anteroom.models.conversation import Conversation, utcnow
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.routes.internal_devices import CloudMarketplaceExecute
from anteroom.services import cloud_marketplace, cloud_sessions, native_marketplaces, order_history, schema_migrations
from anteroom.services.hosted_context import for_seller
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.service_context import use


def _fake_jwt(
    *,
    user_id: str = "64b123456789abcdef012345",
    username: str = "closet_alice",
    exp: int | None = None,
) -> str:
    if exp is None:
        exp = int((datetime.now(UTC) + timedelta(days=7)).timestamp())
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "username": username, "exp": exp}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def _sample_cookies(*, jwt_value: str | None = None, exp: int | None = None, include_foreign: bool = True) -> list[dict]:
    if exp is None:
        exp = int((datetime.now(UTC) + timedelta(days=7)).timestamp())
    cookies = [
        {
            "name": "jwt",
            "value": jwt_value or _fake_jwt(exp=exp),
            "domain": ".poshmark.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "session": False,
            "expirationDate": exp,
        },
        {
            "name": "_web_app_session",
            "value": "sess-123",
            "domain": "poshmark.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "session": True,
        },
    ]
    if include_foreign:
        cookies.append({
            "name": "evil_cookie",
            "value": "do-not-store",
            "domain": ".example.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "session": True,
        })
    return cookies


class _FakeVault:
    """In-memory stand-in for VaultSecretStore tracking fresh reads and writes."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.get_fresh_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def bind(self, seller_id: str):
        vault = self

        class _Bound:
            def get(self, account: str) -> str | None:
                return vault.data.get((seller_id, account))

            def get_fresh(self, account: str) -> str | None:
                vault.get_fresh_calls.append((seller_id, account))
                return vault.data.get((seller_id, account))

            def set(self, account: str, value: str) -> bool:
                vault.data[(seller_id, account)] = value
                return True

            def delete(self, account: str) -> bool:
                vault.delete_calls.append((seller_id, account))
                vault.data.pop((seller_id, account), None)
                return True

        return _Bound()


def _patch_vault(monkeypatch) -> _FakeVault:
    vault = _FakeVault()
    from anteroom.services import hosted_context

    orig_for_seller = hosted_context.for_seller

    def _wrapped(seller_id: str, *args, **kwargs):
        ctx = orig_for_seller(seller_id, *args, **kwargs)
        return SimpleNamespace(
            seller_id=ctx.seller_id,
            session_factory=ctx.session_factory,
            secrets=vault.bind(seller_id),
            photos=ctx.photos,
        )

    monkeypatch.setattr("anteroom.services.hosted_context.for_seller", _wrapped)
    monkeypatch.setattr("anteroom.routes.cloud_sessions.for_seller", _wrapped)
    monkeypatch.setattr(
        "test_cloud_marketplace_sessions.for_seller", _wrapped, raising=False
    )
    return vault


def test_cloud_sessions_disabled_by_default_and_never_in_local_mode(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    monkeypatch.delenv("ANTEROOM_CLOUD_MARKETPLACES", raising=False)
    alice = headers(signing_keys)

    listing = client.get("/api/cloud-sessions", headers=alice)
    assert listing.status_code == 200
    assert listing.json() == {"enabled": [], "sessions": {}}

    assert client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice).status_code == 404
    assert client.post("/api/cloud-sessions/poshmark/verify", headers=alice).status_code == 404
    assert client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": f"{USER_ID}.{'a' * 32}"},
        json={"cookies": _sample_cookies()},
    ).status_code == 404

    # Local mode (no hosted ServiceContext) never enables cloud sessions even if env var is set.
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    assert cloud_sessions.enabled_marketplaces() == frozenset()
    assert cloud_sessions.is_cloud_enabled("poshmark") is False


def test_connect_token_single_use_upload_cookie_filtering_and_disconnect(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    alice = headers(signing_keys)

    # 1. Mint single-use upload token
    minted = client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice)
    assert minted.status_code == 200
    token_data = minted.json()
    assert token_data["marketplace"] == "poshmark"
    token = token_data["token"]
    assert token.startswith(f"{USER_ID}.")

    # 2. Direct upload without Supabase JWT succeeds, strips foreign cookies, never returns cookies
    upload = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": token},
        json={"cookies": _sample_cookies(include_foreign=True), "user_agent": "Mozilla/5.0 TestChrome"},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["ok"] is True
    assert body["session"]["state"] == "signed_in"
    assert body["session"]["cloud_connected"] is True
    assert body["session"]["label"] == "closet_alice"
    assert "cookies" not in json.dumps(body)
    assert "evil_cookie" not in json.dumps(body)

    # Stored Vault payload only contains poshmark.com cookies
    stored_raw = vault.data[(USER_ID, "cloud-session-poshmark")]
    stored = json.loads(stored_raw)
    assert stored["user_agent"] == "Mozilla/5.0 TestChrome"
    assert {c["name"] for c in stored["cookies"]} == {"jwt", "_web_app_session"}

    # 3. Replay of the same single-use upload token is rejected (401)
    replay = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": token},
        json={"cookies": _sample_cookies()},
    )
    assert replay.status_code == 401

    # 4. Expired upload token is rejected (401)
    minted2 = client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice).json()
    with Session(engine) as db:
        row = db.execute(
            select(CloudMarketplaceSession).where(CloudMarketplaceSession.user_id == USER_ID)
        ).scalar_one()
        row.upload_token_expires_at = utcnow() - timedelta(seconds=5)
        db.commit()
    expired_upload = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": minted2["token"]},
        json={"cookies": _sample_cookies()},
    )
    assert expired_upload.status_code == 401

    # 5. Missing or expired Poshmark JWT cookie is rejected (400)
    minted3 = client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice).json()
    bad_upload = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": minted3["token"]},
        json={"cookies": [{"name": "_web_app_session", "value": "x", "domain": "poshmark.com"}]},
    )
    assert bad_upload.status_code == 400

    minted4 = client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice).json()
    expired_ts = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    expired_jwt_upload = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": minted4["token"]},
        json={"cookies": _sample_cookies(exp=expired_ts)},
    )
    assert expired_jwt_upload.status_code == 400

    # 6. Active owners helper sees Alice only
    assert cloud_sessions.active_owners(engine, "poshmark") == [USER_ID]

    # 7. Disconnect deletes Vault slot and resets metadata to signed_out
    disconnected = client.delete("/api/cloud-sessions/poshmark", headers=alice)
    assert disconnected.status_code == 200
    assert disconnected.json()["session"]["state"] == "signed_out"
    assert disconnected.json()["session"]["cloud_connected"] is False
    assert (USER_ID, "cloud-session-poshmark") not in vault.data
    assert (USER_ID, "cloud-session-poshmark") in vault.delete_calls
    assert cloud_sessions.active_owners(engine, "poshmark") == []


def test_web_process_role_forbids_decrypt_and_forwards_to_worker(cloud, monkeypatch):  # noqa: F811
    _, _engine = cloud
    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "all")

    with use(for_seller(USER_ID)):
        from anteroom.database import SessionLocal

        with SessionLocal() as db:
            cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(), "UA/1.0")

        # Web process must NEVER decrypt or refresh cookies
        monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "web")
        with SessionLocal() as db:
            with pytest.raises(cloud_sessions.CloudSessionDecryptForbidden):
                cloud_sessions.read_credentials_fresh(db, "poshmark")
            with pytest.raises(cloud_sessions.CloudSessionDecryptForbidden):
                cloud_sessions.refresh_cookies(db, "poshmark", _sample_cookies(), None, None)

        # Web process forwards execute() to worker instead of calling execute_local()
        forwarded: list[dict] = []

        async def _fake_forward(path, payload, **kwargs):
            forwarded.append({"path": path, "payload": payload, **kwargs})
            return {"ok": True, "result": {"ok": True, "forwarded": True}}

        async def _boom(*args, **kwargs):
            raise AssertionError("web process must never call execute_local")

        monkeypatch.setattr(cloud_marketplace, "_forward_to_worker", _fake_forward)
        monkeypatch.setattr(cloud_marketplace, "execute_local", _boom)

        res = asyncio.run(
            cloud_marketplace.execute(
                "poshmark",
                {"type": "marketplace.sessions", "payload": {"marketplace": "poshmark"}},
            )
        )
        assert res == {"ok": True, "forwarded": True}
        assert len(forwarded) == 1
        assert vault.get_fresh_calls == []


@pytest.mark.parametrize("failure", ["timeout", "http_error", "invalid_json", "invalid_body"])
def test_lost_worker_reply_requires_listing_reconciliation(monkeypatch, failure):
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "web")
    monkeypatch.setenv("ANTEROOM_WORKER_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("ANTEROOM_PROCESS_TOKEN", "test-token")
    monkeypatch.setattr(cloud_sessions, "is_cloud_enabled", lambda market: True)
    monkeypatch.setattr(cloud_marketplace, "_seller_id", lambda: USER_ID)

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("lost response", request=request)
        if failure == "http_error":
            return httpx.Response(500)
        if failure == "invalid_json":
            return httpx.Response(200, text="invalid")
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient
    monkeypatch.setattr(cloud_marketplace.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    result = asyncio.run(cloud_marketplace.execute("poshmark", {"type": "marketplace.execute", "payload": {}}))
    assert result["safe_to_retry"] is False
    outcome = native_marketplaces._outcome("poshmark", "list", {}, result)
    assert outcome["state"] == "unknown"
    assert outcome["error_code"] == "uncertain"


def test_missing_worker_configuration_is_safe_to_retry(monkeypatch):
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "web")
    monkeypatch.delenv("ANTEROOM_WORKER_BASE_URL", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("ANTEROOM_PROCESS_TOKEN", raising=False)
    monkeypatch.setattr(cloud_sessions, "is_cloud_enabled", lambda market: True)
    monkeypatch.setattr(cloud_marketplace, "_seller_id", lambda: USER_ID)
    result = asyncio.run(cloud_marketplace.execute("poshmark", {"type": "marketplace.execute"}))
    assert result["safe_to_retry"] is True


def test_worker_accepts_sales_history_deadline():
    request = {"user_id": USER_ID, "marketplace": "poshmark", "command": {"type": "marketplace.sales_history"}}
    assert CloudMarketplaceExecute(**request, timeout=order_history.READ_TIMEOUT_S).timeout == 1200
    with pytest.raises(ValidationError):
        CloudMarketplaceExecute(**request, timeout=1201)


def test_verify_persists_live_sign_out(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "worker")
    alice = headers(signing_keys)
    token = client.post("/api/cloud-sessions/poshmark/connect-token", headers=alice).json()["token"]
    uploaded = client.post(
        "/api/cloud-sessions/poshmark/upload",
        headers={"X-Cloud-Upload-Token": token},
        json={"cookies": _sample_cookies(), "user_agent": "UA/1.0"},
    )
    assert uploaded.json()["session"]["state"] == "signed_in"

    async def rejected_session(marketplace, seller_id, job_id, run_payload, timeout):
        assert run_payload["command"]["type"] == "marketplace.verify_session"
        return {"result": {"ok": True, "session": {"state": "expired"}}, "cookies_changed": False}

    monkeypatch.setattr(cloud_marketplace, "_run_subprocess", rejected_session)
    verified = client.post("/api/cloud-sessions/poshmark/verify", headers=alice)
    assert verified.status_code == 200
    assert verified.json()["session"]["state"] == "expired"
    assert client.get("/api/cloud-sessions", headers=alice).json()["sessions"]["poshmark"]["state"] == "expired"


@pytest.mark.parametrize(("market", "cookie_name"), [("mercari", "session"), ("depop", "access_token")])
def test_marketplace_cloud_verification_and_resume(cloud, signing_keys, monkeypatch, market, cookie_name):  # noqa: F811
    from anteroom.models.promotion_automation import PromotionAutomation
    from anteroom.services import promotion_automations

    client, engine = cloud
    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "mercari,depop")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "worker")
    alice = headers(signing_keys)
    cookies = [
        {"name": cookie_name, "value": "session-secret", "domain": f"www.{market}.com"},
        {"name": cookie_name, "value": "foreign-secret", "domain": f"evil-{market}.com"},
    ]
    verified_state = "signed_in"
    expected_cookie = "session-secret"

    async def verify_market(marketplace, seller_id, job_id, run_payload, timeout):
        assert marketplace == market
        assert seller_id == USER_ID
        assert run_payload["command"]["type"] == "marketplace.verify_session"
        assert [cookie["value"] for cookie in run_payload["cookies"]] == [expected_cookie]
        return {"result": {"ok": True, "session": {"state": verified_state, "label": "Seller"}}}

    monkeypatch.setattr(cloud_marketplace, "_run_subprocess", verify_market)

    def connect(value="session-secret"):
        token = client.post(f"/api/cloud-sessions/{market}/connect-token", headers=alice).json()["token"]
        submitted = [{**cookies[0], "value": value}, cookies[1]]
        return client.post(f"/api/cloud-sessions/{market}/upload", headers={"X-Cloud-Upload-Token": token},
                           json={"cookies": submitted})

    first = connect()
    assert first.status_code == 200, first.text
    assert first.json()["session"]["label"] == "Seller"
    assert "session-secret" not in first.text
    assert "foreign-secret" not in vault.data[(USER_ID, f"cloud-session-{market}")]
    saved = vault.data[(USER_ID, f"cloud-session-{market}")]
    verified_state = "expired"
    expected_cookie = "expired-candidate"
    assert connect(expected_cookie).status_code == 409
    assert vault.data[(USER_ID, f"cloud-session-{market}")] == saved
    assert client.get("/api/cloud-sessions", headers=alice).json()["sessions"][market]["state"] == "signed_in"
    expected_cookie = "session-secret"
    with Session(engine) as db:
        for name, enabled, reason in (("enabled", True, None), ("manual", False, None), ("unknown", False, "Unconfirmed send")):
            db.add(PromotionAutomation(user_id=USER_ID, name=name, marketplace=market, action="offer",
                                       enabled=enabled, paused_reason=reason))
        db.commit()

    verified_state = "expired"
    failed = client.post(f"/api/cloud-sessions/{market}/verify", headers=alice)
    assert failed.json()["ok"] is False
    assert failed.json()["session"]["state"] == "expired"
    with Session(engine) as db:
        paused = db.scalar(select(PromotionAutomation).where(PromotionAutomation.name == "enabled"))
        assert paused is not None and not paused.enabled
        assert paused.paused_reason == promotion_automations.CLOUD_DISCONNECTED

    verified_state = "signed_in"
    assert connect().status_code == 200
    with Session(engine) as db:
        rows = {row.name: row for row in db.scalars(select(PromotionAutomation))}
        assert rows["enabled"].enabled and rows["enabled"].paused_reason is None
        assert rows["enabled"].next_run_at is not None
        assert not rows["manual"].enabled and not rows["unknown"].enabled
        assert rows["unknown"].paused_reason == "Unconfirmed send"
        assert db.scalar(select(CloudSessionAudit).where(CloudSessionAudit.event == "automations_resumed"))

    assert client.delete(f"/api/cloud-sessions/{market}", headers=alice).json()["ok"]
    assert (USER_ID, f"cloud-session-{market}") not in vault.data


@pytest.mark.parametrize("dimensions", [(1, 4096), (4096, 1)])
def test_depop_photo_preparation_preserves_a_narrow_image(dimensions):
    from io import BytesIO
    from PIL import Image

    source = BytesIO()
    Image.new("RGB", dimensions, "black").save(source, "PNG")
    result = cloud_marketplace._prepare_photos("depop", [
        {"id": "photo", "base64": base64.b64encode(source.getvalue()).decode("ascii")},
    ])
    with Image.open(BytesIO(base64.b64decode(result[0]["base64"]))) as photo:
        assert photo.size == (1280, 1280)
        assert max(photo.getpixel((639, 639))) < 128
        assert min(photo.getpixel((0, 0))) > 240


def test_consumed_token_cannot_restore_a_revoked_session(cloud, monkeypatch):  # noqa: F811
    from anteroom.database import SessionLocal

    _, engine = cloud
    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    with use(for_seller(USER_ID)), SessionLocal() as db:
        token = cloud_sessions.mint_upload_token(db, "poshmark")["token"]
        upload = cloud_sessions.consume_upload_token(engine, "poshmark", token)
        assert upload is not None
        cloud_sessions.revoke_session(db, "poshmark")
        with pytest.raises(cloud_sessions.CloudSessionError, match="connection changed"):
            cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(), upload=upload)
        assert not vault.data


def test_execute_local_uses_fresh_vault_rotates_cookies_and_marks_expired_on_signed_out(cloud, monkeypatch):  # noqa: F811
    _, _engine = cloud
    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "worker")

    from anteroom.database import SessionLocal

    with use(for_seller(USER_ID)):
        with SessionLocal() as db:
            cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(), "UA/1.0")

        rotated_jwt = _fake_jwt(username="closet_alice_rotated")
        rotated_cookies = _sample_cookies(jwt_value=rotated_jwt, include_foreign=False)

        async def _fake_subproc_rotate(marketplace, seller_id, job_id, run_payload, timeout):
            assert run_payload["user_agent"] == "UA/1.0"
            return {
                "result": {"ok": True, "action": "list"},
                "cookies_changed": True,
                "cookies": rotated_cookies,
            }

        monkeypatch.setattr(cloud_marketplace, "_run_subprocess", _fake_subproc_rotate)
        res = asyncio.run(
            cloud_marketplace.execute_local(
                "poshmark",
                {"type": "marketplace.sessions", "payload": {"marketplace": "poshmark"}},
            )
        )
        assert res["ok"] is True
        assert vault.get_fresh_calls == [(USER_ID, "cloud-session-poshmark")]
        stored = json.loads(vault.data[(USER_ID, "cloud-session-poshmark")])
        jwt_cookie = next(c for c in stored["cookies"] if c["name"] == "jwt")
        assert jwt_cookie["value"] == rotated_jwt

        # On signed_out, session transitions to expired
        async def _fake_subproc_signed_out(marketplace, seller_id, job_id, run_payload, timeout):
            return {
                "result": {"ok": False, "error_code": "signed_out", "error": "Poshmark session expired"},
                "cookies_changed": False,
                "cookies": [],
            }

        monkeypatch.setattr(cloud_marketplace, "_run_subprocess", _fake_subproc_signed_out)
        res2 = asyncio.run(
            cloud_marketplace.execute_local(
                "poshmark",
                {"type": "marketplace.sessions", "payload": {"marketplace": "poshmark"}},
            )
        )
        assert res2["error_code"] == "signed_out"
        with SessionLocal() as db:
            summary = cloud_sessions.public_session("poshmark", db)
            assert summary is not None and summary["state"] == "expired"
            assert cloud_sessions.has_active_session("poshmark", db) is False


def test_real_node_runner_enforces_confirm_true_for_writes(cloud, monkeypatch):  # noqa: F811
    """Even if a caller omits confirm=True, the isolated Node runner rejects writes before touching Poshmark."""
    _, _engine = cloud
    _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "worker")

    from anteroom.database import SessionLocal

    with use(for_seller(USER_ID)):
        with SessionLocal() as db:
            cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(), "UA/1.0")

        for cmd in [
            {"type": "marketplace.execute", "payload": {"marketplace": "poshmark", "action": "list", "confirm": False}},
            {"type": "marketplace.delete", "payload": {"marketplace": "poshmark", "confirm": False}},
            {"type": "marketplace.promote", "payload": {"marketplace": "poshmark", "confirm": False}},
        ]:
            result = asyncio.run(cloud_marketplace.execute_local("poshmark", cmd, timeout=15.0))
            assert result["ok"] is False
            assert result["error_code"] == "rejected"


def test_postgres_rls_isolates_cloud_session_tables_and_blocks_private_functions(migration_engine):  # noqa: F811
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enforces hosted row security")
    schema_migrations.ensure_schema(migration_engine)

    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())

    with Session(migration_engine) as db:
        db.add_all([
            CloudMarketplaceSession(
                user_id=alice_id,
                marketplace="poshmark",
                state="connected",
                account_label="alice_closet",
                upload_token_hash="a" * 64,
                upload_token_expires_at=utcnow() + timedelta(minutes=2),
            ),
            CloudMarketplaceSession(
                user_id=bob_id,
                marketplace="poshmark",
                state="connected",
                account_label="bob_closet",
                upload_token_hash="b" * 64,
                upload_token_expires_at=utcnow() + timedelta(minutes=2),
            ),
            CloudSessionAudit(user_id=alice_id, marketplace="poshmark", event="connect"),
            CloudSessionAudit(user_id=bob_id, marketplace="poshmark", event="connect"),
            CloudOperationJournal(
                user_id=alice_id,
                marketplace="poshmark",
                journal_key="promotion_act_alice",
                payload={"state": "sent"},
            ),
            CloudOperationJournal(
                user_id=bob_id,
                marketplace="poshmark",
                journal_key="promotion_act_bob",
                payload={"state": "sent"},
            ),
        ])
        db.commit()

    # Alice can only see and modify Alice's rows across all three tables
    with SellerDatabaseSession(bind=migration_engine, user_id=alice_id) as db:
        sessions = db.scalars(select(CloudMarketplaceSession)).all()
        assert [s.account_label for s in sessions] == ["alice_closet"]
        audits = db.scalars(select(CloudSessionAudit)).all()
        assert all(a.user_id == alice_id for a in audits) and len(audits) == 1
        journals = db.scalars(select(CloudOperationJournal)).all()
        assert [j.journal_key for j in journals] == ["promotion_act_alice"]

        # Unfiltered update/delete cannot touch Bob's rows
        assert db.execute(text("UPDATE cloud_marketplace_sessions SET account_label = 'edited'")).rowcount == 1
        assert db.execute(text("DELETE FROM cloud_operation_journals")).rowcount == 1
        db.commit()

        # Restricted seller role must NOT execute SECURITY DEFINER private helpers
        with pytest.raises(DBAPIError):
            db.execute(text("SELECT * FROM anteroom_private.cloud_session_owners('poshmark')")).all()
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(
                text("SELECT anteroom_private.consume_cloud_upload_token(:owner, 'poshmark', :hash)"),
                {"owner": bob_id, "hash": "b" * 64},
            ).scalar()
        db.rollback()

    with Session(migration_engine) as db:
        bob_session = db.execute(
            select(CloudMarketplaceSession).where(CloudMarketplaceSession.user_id == bob_id)
        ).scalar_one()
        assert bob_session.account_label == "bob_closet"
        bob_journal = db.execute(
            select(CloudOperationJournal).where(CloudOperationJournal.user_id == bob_id)
        ).scalar_one()
        assert bob_journal.journal_key == "promotion_act_bob"


def test_executor_pinning_on_marketplace_listing_and_cloud_promotion_journal(cloud, monkeypatch):  # noqa: F811
    _, _engine = cloud
    _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")

    from anteroom.database import SessionLocal
    from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
    from anteroom.services import listing_promotions

    with use(for_seller(USER_ID)):
        with SessionLocal() as db:
            conv = Conversation(title="Pin test jacket", status="draft")
            db.add(conv)
            db.commit()
            conv_id = conv.id

            MarketplaceListingRepo(db).save(
                conv_id,
                "poshmark",
                {"state": "publishing", "action": "publish", "executor": "cloud", "job_id": "job-1"},
            )
            saved = MarketplaceListingRepo(db).get(conv_id, "poshmark")
            assert saved is not None and saved["executor"] == "cloud"

            # Persisting a follow-up update without executor preserves pinned executor or lets explicit update overwrite
            row = db.execute(
                select(MarketplaceListing).where(
                    MarketplaceListing.conversation_id == conv_id,
                    MarketplaceListing.marketplace == "poshmark",
                )
            ).scalar_one()
            assert row.executor == "cloud"

            # Cloud promotion journal check reads directly from Postgres without Chrome or Node
            from anteroom.models.listing_promotion import ListingPromotion
            from anteroom.repositories.queries import JobRepo

            promo_job = JobRepo(db).create(
                conv_id,
                "rev-1",
                {"promotion": {"marketplace": "poshmark", "action": "share", "executor": "cloud"}},
                status="failed",
                current_step="listing_promotion",
            )
            promo_row = ListingPromotion(
                batch_id="batch-1",
                conversation_id=conv_id,
                marketplace="poshmark",
                action="share",
                listing_id="64b123456789abcdef012345",
                state="unknown",
                job_id=promo_job.id,
                price_cents=4500,
                offer_price_cents=4500,
                floor_cents=3000,
                expires_at=utcnow() + timedelta(minutes=15),
            )
            db.add(promo_row)
            db.commit()
            promo_id = promo_row.id

            cloud_sessions.upsert_journals(
                db,
                "poshmark",
                {f"promotion_{promo_id}": {"finished": True, "result": {"ok": True, "state": "done"}}},
            )

        checked = asyncio.run(listing_promotions.check(promo_id))
        assert checked["state"] == "done"
        assert "Shared" in (checked.get("note") or "")


def test_internal_cloud_marketplace_route_requires_process_token(cloud, monkeypatch):  # noqa: F811
    client, _engine = cloud
    monkeypatch.setenv("ANTEROOM_PROCESS_TOKEN", "secret-process-token")
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")

    payload = {
        "user_id": USER_ID,
        "marketplace": "poshmark",
        "command": {"type": "marketplace.sessions", "payload": {"marketplace": "poshmark"}},
    }
    assert client.post("/internal/cloud-marketplace/execute", json=payload).status_code == 404
    assert client.post(
        "/internal/cloud-marketplace/execute",
        headers={"Authorization": "Bearer wrong"},
        json=payload,
    ).status_code == 404

    async def _fake_local(marketplace, command, **kwargs):
        return {"ok": True, "sessions": {"poshmark": {"state": "signed_in"}}}

    monkeypatch.setattr(cloud_marketplace, "execute_local", _fake_local)
    ok = client.post(
        "/internal/cloud-marketplace/execute",
        headers={"Authorization": "Bearer secret-process-token"},
        json=payload,
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["result"]["ok"] is True


def test_stale_worker_cannot_modify_replacement_session(cloud, monkeypatch):  # noqa: F811
    from anteroom.database import SessionLocal

    vault = _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    with use(for_seller(USER_ID)), SessionLocal() as db:
        cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies())
        credentials = cloud_sessions.read_credentials_fresh(db, "poshmark")
        assert credentials is not None
        generation = credentials[2]
        cloud_sessions.revoke_session(db, "poshmark")
        cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(jwt_value=_fake_jwt(username="replacement")))
        replacement = dict(vault.data)
        cloud_sessions.refresh_cookies(db, "poshmark", _sample_cookies(), None, generation)
        cloud_sessions.mark_session_expired(db, "poshmark", "old response", generation)
        assert vault.data == replacement
        assert cloud_sessions.has_active_session("poshmark", db)


def test_cancel_before_slot_prevents_runner_start(monkeypatch):
    monkeypatch.setattr(cloud_marketplace, "_seller_id", lambda: USER_ID)
    ready = asyncio.Event()
    sem = asyncio.Semaphore(0)

    async def session(work, *args):
        if work is cloud_sessions.read_credentials_fresh:
            return {}, None, None
        ready.set()
        return {}

    monkeypatch.setattr(cloud_marketplace, "in_worker_session", session)
    monkeypatch.setattr(cloud_marketplace, "_loop_semaphore", lambda: sem)

    async def run():
        task = asyncio.create_task(cloud_marketplace.execute_local(
            "poshmark", {"job_id": "job", "payload": {"operation_id": "operation"}}))
        await ready.wait()
        assert await cloud_marketplace.cancel_local(USER_ID, "operation")
        sem.release()
        assert (await task)["error_code"] == "cancelled"
        assert not await cloud_marketplace.cancel_local(USER_ID, "operation")

    asyncio.run(run())


def test_cancel_reaches_worker_with_operation(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(cloud_marketplace, "_seller_id", lambda: USER_ID)
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "web")
    monkeypatch.setenv("ANTEROOM_WORKER_BASE_URL", "http://worker.process.test.internal:8080")
    targets = set()

    async def forward(path, body, timeout, target):
        targets.add(target)
        return {"cancelled": target == "http://[fd00::2]:8080" and body["job_id"] == "owned"}

    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
            (0, 0, 0, "", (ip, 8080, 0, 0)) for ip in ("fd00::1", "fd00::2")]))
        monkeypatch.setattr(cloud_marketplace, "_forward_to_worker", forward)
        assert await cloud_marketplace.cancel("owned")
        assert not await cloud_marketplace.cancel("absent")

    asyncio.run(run())
    assert len(targets) == 2


def test_runner_reads_large_result_and_reports_invalid_frames(tmp_path, monkeypatch):
    script = tmp_path / "runner.cjs"
    monkeypatch.setattr(cloud_marketplace, "_RUNNER_SCRIPT", script)
    for output, ok in [
        ("JSON.stringify({type:'result',result:{ok:true, data:'v'.repeat(300000)}})", True),
        ("'v'.repeat(17*1024*1024)", False),
    ]:
        script.write_text(f"process.stdin.once('data', () => console.log({output}));")
        outcome = asyncio.run(cloud_marketplace._run_subprocess("poshmark", USER_ID, "", {}, 10))
        assert outcome["result"]["ok"] is ok
        if ok:
            assert len(outcome["result"]["data"]) == 300000
        else:
            assert outcome["result"]["safe_to_retry"] is False
            assert outcome["result"]["error_code"] == "uncertain"


def test_cooperative_cancel_allows_cleanup(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    process = Mock(returncode=None)
    process.stdin.is_closing.return_value = False
    process.stdin.drain = AsyncMock()
    process.wait = AsyncMock(return_value=0)
    monkeypatch.setitem(cloud_marketplace._active_processes, (USER_ID, "operation"), process)
    assert asyncio.run(cloud_marketplace.cancel_local(USER_ID, "operation"))
    process.wait.assert_awaited_once()
    process.terminate.assert_not_called()


def test_decrypt_audit_records_operation_type(cloud, monkeypatch):  # noqa: F811
    from anteroom.database import SessionLocal

    _, _engine = cloud
    _patch_vault(monkeypatch)
    monkeypatch.setenv("ANTEROOM_CLOUD_MARKETPLACES", "poshmark")
    monkeypatch.setenv("ANTEROOM_PROCESS_ROLE", "worker")

    with use(for_seller(USER_ID)):
        with SessionLocal() as db:
            cloud_sessions.save_uploaded_session(db, "poshmark", _sample_cookies(), "Mozilla/5.0 TestChrome")
        with SessionLocal() as db:
            creds = cloud_sessions.read_credentials_fresh(db, "poshmark", "marketplace.like_events")
            assert creds is not None
            audit = db.execute(
                select(CloudSessionAudit)
                .where(CloudSessionAudit.event == "decrypt")
                .order_by(CloudSessionAudit.created_at.desc())
            ).scalars().first()
            assert audit is not None
            assert audit.detail == "marketplace.like_events"


