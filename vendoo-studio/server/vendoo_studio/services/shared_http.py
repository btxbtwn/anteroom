"""One HTTP client, and its open connections, shared on the server's loop.

A fresh client per call paid a TLS handshake every time: about 10 ms of CPU
and 120 ms of waiting, ten-odd times per listing. A client is tied to the loop
it ran on, so calls on any other loop (a worker thread, a test) still get
their own. Callers pass their own ``timeout`` on each request: the shared
client's default is httpx's, not theirs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

_shared: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


def keep_connection_on_this_loop() -> None:
    global _shared
    _shared = (asyncio.get_running_loop(), httpx.AsyncClient())


async def close_connection() -> None:
    global _shared
    shared, _shared = _shared, None
    if shared is not None:
        await shared[1].aclose()


@asynccontextmanager
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    shared = _shared
    if shared is not None and shared[0] is asyncio.get_running_loop():
        yield shared[1]
        return
    async with httpx.AsyncClient() as client:
        yield client
