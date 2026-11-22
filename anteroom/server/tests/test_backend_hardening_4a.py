"""Focused regression tests for Batch 4a defensive backend hardening."""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anteroom.hosted import ResponseHeadersMiddleware, _build_csp
from anteroom.models.assistant import AssistantMessage, AssistantThread
from anteroom.models.conversation import Conversation
from anteroom.routes import (
    assistant as assistant_route,
    hosted_account_import,
    hosted_browser_relay,
    hosted_workspace,
    mac_inference,
    measurements,
    scout as scout_route,
)
from anteroom.services import account_deletion, account_import, hosted_photos, redaction, safe_fetch
from anteroom.services.chat_attachments import MAX_ATTACHMENTS
from anteroom.services.hosted_limits import (
    UploadSlots,
    bounded_route_class,
    read_bounded_upload,
    stream_upload_to_path,
)
from anteroom.services.service_context import use


# ---------------------------------------------------------------------------
# 1. Streaming body caps & UploadSlots guards
# ---------------------------------------------------------------------------


def test_stream_upload_to_path_rejects_oversized_chunks_without_trusting_size(tmp_path):
    """Chunked streaming raises 413 immediately even when UploadFile.size is None or spoofed small."""
    dest = tmp_path / "photo.jpg"
    upload = UploadFile(file=io.BytesIO(b"x" * 2048), filename="big.jpg")
    # Spoofed/missing size header must not bypass the streaming cap.
    upload.size = 100  # type: ignore[misc]

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stream_upload_to_path(upload, dest, 1024, too_large_detail="Too large", chunk_size=256))
    assert exc_info.value.status_code == 413
    assert not dest.exists()


def test_read_bounded_upload_rejects_oversized_stream_without_trusting_size():
    upload = UploadFile(file=io.BytesIO(b"y" * 2048), filename="big.jpg")
    upload.size = None  # type: ignore[misc]

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_bounded_upload(upload, 1024, too_large_detail="Too large", chunk_size=256))
    assert exc_info.value.status_code == 413


def test_bounded_route_class_enforces_streaming_413_without_content_length():
    app = FastAPI()
    from fastapi import APIRouter

    limited = APIRouter(route_class=bounded_route_class(64, too_large_detail="Payload too big"))

    @limited.post("/echo")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    app.include_router(limited)
    client = TestClient(app)

    ok = client.post("/echo", content=b"a" * 32)
    assert ok.status_code == 200 and ok.json() == {"size": 32}

    # Streaming chunked generator omits Content-Length, exercising limited_receive().
    def chunks():
        yield b"a" * 40
        yield b"b" * 40

    too_big = client.post("/echo", content=chunks())
    assert too_big.status_code == 413
    assert too_big.json()["detail"] == "Payload too big"


def test_measurements_streaming_size_cap_and_upload_slots(monkeypatch):
    app = FastAPI()
    app.include_router(measurements.hosted_router)
    app.state.upload_slots = UploadSlots(total=1, wait=0.05)

    fake_provider = SimpleNamespace(vision_chat=lambda *_args, **_kwargs: "{}")
    monkeypatch.setattr(measurements, "get_photo_provider", lambda: fake_provider)
    monkeypatch.setattr(measurements, "MAX_PHOTO_SIZE_MB", 1)

    client = TestClient(app)
    oversized = b"0" * (1024 * 1024 + 512)
    resp = client.post(
        "/api/measurements/read",
        data={"garment": "top"},
        files={"files": ("big.jpg", oversized, "image/jpeg")},
    )
    assert resp.status_code == 413

    resp_tag = client.post(
        "/api/size-tags/read",
        files={"files": ("big.jpg", oversized, "image/jpeg")},
    )
    assert resp_tag.status_code == 413


def test_measurements_hold_upload_slot_rejects_concurrent_seller_upload(monkeypatch):
    slots = UploadSlots(total=2, wait=0.05)

    app = FastAPI()
    app.include_router(measurements.hosted_router)
    app.state.upload_slots = slots
    ctx = SimpleNamespace(seller_id="seller-1")

    async def exercise():
        from starlette.requests import Request
        route = next(r for r in measurements._hosted_upload_router.routes if getattr(r, "path", "") == "/api/size-tags/read")
        async def receive():
            pytest.fail("A rejected request must not receive or parse its body")
        request = Request({"type": "http", "method": "POST", "path": "/api/size-tags/read",
                           "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
                           "app": app, "query_string": b""}, receive)
        with use(ctx):  # type: ignore[arg-type]
            async with slots.hold("seller-1"):
                with pytest.raises(HTTPException) as exc_info:
                    await route.get_route_handler()(request)
                assert exc_info.value.status_code == 429

    asyncio.run(exercise())


def test_measurements_releases_upload_slot_before_llm(monkeypatch):
    app = FastAPI()
    app.include_router(measurements.hosted_router)
    slots = UploadSlots(total=1, wait=0.05)
    app.state.upload_slots = slots

    fake_provider = SimpleNamespace(vision_chat=lambda *_args, **_kwargs: "{}")
    monkeypatch.setattr(measurements, "get_photo_provider", lambda: fake_provider)

    llm_started = asyncio.Event()
    release_llm = asyncio.Event()

    async def slow_read_size_tag(_provider, _paths):
        llm_started.set()
        await release_llm.wait()
        return {"photos_read": 1, "status": "accepted", "size": "M", "sizes": ["M"]}

    monkeypatch.setattr("anteroom.services.size_tag_read.read_size_tag", slow_read_size_tag)

    async def exercise():
        ctx = SimpleNamespace(seller_id="seller-llm")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with use(ctx):  # type: ignore[arg-type]
                first = asyncio.create_task(
                    client.post(
                        "/api/size-tags/read",
                        files={"files": ("a.jpg", b"abc", "image/jpeg")},
                    )
                )
                await asyncio.wait_for(llm_started.wait(), timeout=2.0)
                # Upload slot must already be released while vision LLM inference runs.
                async with slots.hold("seller-llm"):
                    pass
                release_llm.set()
                resp = await first
                assert resp.status_code == 200

    asyncio.run(exercise())


def test_assistant_messages_releases_upload_slot_before_sse_streaming(monkeypatch):
    from fastapi.responses import StreamingResponse

    app = FastAPI()
    app.include_router(assistant_route.router)
    slots = UploadSlots(total=1, wait=0.05)
    app.state.upload_slots = slots

    slot_free_during_stream = False

    def fake_queue_question(_db, _request, _question, _body):
        async def sse():
            nonlocal slot_free_during_stream
            async with slots.hold("seller-sse"):
                slot_free_during_stream = True
            yield "data: hello\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    async def fake_in_worker(fn, *args):
        return fn(MagicMock(), *args)

    monkeypatch.setattr(assistant_route, "_queued", lambda: True)
    monkeypatch.setattr(assistant_route, "in_worker_session", fake_in_worker)
    monkeypatch.setattr(assistant_route, "_queue_question", fake_queue_question)

    async def exercise():
        ctx = SimpleNamespace(seller_id="seller-sse")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with use(ctx):  # type: ignore[arg-type]
                resp = await client.post("/api/assistant/messages", json={"text": "hello"})
                assert resp.status_code == 200
                assert "data: hello" in resp.text
                assert slot_free_during_stream is True

    asyncio.run(exercise())


def test_assistant_attachments_schema_and_body_size_bounds(monkeypatch):
    # More than MAX_ATTACHMENTS attachments must fail Pydantic validation.
    with pytest.raises(ValidationError):
        assistant_route.AssistantQuestion(
            text="check these",
            attachments=[
                {"name": f"img-{i}.jpg", "media_type": "image/jpeg", "data_base64": "AAAA"}
                for i in range(MAX_ATTACHMENTS + 1)
            ],
        )

    app = FastAPI()
    app.include_router(assistant_route.router)
    monkeypatch.setattr(assistant_route, "MAX_ASSISTANT_MESSAGE_BYTES", 256)
    client = TestClient(app)

    resp = client.post("/api/assistant/messages", json={"text": "x" * 512})
    assert resp.status_code == 413


def test_assistant_remove_thread_logs_warning_on_post_commit_cleanup_error(monkeypatch, caplog, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from anteroom.database import Base, load_models

    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            thread = AssistantThread(title="Cleanup test")
            db.add(thread)
            db.flush()
            db.add(AssistantMessage(
                thread_id=thread.id,
                role="user",
                text="with image",
                attachments=[{"filename": "abc.jpg"}],
            ))
            db.commit()
            thread_id = thread.id

        monkeypatch.setattr(
            "anteroom.services.chat_attachments.delete_attachments",
            lambda _images: (_ for _ in ()).throw(RuntimeError("storage boom")),
        )
        with Session(engine) as db, caplog.at_level(logging.WARNING, logger="anteroom.assistant"):
            result = assistant_route.remove_thread(thread_id, db=db)
        assert result == {"ok": True}
        assert any("Post-commit attachment cleanup failed" in r.getMessage() for r in caplog.records)
        with Session(engine) as db:
            assert db.get(AssistantThread, thread_id) is None
    finally:
        engine.dispose()


def test_assistant_proposal_apply_and_automation_confirm_prevent_double_execution(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from anteroom.database import Base, load_models

    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'proposals.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            conv = Conversation(title="Jacket", status="draft")
            thread = AssistantThread(title="Proposals")
            db.add_all([conv, thread])
            db.flush()
            change_msg = AssistantMessage(
                thread_id=thread.id,
                role="assistant",
                text="Update price",
                changes={
                    "status": "proposed",
                    "items": [{"conversation_id": conv.id, "fields": {"price": 50}}],
                },
            )
            auto_msg = AssistantMessage(
                thread_id=thread.id,
                role="assistant",
                text="Create rule",
                automation={"status": "proposed", "name": "Drop prices", "actions": []},
            )
            db.add_all([change_msg, auto_msg])
            db.commit()
            change_id, auto_id = change_msg.id, auto_msg.id

        applied_calls = []

        def fake_apply(item, _db):
            # Inside the first apply, the row must already be claimed as "applying",
            # so a concurrent second call sees 409 and never runs _apply_to_listing twice.
            with Session(engine) as concurrent_db:
                with pytest.raises(HTTPException) as exc_info:
                    assistant_route.apply_changes(change_id, db=concurrent_db)
                assert exc_info.value.status_code == 409
            applied_calls.append(item)
            return None

        monkeypatch.setattr(assistant_route, "_apply_to_listing", fake_apply)
        with Session(engine) as db:
            result = assistant_route.apply_changes(change_id, db=db)
        assert len(applied_calls) == 1
        assert result["changes"]["status"] == "applied"
        with Session(engine) as db:
            saved = db.get(AssistantMessage, change_id)
            assert saved is not None and saved.changes["status"] == "applied"

        # Now verify automation confirmation rejects an already-in-flight "confirming" state.
        with Session(engine) as db:
            msg = db.get(AssistantMessage, auto_id)
            assert msg is not None
            msg.automation = {**msg.automation, "status": "confirming"}
            db.commit()
        with Session(engine) as db:
            with pytest.raises(HTTPException) as exc_info:
                assistant_route.confirm_automation_message(auto_id, db=db)
            assert exc_info.value.status_code == 409
    finally:
        engine.dispose()


def test_scout_streaming_cap_and_db_release_before_photo_store(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(scout_route.router)
    monkeypatch.setattr(scout_route, "MAX_PHOTO_SIZE_MB", 1)

    client = TestClient(app)
    oversized = b"z" * (1024 * 1024 + 256)
    resp = client.post(
        "/api/scout",
        files={"files": ("huge.jpg", oversized, "image/jpeg")},
    )
    assert resp.status_code == 413

    # Verify serve_photo calls db.rollback() BEFORE photo_store().local_path(...)
    events: list[str] = []
    photo_file = tmp_path / "scout.jpg"
    photo_file.write_bytes(b"\xff\xd8\xff\xd9")

    fake_check = SimpleNamespace(photos=["scout-photo.jpg"])
    fake_db = MagicMock()
    fake_db.get.return_value = fake_check
    fake_db.rollback.side_effect = lambda: events.append("db.rollback")

    class _Store:
        def local_path(self, key: str) -> Path:
            events.append(f"photo_store:{key}")
            return photo_file

    monkeypatch.setattr(scout_route, "photo_store", lambda: _Store())
    response = scout_route.serve_photo("check-1", 0, db=fake_db)
    assert response.path == photo_file
    assert events == ["db.rollback", "photo_store:scout-photo.jpg"]


def test_hosted_account_import_body_caps_and_upload_slots(monkeypatch):
    app = FastAPI()
    app.include_router(hosted_account_import.router)
    app.state.upload_slots = UploadSlots(total=1, wait=0.05)

    monkeypatch.setattr(hosted_account_import, "MAX_ROWS_BYTES", 128)
    monkeypatch.setattr(hosted_account_import, "MAX_CONFIG_BYTES", 128)
    monkeypatch.setattr(hosted_photos, "MAX_BYTES", 128)

    client = TestClient(app)
    assert client.post("/api/account-import/rows", json={"table": "conversations", "rows": [{"id": "a" * 200}]}).status_code == 413
    assert client.put("/api/account-import/settings", json={"ships_from_zip": "1" * 200}).status_code == 413
    assert client.put("/api/account-import/secrets", json={"ai-api-keys": "k" * 200}).status_code == 413
    assert client.post("/api/account-import/photos/p1/stored", content=b"x" * 256).status_code == 413


def test_hosted_account_import_allows_four_concurrent_same_seller_photo_uploads(monkeypatch):
    import threading

    app = FastAPI()
    # Even with total=1, releasing the upload slot BEFORE slow storage_call allows
    # all 4 concurrent same-seller account_move photo uploads to overlap in Storage I/O
    # without 429 or same-seller serialization.
    app.state.upload_slots = UploadSlots(total=1, wait=1.0)

    @app.middleware("http")
    async def attach_seller(request: Request, call_next):
        request.state.seller = SimpleNamespace(user_id="seller-migrate")
        return await call_next(request)

    app.include_router(hosted_account_import.router)

    async def fake_in_seller_session(_request, fn, *args):
        if fn is account_import.photo_to_import:
            return f"draft-{args[0]}"
        return True

    barrier = threading.Barrier(4, timeout=5.0)
    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()

    def slow_storage_call(method, _key, _token, _image=None):
        nonlocal in_flight, max_in_flight
        assert method == "POST"
        assert threading.current_thread().name.startswith("import-storage")
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            # All 4 same-seller uploads MUST reach storage_call concurrently.
            barrier.wait()
        finally:
            with lock:
                in_flight -= 1

    monkeypatch.setattr(hosted_account_import, "in_seller_session", fake_in_seller_session)
    monkeypatch.setattr(hosted_photos, "normalize", lambda raw: (raw, 100, 100))
    monkeypatch.setattr(hosted_photos, "new_key", lambda user_id, draft_id: f"{user_id}/{draft_id}.jpg")
    monkeypatch.setattr(hosted_photos, "storage_call", slow_storage_call)

    async def exercise():
        ctx = SimpleNamespace(seller_id="seller-migrate")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with use(ctx):  # type: ignore[arg-type]
                responses = await asyncio.gather(*[
                    client.post(
                        f"/api/account-import/photos/p{i}/stored",
                        content=b"fake-jpg",
                        headers={"Authorization": "Bearer test-jwt"},
                    )
                    for i in range(4)
                ])
                assert [r.status_code for r in responses] == [200, 200, 200, 200]
                assert all(r.json() == {"copied": True} for r in responses)
                assert max_in_flight == 4

    asyncio.run(exercise())


def test_hosted_account_import_photo_slots_do_not_starve_other_sellers(monkeypatch):
    app = FastAPI()
    app.state.upload_slots = UploadSlots(total=2, wait=0.15)

    @app.middleware("http")
    async def attach_seller(request: Request, call_next):
        seller_id = request.headers["x-seller"]
        request.state.seller = SimpleNamespace(user_id=seller_id)
        with use(SimpleNamespace(seller_id=seller_id)):  # type: ignore[arg-type]
            return await call_next(request)

    app.include_router(hosted_account_import.router)

    alice_entered = asyncio.Event()
    release_alice = asyncio.Event()

    async def gated_in_seller_session(request, fn, *args):
        if fn is account_import.photo_to_import:
            if request.state.seller.user_id == "seller-alice":
                alice_entered.set()
                await release_alice.wait()
            return f"draft-{args[0]}"
        return True

    monkeypatch.setattr(hosted_account_import, "in_seller_session", gated_in_seller_session)
    monkeypatch.setattr(hosted_photos, "normalize", lambda raw: (raw, 100, 100))
    monkeypatch.setattr(hosted_photos, "new_key", lambda user_id, draft_id: f"{user_id}/{draft_id}.jpg")
    monkeypatch.setattr(hosted_photos, "storage_call", lambda *_a, **_k: None)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Alice starts two concurrent photo imports; per_seller=True keeps her to 1 global slot.
            alice_one = asyncio.create_task(
                client.post(
                    "/api/account-import/photos/a1/stored",
                    content=b"fake-jpg",
                    headers={"Authorization": "Bearer test-jwt", "x-seller": "seller-alice"},
                )
            )
            await asyncio.wait_for(alice_entered.wait(), timeout=2.0)
            alice_two = asyncio.create_task(
                client.post(
                    "/api/account-import/photos/a2/stored",
                    content=b"fake-jpg",
                    headers={"Authorization": "Bearer test-jwt", "x-seller": "seller-alice"},
                )
            )
            await asyncio.sleep(0.02)
            # Bob must still get the second global slot immediately instead of 503.
            bob = await client.post(
                "/api/account-import/photos/b1/stored",
                content=b"fake-jpg",
                headers={"Authorization": "Bearer test-jwt", "x-seller": "seller-bob"},
            )
            assert bob.status_code == 200
            assert bob.json() == {"copied": True}

            release_alice.set()
            r1, r2 = await asyncio.gather(alice_one, alice_two)
            assert r1.status_code == 200
            assert r2.status_code == 200

    asyncio.run(exercise())


def test_relay_inference_and_workspace_body_caps(monkeypatch):
    app = FastAPI()
    app.include_router(hosted_browser_relay.router)
    app.include_router(mac_inference.router)
    app.include_router(hosted_workspace.router)

    monkeypatch.setattr(hosted_browser_relay, "MAX_RELAY_BODY_BYTES", 256)
    monkeypatch.setattr(mac_inference, "MAX_INFERENCE_BODY_BYTES", 256)
    monkeypatch.setattr(hosted_workspace, "MAX_WORKSPACE_BODY_BYTES", 256)

    client = TestClient(app)
    req_id = "a" * 32
    assert client.post(
        "/api/browser-relay/replies",
        json={"request_id": req_id, "payload": {"blob": "x" * 512}},
    ).status_code == 413
    assert client.post(
        "/api/mac-ai/reply",
        json={"worker": req_id, "identity": req_id, "result": {"blob": "x" * 512}},
    ).status_code == 413
    assert client.put(
        "/api/settings/ui",
        json={"forget_label": "x" * 512},
    ).status_code == 413

    with pytest.raises(ValidationError):
        hosted_workspace.UiInput(restore_labels=["lbl"] * 501)
    with pytest.raises(ValidationError):
        hosted_workspace.UiInput(model_favorites=[{"id": str(i)} for i in range(501)])

    # remember_labels accepts comma-separated strings up to 2000 chars and lists up to 200 items.
    csv_500 = ",".join(f"label{i:03d}" for i in range(55))
    assert len(csv_500) > 200
    assert hosted_workspace.UiInput(remember_labels=csv_500).remember_labels == csv_500
    assert len(hosted_workspace.UiInput(remember_labels=["lbl"] * 200).remember_labels or []) == 200
    with pytest.raises(ValidationError):
        hosted_workspace.UiInput(remember_labels="x" * 2001)
    with pytest.raises(ValidationError):
        hosted_workspace.UiInput(remember_labels=["lbl"] * 201)


# ---------------------------------------------------------------------------
# 2. Hosted security & SSRF / CSP / redaction / settings-import hardening
# ---------------------------------------------------------------------------


def test_safe_fetch_overall_wall_clock_timeout_stops_slow_drip_body(monkeypatch):
    async def fake_resolve(_url: str, *, allow_http: bool = True):
        return "https://cdn.example.com/slow.jpg", ["93.184.216.34"]

    class SlowDripStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(0.05)
                yield b"chunk"

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, headers={"content-type": "image/jpeg"}, stream=SlowDripStream())
    )
    monkeypatch.setattr(safe_fetch, "resolve_fetch_target", fake_resolve)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(TimeoutError):
                await safe_fetch.fetch_public(client, "https://cdn.example.com/slow.jpg", timeout=0.12)

    asyncio.run(run())


def test_hosted_csp_pins_supabase_origin_and_falls_back_when_unset():
    pinned = _build_csp("https://my-proj.supabase.co/some/path")
    assert "connect-src 'self' https://my-proj.supabase.co wss://my-proj.supabase.co;" in pinned
    assert "media-src 'self' https://my-proj.supabase.co;" in pinned
    assert "img-src 'self' data: blob: https://my-proj.supabase.co;" in pinned
    assert "*.supabase.co" not in pinned
    # Retained for pywebview WKWebView API bootstrap.
    assert "script-src 'self' 'unsafe-eval';" in pinned

    fallback = _build_csp("")
    assert "connect-src 'self' https://*.supabase.co wss://*.supabase.co;" in fallback
    assert "media-src 'self';" in fallback
    assert "img-src 'self' data: blob:;" in fallback


def test_hosted_csp_middleware_uses_env_fallback_when_supabase_url_omitted(monkeypatch):
    base = FastAPI()

    @base.get("/ping")
    def ping():
        return {"ok": True}

    base.add_middleware(ResponseHeadersMiddleware)
    client = TestClient(base)

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    policy_unset = client.get("/ping").headers["content-security-policy"]
    assert "connect-src 'self' https://*.supabase.co wss://*.supabase.co;" in policy_unset

    monkeypatch.setenv("SUPABASE_URL", "https://tenant-xyz.supabase.co")
    policy_pinned = client.get("/ping").headers["content-security-policy"]
    assert "connect-src 'self' https://tenant-xyz.supabase.co wss://tenant-xyz.supabase.co;" in policy_pinned
    assert "img-src 'self' data: blob: https://tenant-xyz.supabase.co;" in policy_pinned
    assert "*.supabase.co" not in policy_pinned


def test_redaction_masks_postgres_dsns_and_sensitive_headers():
    dsn = "failed connecting to postgresql+psycopg://admin:SuperSecretPass99@db.internal:5432/anteroom"
    redacted_dsn = redaction.redact(dsn)
    assert "SuperSecretPass99" not in redacted_dsn
    assert redacted_dsn == "failed connecting to postgresql+psycopg://admin:…(masked)@db.internal:5432/anteroom"
    assert redaction.redact(redacted_dsn) == redacted_dsn

    short_dsn = "postgres://u:pw@localhost/db"
    redacted_short = redaction.redact(short_dsn)
    assert "u:pw@" not in redacted_short
    assert "postgres://u:…(masked)@localhost/db" == redacted_short

    comma_dsn = "postgres://alice:first,second@db.example/app"
    redacted_comma = redaction.redact(comma_dsn)
    assert "first,second" not in redacted_comma
    assert redacted_comma == "postgres://alice:…(masked)@db.example/app"
    assert redaction.redact("(postgres://alice:pa)ss@db.example/app)") == "(postgres://alice:…(masked)@db.example/app)"
    adjacent = "postgres://alice:first@db1,postgres://bob:second@db2"
    redacted_adjacent = redaction.redact(adjacent)
    assert "first" not in redacted_adjacent and "second" not in redacted_adjacent
    assert redacted_adjacent == "postgres://alice:…(masked)@db1,postgres://bob:…(masked)@db2"

    raw_headers = (
        "Authorization: Basic dXNlcjpzZWNyZXQ=\n"
        "Cookie: session_id=abc123; csrf=xyz789\n"
        "Set-Cookie: sid=topsecretvalue; Path=/; HttpOnly\n"
        "X-API-Key: my-header-secret-key\n"
        "Authorization: Bearer tok_secret_12345"
    )
    masked_headers = redaction.redact(raw_headers)
    for leaked in ("dXNlcjpzZWNyZXQ=", "abc123", "xyz789", "topsecretvalue", "my-header-secret-key", "tok_secret_12345"):
        assert leaked not in masked_headers
    assert "Authorization: Bearer …(masked)" in masked_headers
    assert redaction.redact(masked_headers) == masked_headers

    # Multi-line empty header must never swallow the next line's header or quoted token.
    assert "Host: example.com" in redaction.redact("Authorization:\nHost: example.com")
    assert '"next-line-value"' in redaction.redact('Authorization:\n"next-line-value"')

    dict_repr = "{'Authorization': 'Basic dXNlcjpwYXNz', 'Cookie': 'a=1; b=2', 'x-api-key': 'secret-key-99', 'Host': 'api.example'}"
    masked_dict = redaction.redact(dict_repr)
    for leaked in ("dXNlcjpwYXNz", "a=1; b=2", "secret-key-99"):
        assert leaked not in masked_dict
    assert "'Host': 'api.example'" in masked_dict


def test_account_import_settings_allowlist_drops_unknown_and_mac_only_keys(monkeypatch):
    stored: dict = {"ships_from_zip": "90210"}

    def fake_update(mutator):
        mutator(stored)
        return stored

    monkeypatch.setattr("anteroom.services.user_settings.update_settings", fake_update)

    added = account_import.import_settings({
        "ships_from_zip": "10001",        # already exists -> account wins
        "business_note": "Vintage shop",  # allowlisted -> imported
        "ui": {"theme": "dark"},          # allowlisted -> imported
        "backup_folder": "/Users/me/bak", # Mac-only -> dropped
        "__proto__": {"admin": True},     # unknown -> dropped
        "arbitrary_injected_key": "boom", # unknown -> dropped
    })

    assert set(added) == {"business_note", "ui"}
    assert stored == {
        "ships_from_zip": "90210",
        "business_note": "Vintage shop",
        "ui": {"theme": "dark"},
    }


def test_account_deletion_unit_ordering_no_external_calls_when_delete_refuses(monkeypatch):
    """Non-Postgres unit verification of account_deletion.delete_account ordering."""
    called: list[str] = []

    monkeypatch.setattr(account_deletion, "_read_relay_device", lambda: {"deviceId": "d", "token": "t"})

    def refuse(_owner: str) -> list[str]:
        called.append("delete_records")
        raise account_deletion.AccountDeletionRefused(409, "Active job. Nothing was deleted.")

    monkeypatch.setattr(account_deletion, "_delete_records", refuse)
    monkeypatch.setattr(
        "anteroom.services.hosted_photos.remove_objects",
        lambda *_args: called.append("remove_objects"),
    )

    async def fake_forget(*_args, **_kwargs):
        called.append("forget_relay")

    monkeypatch.setattr(account_deletion, "_forget_relay_device", fake_forget)

    with pytest.raises(account_deletion.AccountDeletionRefused) as exc_info:
        asyncio.run(account_deletion.delete_account("owner-1", "jwt-token"))
    assert exc_info.value.status == 409
    assert called == ["delete_records"]
