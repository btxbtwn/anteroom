"""Record slow response starts without logging private URLs or stream lifetimes."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from anteroom import request_phases
from anteroom.services.hosted_browser_relay import WAIT_S

log = logging.getLogger(__name__)
SLOW_SECONDS = 5
# Fields a route adds to its own slow-response line. Alert grouping replaces
# digits, so callers keep a constant set of names with numeric values: no
# identifiers, content or free text, and no labels that would split a group.
_details: ContextVar[dict[str, int | str] | None] = ContextVar("request_timing_details", default=None)


def note(**fields: int | str) -> None:
    """Attach bounded timing fields to this request's slow-response line."""
    details = _details.get()
    if details is not None:
        details.update(fields)


@contextmanager
def reply_step(name: str):
    """Time one allowlisted await, including scheduling/network work, not SQL.

    Cancellation and exceptions propagate unchanged. Only fixed numeric fields
    join the existing slow-response line; no new I/O or per-request log.
    """
    if name not in ('lookup', 'persist'):
        yield
        return
    started = perf_counter()
    note(**{f'relay_{name}_calls': 1})
    try:
        yield
    finally:
        note(**{f'relay_{name}_ms': min(round(max(0.0, perf_counter() - started) * 1000), 600000)})


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        details: dict[str, int | str] = {}

        phases = request_phases.Phases()
        failed = True

        async def timed_send(message):
            nonlocal failed
            if message["type"] == "http.response.start":
                failed = message["status"] >= 400
                elapsed = time.perf_counter() - started
                route = getattr(scope.get("route"), "path", "unmatched")
                allowance = SLOW_SECONDS
                # A command arriving during the poll wait returns 200; an empty wait returns 204.
                if scope["method"] == "GET" and route == "/api/browser-relay/next" and message["status"] in (200, 204):
                    allowance += WAIT_S
                if elapsed >= allowance:
                    details.update(phases.snapshot())
                    log.warning("Slow response: %s %s %s %dms%s", scope["method"], route,
                                message["status"], round(elapsed * 1000),
                                "".join(f" {name}={value}" for name, value in details.items()))
            await send(message)

        token = _details.set(details)
        phase_token = request_phases.current.set(phases)
        try:
            await self.app(scope, receive, timed_send)
        except Exception:
            failed = True
            raise
        finally:
            if failed:
                scope["anteroom_request_phases"] = phases.snapshot()
            request_phases.current.reset(phase_token)
            _details.reset(token)
