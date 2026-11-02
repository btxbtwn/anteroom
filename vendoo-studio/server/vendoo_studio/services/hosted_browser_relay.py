"""The seller's own Chrome on the web: their signed-in Anteroom tab relays to their Bridge.

The hosted API cannot reach a browser. Instead, the seller's open Anteroom page
asks for the next command meant for its Chrome, hands it to the Bridge through
the connected content script, and posts the Bridge's reply back. Each seller has
their own relay, so a command only ever reaches the browser of the account that
started it.

Only the marketplace commands a seller starts go through here: an approved
List, Update or Save draft (``marketplace.execute``), reading what one did
(``marketplace.result``), a confirmed Delist (``marketplace.delete``), and
cancelling one (``job.cancel``). Besides those, a few named reads fill the
seller's editor with their marketplace account's own choices, the ones the Mac
reads in its private account windows (Vinted's categories and brands). Each is
a fixed command with fixed inputs; none takes a URL or a script. A command
waits in the relay only while the tab is answering; when no tab has asked
recently, Chrome counts as disconnected and the action is refused before it
starts.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque

from vendoo_studio.services.extension_connection import MARKETPLACE_ACTIONS, MARKETPLACE_READS, ReplyWaits

# Each command, and the capability the Bridge must declare to receive it.
COMMANDS = {"marketplace.execute": MARKETPLACE_ACTIONS, "marketplace.result": MARKETPLACE_ACTIONS,
            "marketplace.delete": MARKETPLACE_ACTIONS, "job.cancel": MARKETPLACE_ACTIONS,
            "marketplace.vinted_categories": MARKETPLACE_READS, "marketplace.vinted_brands": MARKETPLACE_READS}
# A relayed command's fields (``CloudMarketplaceProtocol.command`` in the Bridge).
CONTRACT = ("type", "job_id", "message_id", "payload")
# The page asks again as soon as it answers, and at least this often while idle.
SEEN_FOR_S = 30.0


class BrowserRelay(ReplyWaits):
    def __init__(self):
        super().__init__()
        self._outbox: deque[dict] = deque()
        self._ready = asyncio.Event()
        self._seen_at = 0.0
        self._capabilities: frozenset[str] = frozenset()

    def _can(self, capability: str) -> bool:
        return capability in self._capabilities and time.monotonic() - self._seen_at < SEEN_FOR_S

    @property
    def connected(self) -> bool:
        return self._can(MARKETPLACE_ACTIONS)

    def refusal(self, capability: str = MARKETPLACE_ACTIONS) -> str | None:
        """Why this seller's Chrome cannot take a command needing ``capability`` now, or None."""
        if time.monotonic() - self._seen_at >= SEEN_FOR_S:
            return "Open Anteroom in Chrome on your computer and connect Chrome first."
        if capability not in self._capabilities:
            return ("Update Anteroom Bridge in Chrome to 0.2.42 or later to read these choices from the web."
                    if capability == MARKETPLACE_READS else
                    "Update Anteroom Bridge in Chrome to 0.2.40 or later to list on this marketplace from the web.")
        return None

    async def send_message(self, message: dict) -> bool:
        capability = COMMANDS.get(str(message.get("type")))
        if capability is None or not self._can(capability):
            return False
        # The page and Bridge take exactly these fields; the socket envelope's others stay here.
        command = {key: message.get(key) for key in CONTRACT}
        if command["type"] == "job.cancel":
            # Nothing waits for a cancel's reply, but the page still needs an id to carry it by.
            request_id = uuid.uuid4().hex
            command.update(message_id=request_id, payload={**(command["payload"] or {}), "request_id": request_id})
        self._outbox.append(command)
        self._ready.set()
        return True

    async def next_message(self, capabilities: set[str], timeout: float) -> dict | None:
        """The next command for this seller's Chrome, waiting up to ``timeout`` seconds."""
        self._seen_at = time.monotonic()
        self._capabilities = frozenset(capabilities)
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

    async def read(self, command: str, payload: dict, timeout: float) -> dict:
        """One named read in the seller's Chrome, and the Bridge's reply. Raises
        ``ChromeReadError`` when Chrome cannot take it or does not answer in time."""
        if COMMANDS.get(command) != MARKETPLACE_READS:
            raise ValueError(f"{command} is not a relayed read.")
        if refusal := self.refusal(MARKETPLACE_READS):
            raise ChromeReadError(refusal)
        request_id = uuid.uuid4().hex
        waiter = self.register_wait(request_id)
        if not await self.send_message({"type": command, "job_id": None, "message_id": request_id,
                                        "payload": {**payload, "request_id": request_id}}):
            self.cancel_wait(request_id)
            raise ChromeReadError("Open Anteroom in Chrome on your computer and connect Chrome first.")
        try:
            reply = await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            raise ChromeReadError("Chrome did not answer in time. Try again.") from None
        finally:
            self.cancel_wait(request_id)
        return reply if isinstance(reply, dict) else {"ok": False}


class ChromeReadError(RuntimeError):
    pass


_relays: dict[str, BrowserRelay] = {}
_lock = threading.Lock()


def relay_for(seller_id: str) -> BrowserRelay:
    with _lock:
        return _relays.setdefault(seller_id, BrowserRelay())
