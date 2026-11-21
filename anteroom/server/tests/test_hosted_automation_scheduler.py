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


def test_concurrent_seller_ticks_are_bounded_and_drain_without_waiting_for_next_poll(monkeypatch):
    owners = [f"seller-{i}" for i in range(8)]
    active = 0
    peak_active = 0
    finished: list[str] = []
    all_done = asyncio.Event()

    async def run_owner(owner):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.02)
        finally:
            active -= 1
            finished.append(owner)
            if len(finished) >= len(owners):
                all_done.set()

    # Large tick interval ensures queued sellers run as slots free up rather than waiting 60s.
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
    assert peak_active == 3
    assert set(finished) == set(owners)


def test_scheduler_concurrency_env_validation(monkeypatch):
    monkeypatch.delenv("ANTEROOM_AUTOMATION_CONCURRENCY", raising=False)
    assert scheduler._scheduler_concurrency() == scheduler.DEFAULT_SCHEDULER_CONCURRENCY
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "6")
    assert scheduler._scheduler_concurrency() == 6
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "0")
    assert scheduler._scheduler_concurrency() == 1
    monkeypatch.setenv("ANTEROOM_AUTOMATION_CONCURRENCY", "not-a-number")
    assert scheduler._scheduler_concurrency() == scheduler.DEFAULT_SCHEDULER_CONCURRENCY
