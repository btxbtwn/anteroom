"""Dependencies for one execution, inherited by its async tasks and threads.

Desktop execution uses its local dependencies. Hosted requests install a
verified seller's dependencies before calling the same domain services.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
from typing import Any, Protocol


class SettingsStore(Protocol):
    def read(self) -> dict: ...
    def write(self, payload: dict) -> None: ...
    def update(self, mutator: Callable[[dict], None]) -> dict: ...


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...
    def set(self, account: str, value: str) -> bool: ...
    def delete(self, account: str) -> None: ...
    def update(self, account: str, mutator: Callable[[str | None], str | None]) -> None: ...


@dataclass(frozen=True)
class ServiceContext:
    seller_id: str
    session_factory: Callable[[], Any]
    settings: SettingsStore
    secrets: SecretStore
    photos: Any
    catalog: Any = None
    provider_factory: Callable[[str], Any] | None = None
    vendoo_available: bool = False
    spawned_tasks: set[Any] = field(default_factory=set, compare=False)


_current: ContextVar[ServiceContext | None] = ContextVar('studio_service_context', default=None)


def current() -> ServiceContext | None:
    context = _current.get()
    if context is None and os.environ.get('ANTEROOM_HOSTED') == '1':
        raise RuntimeError('Hosted services require a verified seller execution context')
    return context


@contextmanager
def use(context: ServiceContext) -> Iterator[ServiceContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
