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

    app = create_app('https://shared-http-test.supabase.co')
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        registered = list(shared_http._clients.values())
        assert len(registered) == 1
        assert not registered[0].is_closed
    assert registered[0].is_closed
    assert not shared_http._clients
