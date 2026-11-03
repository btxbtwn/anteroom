"""Share expensive seller reads across tabs, validated against committed changes."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from time import monotonic
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.models.seller_change import SellerChange
from vendoo_studio.services.service_context import current


def _version(context):
    from vendoo_studio.database import engine

    if engine.dialect.name != 'postgresql':
        raise HTTPException(503, 'The cloud database is not configured')
    with context.session_factory() as db:
        changes = tuple(db.execute(select(SellerChange.topic, SellerChange.changed_at)
                                   .order_by(SellerChange.topic)).all())
    return changes


def _load(context, read):
    context.settings.read()
    with context.session_factory() as db:
        return read(db)


class SellerReadSnapshots:
    def __init__(self):
        self.completed: OrderedDict[tuple[str, str], tuple[Any, Any, float]] = OrderedDict()
        self.pending: dict[tuple[str, str], asyncio.Task] = {}

    async def read(self, name: str, load: Callable, *, refresh_seconds: float | None = None):
        context = current()
        if context is None:
            raise HTTPException(401, 'A verified seller session is required')
        key = context.seller_id, name
        try:
            while True:
                # Only a short change-vector query runs for each request. No
                # connection or worker thread is held while waiting on a peer.
                version = await asyncio.to_thread(_version, context)
                cached = self.completed.get(key)
                if (cached is not None and cached[0] == version
                        and (refresh_seconds is None or monotonic() - cached[2] < refresh_seconds)):
                    self.completed.move_to_end(key)
                    return cached[1]
                running = self.pending.get(key)
                if running is not None:
                    await asyncio.shield(running)
                    continue

                async def build(snapshot_version=version):
                    try:
                        result = await asyncio.to_thread(_load, context, load)
                        # Never label a snapshot with changes committed during
                        # its build. The next read must rebuild in that case.
                        if await asyncio.to_thread(_version, context) == snapshot_version:
                            self.completed[key] = snapshot_version, result, monotonic()
                            self.completed.move_to_end(key)
                            while len(self.completed) > 64:
                                self.completed.popitem(last=False)
                        return result
                    finally:
                        self.pending.pop(key, None)

                task = asyncio.create_task(build())
                self.pending[key] = task
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                # The build owns its session. Disconnecting one tab cannot
                # close it underneath other tabs waiting for the same result.
                return await asyncio.shield(task)
        except SQLAlchemyError:
            raise HTTPException(503, 'Cloud data is temporarily unavailable', headers={'Retry-After': '5'}) from None
