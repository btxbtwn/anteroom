"""A deadline on every call Anteroom makes to an AI provider.

A provider that stops answering used to hold its caller forever: a listing sat
on "Generating", a chat answer never ended, and only a restart cleared it.
Each provider bounded some of its waits (HTTP read timeouts, Cursor's idle
timer) and not others (a Claude Code run, a stream that sends keepalives but
no answer). This wraps whichever provider is chosen, so every call has one:

- a stream (``chat``, ``structured_chat``, ``vision_chat``, ``quick_chat``)
  fails when nothing arrives for ``STREAM_IDLE_SEC``. Long answers are fine as
  long as they keep coming;
- a single call (``analyze_photos``, ``web_search``, ...) fails when it has
  not returned within ``CALL_DEADLINE_SEC``.

Either way the caller gets ``ProviderStalled``, naming the provider and what
it was doing, and its usual error handling clears the busy state.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any

# The same idle limit Cursor's own watchdog and the HTTP providers' read timeouts use.
STREAM_IDLE_SEC = 300.0
# A photo read or web search returns in one piece, so it gets an overall limit.
# Generous: a twelve-step web search or a large photo set can take minutes.
CALL_DEADLINE_SEC = 600.0

_STREAM_STEPS = {
    "chat": "writing its answer",
    "structured_chat": "writing its answer",
    "vision_chat": "looking at the photos",
    "quick_chat": "answering",
}
_CALL_STEPS = {
    "analyze_photos": "reading the photos",
    "web_search": "searching the web",
    "test_connection": "testing the connection",
    "list_models": "listing its models",
    "list_model_entries": "listing its models",
}
_LABELS = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "cursor": "Cursor",
    "xiaomi-mimo": "MiMo",
    "sandbox": "The sandbox AI",
}


class ProviderStalled(RuntimeError):
    """A provider call passed its deadline."""


def provider_label(name: str | None) -> str:
    """The name a seller knows a provider by."""
    from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS

    key = str(name or "")
    spec = KEY_PROVIDERS.get(key)
    return _LABELS.get(key) or (spec.label if spec else key) or "The AI provider"


def _span(seconds: float) -> str:
    if seconds >= 60:
        minutes = round(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds:g} seconds"


def _stalled(provider: Any, step: str, seconds: float) -> ProviderStalled:
    label = provider_label(getattr(provider, "name", None))
    return ProviderStalled(f"{label} stopped responding while {step}: nothing came back for {_span(seconds)}.")


def operation_stalled(label: str, operation: str, seconds: float) -> ProviderStalled:
    """``ProviderStalled`` for ``label`` (as the seller knows it) doing ``operation``."""
    step = _STREAM_STEPS.get(operation) or _CALL_STEPS.get(operation) or "answering"
    return ProviderStalled(f"{label} stopped responding while {step}: nothing came back for {_span(seconds)}.")


class DeadlineProvider:
    """``provider`` with a deadline on each call; everything else passes through."""

    def __init__(self, provider: Any) -> None:
        self.wrapped = provider

    def __getattr__(self, name: str) -> Any:
        if name == "wrapped":
            raise AttributeError(name)
        attr = getattr(self.wrapped, name)
        if name in _STREAM_STEPS and inspect.isasyncgenfunction(attr):
            return self._stream(attr, _STREAM_STEPS[name])
        if name in _CALL_STEPS and inspect.iscoroutinefunction(attr):
            return self._call(attr, _CALL_STEPS[name])
        return attr

    def _stream(self, operation, step: str):
        provider = self.wrapped

        async def stream(*args, **kwargs):
            idle = STREAM_IDLE_SEC
            async with contextlib.aclosing(operation(*args, **kwargs)) as source:
                iterator = aiter(source)
                while True:
                    deadline = asyncio.timeout(idle)
                    try:
                        async with deadline:
                            item = await anext(iterator)
                    except StopAsyncIteration:
                        return
                    except TimeoutError:
                        if deadline.expired():
                            raise _stalled(provider, step, idle) from None
                        raise
                    yield item

        return stream

    def _call(self, operation, step: str):
        provider = self.wrapped

        async def call(*args, **kwargs):
            limit = CALL_DEADLINE_SEC
            deadline = asyncio.timeout(limit)
            try:
                async with deadline:
                    return await operation(*args, **kwargs)
            except TimeoutError:
                if deadline.expired():
                    raise _stalled(provider, step, limit) from None
                raise

        return call


def with_deadlines(provider: Any) -> Any:
    if provider is None or isinstance(provider, DeadlineProvider):
        return provider
    return DeadlineProvider(provider)


def unwrap(provider: Any) -> Any:
    """The provider underneath its deadlines."""
    return provider.wrapped if isinstance(provider, DeadlineProvider) else provider
