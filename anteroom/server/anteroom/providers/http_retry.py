"""A short, bounded retry for model API requests that failed before answering.

Anthropic, OpenAI and OpenRouter all ask clients to retry a request timeout,
a rate limit, an overload or a server error after a short wait, honouring
``retry-after``; their own SDKs retry twice with exponential backoff and
jitter. A model request writes nothing, so sending it again is safe. Only the
request's opening is retried: once an answer has started, a failure ends the
call. The waits are seconds, well inside the caller's deadline from
``provider_deadlines``; a ``retry-after`` longer than ``MAX_WAIT_SEC`` is not
waited out, and the seller sees the rate limit instead.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx

MAX_RETRIES = 2
BASE_DELAY_SEC = 0.5
MAX_DELAY_SEC = 8.0
MAX_WAIT_SEC = 20.0
# 408 request timeout, 409 conflict (Anthropic's lock contention), 429 rate
# limit, 5xx server errors and Anthropic's 529 overloaded.
RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
# The request never reached the model, or the server dropped it unanswered.
# A read timeout is not retried: the call already used its whole wait.
RETRY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)


class ProviderHTTPError(RuntimeError):
    """A model API answered with an error status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def retry_after(response: httpx.Response) -> float | None:
    """The server's ``retry-after`` in seconds, when it sent one."""
    value = (response.headers.get("retry-after-ms") or "").strip()
    if value:
        try:
            return max(0.0, float(value) / 1000)
        except ValueError:
            pass
    value = (response.headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def backoff(attempt: int) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(MAX_DELAY_SEC, BASE_DELAY_SEC * 2 ** attempt))


async def send(request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
    """``request()``, sent again up to ``MAX_RETRIES`` times while it fails retryably."""
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        try:
            response = await request()
        except RETRY_ERRORS:
            if last:
                raise
            await asyncio.sleep(backoff(attempt))
            continue
        if last or response.status_code not in RETRY_STATUSES:
            return response
        asked = retry_after(response)
        if asked is not None and asked > MAX_WAIT_SEC:
            return response
        await response.aclose()
        await asyncio.sleep(asked if asked is not None else backoff(attempt))
    raise AssertionError("unreachable")


@asynccontextmanager
async def stream(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> AsyncIterator[httpx.Response]:
    """``client.stream(...)``, with its opening retried like ``send``."""
    response = await send(lambda: client.send(client.build_request(method, url, **kwargs), stream=True))
    try:
        yield response
    finally:
        await response.aclose()
