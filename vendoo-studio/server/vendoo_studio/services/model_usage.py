"""Per-request provider metrics; never retain prompts, replies, credentials or headers."""
from __future__ import annotations

import asyncio
import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_calls: ContextVar[list[dict] | None] = ContextVar("model_calls", default=None)


def bind() -> list[dict]:
    calls: list[dict] = []
    _calls.set(calls)
    return calls


def current() -> list[dict] | None:
    return _calls.get()


@contextmanager
def capture():
    calls: list[dict] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def _number(value, *, integer: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return int(value) if integer else float(value)


class Call:
    def __init__(self, provider: str, model: str):
        self.record = {
            "provider": provider, "model": model, "started_at": datetime.now(UTC).isoformat(),
            "outcome": "running", "latency_ms": None, "input_tokens": None, "output_tokens": None,
            "cached_input_tokens": None, "cache_write_input_tokens": None,
            "reasoning_tokens": None, "cost_usd": None, "cost_source": None,
        }
        self.raw: dict = {}

    def usage(self, raw: dict | None, *, anthropic: bool = False, sdk_cost=None):
        if not isinstance(raw, dict):
            raw = {}
        # Only known numeric counters enter the record. Provider metadata,
        # response ids and any unexpected fields are deliberately ignored.
        for key in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens", "cost"):
            value = _number(raw.get(key))
            if value is not None:
                self.raw[key] = value
        source = self.raw
        input_count = source.get("input_tokens", source.get("prompt_tokens"))
        cached = source.get("cache_read_input_tokens")
        written = source.get("cache_creation_input_tokens")
        details = raw.get("input_tokens_details") or raw.get("prompt_tokens_details") or {}
        if isinstance(details, dict) and _number(details.get("cached_tokens")) is not None:
            cached = details["cached_tokens"]
        if anthropic and input_count is not None:
            input_count += (cached or 0) + (written or 0)
        for key, value in (("input_tokens", input_count), ("output_tokens", source.get("output_tokens", source.get("completion_tokens"))),
                           ("cached_input_tokens", cached), ("cache_write_input_tokens", written)):
            if value is not None:
                self.record[key] = _number(value, integer=True)
        details = raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
        if isinstance(details, dict) and _number(details.get("reasoning_tokens")) is not None:
            self.record["reasoning_tokens"] = _number(details["reasoning_tokens"], integer=True)
        sdk_price = _number(sdk_cost)
        if sdk_price is not None:
            self.record.update(cost_usd=float(sdk_price), cost_source="sdk_estimate")
        elif "cost" in source:
            self.record.update(cost_usd=source["cost"], cost_source="provider_reported")


@contextmanager
def observe(provider: str, model: str):
    call = Call(provider, model)
    calls = current()
    if calls is not None:
        calls.append(call.record)
    start = time.monotonic()
    try:
        yield call
    except (asyncio.CancelledError, GeneratorExit):
        call.record["outcome"] = "cancelled"
        raise
    except BaseException:
        call.record["outcome"] = "failed"
        raise
    else:
        call.record["outcome"] = "done"
    finally:
        call.record["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
