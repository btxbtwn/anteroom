"""The hosted automation clock runs each due seller on their own, never one behind another."""
import asyncio

from anteroom.services import hosted_automation_scheduler as scheduler


def run_serve_for(seconds):
    async def scenario():
        try:
            await asyncio.wait_for(scheduler.serve(object()), timeout=seconds)
        except TimeoutError:
            pass

    asyncio.run(scenario())


def test_a_slow_seller_does_not_delay_another_sellers_due_run(monkeypatch):
    started: list[str] = []

    async def run_owner(owner):
        started.append(owner)
        if owner == "alice":
            # Alice's run waits on a regenerate, which may take up to IDLE_TIMEOUT_S per item.
            await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, "due_owners", lambda _engine: ["alice", "bob"])
    monkeypatch.setattr(scheduler, "run_owner", run_owner)
    run_serve_for(2)
    assert "bob" in started, "Bob's due automation never started while Alice's regenerate was waiting"


def test_a_seller_is_never_ticked_twice_at_once_and_the_clock_stops_their_run(monkeypatch):
    started: list[str] = []
    cancelled: list[str] = []

    async def run_owner(owner):
        started.append(owner)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(owner)
            raise

    monkeypatch.setattr(scheduler, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "due_owners", lambda _engine: ["alice"])
    monkeypatch.setattr(scheduler, "run_owner", run_owner)
    run_serve_for(0.5)
    assert started == ["alice"]
    # Stopping the clock cancels and waits for the seller's unfinished tick.
    assert cancelled == ["alice"]


def test_one_sellers_failed_tick_does_not_stop_the_clock(monkeypatch):
    started: list[str] = []

    async def run_owner(owner):
        started.append(owner)
        if owner == "alice":
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(scheduler, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "due_owners", lambda _engine: ["alice", "bob"])
    monkeypatch.setattr(scheduler, "run_owner", run_owner)
    run_serve_for(0.5)
    # Both sellers keep being ticked after Alice's failure, and a finished tick can start again.
    assert started.count("alice") > 1
    assert started.count("bob") > 1


def test_database_active_steps_are_bounded_across_concurrent_sellers(monkeypatch):
    from anteroom.services.automation_scheduler import db_slot

    owners = [f"seller-{i}" for i in range(8)]
    active_db = 0
    peak_db = 0
    finished: list[str] = []
    all_done = asyncio.Event()

    async def run_owner(owner):
        nonlocal active_db, peak_db
        async with db_slot():
            active_db += 1
            peak_db = max(peak_db, active_db)
            try:
                await asyncio.sleep(0.02)
            finally:
                active_db -= 1
        finished.append(owner)
        if len(finished) >= len(owners):
            all_done.set()

    monkeypatch.setattr(scheduler, "TICK_SECONDS", 60)
    monkeypatch.setattr(scheduler, "due_owners", lambda _engine: list(owners))
    monkeypatch.setattr(scheduler, "run_owner", run_owner)

    async def scenario():
        clock = asyncio.create_task(scheduler.serve(object(), max_concurrency=3))
        try:
            await asyncio.wait_for(all_done.wait(), timeout=2.0)
        finally:
            clock.cancel()
            await asyncio.gather(clock, return_exceptions=True)

    asyncio.run(scenario())
    assert peak_db == 3
    assert set(finished) == set(owners)


def test_waiting_regenerations_above_concurrency_limit_do_not_block_other_sellers(monkeypatch):
    from anteroom.services import automation_scheduler as auto_sched

    slow_owners = [f"slow-{i}" for i in range(5)]
    fast_owner = "fast-seller"
    regenerating: set[str] = set()
    all_slow_waiting = asyncio.Event()
    fast_finished = asyncio.Event()

    async def fake_in_worker(work, *args):
        if work is auto_sched._pending_items:
            return [{"conversation_id": f"conv-{args[0]}"}]
        if work is auto_sched._start_item:
            return {"remote": {"type": "regenerate"}, "markets": []}
        return {"outcome": {"ok": True}}

    async def run_owner(owner):
        if owner in slow_owners:
            async def slow_executor(_item, _action, _markets, _rev):
                regenerating.add(owner)
                if len(regenerating) == len(slow_owners):
                    all_slow_waiting.set()
                await asyncio.Event().wait()

            await auto_sched._execute_items(f"run-{owner}", f"auto-{owner}", auto_sched.datetime.now(auto_sched.UTC), slow_executor)
            return

        # Fast seller waits until all 5 slow sellers (> max_concurrency=2) are actively waiting on regenerate.
        await all_slow_waiting.wait()
        async with auto_sched.db_slot():
            await asyncio.sleep(0.01)
        fast_finished.set()

    monkeypatch.setattr(auto_sched, "in_worker_session", fake_in_worker)
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 60)
    monkeypatch.setattr(scheduler, "due_owners", lambda _engine: [*slow_owners, fast_owner])
    monkeypatch.setattr(scheduler, "run_owner", run_owner)

    async def scenario():
        clock = asyncio.create_task(scheduler.serve(object(), max_concurrency=2))
        try:
            await asyncio.wait_for(fast_finished.wait(), timeout=2.0)
        finally:
            clock.cancel()
            await asyncio.gather(clock, return_exceptions=True)

    asyncio.run(scenario())
    assert len(regenerating) == 5
    assert fast_finished.is_set()


def test_scheduler_concurrency_env_validation(monkeypatch):
    monkeypatch.delenv("ANTEROOM_AUTOMATION_CONCURRENCY", raising=False)
    assert scheduler._scheduler_concurrency() == scheduler.DEFAULT_SCHEDULER_CONCURRENCY
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "6")
    assert scheduler._scheduler_concurrency() == 6
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "0")
    assert scheduler._scheduler_concurrency() == 1
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "not-a-number")
    assert scheduler._scheduler_concurrency() == scheduler.DEFAULT_SCHEDULER_CONCURRENCY


def test_scheduled_draft_and_relist_can_take_the_only_database_slot(monkeypatch):
    from unittest.mock import Mock

    from anteroom.services import automation_scheduler as common

    for name in ("_recover_stale", "end_read"):
        monkeypatch.setattr(common, name, Mock())
    for name in ("_bridge_busy", "_resume_waiting", "_claim_next"):
        monkeypatch.setattr(common, name, Mock(return_value=None))
    for name in ("_has_due_scheduled_drafts", "_has_enabled_auto_relist"):
        monkeypatch.setattr(common, name, Mock(return_value=True))

    async def sweep(_moment):
        async with common.db_slot():
            return 1

    async def run():
        with common.bind_db_gate(asyncio.Semaphore(1)):
            return await asyncio.wait_for(common.tick(Mock()), timeout=1)

    monkeypatch.setattr(common, "_run_scheduled_drafts", sweep)
    monkeypatch.setattr(common, "_run_auto_relist", sweep)
    assert asyncio.run(run()) == 2
