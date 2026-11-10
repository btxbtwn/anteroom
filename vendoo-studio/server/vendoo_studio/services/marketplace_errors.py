"""Report marketplace failures at transport boundaries without changing results or retries."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
import logging
from typing import Any, ParamSpec, TypeVar

from vendoo_studio.services.listing_result import scrub

log = logging.getLogger("vendoo_studio.marketplaces")
P = ParamSpec("P")
T = TypeVar("T")


def report_result(marketplace: str, operation: str, result: Any, *, depth: int = 0) -> None:
    """Inspect result envelopes only; never traverse listing, order or photo data."""
    if not isinstance(result, dict) or depth > 3:
        return
    errors = result.get("errors")
    status = result.get("statusCode")
    if (result.get("ok") is False or result.get("error") or errors
            or result.get("state") in ("failed", "unknown")
            or isinstance(status, int) and status >= 400):
        messages = [str(error.get("message") or error.get("longMessage") or "Marketplace error")
                    for error in errors if isinstance(error, dict)] if isinstance(errors, list) else []
        log.error("%s %s failed (%s): %s; %s", scrub(marketplace), scrub(operation),
                  scrub(result.get("error_code") or status or result.get("state") or "rejected"),
                  scrub(result.get("error") or "; ".join(messages) or "Unconfirmed or failed marketplace result"),
                  scrub(result.get("marketplace_message")) or "-")
    for key in ("result", "results", "responses"):
        children = result.get(key)
        if isinstance(children, list):
            for child in children:
                report_result(marketplace, operation, child, depth=depth + 1)
        elif isinstance(children, dict):
            report_result(marketplace, operation, children, depth=depth + 1)


def report_exception(marketplace: str, operation: str, exc: Exception) -> None:
    log.error("%s %s failed: %s", scrub(marketplace), scrub(operation), scrub(str(exc)), exc_info=exc)


def marketplace_call(marketplace: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Log caught transport exceptions and failure replies; call the operation exactly once."""
    def decorate(call: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(call)
        async def checked(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                result = await call(*args, **kwargs)
            except Exception as exc:
                report_exception(marketplace, call.__qualname__, exc)
                raise
            report_result(marketplace, call.__qualname__, result)
            return result
        return checked
    return decorate
