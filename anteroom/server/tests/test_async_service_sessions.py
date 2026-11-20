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
