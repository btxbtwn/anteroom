"""Async services and routes keep blocking database work off the event loop (see ``loop_checkouts``)."""
from loop_checkouts import loop_session_functions

# Async functions that still open or use a blocking session on the event loop.
# Each one moves its database steps into ``database.in_worker_session`` and
# leaves this list. Never add to it: the list only shrinks.
STILL_ON_THE_LOOP = {
    "routes/chat.py:_persist_chat_result",
    "routes/chat.py:run_message",
    "routes/chat.py:stream_response",
    "services/agent_categories.py:_apply_saved_agent_category",
    "services/agent_categories.py:apply_native_categories",
    "services/agent_categories.py:apply_saved_agent_category",
    "services/automation_scheduler.py:_loop",
    "services/automation_scheduler.py:tick",
    "services/background_worker.py:_execute",
    "services/category_selection.py:select_categories",
    "services/chat_forms.py:forms_prompt",
    "services/chat_listing.py:apply_listing_payload_with_repair",
    "services/chat_listing.py:persist_chat_result",
    "services/chat_prompts.py:build_chat_messages",
    "services/direct_categories.py:attach_direct_categories",
    "services/extension_connection.py:_reconnect_work_after_settle",
    "services/extension_connection.py:extension_websocket",
    "services/generation_schema.py:prepare_generation_schema",
    "services/hosted_automation_scheduler.py:run_owner",
    "services/hosted_promotion_scheduler.py:check_ready_sellers",
    "services/hosted_promotion_scheduler.py:check_seller",
    "services/job_watchdog.py:sweep",
    "services/listing_field_gaps.py:fill_listing_field_gaps",
    "services/listing_generate.py:persist_generated_listing_with_repair",
    "services/listing_generation.py:_finish_generation_background",
    "services/listing_generation.py:choose_categories",
    "services/listing_generation.py:run_listing_generation",
    "services/listing_promotions.py:review",
    "services/marketplace_accounts.py:autodelist_for_sales",
    "services/marketplace_inventory.py:observe",
    "services/marketplace_inventory.py:read_after_sales",
    "services/native_editor.py:editor_form",
    "services/native_editor.py:refresh_form",
    "services/native_field_fill.py:_fill_after_category",
    "services/native_field_fill.py:_fill_native_fields",
    "services/native_field_fill.py:fill_native_fields",
    "services/native_field_fill.py:load_forms",
    "services/native_field_fill.py:prepare_generation_forms",
    "services/native_field_fill.py:record_forms",
    "services/native_marketplaces.py:_execute_step",
    "services/native_marketplaces.py:_wait_for_chrome",
    "services/native_marketplaces.py:run_job",
    "services/order_amounts.py:backfill",
    "services/order_history.py:sync",
    "services/order_recovery.py:recover_ebay_notification",
    "services/promotion_automations.py:_timer",
    "services/promotion_automations.py:check_new_likes",
    "services/promotion_automations.py:run_round",
    "services/promotion_automations.py:tick",
    "services/relay_sales.py:handle_event",
    "services/scout.py:run_check",
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
        "async def released(conv_id):\n    def step(db):\n        db.commit()\n"
        "    await in_worker_session(step)\n    await in_worker_session(lambda db: db.commit())\n")
    monkeypatch.setattr(loop_checkouts, "PACKAGE", tmp_path)
    assert loop_checkouts.loop_session_functions(("services",)) == {
        "services/example.py:opens", "services/example.py:uses", "services/example.py:factory"}
