"""Seller-confirmed direct listings on eBay, Etsy, Poshmark, Mercari, Depop and Sellwild.

List is one seller-confirmed step. It runs as one background job that holds
the single automation slot from the first write to the last answer: saving the
marketplace's own draft (Mercari, Depop, Etsy) and publishing it are steps of
the same job, so no queued Send can start between them, and a job whose Chrome
reply is late keeps the slot until Chrome reports what it did.

What Anteroom knows lives in ``marketplace_listings`` (one row per item and
marketplace). Nothing here retries a write on its own. A reply that never came
leaves the record ``unknown``; Check reads the marketplace (never writes) and
runs by itself after a lost reply, on startup and when Chrome reconnects.
Reset is the seller saying they looked themselves.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_listing import BUSY_STATES, UNSETTLED_STATES
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import browser_bridge, etsy_listings
from vendoo_studio.services.api_job_lock import api_job_is_live, claim_api_job, release_api_job, start_gate
from vendoo_studio.services.chrome_bridge import expected_extension_build
from vendoo_studio.services.listing_result import ERROR_CODES, failure, scrub

log = logging.getLogger("vendoo_studio.native_marketplaces")


class NativeMarketplaceError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


Marketplace = Literal["ebay", "poshmark", "mercari", "depop", "etsy", "sellwild"]
InspectionMarketplace = Literal["poshmark", "mercari", "depop", "etsy", "sellwild"]
API_MARKETPLACES = frozenset({"ebay", "etsy"})
NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "sellwild": "Sellwild"}
UNKNOWN = "Anteroom could not confirm what happened. Check the result before trying again."
# How long Chrome has to answer one step, and how long a job then keeps the
# slot waiting for Chrome to report what it did before giving up as unknown.
EXECUTE_TIMEOUT_S = 180
SETTLE_DEADLINE_S = 8 * 60
SETTLE_POLL_S = 10.0
STEP_STATE = {"save_draft": "saving", "publish": "publishing", "list": "publishing", "update": "updating"}
STEP_LABEL = {"save_draft": "Saving the listing", "publish": "Publishing", "list": "Listing", "update": "Updating the live listing",
              "waiting": "Waiting for Chrome to confirm", "checking": "Checking the result"}
PROGRESS_EVENT = "native_progress"


class EtsyPackage(BaseModel):
    item_weight: float = Field(gt=0, allow_inf_nan=False)
    item_length: float = Field(gt=0, allow_inf_nan=False)
    item_width: float = Field(gt=0, allow_inf_nan=False)
    item_height: float = Field(gt=0, allow_inf_nan=False)
    item_weight_unit: Literal["oz", "lb", "g", "kg"]
    item_dimensions_unit: Literal["in", "cm", "mm", "m", "ft", "yd"]


class ListRequest(BaseModel):
    revision_id: str
    confirm: bool = False
    shipping_profile_id: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    readiness_state_id: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    etsy_package: EtsyPackage | None = None
    sellwild_category_id: int | None = Field(default=None, ge=1)
    sellwild_condition_id: int | None = Field(default=None, ge=1)


class CheckRequest(BaseModel):
    draft_url: str | None = None


class InspectRequest(BaseModel):
    script_url: str | None = None
    background: bool = False


def _launch(coro) -> None:
    from vendoo_studio.services.streaming import spawn

    spawn(coro)


def _conversation(db: Session, conv_id: str):
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise NativeMarketplaceError(404, "Conversation not found")
    return conv


def _record(db: Session, conv_id: str, marketplace: str) -> dict:
    return MarketplaceListingRepo(db).get(conv_id, marketplace)


def _store(db: Session, conv_id: str, marketplace: str, values: dict) -> dict:
    return MarketplaceListingRepo(db).save(conv_id, marketplace, values)


_CLEARED_ERROR = {"error": None, "error_code": None, "error_fields": None, "marketplace_message": None, "retry_after": None}


def _running(record: dict) -> bool:
    return bool(record.get("job_id")) and api_job_is_live(record["job_id"])


def running_marketplace(db: Session, conv_id: str) -> str | None:
    """The marketplace a direct action is running on for this item, if any."""
    for marketplace, record in MarketplaceListingRepo(db).for_conversation(conv_id).items():
        if _running(record):
            return NAMES.get(marketplace, marketplace)
    return None


def status(conv_id: str, db: Session) -> dict:
    """Each marketplace's record, with what a running action is doing now."""
    _conversation(db, conv_id)
    records = MarketplaceListingRepo(db).for_conversation(conv_id)
    repo = JobRepo(db)
    for record in records.values():
        record["running"] = _running(record)
        if record["running"]:
            event = repo.latest_event(record["job_id"], PROGRESS_EVENT)
            record["progress"] = event.payload if event else None
    return records


# --- marketplace links ------------------------------------------------------

def draft_url(marketplace: str, value: str) -> str:
    """Accept only durable, native draft edit links, never arbitrary navigation."""
    parsed = urlparse(value)
    pattern, host = (
        (r"/your/shops/me/listing-editor/edit/\d+/?", "www.etsy.com") if marketplace == "etsy"
        else (r"/sellinghub/drafts/edit/[A-Za-z0-9_-]{1,80}/?", "www.depop.com") if marketplace == "depop"
        else (r"/sell/draft/[A-Za-z0-9_-]+/", "www.mercari.com")
    )
    if parsed.scheme != "https" or parsed.netloc != host or not re.fullmatch(pattern, parsed.path):
        raise NativeMarketplaceError(502, "Chrome did not return a valid saved draft link.")
    return f"https://{host}{parsed.path}"


def listing_url(marketplace: str, value: str) -> str:
    """A public listing link Delist can act on, or an error. Every marketplace, same rule."""
    from vendoo_studio.services.marketplace_delist import listing_id

    parsed = urlparse(str(value or "").strip())
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if not listing_id(marketplace, clean):
        raise NativeMarketplaceError(502, f"{NAMES[marketplace]} returned an invalid listing link.")
    return clean


# --- results ----------------------------------------------------------------

def _resting_state(marketplace: str, record: dict) -> str:
    """Where a record goes when nothing is live: draft when the marketplace (or Anteroom) holds one."""
    if record.get("draft_url") or record.get("storage") == "local":
        return "draft"
    if marketplace == "ebay" and (record.get("ebay_offer_id") or record.get("ebay_inventory_written")):
        return "draft"
    return "not_saved"


def _contract(result: dict) -> dict:
    """The error half of the result contract, as the record keeps it."""
    code = result.get("error_code")
    fields = result.get("fields")
    retry = result.get("retry_after_s")
    return {
        "error_code": code if code in ERROR_CODES else None,
        "error_fields": [{"field": str(item.get("field") or "")[:200], "message": str(item.get("message") or "")[:300]}
                         for item in fields if isinstance(item, dict) and item.get("field")][:20]
        if isinstance(fields, list) else None,
        "retry_after": datetime.now(UTC) + timedelta(seconds=int(retry)) if isinstance(retry, (int, float)) and 0 < retry <= 86400 else None,
        "marketplace_message": scrub(result.get("marketplace_message")),
    }


def _outcome(marketplace: str, action: str, record: dict, result: dict) -> dict:
    """The record's new fields for one step's result."""
    reason = str(result.get("error") or "").strip()
    if result.get("ok") is True:
        url = None
        if marketplace != "sellwild" and action in {"save_draft", "publish"}:
            url = draft_url(marketplace, str(result.get("draft_url") or ""))
            if record.get("draft_url") and url != record["draft_url"]:
                raise NativeMarketplaceError(502, "Chrome answered for a different draft. Check the native listing.")
        expected = "draft" if action == "save_draft" else "published"
        state = result.get("state")
        if not (state == expected or (marketplace == "sellwild" and action == "publish" and state == "pending_review")):
            return {"state": "unknown", **_CLEARED_ERROR, "error": UNKNOWN, "error_code": "uncertain"}
        values = {"state": state, **_CLEARED_ERROR}
        if marketplace == "etsy":
            values.update(etsy_pending_write=None)
        if url:
            values["draft_url"] = url
        if state in {"published", "pending_review"}:
            if result.get("listing_url"):
                values["listing_url"] = listing_url(marketplace, result["listing_url"])
            elif marketplace != "depop":
                # Depop's publish answers without the public link; every other one has it.
                raise NativeMarketplaceError(502, f"{NAMES[marketplace]} did not return the listing link.")
        if action == "update":
            from vendoo_studio.services.marketplace_delist import listing_id

            if listing_id(marketplace, values.get("listing_url")) != record.get("listing_id"):
                raise NativeMarketplaceError(502, "The marketplace answered for a different listing. The live one was not replaced.")
        return values
    contract = _contract(result)
    if result.get("safe_to_retry") is True:
        if action == "update":
            # A refused update leaves the live listing in place. Another Update is the seller's choice.
            return {"state": "published", **contract, "error": reason or "Could not update the live listing."}
        values = {**contract, "error": reason or "Could not finish."}
        if marketplace in {"depop", "etsy"} and result.get("draft_url"):
            candidate = draft_url(marketplace, result["draft_url"])
            if record.get("draft_url") and candidate != record["draft_url"]:
                raise NativeMarketplaceError(502, "Chrome answered for a different marketplace draft.")
            values["draft_url"] = candidate
        values["state"] = _resting_state(marketplace, {**record, **values})
        if action == "save_draft":
            # The draft does not hold this revision.
            values["revision_id"] = record.get("previous_revision_id")
        return values
    return {"state": "unknown", **contract, "error": reason or UNKNOWN, "error_code": "uncertain"}


def _apply_result(db: Session, conv_id: str, marketplace: str, job_id: str, action: str, result: dict) -> dict:
    record = _record(db, conv_id, marketplace)
    if record.get("job_id") != job_id:
        raise NativeMarketplaceError(409, "A newer marketplace action has replaced this result.")
    try:
        values = _outcome(marketplace, action, record, result if isinstance(result, dict) else {})
    except NativeMarketplaceError as exc:
        values = {"state": "unknown", **_CLEARED_ERROR, "error": str(exc), "error_code": "uncertain"}
    stored = _store(db, conv_id, marketplace, values)
    JobRepo(db).add_event(job_id, "native_result", f"native_{marketplace}_{action}", {
        "marketplace": marketplace, "action": action, "ok": result.get("ok") is True, "state": stored["state"],
        "error": stored.get("error"), "error_code": stored.get("error_code"),
        "marketplace_message": stored.get("marketplace_message"),
    })
    return stored


# --- the List / Update job ----------------------------------------------------

_cancel_requested: set[str] = set()


def _progress(db: Session, job_id: str, marketplace: str, step: str, index: int = 0, total: int = 1) -> None:
    JobRepo(db).add_event(job_id, PROGRESS_EVENT, f"native_{marketplace}_{step}", {
        "marketplace": marketplace, "step": step, "label": STEP_LABEL.get(step, step), "index": index, "total": total,
    })


def _plan(marketplace: str, kind: str, record: dict, revision_id: str) -> list[str]:
    if kind == "update":
        return ["update"]
    if marketplace in {"ebay", "poshmark"}:
        return ["list"]
    if marketplace == "sellwild":
        return ["publish"]
    current = record.get("state") == "draft" and record.get("revision_id") == revision_id and record.get("draft_url")
    return ["publish"] if current else ["save_draft", "publish"]


async def _preflight(db: Session, conv_id: str, marketplace: str, body: ListRequest) -> tuple[dict, list]:
    from vendoo_studio.routes.extension import extension_manager
    from vendoo_studio.services import activity
    from vendoo_studio.services.native_form import attach_form
    from vendoo_studio.services.streaming import active_generation

    _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    if not listing or listing.current_revision_id != body.revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    snapshot = ListingRepo(db).get_revision(body.revision_id).listing_json
    if not isinstance(snapshot, dict) or not str(snapshot.get("title") or "").strip():
        raise NativeMarketplaceError(400, "Add a listing title first.")
    snapshot = attach_form(copy.deepcopy(snapshot), marketplace)
    if active_generation(conv_id) or activity.running(conv_id):
        raise NativeMarketplaceError(409, "Wait for generation to finish.")
    if marketplace == "etsy":
        from vendoo_studio.models.etsy_fields import etsy_eligible

        try:
            etsy_listings.connected_shop_id()
        except ValueError as exc:
            raise NativeMarketplaceError(409, str(exc)) from exc
        if not etsy_eligible(snapshot):
            raise NativeMarketplaceError(409, etsy_listings.NOT_ELIGIBLE)
    elif marketplace == "ebay":
        from vendoo_studio.services.ebay_client import EbayClient
        from vendoo_studio.services.marketplace_accounts import SignInRequired

        try:
            await EbayClient.connect()
        except SignInRequired as exc:
            raise NativeMarketplaceError(409, "Connect eBay in Settings → Connections before listing.") from exc
    else:
        if not extension_manager.connected:
            raise NativeMarketplaceError(409, "Connect Chrome first.")
        if getattr(extension_manager, "build", None) != expected_extension_build():
            raise NativeMarketplaceError(409, "Connect Chrome to load the current Anteroom Bridge before continuing.")
    return snapshot, ConversationRepo(db).get_photos(conv_id)


async def _start(conv_id: str, marketplace: str, body: ListRequest, kind: str, db: Session) -> dict:
    name = NAMES[marketplace]
    if body.confirm is not True:
        raise NativeMarketplaceError(400, f"Confirm updating the live {name} listing." if kind == "update" else f"Confirm listing on {name}.")
    if kind == "update" and marketplace not in {"ebay", "etsy", "poshmark"}:
        raise NativeMarketplaceError(409, f"A live {name} listing cannot be updated from Anteroom yet.")
    snapshot, photos = await _preflight(db, conv_id, marketplace, body)
    if not photos:
        raise NativeMarketplaceError(400, "Add photos before updating the live listing." if kind == "update" else "Add photos before listing.")
    if marketplace == "sellwild" and kind == "list":
        save_local_sellwild_draft(conv_id, body, db)
    options = {}
    if marketplace == "etsy":
        from vendoo_studio.services import user_settings

        defaults = user_settings.get_etsy_listing_defaults()
        options = {"shipping_profile_id": body.shipping_profile_id or defaults.get("shipping_profile_id"),
                   "readiness_state_id": body.readiness_state_id or defaults.get("readiness_state_id"),
                   "package": body.etsy_package.model_dump() if body.etsy_package else listing_package(snapshot)}
    with start_gate():
        record = _record(db, conv_id, marketplace)
        if record.get("state") in UNSETTLED_STATES:
            raise NativeMarketplaceError(409, f"Check the last {name} action first; Anteroom has not confirmed it.")
        extra: dict = {}
        if kind == "update":
            from vendoo_studio.services.marketplace_delist import listings as live_listings

            live = live_listings(db, conv_id).get(marketplace) or {}
            if live.get("sold_here") or live.get("state") != "live" or not live.get("listing_id"):
                raise NativeMarketplaceError(409, f"This item is not live on {name}.")
            extra = {"listing_id": live["listing_id"], "listing_url": live.get("url")}
        elif record.get("state") in {"published", "pending_review"}:
            raise NativeMarketplaceError(409, f"This item is already listed on {name}.")
        if marketplace == "sellwild":
            if record.get("photo_ids") != [p.id for p in photos]:
                raise NativeMarketplaceError(409, "The photos changed. List on Sellwild again.")
            if not record.get("sellwild_category_id") or not record.get("sellwild_condition_id"):
                raise NativeMarketplaceError(409, "Choose a Sellwild category and condition first.")
        steps = _plan(marketplace, kind, record, body.revision_id)
        repo = JobRepo(db)
        if repo.get_active():
            raise NativeMarketplaceError(409, "Another automation job is queued or running. Wait for it to finish.")
        job = repo.create(conv_id, body.revision_id, snapshot, status="dispatched", current_step=f"native_{marketplace}_{steps[0]}")
        claim_api_job(job.id)
        record = _store(db, conv_id, marketplace, {
            **extra, **_CLEARED_ERROR, "state": STEP_STATE[steps[0]], "action": steps[0], "job_id": job.id,
            "operation_id": job.id, "previous_revision_id": record.get("revision_id"), "revision_id": body.revision_id,
        })
    _progress(db, job.id, marketplace, steps[0], 0, len(steps))
    _launch(run_job(job.id, conv_id, marketplace, steps, options))
    record["running"] = True
    return record


async def list_now(conv_id: str, marketplace: Marketplace, body: ListRequest, db: Session) -> dict:
    """Start listing on one marketplace in one seller-confirmed step. Returns at once; poll status."""
    return await _start(conv_id, marketplace, body, "list", db)


async def update_live(conv_id: str, marketplace: Marketplace, body: ListRequest, db: Session) -> dict:
    """Write the listing saved in Anteroom onto the one already live. One confirmation, never a second listing."""
    return await _start(conv_id, marketplace, body, "update", db)


def _active(db: Session, job_id: str, conv_id: str, marketplace: str) -> bool:
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES

    job = JobRepo(db).get(job_id)
    if job:
        db.refresh(job)
    return (bool(job) and job.status in ACTIVE_JOB_STATUSES and job_id not in _cancel_requested
            and _record(db, conv_id, marketplace).get("job_id") == job_id)


def _execute_payload(marketplace: str, step: str, record: dict, snapshot: dict, photos: list, job_id: str) -> dict:
    return {
        "marketplace": marketplace, "action": step, "confirm": step in {"publish", "list", "update"},
        "operation_id": record["operation_id"], "draft_url": record.get("draft_url"), "listing_id": record.get("listing_id"),
        "listing": snapshot, "local_draft_id": record.get("local_draft_id"), "revision_id": record.get("revision_id"),
        "photos": [{"id": p.id, "mime_type": p.mime_type} for p in photos],
        **({"sellwild": {"category_id": record["sellwild_category_id"], "condition_id": record["sellwild_condition_id"]}}
           if marketplace == "sellwild" else {}),
    }


def _result_context(db: Session, conv_id: str, marketplace: str, record: dict, snapshot: dict, candidate: str | None) -> dict:
    first_save = record.get("action") == "save_draft" and not record.get("previous_revision_id")
    return {"marketplace": marketplace, "action": record.get("action"), "operation_id": record.get("operation_id"),
            "draft_url": candidate, "listing_id": record.get("listing_id"), "local_draft_id": record.get("local_draft_id"),
            "revision_id": record.get("revision_id"), "photo_ids": record.get("photo_ids"),
            "expected_photo_count": len(ConversationRepo(db).get_photos(conv_id)) if first_save else None,
            "listing": {key: snapshot.get(key) for key in ("title", "description", "price")}}


async def _send_cancel(operation_id: str) -> None:
    from vendoo_studio.models.protocol import ProtocolMessage
    from vendoo_studio.routes.extension import extension_manager

    # Chrome journals the action under its operation id, so that is what it cancels.
    await extension_manager.send_message(ProtocolMessage(
        type="job.cancel", job_id=operation_id, payload={"job_id": operation_id},
    ).model_dump(mode="json"))


CHROME_SILENT = ("Chrome has not said whether this {name} action finished. Look for it on {name}: if it is there, "
                 "use Mark as listed; if not, choose “I checked it myself” and List again.")


async def _wait_for_chrome(db: Session, job, conv_id: str, marketplace: str, record: dict, snapshot: dict) -> dict:
    """Chrome's reply was lost or late. Stop the action, then hold the slot until Chrome reports what it did."""
    operation_id = record["operation_id"]
    await _send_cancel(operation_id)
    deadline = time.monotonic() + SETTLE_DEADLINE_S
    context = _result_context(db, conv_id, marketplace, record, snapshot, record.get("draft_url"))
    while time.monotonic() < deadline:
        _progress(db, job.id, marketplace, "waiting")
        try:
            reply = await browser_bridge.request(job, "marketplace.result", {"operation_id": operation_id, "context": context}, timeout=65)
            if reply.get("finished") and isinstance(reply.get("result"), dict):
                return reply["result"]
        except browser_bridge.BrowserBridgeError:
            pass
        await asyncio.sleep(SETTLE_POLL_S)
    return failure(CHROME_SILENT.format(name=NAMES[marketplace]), "uncertain", safe=False)


async def _execute_step(db: Session, job, conv_id: str, marketplace: str, step: str, options: dict) -> dict:
    snapshot = job.listing_snapshot if isinstance(job.listing_snapshot, dict) else {}
    photos = ConversationRepo(db).get_photos(conv_id)
    binding = _record(db, conv_id, marketplace)
    if marketplace in API_MARKETPLACES:
        def record(values: dict) -> None:
            binding.update(values)
            _store(db, conv_id, marketplace, values)

        def active() -> bool:
            return _active(db, job.id, conv_id, marketplace)

        if marketplace == "ebay":
            from vendoo_studio.services import ebay_listings

            result = await ebay_listings.execute(step, snapshot, photos, binding, record, active, confirm=True,
                                                 conversation_id=conv_id)
        else:
            result = await etsy_listings.execute(step, snapshot, photos, binding, record, active, confirm=True, **options)
        stored = _apply_result(db, conv_id, marketplace, job.id, step, result)
        if stored["state"] == "unknown":
            # A reply was lost. Read what the marketplace has now, while still holding the slot.
            _progress(db, job.id, marketplace, "checking")
            checked = await _read_result(db, conv_id, marketplace, stored, snapshot, stored.get("draft_url"), job)
            if checked is not None:
                stored = _apply_result(db, conv_id, marketplace, job.id, step, checked)
        return stored
    try:
        result = await browser_bridge.request(job, "marketplace.execute",
                                              _execute_payload(marketplace, step, binding, snapshot, photos, job.id),
                                              timeout=EXECUTE_TIMEOUT_S)
    except browser_bridge.BrowserBridgeError:
        result = await _wait_for_chrome(db, job, conv_id, marketplace, binding, snapshot)
    return _apply_result(db, conv_id, marketplace, job.id, step, result)


def _finish_job(db: Session, job_id: str, conv_id: str, marketplace: str) -> None:
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job or job.status not in {"dispatched", "queued", "awaiting_extension"}:
        return
    record = _record(db, conv_id, marketplace)
    if record.get("job_id") == job_id and record.get("state") in BUSY_STATES:
        # The worker ended without an answer for the step it was on.
        record = _store(db, conv_id, marketplace, {**_CLEARED_ERROR, "state": "unknown", "error": UNKNOWN, "error_code": "uncertain"})
    live = record.get("state") in {"published", "pending_review"} and not record.get("error")
    if job_id in _cancel_requested and not live:
        repo.update_status(job_id, "cancelled", job.current_step, error="Cancelled.")
        repo.add_event(job_id, "cancelled", job.current_step)
    elif record.get("job_id") == job_id and not record.get("error") and record.get("state") in {"published", "pending_review", "draft"}:
        repo.update_status(job_id, "completed", job.current_step)
    else:
        repo.update_status(job_id, "failed", job.current_step, error=record.get("error") or UNKNOWN)


async def run_job(job_id: str, conv_id: str, marketplace: str, steps: list[str], options: dict) -> None:
    """Run a List or Update job's steps in order, holding the automation slot until the last answer."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.routes.extension import schedule_advance_job_queue

    db = SessionLocal()
    try:
        for index, step in enumerate(steps):
            job = JobRepo(db).get(job_id)
            if not job or not _active(db, job_id, conv_id, marketplace):
                break
            if index:
                # Each step has its own Chrome journal entry.
                repo = JobRepo(db)
                repo.update_status(job_id, "dispatched", f"native_{marketplace}_{step}")
                _store(db, conv_id, marketplace, {"state": STEP_STATE[step], "action": step,
                                                  "operation_id": f"{job_id}-{index + 1}"})
                _progress(db, job_id, marketplace, step, index, len(steps))
                job = repo.get(job_id)
            record = await _execute_step(db, job, conv_id, marketplace, step, options)
            if step == "save_draft" and not (record.get("state") == "draft" and not record.get("error") and record.get("draft_url")):
                break
    except NativeMarketplaceError:
        # A newer action owns the record; leave it as that action wrote it.
        pass
    except Exception:  # noqa: BLE001 - the job and record still have to end
        log.exception("Direct %s job %s failed", marketplace, job_id)
        db.rollback()
    finally:
        try:
            _finish_job(db, job_id, conv_id, marketplace)
        finally:
            release_api_job(job_id)
            _cancel_requested.discard(job_id)
            db.close()
            schedule_advance_job_queue()


async def cancel(conv_id: str, marketplace: Marketplace, db: Session) -> dict:
    """Stop a running List or Update at its next safe point. The job keeps the slot until the work stops."""
    record = _record(db, conv_id, marketplace)
    if not _running(record):
        raise NativeMarketplaceError(409, f"No {NAMES[marketplace]} action is running.")
    await request_cancel(db, record["job_id"], record)
    return status(conv_id, db)[marketplace]


async def cancel_job(db: Session, job) -> None:
    """Cancel from the jobs list: stop a live direct job, or end one nothing runs any more."""
    marketplace = str(job.current_step or "").removeprefix("native_").split("_", 1)[0]
    record = _record(db, job.conversation_id, marketplace) if marketplace in NAMES else {}
    if api_job_is_live(job.id):
        await request_cancel(db, job.id, record)
        return
    JobRepo(db).update_status(job.id, "cancelled", job.current_step, error="Cancelled.")
    mark_interrupted(db, job.id)


async def request_cancel(db: Session, job_id: str, record: dict) -> None:
    if job_id in _cancel_requested:
        return
    _cancel_requested.add(job_id)
    JobRepo(db).add_event(job_id, "native_cancel_requested", None, {"marketplace": record.get("marketplace")})
    if record.get("operation_id"):
        await _send_cancel(record["operation_id"])


# --- Check, Reset and automatic reconcile --------------------------------------

async def _read_result(db: Session, conv_id: str, marketplace: str, record: dict, snapshot: dict,
                       candidate: str | None, job) -> dict | None:
    """Read the action's result from the marketplace (or Chrome's journal). Never writes.

    None means Chrome has no journal for it: the seller has to look.
    """
    if marketplace == "etsy":
        return await etsy_listings.check(snapshot, record, candidate)
    if marketplace == "ebay":
        from vendoo_studio.services import ebay_listings

        def remember(values: dict) -> None:
            _store(db, conv_id, marketplace, values)

        return await ebay_listings.check(record, snapshot if record.get("action") == "update" else None, remember)
    try:
        reply = await browser_bridge.request(job, "marketplace.result", {
            "operation_id": record.get("operation_id"),
            "context": _result_context(db, conv_id, marketplace, record, snapshot, candidate),
        }, timeout=65)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not check the result. Connect Chrome and check again.") from exc
    if not reply.get("finished") or not isinstance(reply.get("result"), dict):
        return None
    return reply["result"]


async def check_result(conv_id: str, marketplace: Marketplace, db: Session, body: CheckRequest | None = None) -> dict:
    """Read-only: ask the marketplace what the last action did, and record it."""
    from vendoo_studio.routes.extension import extension_manager, schedule_advance_job_queue

    _conversation(db, conv_id)
    record = _record(db, conv_id, marketplace)
    if not record.get("job_id"):
        raise NativeMarketplaceError(409, "No marketplace action to check.")
    if _running(record):
        raise NativeMarketplaceError(409, f"The {NAMES[marketplace]} action is still running. Wait for it to finish.")
    if marketplace not in API_MARKETPLACES and not extension_manager.connected:
        raise NativeMarketplaceError(409, "Connect Chrome to check the result.")
    action_job = JobRepo(db).get(record["job_id"])
    snapshot = action_job.listing_snapshot if action_job and isinstance(action_job.listing_snapshot, dict) else {}
    candidate = draft_url(marketplace, body.draft_url) if body and body.draft_url else record.get("draft_url")
    if record.get("draft_url") and candidate != record["draft_url"]:
        raise NativeMarketplaceError(409, "Check the draft already linked to this listing.")
    repo = JobRepo(db)
    with start_gate():
        if repo.get_active():
            raise NativeMarketplaceError(409, "Another automation job is running. Check again when it finishes.")
        # Reading Chrome's journal can open the marketplace tab: hold the slot like any other job.
        check_job = repo.create(conv_id, record.get("revision_id") or "", snapshot, status="dispatched",
                                current_step=f"native_{marketplace}_check")
        claim_api_job(check_job.id)
    try:
        result = await _read_result(db, conv_id, marketplace, record, snapshot, candidate, check_job)
        if result is None:
            stored = _store(db, conv_id, marketplace, {**_CLEARED_ERROR, "state": "unknown", "error_code": "uncertain",
                                                       "error": CHROME_SILENT.format(name=NAMES[marketplace])})
        else:
            stored = _apply_result(db, conv_id, marketplace, record["job_id"], record.get("action") or "", result)
        settled = stored["state"] not in UNSETTLED_STATES
        if action_job and settled and not stored.get("error"):
            if action_job.status in {"failed", "dispatched"}:
                action_job.status, action_job.last_error = "completed", None
                db.commit()
                repo.add_event(action_job.id, "native_result_verified", action_job.current_step)
        repo.update_status(check_job.id, "completed")
        return stored
    except BaseException:
        repo.update_status(check_job.id, "failed", error="Check did not finish.")
        raise
    finally:
        release_api_job(check_job.id)
        schedule_advance_job_queue()


def reset(conv_id: str, marketplace: Marketplace, db: Session) -> dict:
    """The seller looked on the marketplace themselves. Clear an unconfirmed record; never touch the marketplace."""
    _conversation(db, conv_id)
    with start_gate():
        record = _record(db, conv_id, marketplace)
        if record.get("state") not in UNSETTLED_STATES:
            raise NativeMarketplaceError(409, f"Nothing on {NAMES[marketplace]} is waiting to be confirmed.")
        if _running(record):
            raise NativeMarketplaceError(409, f"The {NAMES[marketplace]} action is still running. Cancel it first.")
        state = "published" if record.get("action") == "update" else _resting_state(marketplace, record)
        values = {**_CLEARED_ERROR, "state": state, "etsy_pending_write": None}
        if state != "published":
            # Whatever the marketplace holds is not known to match a revision.
            values["revision_id"] = None
        stored = _store(db, conv_id, marketplace, values)
    if JobRepo(db).get(record["job_id"]):
        JobRepo(db).add_event(record["job_id"], "native_reset", f"native_{marketplace}_{record.get('action')}",
                              {"marketplace": marketplace, "from": record.get("state"), "to": state})
    return stored


def mark_interrupted(db: Session, job_id: str) -> None:
    """A job died with this process or stalled: its record is unconfirmed until checked."""
    repo = MarketplaceListingRepo(db)
    for row in repo.unsettled():
        if row.job_id == job_id and row.state in BUSY_STATES:
            repo.save(row.conversation_id, row.marketplace, {**_CLEARED_ERROR, "state": "unknown", "error": UNKNOWN,
                                                             "error_code": "uncertain"})


async def reconcile(*, chrome: bool) -> int:
    """Check every unconfirmed record that no job is running. Read-only; never lists or publishes.

    Runs on startup (eBay and Etsy) and when Chrome reconnects (all). A record
    whose check cannot run now (another job holds the slot) waits for the next one.
    """
    from vendoo_studio.database import SessionLocal

    checked = 0
    with SessionLocal() as db:
        targets = [(row.conversation_id, row.marketplace, row.job_id) for row in MarketplaceListingRepo(db).unsettled()]
        for conv_id, marketplace, job_id in targets:
            if not job_id or api_job_is_live(job_id) or (marketplace not in API_MARKETPLACES and not chrome):
                continue
            try:
                await check_result(conv_id, marketplace, db)
                checked += 1
            except NativeMarketplaceError as exc:
                log.info("Reconcile skipped %s for %s: %s", marketplace, conv_id, exc)
            except Exception:  # noqa: BLE001 - one record must not stop the rest
                log.exception("Reconcile failed for %s on %s", conv_id, marketplace)
    return checked


async def reconcile_then_dispatch() -> None:
    """On Chrome reconnect: settle what Chrome may have finished, then start the queue."""
    from vendoo_studio.routes.extension import dispatch_queued_jobs

    try:
        await reconcile(chrome=True)
    except Exception:  # noqa: BLE001 - the queue must still start
        log.exception("Reconnect reconcile failed")
    await dispatch_queued_jobs()


async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest, db: Session):
    """Read a marketplace page's form. Holds the automation slot so a Send cannot start into a busy Chrome."""
    from vendoo_studio.routes.extension import schedule_advance_job_queue

    _conversation(db, conv_id)
    repo = JobRepo(db)
    with start_gate():
        if repo.get_active():
            raise NativeMarketplaceError(409, "Wait for the current Chrome job to finish.")
        job = repo.create(conv_id, "", {}, status="dispatched", current_step=f"native_{marketplace}_inspect")
        claim_api_job(job.id)
    try:
        reply = await browser_bridge.request(job, "marketplace.inspect", {
            "marketplace": marketplace, "script_url": body.script_url, "background": body.background,
        }, timeout=45)
        repo.update_status(job.id, "completed")
        return reply
    except browser_bridge.BrowserBridgeError as exc:
        repo.update_status(job.id, "failed", error="Chrome could not inspect the marketplace page.")
        raise NativeMarketplaceError(502, "Chrome could not inspect the marketplace page.") from exc
    finally:
        release_api_job(job.id)
        schedule_advance_job_queue()


def listing_package(snapshot: dict) -> dict | None:
    """Etsy's packed weight (oz) and size (in) from the listing's own package fields."""
    try:
        ounces = float(snapshot.get("weight_lb") or 0) * 16 + float(snapshot.get("weight_oz") or 0)
    except (TypeError, ValueError):
        return None
    sides = [float(side) for side in re.findall(r"\d+(?:\.\d+)?", str(snapshot.get("package_dimensions_in") or ""))]
    if ounces <= 0 or len(sides) != 3 or min(sides) <= 0:
        return None
    return {"item_weight": round(ounces, 2), "item_length": sides[0], "item_width": sides[1], "item_height": sides[2],
            "item_weight_unit": "oz", "item_dimensions_unit": "in"}


@cache
def sellwild_catalog() -> dict:
    """Sellwild's own categories and conditions. Vendoo's Sellwild IDs are retired there."""
    import json

    from vendoo_studio.config import skills_dir

    return json.loads((skills_dir() / "list-this" / "references" / "sellwild-native-catalog.json").read_text(encoding="utf-8"))


# Marketplace and general category words that name the same Sellwild leaf.
_SELLWILD_SYNONYMS = {"tee": "shirt", "tshirt": "shirt", "tees": "shirt", "blouse": "top", "tank": "top", "cami": "top",
                      "jumper": "sweater", "sweatshirt": "sweater", "cardigan": "sweater", "pullover": "sweater",
                      "vest": "jacket", "gilet": "jacket", "parka": "coat", "trouser": "pant", "legging": "activewear",
                      "sneaker": "sneaker", "trainer": "sneaker", "bag": "handbag", "purse": "handbag"}


def _sellwild_words(text: str) -> set[str]:
    text = re.sub(r"\b(short|long|3/4)[ -]sleeves?\b", " ", text.lower()).replace("t-shirt", "tshirt")
    words = set()
    for word in re.findall(r"[a-z0-9]+", text):
        word = word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        words.add(_SELLWILD_SYNONYMS.get(word, word))
    return words


def _sellwild_text_sources(listing: dict) -> list[str]:
    """Where the listing already says what it is, most specific first."""
    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    depop = str((listing.get("marketplace_category_ids") or {}).get("depop") or "")
    # eBay's top level names every clothing branch; it says nothing about the item.
    general = re.sub(r"clothing,? shoes (&|and) accessories", " ", str(listing.get("category_path") or ""), flags=re.I)
    return [" ".join(map(str, poshmark[1:])), depop.split("_", 1)[-1].replace("-", " "),
            " ".join(general.replace(">", "/").split("/")[-2:]), str(listing.get("title") or "")]


def suggest_sellwild(listing: dict) -> dict:
    """Sellwild's own category and condition, worked out from what the listing already says."""
    catalog = sellwild_catalog()
    ids = {option["id"] for option in catalog["categories"]}
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    explicit = specifics.get("nativeCategoryId")
    category_id = int(explicit) if str(explicit or "").isdigit() and int(explicit) in ids else None
    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    everything = " ".join([str(listing.get("category_path") or ""), " ".join(map(str, poshmark)),
                           str((listing.get("marketplace_category_ids") or {}).get("depop") or "")]).lower()
    if category_id is None and re.search(r"\bbaby\b|infant|newborn", everything):
        category_id = 66
    elif category_id is None and re.search(r"\bkids?\b|kidswear|\bboys?\b|\bgirls?\b", everything):
        category_id = 246
    # Vendoo's (retired) Sellwild category still names the right top-level group.
    vendoo_group = str((((listing.get("marketplace_category_objects") or {}).get("sellwild") or {}).get("displayPath") or [""])[0] or "")
    clothing = bool(re.search(r"clothing|menswear|womenswear|\bmen\b|\bwomen\b|shoes|accessories", everything))
    group = vendoo_group or ("Fashion" if clothing else "")
    if category_id is None:
        for text in _sellwild_text_sources(listing):
            words = _sellwild_words(text)
            best, score = None, 0
            for option in catalog["categories"]:
                parent, _, leaf = option["label"].partition(" > ")
                if group and parent != group:
                    continue
                hits = len(_sellwild_words(leaf.replace("&", " ").replace(",", " ")) & words)
                if hits > score:
                    best, score = option["id"], hits
            if best is not None:
                category_id = best
                break
    native_condition = specifics.get("nativeConditionId")
    condition = str(listing.get("condition") or "").lower()
    condition_id = (int(native_condition) if str(native_condition or "") in {"1", "2", "3", "4", "5"}
                    else 1 if re.search(r"poor|major flaw", condition)
                    else 4 if re.search(r"new without tags|nwot|like new", condition)
                    else 3 if re.search(r"new with defects|imperfection", condition)
                    else 5 if re.search(r"new with tags|nwt|brand new|^new\b", condition)
                    else 2 if re.search(r"pre-owned|used|good|fair|excellent", condition) else None)
    return {"category_id": category_id, "condition_id": condition_id}


def sellwild_options(conv_id: str, db: Session) -> dict:
    _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    snapshot = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    catalog = sellwild_catalog()
    return {"categories": catalog["categories"], "conditions": catalog["conditions"], "suggested": suggest_sellwild(snapshot)}


def save_local_sellwild_draft(conv_id: str, body: ListRequest, db: Session) -> dict:
    """Record Sellwild's category and condition in Anteroom; Sellwild has no draft of its own."""
    import uuid

    _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    if not listing or listing.current_revision_id != body.revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    snapshot = ListingRepo(db).get_revision(body.revision_id).listing_json
    if not isinstance(snapshot, dict) or not str(snapshot.get("title") or "").strip():
        raise NativeMarketplaceError(400, "Add a listing title first.")
    catalog = sellwild_catalog()
    automatic = suggest_sellwild(snapshot)
    category_id = body.sellwild_category_id or automatic["category_id"]
    condition_id = body.sellwild_condition_id or automatic["condition_id"]
    if category_id not in {option["id"] for option in catalog["categories"]}:
        raise NativeMarketplaceError(400, "Anteroom couldn't match a Sellwild category for this item. Choose one with Change.")
    if condition_id not in {option["id"] for option in catalog["conditions"]}:
        raise NativeMarketplaceError(400, "Anteroom couldn't match a Sellwild condition for this item. Choose one with Change.")
    with start_gate():
        previous = _record(db, conv_id, "sellwild")
        if previous.get("state") in {*UNSETTLED_STATES, "published", "pending_review"}:
            raise NativeMarketplaceError(409, "Check the previous Sellwild action before listing again.")
        return _store(db, conv_id, "sellwild", {
            **_CLEARED_ERROR, "storage": "local", "state": "draft", "revision_id": body.revision_id,
            "photo_ids": [p.id for p in ConversationRepo(db).get_photos(conv_id)],
            "local_draft_id": previous.get("local_draft_id") or uuid.uuid4().hex,
            "sellwild_category_id": category_id, "sellwild_condition_id": condition_id,
        })
