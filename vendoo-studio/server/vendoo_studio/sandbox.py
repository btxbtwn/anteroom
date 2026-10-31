"""Sandbox mode: the whole app on throwaway data, with nothing leaving the machine.

``ANTEROOM_SANDBOX=1`` turns it on (``./sandbox.sh`` sets it). In sandbox mode:

- secrets live in an in-memory keyring, never the login Keychain;
- every outbound connection that is not loopback is refused, whatever library
  makes it, so no marketplace, Vendoo, relay or AI provider is ever reached;
- listing generation and chat use ``SandboxProvider``, which answers the same
  way every time;
- background timers that crawl suppliers or act on marketplaces stay off;
- routes that would publish, delist, update or delete a live listing answer 403,
  and so do routes that start git, Chrome, tailscale or a provider sign-in.

Agents and tests use it to reproduce bugs and check changes end to end before
anything reaches ``main``.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket

import httpx
from keyring.backend import KeyringBackend

ENV_VAR = "ANTEROOM_SANDBOX"
LISTING_DETAIL = "Sandbox mode never publishes, delists, updates or deletes a marketplace listing."
OFFLINE_DETAIL = "Sandbox mode stays offline: no app updates, Chrome launch, Tailscale or provider sign-in."
BLOCKED_DETAIL = "Sandbox mode blocks outbound connections"

# POST routes that change a live listing on Vendoo or a marketplace.
_LISTING_POSTS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/conversations/[^/]+/vendoo-api/(list|delist)$",
        r"^/api/vendoo-api/(update|delete)$",
        r"^/api/conversations/[^/]+/native-marketplaces/[^/]+/(list|update)$",
        r"^/api/conversations/[^/]+/marketplace-listings/delist$",
        r"^/api/listings/marketplace-actions/perform$",
        r"^/api/ebay/actions/confirm$",
        r"^/api/ebay-offers/send$",
        r"^/api/promotions/[^/]+/perform$",
    )
)

# Routes, any method, that start another program which would reach the
# network or the seller's real browser: git for updates, Chrome, the
# tailscale and claude command line tools.
_OFFLINE_ROUTES = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/updates(/.*)?$",
        r"^/api/desktop/chrome/connect$",
        r"^/api/settings/tailscale/(enable|disable)$",
        r"^/api/settings/chatgpt/login$",
        r"^/api/settings/claude/terminal$",
    )
)


def enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"


def refusal(method: str, path: str) -> str | None:
    """Why sandbox mode refuses this request, or None when it may run."""
    clean = path.split("?", 1)[0].rstrip("/")
    if method.upper() == "POST" and any(pattern.match(clean) for pattern in _LISTING_POSTS):
        return LISTING_DETAIL
    if any(pattern.match(clean) for pattern in _OFFLINE_ROUTES):
        return OFFLINE_DETAIL
    return None


_installed = False


class MemoryKeyring(KeyringBackend):
    """A keyring that forgets everything when the process ends."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def _is_loopback(host: str) -> bool:
    host = host.strip("[]").split("%", 1)[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guard_address(address) -> None:
    # Unix sockets pass a path; only internet addresses are checked.
    if isinstance(address, tuple) and address and not _is_loopback(str(address[0])):
        raise ConnectionRefusedError(f"{BLOCKED_DETAIL}: {address[0]}")


def _block_outbound() -> None:
    sync_send = httpx.HTTPTransport.handle_request
    async_send = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        if not _is_loopback(request.url.host):
            raise httpx.ConnectError(f"{BLOCKED_DETAIL}: {request.url.host}", request=request)
        return sync_send(self, request)

    async def handle_async_request(self, request):
        if not _is_loopback(request.url.host):
            raise httpx.ConnectError(f"{BLOCKED_DETAIL}: {request.url.host}", request=request)
        return await async_send(self, request)

    httpx.HTTPTransport.handle_request = handle_request
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request

    # Backstop for urllib, websockets and anything else that opens a socket.
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        _guard_address(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        _guard_address(address)
        return connect_ex(self, address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex


def install() -> None:
    """Swap in the in-memory keyring and block outbound connections. Idempotent."""
    global _installed
    if _installed:
        return
    import keyring

    keyring.set_keyring(MemoryKeyring())
    _block_outbound()
    _installed = True
