import asyncio

from anteroom.services import shared_http


def test_calls_on_the_server_loop_share_one_client():
    async def run():
        shared_http.keep_connection_on_this_loop()
        try:
            async with shared_http.http_client() as first:
                pass
            async with shared_http.http_client() as second:
                pass
            assert first is second
            assert not first.is_closed
        finally:
            await shared_http.close_connection()
        assert first.is_closed

    asyncio.run(run())


def test_calls_on_another_loop_get_their_own_client():
    async def run():
        async with shared_http.http_client() as client:
            pass
        assert client.is_closed

    asyncio.run(run())


def test_multiple_loops_keep_independent_shared_clients():
    from anteroom.services.background_loop import BackgroundLoop

    bg = BackgroundLoop('test-shared-http')
    try:
        async def run():
            shared_http.keep_connection_on_this_loop()
            try:
                main_client = shared_http.current_client()
                assert main_client is not None

                async def register_bg():
                    shared_http.keep_connection_on_this_loop()
                    return shared_http.current_client()

                bg_client = bg.run(register_bg()).result(timeout=5)
                assert bg_client is not None
                assert bg_client is not main_client
                # Registering on the background loop must not clobber the main loop's client.
                assert shared_http.current_client() is main_client

                bg.run(shared_http.close_connection()).result(timeout=5)
                assert bg_client.is_closed
                assert not main_client.is_closed
            finally:
                await shared_http.close_connection()

        asyncio.run(run())
    finally:
        bg.stop()


def test_hosted_lifespan_enables_and_closes_shared_http():
    from fastapi.testclient import TestClient
    from anteroom.hosted import create_app

    before = set(shared_http._clients)
    app = create_app('https://shared-http-test.supabase.co')
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        added = [http for loop, http in shared_http._clients.items() if loop not in before]
        assert len(added) == 1
        assert not added[0].is_closed
    assert added[0].is_closed
    assert set(shared_http._clients) == before


def test_hosted_startup_failure_does_not_leak_shared_http():
    import pytest
    from fastapi.testclient import TestClient
    from anteroom.hosted import create_app

    before = set(shared_http._clients)
    with pytest.raises(ValueError):
        with TestClient(create_app('')):
            pass
    assert set(shared_http._clients) == before


def test_shared_http_initialization_failure_stops_hosted_background_loops(monkeypatch):
    import threading

    import pytest
    from fastapi.testclient import TestClient
    from anteroom import hosted
    from anteroom.services import background_worker, process_handoff
    from anteroom.services.background_loop import BackgroundLoop

    loops: dict[str, BackgroundLoop] = {}
    released = threading.Event()

    class TrackedLoop(BackgroundLoop):
        def __init__(self, name: str):
            super().__init__(name)
            loops[name] = self

    async def serve(_engine, stopping):
        while not stopping.is_set():
            await asyncio.sleep(0.01)

    def fail_initialization():
        raise OSError('Cannot load the HTTP client certificate')

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'worker')
    monkeypatch.setattr(hosted, 'BackgroundLoop', TrackedLoop)
    monkeypatch.setattr(background_worker, 'serve', serve)
    monkeypatch.setattr(process_handoff, 'hold', lambda *_: False)
    monkeypatch.setattr(process_handoff, 'release', lambda _: released.set())
    monkeypatch.setattr(shared_http, 'keep_connection_on_this_loop', fail_initialization)

    try:
        with pytest.raises(OSError, match='Cannot load the HTTP client certificate'):
            with TestClient(hosted.create_app('https://shared-http-test.supabase.co')):
                pytest.fail('The app started despite the shared HTTP client failure')
        assert set(loops) == {'anteroom-drafts', 'anteroom-handoff'}
        assert released.is_set()
        assert all(not loop.thread.is_alive() for loop in loops.values())
    finally:
        process_handoff.begin_draining()
        for loop in loops.values():
            if loop.thread.is_alive():
                loop.stop()
