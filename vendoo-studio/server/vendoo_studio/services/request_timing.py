"""Record slow response starts without logging private URLs or stream lifetimes."""
import logging
import time

log = logging.getLogger(__name__)
SLOW_SECONDS = 5


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                elapsed = time.perf_counter() - started
                if elapsed >= SLOW_SECONDS:
                    route = getattr(scope.get("route"), "path", "unmatched")
                    log.warning("Slow response: %s %s %s %dms", scope["method"], route,
                                message["status"], round(elapsed * 1000))
            await send(message)

        await self.app(scope, receive, timed_send)
