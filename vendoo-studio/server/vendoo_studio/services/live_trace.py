"""Let deep generation steps report what they are doing to the chat stream.

Photo analysis and sold-comps research sit several calls below the generate
run and return one finished result, so the chat saw nothing until their cards
landed. A generate run binds a sink here; the steps underneath call ``emit``
and the run relays it as SSE. With nothing bound, ``emit`` does nothing.

Work that runs beside other work (sold comps next to category selection, one
marketplace's fields next to another's) runs inside ``agent``. The sink then
gets ``agent`` events — one JSON object per change, the same shape every time::

    {"id": "comps-sold-3", "label": "Sold comps", "status": "running",
     "detail": "Claude searched: levis 501 sold", "started_at": 1760000000000,
     "ended_at": null}

``status`` is ``running``, ``done``, ``failed`` or ``cancelled``; times are epoch
milliseconds. A ``step`` emitted inside an agent becomes that agent's
``detail`` instead of a line in the main trace, and its ``thinking`` is dropped
so parallel reasoning does not interleave in one stream.

The sink and the current agent live in context variables, so tasks the run
spawns inherit them.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

log = logging.getLogger("vendoo_studio.chat")

Sink = Callable[[str, str], None]

_sink: ContextVar[Sink | None] = ContextVar("live_trace_sink", default=None)
_agent: ContextVar[Agent | None] = ContextVar("live_trace_agent", default=None)
_ids = itertools.count(1)
DETAIL_MAX_CHARS = 200


def bind(sink: Sink | None) -> None:
    """Route this task's (and its children's) trace into ``sink``."""
    _sink.set(sink)
    _agent.set(None)


def _send(sink: Sink | None, kind: str, text: str) -> None:
    if sink is None or not text:
        return
    try:
        sink(kind, text)
    except Exception:
        log.debug("live trace sink failed", exc_info=True)


def emit(kind: str, text: str) -> None:
    """Report ``text`` as a ``thinking`` delta or a finished ``step`` line."""
    if not text:
        return
    current = _agent.get()
    if current is not None:
        if kind == "step":
            current.update(text)
        return
    _send(_sink.get(), kind, text)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _one_line(text: str | None) -> str | None:
    if text is None:
        return None
    line = " ".join(str(text).split())
    if len(line) > DETAIL_MAX_CHARS:
        line = line[: DETAIL_MAX_CHARS - 1] + "…"
    return line or None


class Agent:
    """One unit of parallel work, as the seller sees it."""

    def __init__(self, key: str, label: str, sink: Sink | None) -> None:
        self.id = f"{key}-{next(_ids)}"
        self.label = label
        self.status = "running"
        self.detail: str | None = None
        self.started_at = _now_ms()
        self.ended_at: int | None = None
        self._sink = sink

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    def _publish(self) -> None:
        _send(self._sink, "agent", json.dumps(self.as_dict(), ensure_ascii=False))

    def update(self, detail: str | None) -> None:
        """Show progress; a no-op once the agent ended or when nothing changed."""
        line = _one_line(detail)
        if self.ended_at is not None or line is None or line == self.detail:
            return
        self.detail = line
        self._publish()

    def result(self, detail: str | None) -> None:
        """The one-line outcome shown once the agent is done."""
        line = _one_line(detail)
        if line is not None:
            self.detail = line

    def _end(self, status: str) -> None:
        self.status = status
        self.ended_at = _now_ms()
        self._publish()


@asynccontextmanager
async def agent(key: str, label: str) -> AsyncIterator[Agent]:
    """Run the body as a named agent: ``running`` now, then done/failed/cancelled.

    ``key`` names the kind of work ("comps-sold"); the id gets a unique suffix.
    Exceptions and cancellation are reported, then re-raised.
    """
    current = Agent(key, label, _sink.get())
    token = _agent.set(current)
    current._publish()
    try:
        yield current
    except asyncio.CancelledError:
        current._end("cancelled")
        raise
    except BaseException as exc:
        current.result(str(exc) or type(exc).__name__)
        current._end("failed")
        raise
    else:
        current._end("done")
    finally:
        _agent.reset(token)
