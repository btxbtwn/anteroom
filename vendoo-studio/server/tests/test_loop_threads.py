"""Blocking work handed off with ``asyncio.to_thread`` must not queue behind a CPU-sized pool.

asyncio sizes its default pool from the CPU count: five threads on the one-vCPU
hosted Machine. Every database step of an async route and every photo copy to
Storage shares it, so four or five marketplace starts copying photos at once
stalled every other request for 30 s or more (production, 2026-11-11).
"""
import asyncio
import os
import threading

from fastapi.testclient import TestClient

from vendoo_studio.services.background_loop import BackgroundLoop

# More than the five threads a one-CPU Machine gets by default.
AT_ONCE = 12


async def _blocking_calls_run_together() -> bool:
    barrier = threading.Barrier(AT_ONCE, timeout=3)

    def wait_for_the_others():
        barrier.wait()

    try:
        await asyncio.gather(*(asyncio.to_thread(wait_for_the_others) for _ in range(AT_ONCE)))
        return True
    except threading.BrokenBarrierError:
        return False


def test_hosted_request_loop_runs_blocking_steps_side_by_side_on_one_cpu(monkeypatch):
    from vendoo_studio.hosted import create_app

    monkeypatch.setattr(os, 'cpu_count', lambda: 1)
    app = create_app('https://example.supabase.co')

    async def probe():
        return {'together': await _blocking_calls_run_together()}

    app.add_api_route('/internal/thread-probe', probe)
    with TestClient(app) as client:
        response = client.get('/internal/thread-probe')
    assert response.json() == {'together': True}


def test_background_loops_run_blocking_steps_side_by_side_on_one_cpu(monkeypatch):
    monkeypatch.setattr(os, 'cpu_count', lambda: 1)
    loop = BackgroundLoop('test-loop-threads')
    try:
        assert loop.run(_blocking_calls_run_together()).result(timeout=10)
    finally:
        loop.stop()
