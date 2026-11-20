"""Async services and routes keep blocking database work off the event loop (see ``loop_checkouts``)."""
from loop_checkouts import loop_session_functions

# Async functions that still open or use a blocking session on the event loop.
# Each one moves its database steps into ``database.in_worker_session`` and
# leaves this list. Never add to it: the list only shrinks.
STILL_ON_THE_LOOP = {
    "services/automation_scheduler.py:_loop",
    "services/automation_scheduler.py:tick",
    "services/extension_connection.py:_reconnect_work_after_settle",
    "services/extension_connection.py:extension_websocket",
    "services/hosted_automation_scheduler.py:run_owner",
    "services/hosted_promotion_scheduler.py:check_seller",
    "services/listing_promotions.py:review",
    "services/marketplace_accounts.py:autodelist_for_sales",
    "services/promotion_automations.py:_timer",
    "services/promotion_automations.py:check_new_likes",
    "services/promotion_automations.py:run_round",
    "services/promotion_automations.py:tick",
    "services/vendoo_drafts.py:_ops",
    "services/vendoo_drafts.py:run",
}


def test_no_new_async_function_uses_a_session_on_the_event_loop():
    new = sorted(loop_session_functions() - STILL_ON_THE_LOOP)
    assert not new, (
        "These async functions open or use a blocking database session on the event loop. Run each "
        f"database step through database.in_worker_session (a nested def or a plain def helper): {new}")


def test_the_list_of_remaining_cases_only_shrinks():
    fixed = sorted(STILL_ON_THE_LOOP - loop_session_functions())
    assert not fixed, f"These no longer use a session on the event loop; remove them from STILL_ON_THE_LOOP: {fixed}"


def test_the_check_finds_a_session_on_the_loop(tmp_path, monkeypatch):
    import loop_checkouts

    folder = tmp_path / "services"
    folder.mkdir()
    (folder / "example.py").write_text(
        "async def opens():\n    with SessionLocal() as db:\n        db.commit()\n\n"
        "async def uses(db, conv_id):\n    await thing()\n    db.commit()\n\n"
        "async def factory(session_factory):\n    with session_factory() as db:\n        db.commit()\n\n"
        "async def outer():\n    async def inner(db):\n        db.commit()\n    return inner\n\n"
        "async def released(conv_id):\n    def step(db):\n        db.commit()\n"
        "    await in_worker_session(step)\n    await in_worker_session(lambda db: db.commit())\n")
    monkeypatch.setattr(loop_checkouts, "PACKAGE", tmp_path)
    assert loop_checkouts.loop_session_functions(("services",)) == {
        "services/example.py:opens", "services/example.py:uses", "services/example.py:factory",
        "services/example.py:inner"}


def test_saving_a_generated_listing_and_a_chat_reply_queries_off_the_event_loop(monkeypatch):
    """On the Mac a commit can wait 30 s for SQLite's write lock; on the loop that froze every request."""
    import asyncio
    import json

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from anteroom.database import Base, load_models
    from anteroom.repositories.queries import ConversationRepo
    from anteroom.services import listing_generate
    from anteroom.services.chat_listing import persist_chat_result
    from worker_sessions import memory_engine, worker_sessions

    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conv_id = ConversationRepo(db).create(title="Tee").id
    on_the_loop = []

    def checkout(*_args):
        on_the_loop.append(asyncio._get_running_loop() is not None)

    monkeypatch.setattr(listing_generate, "_validation_blockers", lambda listing: [])
    event.listen(engine, "checkout", checkout)

    async def generate_then_chat():
        listing = await listing_generate.persist_generated_listing_with_repair(
            conv_id, "```json\n" + json.dumps({"title": "Blue tee", "price": 20}) + "\n```", object())
        await persist_chat_result(conv_id, "It is a blue tee.", object(), provider_name="test", provider_model="test",
                                  stream_error="", user_message="What color is it?")
        return listing

    try:
        with worker_sessions(engine):
            listing = asyncio.run(generate_then_chat())
    finally:
        event.remove(engine, "checkout", checkout)
        engine.dispose()
    assert listing and listing["title"] == "Blue tee"
    assert on_the_loop and not any(on_the_loop)


def test_marketplace_schema_and_native_job_run_off_the_event_loop(monkeypatch):
    """Schema cache reads and marketplace job DB updates must run in worker threads, never on the loop."""
    import asyncio
    from unittest.mock import AsyncMock

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from anteroom.database import Base, load_models
    from anteroom.repositories.queries import ConversationRepo, JobRepo
    from anteroom.services import (
        ebay_listings,
        listing_persistence,
        marketplace_accounts,
        marketplace_schemas,
        native_marketplaces,
    )
    from worker_sessions import memory_engine, worker_sessions

    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conv = ConversationRepo(db).create(title="Tee")
        rev = listing_persistence.save_revision(db, conv.id, {"title": "Blue tee", "price": 20}, "user_form")
        job = JobRepo(db).create(
            conv.id,
            rev.id,
            {"title": "Blue tee", "price": 20, "_approved_photos": []},
            status="dispatched",
            current_step="native_ebay_save_draft",
        )
        conv_id = conv.id
        job_id = job.id
    on_the_loop: list[bool] = []

    def checkout(*_args):
        on_the_loop.append(asyncio._get_running_loop() is not None)

    async def fake_etsy_api(_method, _path, **_kwargs):
        return {"results": [{"property_id": 100, "name": "Color", "is_required": False, "possible_values": []}]}

    async def fake_ebay_execute(_action, _snapshot, _photos, _binding, record, active, **_kwargs):
        assert await active() is True
        await record({"ebay_sku": "ar-off-loop", "ebay_offer_id": "42", "ebay_inventory_written": True})
        return {"ok": True, "state": "draft"}

    monkeypatch.setattr(marketplace_accounts, "etsy_api", fake_etsy_api)
    monkeypatch.setattr(ebay_listings, "execute", fake_ebay_execute)
    monkeypatch.setattr(ebay_listings.EbayClient, "connect", AsyncMock())
    event.listen(engine, "checkout", checkout)

    async def run_both():
        rows = await marketplace_schemas.etsy_properties("1234")
        await native_marketplaces.run_job(job_id, conv_id, "ebay", ["save_draft"], {})
        return rows

    try:
        with worker_sessions(engine):
            rows = asyncio.run(run_both())
    finally:
        event.remove(engine, "checkout", checkout)
        engine.dispose()
    assert rows and rows[0]["name"] == "Color"
    assert on_the_loop and not any(on_the_loop)


def test_native_publish_holds_no_connection_across_awaits_and_offloads_io(tmp_path, monkeypatch):
    """eBay/Etsy jobs hold zero DB connections across awaits (even after callbacks) and offload file/settings I/O."""
    import asyncio
    from io import BytesIO
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from PIL import Image
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from anteroom.database import Base, load_models
    from anteroom.repositories.queries import ConversationRepo, JobRepo
    from anteroom.services import ebay_listings, etsy_listings, listing_persistence, native_marketplaces
    from anteroom.services.photo_store import LocalPhotoStore
    from worker_sessions import memory_engine, worker_sessions

    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    store = LocalPhotoStore(str(tmp_path))
    buf = BytesIO()
    Image.new("RGB", (64, 64), (40, 90, 180)).save(buf, "JPEG")
    jpg = buf.getvalue()
    (tmp_path / "shot.jpg").write_bytes(jpg)
    monkeypatch.setattr("anteroom.services.photo_store._store", store)

    with Session(engine) as db:
        conv = ConversationRepo(db).create(title="Jacket")
        rev = listing_persistence.save_revision(db, conv.id, {"title": "Jacket", "price": 40}, "user_form")
        ebay_job = JobRepo(db).create(
            conv.id, rev.id, {"title": "Jacket", "price": 40, "_approved_photos": []},
            status="dispatched", current_step="native_ebay_list",
        )
        native_marketplaces._store(db, conv.id, "ebay", {"state": "listing", "action": "list", "job_id": ebay_job.id})
        etsy_job = JobRepo(db).create(
            conv.id, rev.id, {"title": "Jacket", "price": 40, "_approved_photos": []},
            status="dispatched", current_step="native_etsy_save_draft",
        )
        native_marketplaces._store(db, conv.id, "etsy", {"state": "saving_draft", "action": "save_draft", "job_id": etsy_job.id})
        conv_id, ebay_job_id, etsy_job_id = conv.id, ebay_job.id, etsy_job.id

    checked_out = 0
    on_the_loop: list[bool] = []
    observations: list[tuple[str, int]] = []

    def checkout(*_args):
        nonlocal checked_out
        checked_out += 1
        on_the_loop.append(asyncio._get_running_loop() is not None)

    def checkin(*_args):
        nonlocal checked_out
        checked_out -= 1

    async def fake_ebay_execute(_step, _snap, _photos, _binding, record, active, **_kwargs):
        observations.append(("ebay_enter", checked_out))
        await record({"ebay_sku": "sku-abc"})
        observations.append(("ebay_after_record", checked_out))
        assert await active() is True
        observations.append(("ebay_after_active", checked_out))
        return {"ok": False, "safe_to_retry": False, "error": "Reply lost"}

    async def fake_ebay_check(_record, _snap, remember):
        observations.append(("ebay_check_enter", checked_out))
        await remember({"ebay_offer_id": "offer-42"})
        observations.append(("ebay_after_remember", checked_out))
        return {"ok": True, "state": "published", "listing_url": "https://www.ebay.com/itm/1234567890"}

    async def fake_etsy_execute(_step, _snap, _photos, _binding, record, active, **_kwargs):
        observations.append(("etsy_enter", checked_out))
        await record({"etsy_shop_id": "999"})
        observations.append(("etsy_after_record", checked_out))
        assert await active() is True
        observations.append(("etsy_after_active", checked_out))
        return {"ok": False, "safe_to_retry": False, "error": "Reply lost"}

    async def fake_etsy_check(_snap, _record, _candidate):
        observations.append(("etsy_check_enter", checked_out))
        return {"ok": True, "state": "draft", "draft_url": "https://www.etsy.com/your/shops/me/listing-editor/edit/123456"}

    orig_etsy_check = etsy_listings.check
    monkeypatch.setattr(ebay_listings, "execute", fake_ebay_execute)
    monkeypatch.setattr(ebay_listings, "check", fake_ebay_check)
    monkeypatch.setattr(etsy_listings, "execute", fake_etsy_execute)
    monkeypatch.setattr(etsy_listings, "check", fake_etsy_check)

    read_threads: list[int | None] = []
    local_path_threads: list[int | None] = []
    settings_threads: list[int | None] = []
    shop_threads: list[int | None] = []
    orig_read = store.read
    orig_local_path = store.local_path
    monkeypatch.setattr(store, "read", lambda name: (read_threads.append(threading.current_thread().ident), orig_read(name))[1])
    monkeypatch.setattr(store, "local_path", lambda name: (local_path_threads.append(threading.current_thread().ident), orig_local_path(name))[1])

    photo = SimpleNamespace(
        id="p1", stored_filename="shot.jpg", original_filename="shot.jpg",
        mime_type="image/jpeg", checksum="",
    )
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)

    async def run_all():
        loop_tid = threading.current_thread().ident
        await native_marketplaces.run_job(ebay_job_id, conv_id, "ebay", ["list"], {"confirm": True})
        observations.append(("after_ebay_job", checked_out))
        await native_marketplaces.run_job(etsy_job_id, conv_id, "etsy", ["save_draft"], {})
        observations.append(("after_etsy_job", checked_out))

        fake_client = SimpleNamespace(request=AsyncMock(return_value={
            "imageUrl": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
            "fulfillmentPolicies": [{"fulfillmentPolicyId": "f1"}],
            "paymentPolicies": [{"paymentPolicyId": "p1"}],
            "returnPolicies": [{"returnPolicyId": "r1"}],
            "locations": [{"merchantLocationKey": "loc1", "merchantLocationStatus": "ENABLED"}],
        }))
        urls = await ebay_listings._image_urls(fake_client, [photo], {}, lambda _v: None, lambda: True)
        assert urls == ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"]

        monkeypatch.setattr(ebay_listings, "get_ebay_listing_defaults", lambda: (settings_threads.append(threading.current_thread().ident), {})[1])
        choices = await ebay_listings.listing_choices(fake_client, {})
        assert choices["merchantLocationKey"] == "loc1"

        await etsy_listings._sync_photos(AsyncMock(return_value={"listing_image_id": 777}), "123", "456", {"images": []}, [photo], {}, lambda _v: None)

        monkeypatch.setattr(etsy_listings, "connected_shop_id", lambda: (shop_threads.append(threading.current_thread().ident), "123")[1])
        await native_marketplaces._require_etsy()
        monkeypatch.setattr(etsy_listings, "find_draft", AsyncMock(return_value=None))
        checked = await orig_etsy_check({}, {"action": "save_draft"}, None)
        assert checked["safe_to_retry"] is True
        assert read_threads and all(tid != loop_tid for tid in read_threads)
        assert local_path_threads and all(tid != loop_tid for tid in local_path_threads)
        assert settings_threads and all(tid != loop_tid for tid in settings_threads)
        assert len(shop_threads) == 2 and all(tid != loop_tid for tid in shop_threads)

    try:
        with worker_sessions(engine):
            asyncio.run(run_all())
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
        engine.dispose()

    assert on_the_loop and not any(on_the_loop)
    assert observations == [
        ("ebay_enter", 0),
        ("ebay_after_record", 0),
        ("ebay_after_active", 0),
        ("ebay_check_enter", 0),
        ("ebay_after_remember", 0),
        ("after_ebay_job", 0),
        ("etsy_enter", 0),
        ("etsy_after_record", 0),
        ("etsy_after_active", 0),
        ("etsy_check_enter", 0),
        ("after_etsy_job", 0),
    ]

