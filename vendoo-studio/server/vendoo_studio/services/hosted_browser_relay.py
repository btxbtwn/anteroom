"""The seller's own Chrome on the web: their signed-in Anteroom tab relays to their Bridge.

The hosted API cannot reach a browser. Instead, the seller's open Anteroom page
asks for the next command meant for its Chrome, hands it to the Bridge through
the connected content script, and posts the Bridge's reply back. Each seller has
their own relay, so a command only ever reaches the browser of the account that
started it.

Only the marketplace commands a seller starts go through here: an approved
List, Update or Save draft (``marketplace.execute``), reading what one did
(``marketplace.result``), a confirmed Delist (``marketplace.delete``), and
cancelling one (``job.cancel``). Each command carries a signed approval
(``bridge_approvals``) that the Bridge checks before it runs it (an older Bridge, writes only). Besides those, a few named commands do what
the Mac does outside its listing actions: reads that fill the seller's editor
the sign-in status and Sign in of Settings → Connections, and the Chrome
inventory import's reads of the seller's own active listings. Each is a fixed
command with fixed inputs; none takes a URL or a
script. A command waits in the relay only while the tab is answering; when no
tab has asked recently, Chrome counts as disconnected and the action is refused
before it starts.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque

from vendoo_studio.services import bridge_approvals
from vendoo_studio.services.marketplace_diagnostics import failure_context
from vendoo_studio.services.marketplace_errors import report_exception, report_result
from vendoo_studio.services.extension_connection import MARKETPLACE_ACTIONS, ReplyWaits

logger = logging.getLogger(__name__)

# Each named read has its own Bridge capability, so a later read never changes
# what an existing name promises (docs/cloud-extension.md).
# Checking which marketplaces Chrome is signed in to, and opening one's sign-in page there.
MARKETPLACE_SESSIONS = "marketplace.sessions"
# Reading the seller's own active listings to import them.
MARKETPLACE_IMPORT = "marketplace.import"
SALES_HISTORY = "marketplace.sales_history"
MARKETPLACE_PROMOTIONS = "marketplace.promotions"
# The Bridge version that first declares each capability, and what it lets the web do.
NEEDS = {"vendoo.drafts": ("0.2.55", "save Vendoo drafts"), MARKETPLACE_ACTIONS: ("0.2.40", "list on this marketplace"),
         MARKETPLACE_SESSIONS: ("0.2.44", "check marketplace sign-in"),
         MARKETPLACE_IMPORT: ("0.2.53", "import your listings"),
         SALES_HISTORY: ("0.2.47", "read marketplace orders"),
         MARKETPLACE_PROMOTIONS: ("0.2.52", "share listings and send offers")}

# Each command, and the capability the Bridge must declare to receive it.
COMMANDS = {"job.vendoo_api": "vendoo.drafts", "marketplace.execute": MARKETPLACE_ACTIONS, "marketplace.result": MARKETPLACE_ACTIONS,
            "marketplace.delete": MARKETPLACE_ACTIONS, "job.cancel": MARKETPLACE_ACTIONS,
            "marketplace.sessions": MARKETPLACE_SESSIONS, "marketplace.connect": MARKETPLACE_SESSIONS,
            "marketplace.import.page": MARKETPLACE_IMPORT, "marketplace.import.listing": MARKETPLACE_IMPORT,
            "marketplace.sales_history": SALES_HISTORY,
            "marketplace.promotion_check": MARKETPLACE_PROMOTIONS, "marketplace.promote": MARKETPLACE_PROMOTIONS,
            "marketplace.promotion_result": MARKETPLACE_PROMOTIONS, "marketplace.like_events": MARKETPLACE_PROMOTIONS}
# A relayed command's fields (``CloudMarketplaceProtocol.command`` in the Bridge).
CONTRACT = ("type", "job_id", "message_id", "payload")
# How long a tab stays connected after it last asked or beat. A background tab's
# timers can run as rarely as once a minute in Chrome, so this outlasts that.
SEEN_FOR_S = 90.0
# Empty /next requests deliberately wait this long for a command.
WAIT_S = 20.0
# Commands whose timing is kept until their reply; older ones are dropped.
SENT_KEPT = 200


class BrowserRelay(ReplyWaits):
    def __init__(self, owner: str | None = None):
        super().__init__()
        self.owner = owner
        self._outbox: deque[dict] = deque()
        self._ready = asyncio.Event()
        self._seen_at = 0.0
        self._capabilities: frozenset[str] = frozenset()
        self._loop: asyncio.AbstractEventLoop | None = None
        # When each job's command was queued and handed to the page, by message id.
        self._sent: dict[str, dict] = {}

    def _can(self, capability: str) -> bool:
        return capability in self._capabilities and time.monotonic() - self._seen_at < SEEN_FOR_S

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def connected(self) -> bool:
        if self._can(MARKETPLACE_ACTIONS):
            return True
        from vendoo_studio.services.device_routes import remote_fresh
        return remote_fresh(self.owner, 'chrome')

    def note_seen(self, capabilities: set[str]) -> None:
        self._seen_at = time.monotonic()
        self._capabilities = frozenset(capabilities)

    def refusal(self, capability: str = MARKETPLACE_ACTIONS) -> str | None:
        """Why this seller's Chrome cannot take a command needing ``capability`` now, or None."""
        if self._local_refusal(capability) is None:
            return None
        from vendoo_studio.services.device_routes import remote_fresh
        if remote_fresh(self.owner, 'chrome'):
            return None
        return self._local_refusal(capability)

    def _local_refusal(self, capability: str) -> str | None:
        if time.monotonic() - self._seen_at >= SEEN_FOR_S:
            return "Open Anteroom in Chrome on your computer and connect Chrome first."
        if capability not in self._capabilities:
            version, purpose = NEEDS[capability]
            return f"Update Anteroom Bridge in Chrome to {version} or later to {purpose} from the web."
        return None

    def resolve_wait(self, request_id: str, payload: dict) -> None:
        # Timed promotions wait on the background loop, while the tab replies
        # on the web loop. A future must be completed on the loop that owns it.
        future = self._waits.get(request_id)
        if future is not None and not future.get_loop().is_closed():
            future.get_loop().call_soon_threadsafe(super().resolve_wait, request_id, payload)

    async def deliver_local(self, message: dict) -> bool:
        loop = self._loop
        if loop is not None and loop is not asyncio.get_running_loop():
            if loop.is_closed():
                return False
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.deliver_local(message), loop))
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
        if command["job_id"] and command["message_id"]:
            while len(self._sent) >= SENT_KEPT:
                self._sent.pop(next(iter(self._sent)))
            self._sent[command["message_id"]] = {"job_id": command["job_id"], "type": command["type"],
                                                  "queued": time.monotonic()}
        self._ready.set()
        return True

    def timing(self, request_id: str) -> dict | None:
        """How long a job's command waited for the page and for Chrome's reply, in ms. Once per command."""
        sent = self._sent.pop(request_id, None)
        if sent is None:
            return None
        now = time.monotonic()
        delivered = sent.get("delivered", now)
        return {"job_id": sent["job_id"], "type": sent["type"], "queued_ms": round((delivered - sent["queued"]) * 1000),
                "total_ms": round((now - sent["queued"]) * 1000)}

    async def send_message(self, message: dict) -> bool:
        if await self.deliver_local(message):
            return True
        from vendoo_studio.services.device_routes import RoutingUnavailable, forward_target, post
        try:
            target = await forward_target(self.owner, 'chrome')
        except RoutingUnavailable:
            return False
        if target is None:
            return False
        try:
            payload = await post(target, '/internal/browser-relay/send',
                                 {'user_id': self.owner, 'message': message}, timeout=10)
        except RoutingUnavailable:
            return False
        return payload.get('sent') is True

    async def next_message(self, capabilities: set[str], timeout: float) -> dict | None:
        """The next command for this seller's Chrome, waiting up to ``timeout`` seconds."""
        self.note_seen(capabilities)
        self._loop = asyncio.get_running_loop()
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
        command = self._outbox.popleft()
        if (sent := self._sent.get(str(command.get("message_id")))) is not None:
            sent["delivered"] = time.monotonic()
        # Signed as the page takes it, so the approval's few minutes start now.
        photos = None
        if (bridge_approvals.signs_every_command(self._capabilities)
                and command.get("type") in bridge_approvals.PHOTO_COMMANDS and command.get("job_id")):
            try:
                photos = await approved_photo_hashes(str(command["job_id"]))
            except asyncio.CancelledError:
                # The page went away while the photos were read: the next poll takes the command.
                self._outbox.appendleft(command)
                raise
        return bridge_approvals.signed(self.owner, command, self._capabilities, photos)

    async def read_local(self, command: str, payload: dict, timeout: float) -> dict:
        """One named command other than a marketplace action, run in the seller's Chrome,
        and the Bridge's reply. Raises ``ChromeReadError`` when Chrome cannot take it or
        does not answer in time."""
        capability = COMMANDS.get(command)
        if capability is None or capability == MARKETPLACE_ACTIONS or command == "marketplace.promote":
            raise ValueError(f"{command} is not a relayed read.")
        if refusal := self._local_refusal(capability):
            raise ChromeReadError(refusal)
        loop = self._loop
        if loop is not None and loop is not asyncio.get_running_loop():
            if loop.is_closed():
                raise ChromeReadError("Open Anteroom in Chrome and connect Chrome first.")
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.read(command, payload, timeout), loop))
        request_id = uuid.uuid4().hex
        waiter = self.register_wait(request_id)
        if not await self.deliver_local({"type": command, "job_id": None, "message_id": request_id,
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

    async def request_local(self, message: dict, timeout: float) -> dict:
        """Send one command and wait for Chrome's reply on this process."""
        request_id = message.get("message_id")
        if not isinstance(request_id, str):
            raise ChromeReadError("Chrome did not answer in time. Try again.")
        waiter = self.register_wait(request_id, message.get("job_id"))
        if not await self.deliver_local(message):
            self.cancel_wait(request_id)
            raise ChromeReadError("Open Anteroom in Chrome on your computer and connect Chrome first.")
        try:
            result = await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            raise ChromeReadError("Chrome did not answer in time. Try again.") from None
        finally:
            self.cancel_wait(request_id)
        return result if isinstance(result, dict) else {"ok": False}

    async def read(self, command: str, payload: dict, timeout: float) -> dict:
        started = time.monotonic()
        marketplace = str(payload.get('marketplace') or 'chrome')
        try:
            result = await self._read(command, payload, timeout)
        except Exception as exc:
            report_exception(marketplace, command, exc, context=failure_context(
                command, payload, None, time.monotonic() - started, timeout))
            raise
        report_result(marketplace, command, result, context=failure_context(
            command, payload, result, time.monotonic() - started, timeout))
        return result

    async def _read(self, command: str, payload: dict, timeout: float) -> dict:
        try:
            return await self.read_local(command, payload, timeout)
        except ChromeReadError as local_error:
            from vendoo_studio.services.device_routes import RoutingUnavailable, forward_target, post
            try:
                target = await forward_target(self.owner, 'chrome')
            except RoutingUnavailable:
                raise local_error from None
            if target is None:
                raise
            try:
                body = await post(target, '/internal/browser-relay/read', {
                    'user_id': self.owner, 'command': command, 'payload': payload, 'timeout': timeout,
                }, timeout=timeout + 5)
            except RoutingUnavailable:
                raise local_error from None
            if not body.get('ok'):
                raise ChromeReadError(body.get('error') or 'Chrome did not answer in time. Try again.') from None
            reply = body.get('reply')
            return reply if isinstance(reply, dict) else {"ok": False}


class ChromeReadError(RuntimeError):
    pass


async def approved_photo_hashes(job_id: str) -> list[dict]:
    """The SHA-256 of each photo approved for ``job_id``, which the Bridge checks the relayed bytes against.

    When the database cannot answer, the approval names no photos, so the Bridge
    refuses the command if photos come with it and the seller can try again."""
    from sqlalchemy.exc import SQLAlchemyError

    from vendoo_studio.database import in_worker_session
    try:
        return await in_worker_session(bridge_approvals.photo_hashes, job_id)
    except SQLAlchemyError:
        logger.warning("Could not read the approved photos of job %s to sign its command.", job_id, exc_info=True)
        return []


_relays: dict[str, BrowserRelay] = {}
_lock = threading.Lock()


def relay_for(seller_id: str) -> BrowserRelay:
    with _lock:
        relay = _relays.get(seller_id)
        if relay is None:
            relay = BrowserRelay(seller_id)
            _relays[seller_id] = relay
        return relay

def ready_sellers(capability: str) -> list[str]:
    """Sellers with a recently connected tab and the required Bridge capability."""
    with _lock:
        relays = list(_relays.items())
    return [seller for seller, relay in relays if relay.refusal(capability) is None]
