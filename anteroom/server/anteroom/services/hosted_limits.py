"""Abuse and memory guards for the hosted API.

These are not product quotas. Neither limits how many drafts a seller keeps
or generates; they stop one seller (or a stolen session) from exhausting the
shared Machine's memory or queue. Both live in this process only: with more
than one Machine each keeps its own count.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.responses import Response

# One image message holds its raw body (at most MAX_MESSAGE_BYTES, about
# 85 MB) and briefly the parsed copy of it, about 170 MB at peak. The Machine
# has 2 GB with about 1.2 GB free under load (docs/hosted-production.md), so
# four at once (about 680 MB) fit with room for the rest of the process.
UPLOAD_SLOTS = 4
# How long a message waits for a slot before the seller is asked to retry.
# Long enough to ride out a burst, short enough that nobody stares at a
# spinner behind someone else's 120-second upload.
UPLOAD_WAIT_SECONDS = 10.0
BODY_RECEIVE_SECONDS = 120.0
STREAM_CHUNK_BYTES = 64 * 1024


class UploadSlots:
    """At most one upload per seller (unless ``per_seller=False``), and ``total`` across sellers."""

    def __init__(self, total: int = UPLOAD_SLOTS, wait: float = UPLOAD_WAIT_SECONDS):
        self._total = asyncio.Semaphore(total)
        self._wait = wait
        # Seller -> [lock, holders]. Removed when nobody holds or waits for it.
        self._sellers: dict[str, list] = {}

    @asynccontextmanager
    async def hold(self, seller: str, *, per_seller: bool = True):
        if not per_seller:
            try:
                async with asyncio.timeout(self._wait):
                    await self._total.acquire()
            except TimeoutError:
                raise HTTPException(
                    503, 'Image uploads are busy right now. Try again in a moment.',
                    headers={'Retry-After': '5'}) from None
            try:
                yield
            finally:
                self._total.release()
            return
        entry = self._sellers.setdefault(seller, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            try:
                async with asyncio.timeout(self._wait):
                    await entry[0].acquire()
            except TimeoutError:
                raise HTTPException(
                    429, 'Another of your messages is still uploading. Try again when it finishes.',
                    headers={'Retry-After': '5'}) from None
            try:
                try:
                    async with asyncio.timeout(self._wait):
                        await self._total.acquire()
                except TimeoutError:
                    raise HTTPException(
                        503, 'Image uploads are busy right now. Try again in a moment.',
                        headers={'Retry-After': '5'}) from None
                try:
                    yield
                finally:
                    self._total.release()
            finally:
                entry[0].release()
        finally:
            entry[1] -= 1
            if not entry[1]:
                self._sellers.pop(seller, None)


@asynccontextmanager
async def hold_upload_slot(request: Request | None, *, per_seller: bool = True):
    """Acquire the hosted app's UploadSlots for the current seller when configured."""
    if request is None:
        yield
        return
    slots = getattr(request.app.state, 'upload_slots', None)
    if slots is None:
        yield
        return
    from anteroom.services.service_context import current
    context = current()
    if context is None:
        yield
        return
    async with slots.hold(context.seller_id, per_seller=per_seller):
        yield


async def release_upload_slot(request: Request | None) -> None:
    """Release an upload slot acquired pre-parser by ``bounded_route_class`` early.

    Used by endpoints that run slow downstream inference after finishing upload I/O,
    so the seller's upload slot is not held while waiting on an LLM response.
    """
    if request is None:
        return
    releaser = getattr(request.state, '_release_upload_slot', None)
    if releaser is not None:
        request.state._release_upload_slot = None
        await releaser()


def _check_content_length(request: Request, max_bytes: int, too_large_detail: str) -> None:
    raw_length = request.headers.get('content-length')
    if raw_length is None:
        return
    try:
        if int(raw_length) > max_bytes:
            raise HTTPException(413, too_large_detail)
    except ValueError:
        pass


async def read_bounded_body(
    request: Request,
    max_bytes: int,
    *,
    too_large_detail: str = 'Request body is too large',
    timeout_seconds: float | None = 120.0,
    timeout_detail: str = 'Upload timed out. Try again.',
) -> bytearray:
    """Stream an HTTP request body into memory while enforcing a hard byte cap."""
    _check_content_length(request, max_bytes, too_large_detail)
    data = bytearray()
    try:
        async with asyncio.timeout(timeout_seconds):
            async for chunk in request.stream():
                if len(data) + len(chunk) > max_bytes:
                    raise HTTPException(413, too_large_detail)
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, timeout_detail) from None
    return data


async def read_bounded_upload(
    upload: UploadFile,
    max_bytes: int,
    *,
    too_large_detail: str,
    chunk_size: int = STREAM_CHUNK_BYTES,
) -> bytearray:
    """Read an UploadFile in fixed-size chunks, raising 413 immediately on overflow."""
    if upload.size is not None and upload.size > max_bytes:
        raise HTTPException(413, too_large_detail)
    data = bytearray()
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        if len(data) + len(chunk) > max_bytes:
            raise HTTPException(413, too_large_detail)
        data.extend(chunk)
    return data


async def stream_upload_to_path(
    upload: UploadFile,
    dest: Path,
    max_bytes: int,
    *,
    too_large_detail: str,
    chunk_size: int = STREAM_CHUNK_BYTES,
) -> int:
    """Stream an UploadFile to ``dest`` in chunks without loading the whole file into RAM."""
    if upload.size is not None and upload.size > max_bytes:
        raise HTTPException(413, too_large_detail)
    written = 0
    try:
        with dest.open('wb') as out:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, too_large_detail)
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written


def bounded_route_class(
    max_bytes: int | Callable[[], int],
    *,
    too_large_detail: str = 'Request body is too large',
    use_upload_slots: bool = False,
) -> type[APIRoute]:
    """APIRoute subclass that caps incoming request body bytes as they stream in.

    Preserves FastAPI's declared requestBody schema and handler signatures while
    rejecting oversized requests with 413 before buffering them into memory.
    When ``use_upload_slots=True``, acquires the upload slot before multipart/body
    parsing, enforces ``BODY_RECEIVE_SECONDS`` while receiving the body, and
    exposes ``release_upload_slot(request)`` so handlers can drop the slot before
    slow downstream LLM calls.
    """

    class _BoundedRoute(APIRoute):
        def get_route_handler(self):
            original_handler = super().get_route_handler()

            async def bounded_handler(request: Request) -> Response:
                limit = max_bytes() if callable(max_bytes) else max_bytes
                _check_content_length(request, limit, too_large_detail)
                raw_receive = request._receive
                received = 0
                receive_deadline = None
                body_complete = False

                async def limited_receive():
                    nonlocal received, body_complete
                    if receive_deadline is not None and not body_complete:
                        try:
                            async with asyncio.timeout_at(receive_deadline):
                                message = await raw_receive()
                        except TimeoutError:
                            raise HTTPException(408, 'Upload timed out. Try again.') from None
                    else:
                        message = await raw_receive()
                    if message.get('type') == 'http.request':
                        body_complete = not message.get('more_body', False)
                        received += len(message.get('body', b''))
                        if received > limit:
                            raise HTTPException(413, too_large_detail)
                    elif message.get('type') == 'http.disconnect':
                        body_complete = True
                    return message

                request._receive = limited_receive
                if use_upload_slots and request.method in {'POST', 'PUT', 'PATCH'}:
                    slot_cm = hold_upload_slot(request)
                    await slot_cm.__aenter__()
                    slot_held = True

                    async def _release_slot() -> None:
                        nonlocal slot_held
                        if slot_held:
                            slot_held = False
                            await slot_cm.__aexit__(None, None, None)

                    request.state._release_upload_slot = _release_slot
                    receive_deadline = asyncio.get_running_loop().time() + BODY_RECEIVE_SECONDS
                    try:
                        return await original_handler(request)
                    finally:
                        if slot_held:
                            slot_held = False
                            await slot_cm.__aexit__(None, None, None)
                return await original_handler(request)

            return bounded_handler

    return _BoundedRoute


# Generous enough that selecting a hundred drafts and generating them all at
# once goes through; a script submitting thousands is slowed to one a second.
ENQUEUE_BURST = 120
ENQUEUE_PER_SECOND = 1.0
# Buckets for sellers idle long enough to be full again carry no state worth
# keeping; this only bounds memory if many sellers appear at once.
MAX_TRACKED_SELLERS = 4096


# Browser crash reports: plenty for a burst of real errors, stops a stuck tab
# or abusive session from flooding logs and operator alert capacity.
ERROR_REPORT_BURST = 30
ERROR_REPORT_PER_SECOND = 0.5


class SellerRateLimit:
    """A token bucket per seller for submitting background work."""

    def __init__(self, burst: int = ENQUEUE_BURST, per_second: float = ENQUEUE_PER_SECOND,
                 clock=time.monotonic):
        self._burst, self._rate, self._clock = float(burst), per_second, clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, seller: str) -> None:
        now = self._clock()
        with self._lock:
            tokens, then = self._buckets.pop(seller, (self._burst, now))
            tokens = min(self._burst, tokens + (now - then) * self._rate)
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._buckets[seller] = (tokens, now)
            while len(self._buckets) > MAX_TRACKED_SELLERS:
                self._buckets.popitem(last=False)
        if not allowed:
            wait = max(1, int((1 - tokens) / self._rate + 0.999))
            raise HTTPException(429, 'Too many requests in a short time. Try again in a moment.',
                                headers={'Retry-After': str(wait)})


def limit_seller_submissions(request: Request) -> None:
    """Apply the hosted app's per-seller submission bucket when running hosted."""
    limiter = getattr(request.app.state, 'submission_limit', None)
    if limiter is None:
        return
    from anteroom.services.service_context import current
    context = current()
    if context is not None:
        limiter.take(context.seller_id)
        return
    seller = getattr(request.state, 'seller', None)
    if seller is not None:
        limiter.take(seller.user_id)
