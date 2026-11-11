import asyncio

from vendoo_studio.services import shared_http


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
