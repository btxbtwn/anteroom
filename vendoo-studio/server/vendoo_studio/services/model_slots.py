"""Cap how many model calls one listing runs at once.

Generation fans out (sold comps beside active listings beside category
selection, one marketplace's fields beside another's), and every call is a
model request — for Claude, a CLI process on the seller's subscription. A run
binds one semaphore here; each model call takes a slot. Tasks the run spawns
inherit it. With nothing bound, calls are not limited.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

MAX_MODEL_CALLS = 3

_slots: ContextVar[asyncio.Semaphore | None] = ContextVar("model_slots", default=None)


def bind(limit: int = MAX_MODEL_CALLS) -> None:
    """Limit this task (and its children) to ``limit`` concurrent model calls."""
    _slots.set(asyncio.Semaphore(limit))


@asynccontextmanager
async def slot() -> AsyncIterator[None]:
    """Hold one model-call slot for the body. Do not nest: a call holding a
    slot must not wait on another call that needs one."""
    slots = _slots.get()
    if slots is None:
        yield
        return
    async with slots:
        yield
