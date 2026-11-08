"""Reject browser requests from outside the local app and its private proxy."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from vendoo_studio.config import CORS_ORIGINS

_EXTENSION = re.compile(r"chrome-extension://[a-p]{32}")
_private_origin: str | None = None


def set_private_origin(origin: str | None) -> None:
    global _private_origin
    _private_origin = origin


def origin_allowed(origin: str, *, extension: bool = False) -> bool:
    return origin in CORS_ORIGINS or origin == _private_origin or (extension and bool(_EXTENSION.fullmatch(origin)))


_SIGN_IN_RETURN = re.compile(r"/api/marketplace-accounts/(ebay|etsy)/finish")


def _returning_from_sign_in(scope) -> bool:
    return scope["type"] == "http" and scope.get("method") == "GET" and bool(_SIGN_IN_RETURN.fullmatch(scope.get("path", "")))


class LocalRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        try:
            parsed = urlsplit(f"http://{host}")
            valid_host = (parsed.netloc == host and not parsed.username and not parsed.password
                          and parsed.port != 0 and len(headers.getlist("host")) == 1
                          and parsed.hostname in {"127.0.0.1", "localhost"})
            if _private_origin and host == urlsplit(_private_origin).netloc and len(headers.getlist("host")) == 1:
                valid_host = True
        except ValueError:
            valid_host = False
        origin = headers.get("origin")
        trusted_origin = origin is not None and origin_allowed(origin, extension=True)
        rejected = not valid_host or (origin is not None and not trusted_origin)
        rejected = rejected or (headers.get("sec-fetch-site") in {"cross-site", "same-site"} and not trusted_origin)
        # The relay sends the seller's browser back here after an eBay or Etsy
        # sign-in. That page only finishes a sign-in this Mac started, and only
        # with the one-time claim the relay gave that browser.
        if rejected and valid_host and _returning_from_sign_in(scope):
            rejected = False
        if rejected:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Request must come from Anteroom's trusted address."},
                                   status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
