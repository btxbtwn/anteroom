"""Reject browser requests from outside the local app and its private proxy."""
from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from vendoo_studio.config import CORS_ORIGINS, LEGACY_APP_NAME, extension_source_dir, user_data_root

logger = logging.getLogger(__name__)

# Anteroom Bridge on the Chrome Web Store.
STORE_BRIDGE_ID = "empinkdlhndpfoigoipbiiofaeepcheo"
# Further Bridge IDs, comma-separated, for a developer who loads the extension
# unpacked from a folder Anteroom does not know about.
EXTRA_IDS_ENV = "ANTEROOM_BRIDGE_EXTENSION_IDS"
_EXTENSION = re.compile(r"chrome-extension://([a-p]{32})")
_refused_extensions: set[str] = set()
_private_origin: str | None = None
# The Tailscale login of the person this Mac belongs to. Tailscale Serve names
# the tailnet user behind each request in Tailscale-User-Login and strips any
# copy the client sent. Tagged devices get no identity, so they are refused,
# and so is everyone when this Mac is itself tagged (it has no owner login).
_private_owner: str | None = None
_IDENTITY_HEADER = "tailscale-user-login"


def set_private_origin(origin: str | None, owner_login: str | None = None) -> None:
    global _private_origin, _private_owner
    _private_origin = origin
    _private_owner = owner_login if origin else None


def unpacked_extension_id(path: str) -> str:
    """The ID Chrome gives an extension loaded unpacked from ``path``."""
    digest = hashlib.sha256(path.encode()).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


@functools.cache
def trusted_extension_ids() -> frozenset[str]:
    """Anteroom Bridge: the store copy, and the unpacked copies this Mac loads.

    Chrome derives an unpacked extension's ID from the real path it was loaded
    from, and keeps that ID when the folder later moves. Anteroom has Chrome
    load its copy from the data folder (from the old-named folder, before the
    rename), and a checkout loads the repository's ``vendoo-extension``.
    """
    from vendoo_studio.services.chrome_bridge import installed_extension_dir

    installed = installed_extension_dir()
    source = extension_source_dir()
    folders = {installed, installed.resolve(), source, source.resolve(),
               user_data_root().with_name(LEGACY_APP_NAME) / installed.name}
    ids = {STORE_BRIDGE_ID, *(unpacked_extension_id(str(folder)) for folder in folders)}
    extra = (part.strip() for part in os.environ.get(EXTRA_IDS_ENV, "").split(","))
    ids.update(part for part in extra if re.fullmatch(r"[a-p]{32}", part))
    return frozenset(ids)


def _trusted_extension(origin: str) -> bool:
    match = _EXTENSION.fullmatch(origin)
    if not match:
        return False
    if match.group(1) in trusted_extension_ids():
        return True
    if match.group(1) not in _refused_extensions:
        _refused_extensions.add(match.group(1))
        logger.warning("Refused Chrome extension %s: it is not Anteroom Bridge. A developer's own "
                       "unpacked Bridge can be allowed with %s.", match.group(1), EXTRA_IDS_ENV)
    return False


def origin_allowed(origin: str, *, extension: bool = False) -> bool:
    return origin in CORS_ORIGINS or origin == _private_origin or (extension and _trusted_extension(origin))


def _from_tailnet_owner(headers: Headers) -> bool:
    logins = headers.getlist(_IDENTITY_HEADER)
    return bool(_private_owner and len(logins) == 1 and logins[0].casefold() == _private_owner.casefold())


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
                valid_host = _from_tailnet_owner(headers)
        except ValueError:
            valid_host = False
        # Only Tailscale Serve sets this header, so whatever Host such a request
        # names, it crossed the tailnet and must come from the Mac's owner.
        if _IDENTITY_HEADER in headers and not _from_tailnet_owner(headers):
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
