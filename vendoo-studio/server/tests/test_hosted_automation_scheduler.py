"""The hosted automation clock runs each due seller on their own, never one behind another."""
import asyncio

from vendoo_studio.services import hosted_automation_scheduler as scheduler


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
