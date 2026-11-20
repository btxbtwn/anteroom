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
from contextlib import asynccontextmanager

from fastapi import HTTPException

# One image message holds its raw body (at most MAX_MESSAGE_BYTES, about
# 85 MB) and briefly the parsed copy of it, about 170 MB at peak. The Machine
# has 2 GB with about 1.2 GB free under load (docs/hosted-production.md), so
# four at once (about 680 MB) fit with room for the rest of the process.
UPLOAD_SLOTS = 4
# How long a message waits for a slot before the seller is asked to retry.
# Long enough to ride out a burst, short enough that nobody stares at a
# spinner behind someone else's 120-second upload.
UPLOAD_WAIT_SECONDS = 10.0


class UploadSlots:
    """At most one message upload per seller, and ``total`` across sellers."""

    def __init__(self, total: int = UPLOAD_SLOTS, wait: float = UPLOAD_WAIT_SECONDS):
        self._total = asyncio.Semaphore(total)
        self._wait = wait
        # Seller -> [lock, holders]. Removed when nobody holds or waits for it.
        self._sellers: dict[str, list] = {}

    @asynccontextmanager
    async def hold(self, seller: str):
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


# Generous enough that selecting a hundred drafts and generating them all at
# once goes through; a script submitting thousands is slowed to one a second.
ENQUEUE_BURST = 120
ENQUEUE_PER_SECOND = 1.0
# Buckets for sellers idle long enough to be full again carry no state worth
# keeping; this only bounds memory if many sellers appear at once.
MAX_TRACKED_SELLERS = 4096


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
