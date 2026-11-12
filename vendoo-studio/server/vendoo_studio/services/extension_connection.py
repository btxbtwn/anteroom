from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from vendoo_studio.config import PAIRING_FILE
from vendoo_studio.models.protocol import ProtocolMessage
from vendoo_studio.database import SessionLocal
from vendoo_studio.services.chrome_bridge import (
    ChromeBridgeError,
    clear_extension_reload_pending,
    extension_build_status,
    extension_in_lockstep,
    extension_reload_token_if_needed,
    install_bundled_extension,
    pending_extension_reload_token,
)

log = logging.getLogger("vendoo_studio.extension")

# A message is a few KB. A send that has not gone out in this long is waiting on
# an extension that stopped reading, and would otherwise wait forever.
SEND_TIMEOUT_SEC = 5.0
# A worker that pairs again this soon after the last pairing is flapping rather
# than reconnecting: reconciling direct listings on every one of those ticks
# keeps Chrome busy in a loop.
RECONNECT_SETTLE_SEC = 5.0
# A socket that drops this fast never held a conversation, so say so in the log.
FLAP_WARN_SEC = 10.0
# Two copies of the bridge (the app's folder plus an older Anteroom data folder,
# or a checkout loaded by hand) both pair. Each pairing used to close the
# other's socket, so they closed each other every half second and the replies in
# flight went with them: a form read failed and a listing check never finished.
DUPLICATE_BRIDGE = (
    "Another copy of the Anteroom Bridge is loaded in Chrome. Remove it in chrome://extensions, "
    "keep the copy Settings → Connections names, then reload that one."
)
# How long a refusal keeps saying so, so a seller who opens Settings sees it.
DUPLICATE_REPORT_SEC = 300.0
# What a Bridge must declare before Anteroom sends it a List, Update, Save
# draft or Delist. Any build that declares it may run them, so Chrome does not
# need the exact copy this app ships. A change an older Bridge cannot handle
# gets a new name here and in the Bridge's capabilities.
MARKETPLACE_ACTIONS = "marketplace.actions"


def _ws_origin_allowed(origin: str) -> bool:
    from vendoo_studio.services.local_requests import origin_allowed

    value = (origin or "").strip()
    if not value:
        return False
    return origin_allowed(value, extension=True)


def _websocket_allowed(ws: WebSocket) -> bool:
    origin = (ws.headers.get("origin") or "").strip()
    if _ws_origin_allowed(origin):
        return True
    host = (ws.client.host if ws.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"} and not origin


class ReplyWaits:
    """Requests sent to Chrome that are waiting for its reply, by request id."""

    def __init__(self):
        self._waits: dict[str, asyncio.Future] = {}
        self._wait_jobs: dict[str, str] = {}

    def register_wait(self, request_id: str, job_id: str | None = None) -> asyncio.Future:
        from vendoo_studio.services.chrome_wait_connections import check_released

        check_released()
        self.cancel_wait(request_id)
        fut = asyncio.get_running_loop().create_future()
        self._waits[request_id] = fut
        if job_id:
            self._wait_jobs[request_id] = job_id
        return fut

    def resolve_wait(self, request_id: str, payload: dict) -> None:
        self._wait_jobs.pop(request_id, None)
        fut = self._waits.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(payload)

    def is_waiting(self, request_id: str) -> bool:
        fut = self._waits.get(request_id)
        return bool(fut and not fut.done())

    def cancel_wait(self, request_id: str) -> None:
        self._wait_jobs.pop(request_id, None)
        fut = self._waits.pop(request_id, None)
        if fut and not fut.done():
            fut.cancel()

    def cancel_waits_for_job(self, job_id: str) -> None:
        for request_id, wait_job_id in list(self._wait_jobs.items()):
            if wait_job_id == job_id:
                self.cancel_wait(request_id)


class ExtensionManager(ReplyWaits):
    def __init__(self):
        self.connection: WebSocket | None = None
        self.paired = False
        self.version: str | None = None
        self.build: str | None = None
        self.capabilities: frozenset[str] = frozenset()
        self.reload_generation: str | None = None
        # The server's event loop, for the sourcing thread to reach Chrome through.
        self.loop: asyncio.AbstractEventLoop | None = None
        self._pairing_token: str | None = None
        ReplyWaits.__init__(self)
        # Monotonic time of the last accepted pairing, kept across disconnects so
        # a reconnect loop is visible as one.
        self._last_handshake_at: float | None = None
        self._duplicate_refused_at: float | None = None

    def note_handshake(self, now: float | None = None) -> bool:
        """Record an accepted pairing. True when it is not part of a reconnect loop.

        A worker that pairs, drops and pairs again every second looks healthy
        from the outside while it re-runs the work a reconnect implies. Those
        side effects only run for a pairing that stands on its own.
        """
        moment = time.monotonic() if now is None else now
        previous = self._last_handshake_at
        self._last_handshake_at = moment
        return previous is None or (moment - previous) >= RECONNECT_SETTLE_SEC

    def _mark_disconnected(self) -> None:
        self.connection = None
        self.paired = False
        self.version = None
        self.build = None
        self.capabilities = frozenset()
        self.reload_generation = None

    def claims_connection(self, version: str | None, build: str | None) -> bool:
        """Whether a connecting copy may take the socket from the paired one.

        A connected copy is only replaced by one that runs this app's extension
        while the paired copy does not. Anything else is a second copy asking
        while the first is healthy, and taking it would drop every request in
        flight and hand the socket back a moment later, forever.
        """
        if self.connection is None or not self.paired:
            return True
        if extension_in_lockstep(build, version) and not extension_in_lockstep(self.build, self.version):
            return True
        self._duplicate_refused_at = time.monotonic()
        return False

    @property
    def duplicate_bridge(self) -> bool:
        """True when a second copy asked to connect in the last few minutes."""
        if self._duplicate_refused_at is None:
            return False
        if time.monotonic() - self._duplicate_refused_at > DUPLICATE_REPORT_SEC:
            self._duplicate_refused_at = None
            return False
        return True

    @property
    def connected(self) -> bool:
        return self.connection is not None and self.paired

    def get_persistent_token(self) -> str | None:
        try:
            with open(PAIRING_FILE) as f:
                return f.read().strip() or None
        except FileNotFoundError:
            return None

    def generate_pairing_token(self) -> str:
        existing = self.get_persistent_token()
        if existing:
            self._pairing_token = existing
            return existing
        self._pairing_token = uuid.uuid4().hex
        self.save_persistent_token(self._pairing_token)
        return self._pairing_token

    def save_persistent_token(self, token: str):
        os.makedirs(os.path.dirname(PAIRING_FILE), exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(PAIRING_FILE, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(token)
        os.chmod(PAIRING_FILE, 0o600)

    def verify_token(self, token: str) -> bool:
        candidate = (token or "").strip()
        if not candidate:
            return False
        persisted = self.get_persistent_token()
        if persisted and persisted == candidate:
            return True
        if self._pairing_token and self._pairing_token == candidate:
            return True
        return False

    async def send_message(self, message: dict) -> bool:
        connection = self.connection
        if not connection:
            return False
        try:
            await asyncio.wait_for(connection.send_json(message), SEND_TIMEOUT_SEC)
            return True
        except Exception:
            # Studio closing the socket on purpose is the other half of a
            # reconnect loop; name it, or a flapping bridge looks like Chrome's
            # fault alone.
            log.warning(
                "Extension send failed (%s); closing the socket so it reconnects",
                message.get("type"),
                exc_info=True,
            )
            if self.connection is connection:
                self._mark_disconnected()
                # Closing makes the extension reconnect on a fresh socket.
                try:
                    await asyncio.wait_for(connection.close(), 1.0)
                except Exception:
                    pass
            return False

    async def disconnect(self, ws: WebSocket | None = None):
        connection = self.connection if ws is None else ws
        if connection is None:
            if ws is None:
                self._mark_disconnected()
            return
        try:
            await connection.close()
        except Exception:
            pass
        if self.connection is connection:
            self._mark_disconnected()


extension_manager = ExtensionManager()


def current_extension():
    """The Chrome this work can reach: the Mac's paired Bridge, or on the web the
    signed-in seller's own tab relaying to their Bridge."""
    from vendoo_studio.services.service_context import current

    context = current()
    if context is None:
        return extension_manager
    from vendoo_studio.services.hosted_browser_relay import relay_for

    return relay_for(context.seller_id)


def _reported_reload_generation(payload: dict) -> str | None:
    reported = payload.get("reload_generation")
    if reported is None:
        return None
    token = str(reported).strip()
    return token or None


def _reported_build(payload: dict) -> str | None:
    reported = payload.get("build")
    if reported is None:
        return None
    text = str(reported).strip()
    return text or None


def _reported_capabilities(payload: dict) -> frozenset[str]:
    reported = payload.get("capabilities")
    if not isinstance(reported, list):
        return frozenset()
    return frozenset(item for item in reported if isinstance(item, str) and 0 < len(item) <= 64)


def _reported_version(payload: dict) -> str | None:
    version = payload.get("version")
    if version is None:
        return None
    text = str(version).strip()
    return text or None


async def request_extension_reload(generation: str) -> bool:
    return await extension_manager.send_message(ProtocolMessage(
        type="extension.reload",
        payload={"generation": generation},
    ).model_dump(mode="json"))


async def wait_for_extension_connection(
    timeout_sec: float = 20.0,
    *,
    poll_interval_sec: float = 0.25,
) -> bool:
    """Wait until the bridge pairs after Connect Chrome opens everyday Chrome.

    MV3 workers often need a moment (and sometimes one reload) before the
    WebSocket is live. Keep nudging a pending reload while we wait so a single
    Connect click can finish the handshake without quitting Chrome.
    """
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_reload_at = 0.0
    while True:
        if extension_manager.connected:
            return True
        now = time.monotonic()
        if now >= deadline:
            return extension_manager.connected
        if now - last_reload_at >= 2.0:
            token = pending_extension_reload_token()
            if not token:
                token = extension_reload_token_if_needed(
                    extension_manager.version,
                    extension_manager.reload_generation,
                    extension_manager.build,
                )
            if token and extension_manager.connection is not None:
                await request_extension_reload(token)
            last_reload_at = now
        await asyncio.sleep(poll_interval_sec)


async def handshake_extension(
    ws: WebSocket,
    reported_generation: str | None,
    reported_version: str | None = None,
    reported_build: str | None = None,
) -> bool:
    try:
        install_bundled_extension()
    except ChromeBridgeError:
        pass
    token = extension_reload_token_if_needed(reported_version, reported_generation, reported_build)
    if token:
        # One worker reload when Chrome is not yet on this Studio copy. A
        # second pass with the same generation is accepted so Vendoo tabs
        # are not reloaded in a loop.
        await ws.send_json(ProtocolMessage(
            type="extension.reload",
            payload={"generation": token},
        ).model_dump(mode="json"))
        return False
    clear_extension_reload_pending()
    await ws.send_json(ProtocolMessage(
        type="connection.accepted",
        payload={"paired": True},
    ).model_dump(mode="json"))
    return True


_reconnect_tasks: set[asyncio.Task] = set()


def _run_reconnect_work(db) -> bool:
    """Resume what a reconnect implies, once the pairing is not itself a flap.

    A worker that pairs, drops and pairs again every half second looks paired
    each time. Reconciling direct listings on every one of those ticks keeps
    asking Chrome for a listing check it cannot answer, until the check times
    out. So the work waits for a pairing that stands on its own.
    """
    if not extension_manager.note_handshake():
        log.info("Extension paired again within %ss; holding reconnect work", RECONNECT_SETTLE_SEC)
        return False

    from vendoo_studio.services.native_marketplaces import reconcile_after_reconnect

    # Read unconfirmed direct listings. Off this loop: the checks wait on
    # replies it reads.
    _reconnect_tasks.add(task := asyncio.create_task(reconcile_after_reconnect()))
    task.add_done_callback(_reconnect_tasks.discard)
    return True


async def _reconnect_work_after_settle(ws: WebSocket) -> None:
    """Run the reconnect work for a pairing that was too soon, if it survives."""
    await asyncio.sleep(RECONNECT_SETTLE_SEC)
    if extension_manager.connection is not ws:
        # It flapped again; whichever pairing outlasts the window decides.
        return
    with SessionLocal() as db:
        _run_reconnect_work(db)


def _after_pairing(db, ws: WebSocket) -> None:
    """Do the reconnect work now, or once a flapping pairing has settled."""
    if _run_reconnect_work(db):
        return
    _reconnect_tasks.add(task := asyncio.create_task(_reconnect_work_after_settle(ws)))
    task.add_done_callback(_reconnect_tasks.discard)


def get_pairing_token():
    token = extension_manager.generate_pairing_token()
    return {"token": token}


def extension_status():
    return {
        "connected": extension_manager.connected,
        "paired": extension_manager.paired,
        "duplicate_bridge": extension_manager.duplicate_bridge,
        "runs_marketplace_actions": MARKETPLACE_ACTIONS in extension_manager.capabilities,
        **extension_build_status(
            extension_manager.version,
            extension_manager.reload_generation,
            extension_manager.build,
        ),
    }


async def reload_extension():
    try:
        from vendoo_studio.services.chrome_bridge import sync_bundled_extension

        await asyncio.to_thread(sync_bundled_extension)
    except ChromeBridgeError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not extension_manager.connected:
        raise HTTPException(409, "Connect Chrome before reloading the bridge.")
    token = extension_reload_token_if_needed(extension_manager.version, extension_manager.reload_generation, extension_manager.build)
    return {"ok": True, "sent": await request_extension_reload(token) if token else False}


_SESSION_MARKETS = ("depop", "poshmark", "mercari")
_SESSION_STATES = {"signed_in", "expired", "signed_out", "unknown"}


def _public_session(raw: object) -> dict:
    """Sign-in summary only. Cookie values, tokens, and extra fields are dropped."""
    data = raw if isinstance(raw, dict) else {}
    state = data.get("state") if data.get("state") in _SESSION_STATES else "unknown"
    expires = data.get("expires_at")
    expires_at = int(expires) if isinstance(expires, (int, float)) and not isinstance(expires, bool) else None
    label = data.get("label")
    label = label.strip() if isinstance(label, str) else ""
    if not label or len(label) > 80:
        label = None
    if state == "signed_out":
        expires_at = None
        label = None
    return {
        "state": state,
        "expires_at": expires_at,
        "label": label,
        "expiring": bool(data.get("expiring")) and state == "signed_in",
    }


def _public_connect(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    if data.get("ok") is not True:
        error = data.get("error")
        text = error.strip() if isinstance(error, str) else ""
        if not text or len(text) > 200:
            text = "Finish signing in in Chrome, then try again."
        return {"ok": False, "error": text}
    session = _public_session(data)
    return {"ok": True, "signed_in": session["state"] == "signed_in", **session}


async def _web_chrome(command: str, payload: dict, timeout: float) -> dict | None:
    """On the web, ask the seller's own Chrome through their Anteroom tab; None on the Mac."""
    from vendoo_studio.services.service_context import current

    context = current()
    if context is None:
        return None
    from vendoo_studio.services.hosted_browser_relay import ChromeReadError, relay_for

    try:
        return await relay_for(context.seller_id).read(command, payload, timeout)
    except ChromeReadError as exc:
        raise HTTPException(409, str(exc)) from exc


async def marketplace_sessions():
    from types import SimpleNamespace

    from vendoo_studio.services import browser_bridge

    raw = await _web_chrome("marketplace.sessions", {}, 25)
    try:
        if raw is None:
            raw = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.sessions", {}, timeout=20)
    except browser_bridge.BrowserBridgeError as exc:
        status = 409 if "Connect Chrome" in str(exc) else 502
        raise HTTPException(status, "Connect Chrome, then check marketplace sign-in.") from exc
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    if not isinstance(sessions, dict):
        raise HTTPException(502, "Chrome did not report marketplace sign-in.")
    return {"sessions": {name: _public_session(sessions.get(name)) for name in _SESSION_MARKETS}}


async def connect_marketplace(marketplace: str):
    from types import SimpleNamespace

    from vendoo_studio.services import browser_bridge

    if marketplace not in set(_SESSION_MARKETS):
        raise HTTPException(404, "Unknown marketplace.")
    raw = await _web_chrome("marketplace.connect", {"marketplace": marketplace}, 45)
    if raw is not None:
        return _public_connect(raw)
    try:
        raw = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.connect", {"marketplace": marketplace}, timeout=40)
    except browser_bridge.BrowserBridgeError as exc:
        raise HTTPException(502, "Finish signing in in Chrome, then reconnect.") from exc
    return _public_connect(raw)


async def extension_websocket(ws: WebSocket):
    if not _websocket_allowed(ws):
        await ws.close(code=1008)
        return

    await ws.accept()
    opened_at = time.monotonic()
    db = SessionLocal()
    try:
        while True:
            raw = await ws.receive_text()
            message = json.loads(raw)

            msg_type = message.get("type", "")

            if msg_type == "extension.ready":
                payload = message.get("payload", {}) or {}
                token = payload.get("token", "")
                if not extension_manager.verify_token(token):
                    await ws.send_json({"type": "error", "message": "Invalid pairing token"})
                    continue
                version = _reported_version(payload)
                build = _reported_build(payload)
                if not extension_manager.claims_connection(version, build):
                    # Leave the paired copy alone and tell this one to stand down,
                    # rather than closing a working socket for a duplicate.
                    log.warning("Refused a second bridge copy (version %s, build %s)", version, build)
                    await ws.send_json({"type": "error", "message": DUPLICATE_BRIDGE, "duplicate_bridge": True})
                    continue
                old = extension_manager.connection
                extension_manager.connection = ws
                extension_manager.loop = asyncio.get_running_loop()
                extension_manager.version = version
                extension_manager.build = build
                extension_manager.capabilities = _reported_capabilities(payload)
                extension_manager.reload_generation = _reported_reload_generation(payload)
                extension_manager._duplicate_refused_at = None
                if old is not None and old is not ws:
                    try:
                        await old.close()
                    except Exception:
                        pass
                accepted = await handshake_extension(
                    ws,
                    _reported_reload_generation(payload),
                    version,
                    build,
                )
                if accepted:
                    extension_manager.paired = True
                    _after_pairing(db, ws)
                continue

            if extension_manager.connection is not ws or not extension_manager.paired:
                await ws.send_json({"type": "error", "message": "Not paired"})
                continue

            if msg_type == "browser.result":
                payload = message.get("payload") or {}
                extension_manager.resolve_wait(str(payload.get("request_id") or ""), payload)
                continue

            if msg_type == "market.sold_listings_result":
                payload = message.get("payload") or {}
                extension_manager.resolve_wait(str(payload.get("request_id") or ""), payload)
                continue

            if msg_type == "diagnostic.observed":
                payload = message.get("payload", {})
                obs_id = payload.get("observation_id", "")
                from vendoo_studio.repositories.queries import DiagnosticRepo
                repo = DiagnosticRepo(db)
                repo.save_observation(payload)
                await ws.send_json(ProtocolMessage(
                    type="diagnostic.ack",
                    payload={"observation_id": obs_id},
                ).model_dump(mode="json"))

            elif msg_type == "pong":
                pass

    except WebSocketDisconnect:
        lived = time.monotonic() - opened_at
        if lived < FLAP_WARN_SEC:
            # A worker that pairs and drops within seconds is flapping, and a
            # silent close is what let that go unnoticed.
            log.warning("Extension socket closed %.1fs after it paired", lived)
    except Exception:
        # Never swallow: a socket that dies mid-message has to leave a trace.
        log.warning("Extension socket failed after %.1fs", time.monotonic() - opened_at, exc_info=True)
    finally:
        if extension_manager.connection is ws:
            extension_manager._mark_disconnected()
        db.close()


def marketplace_actions_refusal() -> str | None:
    """Why Chrome cannot run a marketplace action now, or None when it can."""
    from vendoo_studio.services.service_context import current

    context = current()
    if context is not None:
        from vendoo_studio.services.hosted_browser_relay import relay_for

        return relay_for(context.seller_id).refusal()
    if not extension_manager.connected:
        return "Connect Chrome first."
    if MARKETPLACE_ACTIONS not in extension_manager.capabilities:
        return "Update Anteroom Bridge: press Connect Chrome to load Anteroom's copy, then try again."
    return None
