"""The seller's own Chrome on the web: their signed-in Anteroom tab relays to their Bridge.

The hosted API cannot reach a browser. Instead, the seller's open Anteroom page
asks for the next command meant for its Chrome, hands it to the Bridge through
the connected content script, and posts the Bridge's reply back. Each seller has
their own relay, so a command only ever reaches the browser of the account that
started it.

Only the marketplace commands a seller starts go through here: an approved
List, Update or Save draft (``marketplace.execute``), reading what one did
(``marketplace.result``), a confirmed Delist (``marketplace.delete``), and
cancelling one (``job.cancel``). A command waits in the relay only while the
tab is answering; when no tab has asked recently, Chrome counts as
disconnected and the action is refused before it starts.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque

from vendoo_studio.services.extension_connection import ReplyWaits

COMMANDS = frozenset({"marketplace.execute", "marketplace.result", "marketplace.delete", "job.cancel"})
# The Bridge's declared capability for running relayed marketplace commands.
CAPABILITY = "marketplace.actions"
# The page asks again as soon as it answers, and at least this often while idle.
SEEN_FOR_S = 30.0


class BrowserRelay(ReplyWaits):
    def __init__(self):
        super().__init__()
        self._outbox: deque[dict] = deque()
        self._ready = asyncio.Event()
        self._seen_at = 0.0
        self._capable = False

    @property
    def connected(self) -> bool:
        return self._capable and time.monotonic() - self._seen_at < SEEN_FOR_S

    def marketplace_actions_refusal(self) -> str | None:
        if time.monotonic() - self._seen_at >= SEEN_FOR_S:
            return "Open Anteroom in Chrome on your computer and connect Chrome first."
        if not self._capable:
            return "Update Anteroom Bridge in Chrome to 0.2.40 or later to list on this marketplace from the web."
        return None

    async def send_message(self, message: dict) -> bool:
        if message.get("type") not in COMMANDS or not self.connected:
            return False
        self._outbox.append(message)
        self._ready.set()
        return True

    async def next_message(self, capabilities: set[str], timeout: float) -> dict | None:
        """The next command for this seller's Chrome, waiting up to ``timeout`` seconds."""
        self._seen_at = time.monotonic()
        self._capable = CAPABILITY in capabilities
        deadline = time.monotonic() + timeout
        while not self._outbox:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._ready.clear()
            try:
                await asyncio.wait_for(self._ready.wait(), remaining)
            except TimeoutError:
                return None
            finally:
                self._seen_at = time.monotonic()
        return self._outbox.popleft()


_relays: dict[str, BrowserRelay] = {}
_lock = threading.Lock()


def relay_for(seller_id: str) -> BrowserRelay:
    with _lock:
        return _relays.setdefault(seller_id, BrowserRelay())
