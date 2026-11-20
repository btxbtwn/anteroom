"""Focused regression tests for backend DB session lifecycle, locks, and worker concurrency."""
from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.models.conversation import Photo
from anteroom.repositories.queries import ConversationRepo, JobRepo
from anteroom.services import (
    automation_actions,
    background_worker,
    ebay_listings,
    etsy_listings,
    listing_persistence,
    native_marketplaces,
    photo_edits,
)
from anteroom.services.photo_store import LocalPhotoStore
from anteroom.services.service_context import ServiceContext, use


def _sample_jpeg(width: int = 240, height: int = 160, color: tuple[int, int, int] = (40, 90, 180)) -> bytes:
    img = Image.new("RGB", (width, height), (120, 120, 120))
    ImageDraw.Draw(img).rectangle((20, 20, width - 20, height - 20), fill=color)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _make_sqlite_sessions(tmp_path):
    load_models()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Fix 1: Release Postgres sessions before eBay/Etsy awaits & offload sync I/O
# ---------------------------------------------------------------------------


def test_ebay_execute_releases_db_session_before_await_and_after_callbacks(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    db = sessions()
    try:
        repo = ConversationRepo(db)
        conv = repo.create(title="Vintage jacket")
        data = _sample_jpeg()
        (tmp_path / "p1.jpg").write_bytes(data)
        photo = listing_persistence.add_photo(
            db, conv.id, "p1.jpg", "p1.jpg", "image/jpeg", len(data),
            checksum="1" * 64, width=240, height=160,
        )
        snapshot = {
            "title": "Vintage jacket",
            "_approved_photos": [{"id": photo.id, "stored_filename": photo.stored_filename, "checksum": photo.checksum}],
        }
        job = JobRepo(db).create(conv.id, "rev-1", snapshot, status="dispatched", current_step="native_ebay_list")
        native_marketplaces._store(db, conv.id, "ebay", {"state": "listing", "action": "list", "job_id": job.id})

        observations: list[tuple[str, bool]] = []

        async def fake_execute(step, snap, photos, binding, record, active, *, confirm=False, conversation_id=""):
            observations.append(("execute_enter", db.in_transaction()))
            record({"ebay_sku": "sku-abc"})
            observations.append(("after_record", db.in_transaction()))
            assert active() is True
            observations.append(("after_active", db.in_transaction()))
            return {"ok": False, "safe_to_retry": False, "error": "Reply lost"}

        async def fake_check(record, snap, remember):
            observations.append(("check_enter", db.in_transaction()))
            remember({"ebay_offer_id": "offer-42"})
            observations.append(("after_remember", db.in_transaction()))
            return {"ok": True, "state": "published", "listing_url": "https://www.ebay.com/itm/1234567890"}

        monkeypatch.setattr(ebay_listings, "execute", fake_execute)
        monkeypatch.setattr(ebay_listings, "check", fake_check)

        stored = asyncio.run(native_marketplaces._execute_step(db, job, conv.id, "ebay", "list", {}))
        observations.append(("after_step", db.in_transaction()))

        assert stored["state"] == "published"
        assert stored["ebay_sku"] == "sku-abc"
        assert stored["ebay_offer_id"] == "offer-42"
        assert observations == [
            ("execute_enter", False),
            ("after_record", False),
            ("after_active", False),
            ("check_enter", False),
            ("after_remember", False),
            ("after_step", False),
        ]
    finally:
        db.close()


def test_etsy_execute_releases_db_session_and_offloads_preflight_shop_read(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    db = sessions()
    try:
        repo = ConversationRepo(db)
        conv = repo.create(title="Ceramic mug")
        data = _sample_jpeg()
        (tmp_path / "m1.jpg").write_bytes(data)
        photo = listing_persistence.add_photo(
            db, conv.id, "m1.jpg", "m1.jpg", "image/jpeg", len(data),
            checksum="2" * 64, width=240, height=160,
        )
        snapshot = {
            "title": "Ceramic mug",
            "_approved_photos": [{"id": photo.id, "stored_filename": photo.stored_filename, "checksum": photo.checksum}],
        }
        job = JobRepo(db).create(conv.id, "rev-1", snapshot, status="dispatched", current_step="native_etsy_save_draft")
        native_marketplaces._store(db, conv.id, "etsy", {"state": "saving_draft", "action": "save_draft", "job_id": job.id})

        observations: list[tuple[str, bool]] = []

        async def fake_execute(step, snap, photos, binding, record, active, *, confirm=False, **options):
            observations.append(("execute_enter", db.in_transaction()))
            record({"etsy_shop_id": "999"})
            observations.append(("after_record", db.in_transaction()))
            assert active() is True
            observations.append(("after_active", db.in_transaction()))
            return {"ok": False, "safe_to_retry": False, "error": "Lost reply"}

        async def fake_check(snap, record, candidate):
            observations.append(("check_enter", db.in_transaction()))
            return {"ok": True, "state": "draft", "draft_url": "https://www.etsy.com/your/shops/me/listing-editor/edit/123456"}

        monkeypatch.setattr(etsy_listings, "execute", fake_execute)
        monkeypatch.setattr(etsy_listings, "check", fake_check)

        stored = asyncio.run(native_marketplaces._execute_step(db, job, conv.id, "etsy", "save_draft", {}))
        observations.append(("after_step", db.in_transaction()))

        assert stored["state"] == "draft"
        assert stored["etsy_shop_id"] == "999"
        assert observations == [
            ("execute_enter", False),
            ("after_record", False),
            ("after_active", False),
            ("check_enter", False),
            ("after_step", False),
        ]

        # Also verify _require_etsy runs connected_shop_id off the event-loop thread.
        loop_threads: list[int | None] = []
        call_threads: list[int | None] = []

        def fake_shop_id() -> str:
            call_threads.append(threading.current_thread().ident)
            return "999"

        monkeypatch.setattr(etsy_listings, "connected_shop_id", fake_shop_id)

        async def run_preflight():
            loop_threads.append(threading.current_thread().ident)
            await native_marketplaces._require_etsy()

        asyncio.run(run_preflight())
        assert call_threads and call_threads[0] != loop_threads[0]
    finally:
        db.close()


def test_ebay_and_etsy_offload_storage_and_settings_io_to_worker_threads(tmp_path, monkeypatch):
    store = LocalPhotoStore(str(tmp_path))
    data = _sample_jpeg()
    (tmp_path / "shot.jpg").write_bytes(data)

    read_threads: list[int | None] = []
    local_path_threads: list[int | None] = []
    settings_threads: list[int | None] = []
    shop_threads: list[int | None] = []

    orig_read = store.read
    orig_local_path = store.local_path

    def tracked_read(name: str) -> bytes:
        read_threads.append(threading.current_thread().ident)
        return orig_read(name)

    def tracked_local_path(name: str):
        local_path_threads.append(threading.current_thread().ident)
        return orig_local_path(name)

    monkeypatch.setattr(store, "read", tracked_read)
    monkeypatch.setattr(store, "local_path", tracked_local_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", store)

    photo = SimpleNamespace(
        id="p1", stored_filename="shot.jpg", original_filename="shot.jpg",
        mime_type="image/jpeg", checksum="",
    )

    async def run_all():
        loop_tid = threading.current_thread().ident

        # 1. eBay image upload & checksum offload photo_store().read
        fake_client = SimpleNamespace(
            request=AsyncMock(return_value={
                "imageUrl": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
                "fulfillmentPolicies": [{"fulfillmentPolicyId": "f1"}],
                "paymentPolicies": [{"paymentPolicyId": "p1"}],
                "returnPolicies": [{"returnPolicyId": "r1"}],
                "locations": [{"merchantLocationKey": "loc1", "merchantLocationStatus": "ENABLED"}],
            }),
        )
        urls = await ebay_listings._image_urls(fake_client, [photo], {}, lambda _v: None, lambda: True)
        assert urls == ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"]

        # 2. eBay listing_choices offloads get_ebay_listing_defaults
        def tracked_defaults():
            settings_threads.append(threading.current_thread().ident)
            return {}

        monkeypatch.setattr(ebay_listings, "get_ebay_listing_defaults", tracked_defaults)
        choices = await ebay_listings.listing_choices(fake_client, {})
        assert choices["merchantLocationKey"] == "loc1"

        # 3. Etsy _sync_photos offloads photo file read
        async def fake_write(_method, _path, _kind, **kwargs):
            return {"listing_image_id": 777}

        await etsy_listings._sync_photos(fake_write, "123", "456", {"images": []}, [photo], {}, lambda _v: None)

        # 4. Etsy check() offloads connected_shop_id
        def tracked_shop_id():
            shop_threads.append(threading.current_thread().ident)
            return "123"

        monkeypatch.setattr(etsy_listings, "connected_shop_id", tracked_shop_id)
        monkeypatch.setattr(etsy_listings, "find_draft", AsyncMock(return_value=None))
        checked = await etsy_listings.check({}, {"action": "save_draft"}, None)
        assert checked["safe_to_retry"] is True

        assert read_threads and all(tid != loop_tid for tid in read_threads)
        assert local_path_threads and all(tid != loop_tid for tid in local_path_threads)
        assert settings_threads and all(tid != loop_tid for tid in settings_threads)
        assert shop_threads and all(tid != loop_tid for tid in shop_threads)

    asyncio.run(run_all())


# ---------------------------------------------------------------------------
# Fix 2: Per-photo locks & no open DB transaction across Storage/Pillow I/O
# ---------------------------------------------------------------------------


def test_different_photos_edit_concurrently_without_holding_db_tx(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))

    setup_db = sessions()
    conv = ConversationRepo(setup_db).create(title="Sneakers")
    data = _sample_jpeg()
    (tmp_path / "a.jpg").write_bytes(data)
    (tmp_path / "b.jpg").write_bytes(data)
    photo_a = listing_persistence.add_photo(
        setup_db, conv.id, "a.jpg", "a.jpg", "image/jpeg", len(data), checksum="a" * 64, width=240, height=160,
    )
    photo_b = listing_persistence.add_photo(
        setup_db, conv.id, "b.jpg", "b.jpg", "image/jpeg", len(data), checksum="b" * 64, width=240, height=160,
    )
    conv_id, id_a, id_b = conv.id, photo_a.id, photo_b.id
    setup_db.close()

    barrier = threading.Barrier(2, timeout=5)
    thread_dbs: dict[int, object] = {}
    tx_states: list[bool] = []
    orig_write_edit = photo_edits.write_edit

    def barrier_write_edit(source_filename, mime_type, edits):
        db = thread_dbs[threading.get_ident()]
        tx_states.append(db.in_transaction())
        # Both threads must reach write_edit concurrently; a global lock would deadlock here.
        barrier.wait()
        return orig_write_edit(source_filename, mime_type, edits)

    monkeypatch.setattr(photo_edits, "write_edit", barrier_write_edit)

    errors: list[BaseException] = []

    def worker(photo_id: str):
        db = sessions()
        thread_dbs[threading.get_ident()] = db
        try:
            photo_edits.apply_photo_edit(
                ConversationRepo(db), conv_id, photo_id, lambda _cur: photo_edits.PhotoEdits(exposure=0.5),
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()

    t1 = threading.Thread(target=worker, args=(id_a,))
    t2 = threading.Thread(target=worker, args=(id_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    assert tx_states == [False, False]
    assert photo_edits._photo_locks == {}


def test_same_photo_rotations_serialize_and_clean_up_lock(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))

    setup_db = sessions()
    conv = ConversationRepo(setup_db).create(title="Coat")
    data = _sample_jpeg()
    (tmp_path / "front.jpg").write_bytes(data)
    photo = listing_persistence.add_photo(
        setup_db, conv.id, "front.jpg", "front.jpg", "image/jpeg", len(data), checksum="c" * 64, width=240, height=160,
    )
    conv_id, photo_id = conv.id, photo.id
    setup_db.close()

    errors: list[BaseException] = []

    def rotate_once():
        db = sessions()
        try:
            photo_edits.apply_photo_edit(
                ConversationRepo(db),
                conv_id,
                photo_id,
                lambda cur: None if (nxt := cur.turned(90)).is_original() else nxt,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=rotate_once) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert photo_edits._photo_locks == {}

    verify_db = sessions()
    try:
        refreshed = verify_db.get(Photo, photo_id)
        assert refreshed is not None
        assert (refreshed.edits or {}).get("rotate") == 180
    finally:
        verify_db.close()


def test_aborted_commit_cleans_up_uploaded_blob_and_returns_409(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    store = LocalPhotoStore(str(tmp_path))
    monkeypatch.setattr("anteroom.services.photo_store._store", store)

    db = sessions()
    conv = ConversationRepo(db).create(title="Boots")
    data = _sample_jpeg()
    (tmp_path / "orig.jpg").write_bytes(data)
    photo = listing_persistence.add_photo(
        db, conv.id, "orig.jpg", "orig.jpg", "image/jpeg", len(data), checksum="d" * 64, width=240, height=160,
    )
    conv_id, photo_id = conv.id, photo.id
    uploaded: list[str] = []
    orig_write_edit = photo_edits.write_edit

    def conflicting_write_edit(source_filename, mime_type, edits):
        meta = orig_write_edit(source_filename, mime_type, edits)
        uploaded.append(meta["stored_filename"])
        # Simulate another worker modifying the row while Storage/Pillow ran.
        other = sessions()
        try:
            row = other.get(Photo, photo_id)
            assert row is not None
            row.stored_filename = "other-worker-edit.jpg"
            other.commit()
        finally:
            other.close()
        return meta

    monkeypatch.setattr(photo_edits, "write_edit", conflicting_write_edit)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.put(f"/api/conversations/{conv_id}/photos/{photo_id}/edits", json={"exposure": 0.5})
        assert resp.status_code == 409
        assert uploaded and not (tmp_path / uploaded[0]).exists()
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_post_commit_delete_failure_still_returns_200_and_logs_warning(tmp_path, monkeypatch, caplog):
    sessions = _make_sqlite_sessions(tmp_path)
    store = LocalPhotoStore(str(tmp_path))
    monkeypatch.setattr("anteroom.services.photo_store._store", store)

    db = sessions()
    conv = ConversationRepo(db).create(title="Hat")
    data = _sample_jpeg()
    (tmp_path / "hat.jpg").write_bytes(data)
    photo = listing_persistence.add_photo(
        db, conv.id, "hat.jpg", "hat.jpg", "image/jpeg", len(data), checksum="e" * 64, width=240, height=160,
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        first = client.put(f"/api/conversations/{conv.id}/photos/{photo.id}/edits", json={"exposure": 0.4})
        assert first.status_code == 200

        delete_tx_states: list[bool] = []

        def failing_delete(_name: str) -> None:
            delete_tx_states.append(db.in_transaction())
            raise RuntimeError("transient storage 503")

        monkeypatch.setattr(store, "delete", failing_delete)
        with caplog.at_level(logging.WARNING, logger="anteroom.services.photo_edits"):
            second = client.put(f"/api/conversations/{conv.id}/photos/{photo.id}/edits", json={"exposure": 0.8})

        assert second.status_code == 200
        assert second.json()["edits"]["exposure"] == 0.8
        assert delete_tx_states == [False]
        assert any("Failed to delete superseded edited photo" in rec.message for rec in caplog.records)
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_preview_releases_db_transaction_before_rendering(tmp_path, monkeypatch):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))

    db = sessions()
    conv = ConversationRepo(db).create(title="Scarf")
    data = _sample_jpeg()
    (tmp_path / "scarf.jpg").write_bytes(data)
    photo = listing_persistence.add_photo(
        db, conv.id, "scarf.jpg", "scarf.jpg", "image/jpeg", len(data), checksum="f" * 64, width=240, height=160,
    )
    tx_states: list[bool] = []
    orig_preview = photo_edits.preview_jpeg

    def tracked_preview(source_filename, edits, *, crop):
        tx_states.append(db.in_transaction())
        return orig_preview(source_filename, edits, crop=crop)

    monkeypatch.setattr(photo_edits, "preview_jpeg", tracked_preview)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv.id}/photos/{photo.id}/edits/preview", json={"exposure": 0.2})
        assert resp.status_code == 200
        assert tx_states == [False]
    finally:
        app.dependency_overrides.clear()
        db.close()


# ---------------------------------------------------------------------------
# Fix 3: Cap background_worker.serve() concurrency & acquire before claim
# ---------------------------------------------------------------------------


def test_worker_concurrency_env_parsing_and_clamping(monkeypatch, caplog):
    monkeypatch.delenv("ANTEROOM_WORKER_CONCURRENCY", raising=False)
    assert background_worker._worker_concurrency() == background_worker.DEFAULT_WORKER_CONCURRENCY

    monkeypatch.setenv("ANTEROOM_WORKER_CONCURRENCY", "2")
    assert background_worker._worker_concurrency() == 2

    monkeypatch.setenv("ANTEROOM_WORKER_CONCURRENCY", "0")
    assert background_worker._worker_concurrency() == 1

    monkeypatch.setenv("ANTEROOM_WORKER_CONCURRENCY", "-7")
    assert background_worker._worker_concurrency() == 1

    monkeypatch.setenv("ANTEROOM_WORKER_CONCURRENCY", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="anteroom.services.background_worker"):
        assert background_worker._worker_concurrency() == background_worker.DEFAULT_WORKER_CONCURRENCY
    assert any("Invalid ANTEROOM_WORKER_CONCURRENCY" in rec.message for rec in caplog.records)


def test_serve_caps_concurrency_and_acquires_slot_before_claiming(monkeypatch):
    monkeypatch.setenv("ANTEROOM_WORKER_CONCURRENCY", "2")

    pending_jobs = [(f"task-{i}", f"seller-{i}", f"tok-{i}") for i in range(5)]
    claimed_ids: list[str] = []
    completed_ids: list[str] = []
    in_flight = 0
    max_in_flight = 0
    two_running = asyncio.Event()
    release_first_batch = asyncio.Event()
    all_done = asyncio.Event()

    def fake_claim(_engine):
        if pending_jobs:
            job = pending_jobs.pop(0)
            claimed_ids.append(job[0])
            return job
        return None

    async def fake_run_claimed(task_id, owner, token, *, heartbeat_loop):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            if len(claimed_ids) == 2 and not release_first_batch.is_set():
                two_running.set()
            if task_id in {"task-0", "task-1"}:
                await release_first_batch.wait()
                if task_id == "task-1":
                    # Failing task must still release its concurrency slot.
                    raise RuntimeError("boom")
            else:
                await asyncio.sleep(0.02)
            completed_ids.append(task_id)
        finally:
            in_flight -= 1
            if len(completed_ids) == 4:
                all_done.set()

    monkeypatch.setattr(background_worker.queue, "claim", fake_claim)
    monkeypatch.setattr(background_worker, "run_claimed", fake_run_claimed)

    async def run():
        stopping = threading.Event()
        worker = asyncio.create_task(background_worker.serve(None, stopping))
        try:
            await asyncio.wait_for(two_running.wait(), timeout=3)
            # Give the loop time to prove it does NOT claim task-2 while both slots are occupied.
            await asyncio.sleep(0.1)
            assert claimed_ids == ["task-0", "task-1"]
            assert max_in_flight == 2

            release_first_batch.set()
            await asyncio.wait_for(all_done.wait(), timeout=3)
            stopping.set()
            await asyncio.wait_for(worker, timeout=3)
        finally:
            stopping.set()
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    assert claimed_ids == ["task-0", "task-1", "task-2", "task-3", "task-4"]
    assert max_in_flight == 2
    assert set(completed_ids) == {"task-0", "task-2", "task-3", "task-4"}


# ---------------------------------------------------------------------------
# Fix 4: Scope automation_actions._turns by (loop, owner, marketplace)
# ---------------------------------------------------------------------------


def _fake_context(seller_id: str) -> ServiceContext:
    dummy = SimpleNamespace()
    return ServiceContext(
        seller_id=seller_id,
        session_factory=lambda: None,
        settings=dummy,  # type: ignore[arg-type]
        secrets=dummy,  # type: ignore[arg-type]
        photos=dummy,
    )


def test_turns_allow_different_sellers_concurrently_on_same_marketplace(monkeypatch):
    monkeypatch.setattr(automation_actions, "_turns", {})

    async def run():
        both_inside = asyncio.Event()
        entered = 0

        async def seller_task(seller_id: str):
            nonlocal entered
            with use(_fake_context(seller_id)):
                async with automation_actions._turn("ebay"):
                    entered += 1
                    if entered == 2:
                        both_inside.set()
                    await asyncio.wait_for(both_inside.wait(), timeout=2)

        await asyncio.gather(seller_task("seller-a"), seller_task("seller-b"))

    asyncio.run(run())
    assert automation_actions._turns == {}


def test_turns_serialize_same_seller_and_evict_after_completion_and_cancel(monkeypatch):
    monkeypatch.setattr(automation_actions, "_turns", {})

    async def run():
        active = 0
        max_active = 0
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def holder():
            nonlocal active, max_active
            with use(_fake_context("seller-same")):
                async with automation_actions._turn("ebay"):
                    active += 1
                    max_active = max(max_active, active)
                    first_entered.set()
                    await release_first.wait()
                    active -= 1

        async def waiter():
            nonlocal active, max_active
            with use(_fake_context("seller-same")):
                await first_entered.wait()
                async with automation_actions._turn("ebay"):
                    active += 1
                    max_active = max(max_active, active)
                    await asyncio.sleep(0.01)
                    active -= 1

        t_holder = asyncio.create_task(holder())
        t_waiter = asyncio.create_task(waiter())
        t_cancelled = asyncio.create_task(waiter())
        await first_entered.wait()
        await asyncio.sleep(0.02)
        t_cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_cancelled

        release_first.set()
        await asyncio.gather(t_holder, t_waiter)
        assert max_active == 1

    asyncio.run(run())
    assert automation_actions._turns == {}


def test_turns_work_across_distinct_event_loops(monkeypatch):
    monkeypatch.setattr(automation_actions, "_turns", {})

    async def acquire_once(seller_id: str):
        with use(_fake_context(seller_id)):
            async with automation_actions._turn("ebay"):
                await asyncio.sleep(0.01)

    # Sequential distinct loops.
    asyncio.run(acquire_once("seller-loop"))
    asyncio.run(acquire_once("seller-loop"))

    # Concurrent distinct loops in separate threads.
    errors: list[BaseException] = []

    def thread_loop():
        try:
            asyncio.run(acquire_once("seller-loop"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=thread_loop) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert automation_actions._turns == {}


@pytest.mark.parametrize("cancel_during_lookup", [False, True])
def test_etsy_publish_rechecks_activity_after_shop_lookup(monkeypatch, cancel_during_lookup):
    active = True
    lookups = 0
    records = []

    def shop():
        nonlocal active, lookups
        lookups += 1
        if lookups == 2 and cancel_during_lookup:
            active = False
        return "123"

    monkeypatch.setattr(etsy_listings, "connected_shop_id", shop)
    monkeypatch.setattr(etsy_listings, "_read", AsyncMock(return_value={"state": "draft"}))
    monkeypatch.setattr(etsy_listings, "_matches", lambda *_: True)
    monkeypatch.setattr(etsy_listings, "_video_kept", AsyncMock(return_value=True))
    api = AsyncMock(return_value={"listing_id": "123456", "shop_id": "123", "state": "active"})
    monkeypatch.setattr(etsy_listings.marketplace_accounts, "etsy_api", api)
    result = asyncio.run(etsy_listings.execute(
        "publish", {}, [], {"draft_url": etsy_listings.edit_url("123456")},
        records.append, lambda: active, confirm=True,
    ))
    assert lookups == 2
    assert result["ok"] is not cancel_during_lookup
    if cancel_during_lookup:
        api.assert_not_awaited()
        assert {"etsy_pending_write": "publish"} not in records
    else:
        api.assert_awaited_once()


@pytest.mark.parametrize("failure_stage", ["commit", "refresh", None])
def test_photo_edit_commit_cleanup_and_response_snapshot(tmp_path, monkeypatch, failure_stage):
    sessions = _make_sqlite_sessions(tmp_path)
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    with sessions() as db:
        conv = ConversationRepo(db).create(title="Hat")
        data = _sample_jpeg()
        (tmp_path / "original.jpg").write_bytes(data)
        photo = listing_persistence.add_photo(
            db, conv.id, "original.jpg", "original.jpg", "image/jpeg", len(data),
            checksum="e" * 64, width=240, height=160,
        )
        conv_id, photo_id = conv.id, photo.id
        uploaded = []
        original_write = photo_edits.write_edit

        def write(*args):
            meta = original_write(*args)
            uploaded.append(meta["stored_filename"])
            return meta

        monkeypatch.setattr(photo_edits, "write_edit", write)

        def fail(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        if failure_stage:
            monkeypatch.setattr(db, failure_stage, fail)
            with pytest.raises(RuntimeError, match="database unavailable"):
                photo_edits.apply_photo_edit(
                    ConversationRepo(db), conv_id, photo_id, lambda _: photo_edits.PhotoEdits(exposure=.5),
                )
            db.rollback()
            with sessions() as verify:
                saved = verify.get(Photo, photo_id)
                assert saved is not None
                assert saved.stored_filename == (uploaded[0] if failure_stage == "refresh" else "original.jpg")
            assert (tmp_path / uploaded[0]).exists() is (failure_stage == "refresh")
        else:
            saved = photo_edits.apply_photo_edit(
                ConversationRepo(db), conv_id, photo_id, lambda _: photo_edits.PhotoEdits(exposure=.5),
            )
            assert not db.in_transaction()
            with sessions() as other:
                row = other.get(Photo, photo_id)
                assert row is not None
                other.delete(row)
                other.commit()
            from anteroom.routes.conversations import _photo_response
            response = _photo_response(saved)
            assert response.edits == photo_edits.PhotoEdits(exposure=.5)
            assert saved.stored_filename == uploaded[0]
            assert not db.in_transaction()
