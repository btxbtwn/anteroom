"""Server-side execution of cloud marketplace commands.

Runs each marketplace operation inside a fresh, single-operation Node subprocess
(``cloud_runner/run-<marketplace>.cjs``) with Chrome TLS impersonation (``impit``),
per-seller cookie jar isolation (``tough-cookie``), optional sticky residential
proxy, and write-ahead Postgres journals.

Process-role security boundary:
- ``web`` Machines NEVER decrypt cloud credentials or spawn Node. They forward
  cloud commands to a ``worker`` Machine over the private Fly network via
  ``POST /internal/cloud-marketplace/execute`` using ``ANTEROOM_PROCESS_TOKEN``.
- ``worker`` (and local/test ``all``) Machines decrypt credentials freshly from
  Supabase Vault, enforce a per-Machine concurrency cap, run the isolated
  subprocess, and persist rotated cookies back to Vault once at the end.
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import socket
import shutil
import threading
import weakref
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from anteroom.database import in_worker_session
from anteroom.services import cloud_sessions
from anteroom.services.service_context import current

log = logging.getLogger("anteroom.cloud_marketplace")

_RUNNER_SCRIPT = Path(__file__).resolve().parent.parent / "cloud_runner" / "run-poshmark.cjs"
_WORKER_URL_RE = re.compile(
    r"^http://(127\.0\.0\.1|localhost|\[[0-9a-f:]+\]|[a-z0-9.-]+\.internal):[0-9]{2,5}$",
    re.IGNORECASE,
)
_DEFAULT_MAX_CONCURRENCY = 4
_DEFAULT_TIMEOUT_S = 180.0
_MAX_PHOTO_BYTES = 20 * 1024 * 1024

# Per-event-loop concurrency semaphores (M2)
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()
_photo_pool: ThreadPoolExecutor | None = None
_photo_pool_lock = threading.Lock()
# Active subprocess registry for cancellation: (seller_id, job_id) -> Process
_pending: dict[tuple[str, str], threading.Event] = {}
_active_lock = threading.Lock()
_active_processes: dict[tuple[str, str], asyncio.subprocess.Process] = {}


class CloudExecutionUnavailable(RuntimeError):
    """Raised when the worker cannot be reached or Node runtime is missing."""


class CloudExecutionUncertain(CloudExecutionUnavailable):
    """The worker may have received and executed the command."""


def _process_role() -> str:
    return (os.environ.get("ANTEROOM_PROCESS_ROLE") or "all").strip().lower()


def _max_concurrency() -> int:
    raw = os.environ.get("ANTEROOM_CLOUD_MAX_CONCURRENCY", str(_DEFAULT_MAX_CONCURRENCY))
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY
    return max(1, min(16, value))


def _photo_executor() -> ThreadPoolExecutor:
    # Share one pool across loops and keep the decode bound after task cancellation.
    # Runner slots remain available while other sellers' photos are prepared.
    global _photo_pool
    with _photo_pool_lock:
        if _photo_pool is None:
            _photo_pool = ThreadPoolExecutor(max_workers=_max_concurrency(), thread_name_prefix="cloud-photos")
        return _photo_pool


def _loop_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_max_concurrency())
        _semaphores[loop] = sem
    return sem


def worker_base_url() -> str:
    override = (os.environ.get("ANTEROOM_WORKER_BASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    fly_app = (os.environ.get("FLY_APP_NAME") or "").strip()
    if fly_app and re.match(r"^[a-z0-9-]+$", fly_app):
        return f"http://worker.process.{fly_app}.internal:8080"
    return ""


def _node_binary() -> str:
    configured = (os.environ.get("ANTEROOM_NODE_BIN") or "").strip()
    if configured:
        return configured
    found = shutil.which("node")
    if not found:
        raise CloudExecutionUnavailable("Node.js runtime is not available for cloud marketplace execution.")
    return found


def _seller_id() -> str:
    ctx = current()
    if ctx is None or not ctx.seller_id:
        raise CloudExecutionUnavailable("Cloud marketplace execution requires a seller context.")
    return ctx.seller_id


def _load_approved_photos(approved_photos: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Read approved listing photos directly from photo_store with SHA-256 verification."""
    if not approved_photos:
        return []
    from anteroom.services.photo_store import photo_store

    store = photo_store()
    encoded: list[dict[str, str]] = []
    for item in approved_photos:
        photo_id = str(item.get("id") or "")
        storage_key = str(item.get("stored_filename") or item.get("storage_key") or item.get("filename") or "")
        expected_sha = str(item.get("checksum") or item.get("sha256") or "")
        mime_type = str(item.get("mime_type") or "image/jpeg")
        if not photo_id or not storage_key:
            continue
        data = store.read(storage_key)
        if not isinstance(data, (bytes, bytearray)):
            raise CloudExecutionUnavailable("Could not read approved listing photo.")
        if len(data) > _MAX_PHOTO_BYTES:
            raise ValueError("Listing photo exceeds 20 MB limit.")
        if expected_sha:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha:
                raise ValueError("Listing photo changed after approval. Retry listing.")
        encoded.append({
            "id": photo_id,
            "base64": base64.b64encode(data).decode("ascii"),
            "mime_type": mime_type,
        })
    return encoded


def _prepare_photos(marketplace: str, photos: list[dict[str, str]]) -> list[dict[str, str]]:
    if marketplace == "poshmark":
        return photos
    prepared: list[dict[str, str]] = []
    for photo in photos:
        try:
            raw = base64.b64decode(photo["base64"], validate=True)
            if len(raw) > _MAX_PHOTO_BYTES:
                raise ValueError("Listing photo exceeds 20 MB limit.")
            with Image.open(BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
                if marketplace == "depop":
                    width, height = image.size
                    if width > height:
                        size = (1280, max(1, round(height / width * 1280)))
                    else:
                        size = (max(1, round(width / height * 1280)), 1280)
                    image = image.resize(size, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", (1280, 1280), "white")
                    offset = ((1280 - image.width) // 2, (1280 - image.height) // 2)
                else:
                    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", image.size, "white")
                    offset = (0, 0)
                canvas.paste(image, offset, image.getchannel("A"))
                encoded = BytesIO()
                canvas.save(encoded, format="JPEG", quality=95 if marketplace == "depop" else 92)
        except (KeyError, ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError("Could not prepare an approved listing photo.") from None
        prepared.append({**photo, "base64": base64.b64encode(encoded.getvalue()).decode("ascii"), "mime_type": "image/jpeg"})
    return prepared


async def _forward_to_worker(path: str, body: dict[str, Any], timeout: float, *, target: str | None = None) -> dict[str, Any]:
    base = target or worker_base_url()
    token = os.environ.get("ANTEROOM_PROCESS_TOKEN", "")
    if not base or not token or not _WORKER_URL_RE.match(base):
        raise CloudExecutionUnavailable("Cloud marketplace worker is not configured.")
    try:
        async with httpx.AsyncClient(timeout=timeout + 15.0, trust_env=False) as client:
            response = await client.post(
                f"{base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        log.warning("Cloud worker forward to %s failed: %s", path, type(exc).__name__)
        raise CloudExecutionUncertain("Cloud marketplace worker did not answer in time.") from None
    if response.status_code != 200:
        raise CloudExecutionUncertain(f"Cloud marketplace worker returned HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError:
        raise CloudExecutionUncertain("Cloud marketplace worker returned an invalid response.") from None
    if not isinstance(data, dict):
        raise CloudExecutionUncertain("Cloud marketplace worker returned an invalid response.")
    return data


async def execute(
    marketplace: str,
    command: dict[str, Any],
    *,
    photos: list[dict[str, str]] | None = None,
    approved_photos: list[dict[str, Any]] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a cloud marketplace command for the current seller.

    On ``web`` Machines, forwards the command to the ``worker`` Machine so
    credentials are never decrypted on ``web``.
    """
    market = str(marketplace or "").lower()
    if not cloud_sessions.is_cloud_enabled(market):
        return {
            "ok": False,
            "safe_to_retry": True,
            "error_code": "rejected",
            "error": "Cloud sessions are not enabled for this marketplace.",
        }

    if _process_role() == "web":
        try:
            forwarded = await _forward_to_worker(
                "/internal/cloud-marketplace/execute",
                {
                    "user_id": _seller_id(),
                    "marketplace": market,
                    "command": command,
                    "photos": photos or [],
                    "approved_photos": approved_photos or [],
                    "timeout": timeout,
                },
                timeout=timeout,
            )
        except CloudExecutionUnavailable as exc:
            return {
                "ok": False,
                "safe_to_retry": not isinstance(exc, CloudExecutionUncertain),
                "error_code": "network",
                "error": str(exc),
            }
        reply = forwarded.get("result")
        return reply if isinstance(reply, dict) else {
            "ok": False,
            "safe_to_retry": False,
            "error_code": "network",
            "error": "Cloud marketplace worker returned an empty result.",
        }

    return await execute_local(
        market,
        command,
        photos=photos,
        approved_photos=approved_photos,
        timeout=timeout,
    )


async def cancel(job_id: str) -> bool:
    """Cancel an in-flight cloud marketplace operation for the current seller."""
    seller = _seller_id()
    if _process_role() == "web":
        base = worker_base_url()
        targets = [base]
        # Fly private DNS lists every worker Machine, including overlapping deployments.
        if _WORKER_URL_RE.fullmatch(base) and ".internal:" in base:
            host, port = base.removeprefix("http://").rsplit(":", 1)
            try:
                addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                    host, int(port), family=socket.AF_INET6, type=socket.SOCK_STREAM), timeout=3)
            except (OSError, TimeoutError):
                return False
            targets = list({f"http://[{address[4][0]}]:{port}" for address in addresses})

        async def cancel_on(target: str) -> bool:
            try:
                res = await _forward_to_worker("/internal/cloud-marketplace/cancel",
                    {"user_id": seller, "job_id": str(job_id)}, timeout=10.0, target=target)
                return res.get("cancelled") is True
            except CloudExecutionUnavailable:
                return False

        return any(await asyncio.gather(*(cancel_on(target) for target in targets)))
    return await cancel_local(seller, str(job_id))


async def cancel_local(seller_id: str, job_id: str) -> bool:
    with _active_lock:
        key = (seller_id, str(job_id))
        pending = _pending.get(key)
        if pending is not None:
            pending.set()
        proc = _active_processes.get(key)
    if proc is None:
        return pending is not None
    if proc.returncode is not None:
        return False
    try:
        if proc.stdin is not None and not proc.stdin.is_closing():
            msg = json.dumps({"type": "cancel", "job_id": str(job_id)}) + "\n"
            proc.stdin.write(msg.encode("utf-8"))
            async with asyncio.timeout(5):
                await proc.stdin.drain()
                await proc.wait()
            return True
    except Exception:
        pass
    try:
        proc.terminate()
    except ProcessLookupError:
        return False
    return True


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except (ProcessLookupError, TimeoutError):
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            pass


async def _run_subprocess(
    marketplace: str,
    seller_id: str,
    job_id: str,
    run_payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    node_bin = _node_binary()
    script = _RUNNER_SCRIPT if marketplace == "poshmark" else _RUNNER_SCRIPT.with_name(f"run-{marketplace}.cjs")
    if marketplace not in cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES or not script.is_file():
        raise CloudExecutionUnavailable("Cloud marketplace runner script is missing.")

    proc = await asyncio.create_subprocess_exec(
        node_bin,
        str(script),
        limit=16 * 1024 * 1024,  # NDJSON includes the full cookie bundle and operation results.
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    reg_key = (seller_id, job_id) if job_id else None
    if reg_key is not None:
        with _active_lock:
            _active_processes[reg_key] = proc

    async def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text_line = line.decode("utf-8", errors="replace").strip()
            if text_line:
                log.info("cloud_runner[%s]: %s", marketplace, text_line[:400])

    stderr_task = asyncio.create_task(_drain_stderr())

    async def _communicate() -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        if reg_key and (pending := _pending.get(reg_key)) and pending.is_set():
            return {"result": _cancelled_before_start()}
        proc.stdin.write((json.dumps(run_payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        outcome: dict[str, Any] | None = None
        while True:
            raw_line = await proc.stdout.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                raise ValueError("Incomplete runner frame")
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("Invalid runner frame")
            msg_type = msg.get("type")
            if msg_type == "journal_set":
                seq = msg.get("seq")
                entries = msg.get("entries")
                try:
                    if isinstance(entries, dict):
                        await in_worker_session(cloud_sessions.upsert_journals, marketplace, entries)
                    ack = {"type": "journal_ack", "seq": seq, "ok": True}
                except Exception as exc:
                    log.warning("Failed to persist cloud journal: %s", type(exc).__name__)
                    ack = {"type": "journal_ack", "seq": seq, "ok": False, "error": "Could not save operation journal."}
                if not proc.stdin.is_closing():
                    proc.stdin.write((json.dumps(ack) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
            elif msg_type == "journal_remove":
                seq = msg.get("seq")
                keys = msg.get("keys")
                try:
                    if isinstance(keys, list):
                        await in_worker_session(cloud_sessions.delete_journals, marketplace, keys)
                    ack = {"type": "journal_ack", "seq": seq, "ok": True}
                except Exception as exc:
                    log.warning("Failed to prune cloud journal: %s", type(exc).__name__)
                    ack = {"type": "journal_ack", "seq": seq, "ok": False, "error": "Could not prune operation journal."}
                if not proc.stdin.is_closing():
                    proc.stdin.write((json.dumps(ack) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
            elif msg_type == "result":
                outcome = msg
                break

        try:
            if not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            pass
        await proc.wait()
        return outcome or {
            "result": {
                "ok": False,
                "safe_to_retry": False,
                "error_code": "network",
                "error": "Cloud marketplace runner exited without a result.",
            },
            "cookies_changed": False,
            "cookies": [],
        }

    try:
        return await asyncio.wait_for(_communicate(), timeout=timeout)
    except TimeoutError:
        await _kill_process(proc)
        return {
            "result": {
                "ok": False,
                "safe_to_retry": False,
                "error_code": "uncertain",
                "error": f"{marketplace.title()} did not finish in time.",
            },
            "cookies_changed": False,
            "cookies": [],
        }
    except (ValueError, OSError):
        return {"result": {"ok": False, "safe_to_retry": False, "error_code": "uncertain",
                           "error": "Could not read the cloud runner response."}}
    except asyncio.CancelledError:
        await _kill_process(proc)
        raise
    finally:
        if reg_key is not None:
            with _active_lock:
                if _active_processes.get(reg_key) is proc:
                    _active_processes.pop(reg_key, None)
        await _kill_process(proc)
        try:
            await asyncio.wait_for(stderr_task, timeout=1.0)
        except Exception:
            stderr_task.cancel()


def _cancelled_before_start() -> dict[str, Any]:
    return {"ok": False, "safe_to_retry": True, "error_code": "cancelled", "error": "Cancelled before execution."}


async def execute_local(
    marketplace: str,
    command: dict[str, Any],
    *,
    photos: list[dict[str, str]] | None = None,
    approved_photos: list[dict[str, Any]] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    job_id = str((command.get("payload") or {}).get("operation_id") or command.get("job_id") or "")
    # A status lookup can run alongside the write whose journal it reads.
    if command.get("type") == "marketplace.result":
        job_id = ""
    key = (_seller_id(), job_id)
    pending = threading.Event()
    if job_id:
        with _active_lock:
            if key in _pending:
                return {"ok": False, "safe_to_retry": False, "error_code": "uncertain", "error": "Operation already running."}
            _pending[key] = pending
    try:
        return await _execute_local(marketplace, command, photos=photos, approved_photos=approved_photos,
                                    timeout=timeout, job_id=job_id, pending=pending)
    finally:
        if job_id:
            with _active_lock:
                _pending.pop(key, None)


async def _execute_local(
    marketplace: str,
    command: dict[str, Any],
    *,
    job_id: str,
    pending: threading.Event,
    photos: list[dict[str, str]] | None = None,
    approved_photos: list[dict[str, Any]] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run a single marketplace operation locally in an isolated Node subprocess."""
    market = str(marketplace or "").lower()
    seller = _seller_id()

    operation = str(command.get("type") or "").strip() or None
    creds_tuple = await in_worker_session(cloud_sessions.read_credentials_fresh, market, operation)
    if creds_tuple is None:
        return {
            "ok": False,
            "safe_to_retry": True,
            "error_code": "signed_out",
            "error": f"{market.title()} cloud session is not connected. Reconnect it in Settings → Connections.",
        }
    creds, proxy_session_id, generation = creds_tuple
    cookies = creds.get("cookies") or []
    user_agent = str(creds.get("user_agent") or "")
    proxy_url = cloud_sessions.proxy_url_for(proxy_session_id)

    journals = await in_worker_session(cloud_sessions.load_journals, market)

    photo_payloads = list(photos or [])
    if not photo_payloads and approved_photos:
        try:
            photo_payloads = await asyncio.to_thread(_load_approved_photos, approved_photos)
        except ValueError as exc:
            return {
                "ok": False,
                "safe_to_retry": True,
                "error_code": "rejected",
                "error": str(exc),
            }
        except CloudExecutionUnavailable as exc:
            return {
                "ok": False,
                "safe_to_retry": True,
                "error_code": "network",
                "error": str(exc),
            }

    try:
        if photo_payloads and market != "poshmark":
            photo_payloads = await asyncio.get_running_loop().run_in_executor(
                _photo_executor(), _prepare_photos, market, photo_payloads,
            )
    except ValueError as exc:
        return {"ok": False, "safe_to_retry": True, "error_code": "rejected", "error": str(exc)}

    run_payload = {
        "type": "run",
        "command": command,
        "cookies": cookies,
        "user_agent": user_agent,
        "proxy_url": proxy_url,
        "photos": photo_payloads,
        "journals": journals,
    }

    async with _loop_semaphore():
        if pending.is_set():
            return _cancelled_before_start()
        try:
            outcome = await _run_subprocess(market, seller, job_id, run_payload, timeout)
        except CloudExecutionUnavailable as exc:
            return {
                "ok": False,
                "safe_to_retry": True,
                "error_code": "network",
                "error": str(exc),
            }

    result = outcome.get("result")
    if not isinstance(result, dict):
        result = {
            "ok": False,
            "safe_to_retry": False,
            "error_code": "network",
            "error": "Cloud marketplace runner returned an invalid result.",
        }

    # Transition to expired on signed_out; otherwise persist rotated cookies once (H4).
    err_code = result.get("error_code") or (result.get("error") if result.get("ok") is False else None)
    verified_session = result.get("session")
    verified_expired = (
        command.get("type") == "marketplace.verify_session"
        and isinstance(verified_session, dict)
        and verified_session.get("state") in {"expired", "signed_out"}
    )
    if err_code == "signed_out" or verified_expired:
        try:
            await in_worker_session(cloud_sessions.mark_session_expired, market, str(result.get("error") or ""), generation)
        except Exception:
            log.warning("Could not mark cloud session expired for %s", market)
    elif outcome.get("cookies_changed") and isinstance(outcome.get("cookies"), list) and outcome["cookies"]:
        try:
            await in_worker_session(
                cloud_sessions.refresh_cookies,
                market,
                outcome["cookies"],
                user_agent,
                generation,
            )
        except Exception:
            log.warning("Could not refresh rotated cloud cookies for %s", market)

    return result


async def verify_session(marketplace: str = "poshmark", timeout: float = 25.0) -> dict[str, Any]:
    """Run a live read-only verification against the marketplace for the current seller."""
    return await execute(
        marketplace,
        {"type": "marketplace.verify_session", "payload": {"marketplace": marketplace}},
        timeout=timeout,
    )
