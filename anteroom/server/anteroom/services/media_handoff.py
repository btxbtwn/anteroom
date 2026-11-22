"""Keep request resources alive until an upload and its database handoff settle."""
import asyncio
from collections.abc import Coroutine
from typing import Any


async def finish_media_handoff[T](operation: Coroutine[Any, Any, T]) -> T:
    # Cancelling a thread await doesn't stop the thread. Let the handoff finish
    # before releasing its temp file or deciding which uploaded keys to delete.
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
