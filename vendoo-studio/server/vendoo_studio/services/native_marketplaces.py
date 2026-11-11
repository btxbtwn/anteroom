"""Seller-confirmed direct listings on eBay, Etsy, Poshmark, Mercari and Depop.

List is one seller-confirmed step. It runs as one background job that holds
its marketplace's slot from the first write to the last answer: saving the
marketplace's own draft (Mercari, Depop, Etsy) and publishing it are steps of
the same job, so no other action on that marketplace can start between them,
and a job whose Chrome reply is late keeps the slot until Chrome reports what it did.

What Anteroom knows lives in ``marketplace_listings`` (one row per item and
marketplace). Nothing here retries a write on its own. A reply that never came
leaves the record ``unknown``; Check reads the marketplace (never writes) and
runs by itself after a lost reply, on startup and when Chrome reconnects.
Reset is the seller saying they looked themselves.

Update writes the saved revision onto the listing already live, on every
marketplace here, after one seller confirmation. It never publishes a draft,
relists or creates a second listing; each marketplace's answer is checked by
reading the listing back.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import in_worker_session
from vendoo_studio.models.marketplace_listing import BUSY_STATES, LIVE_STATES, UNSETTLED_STATES
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import browser_bridge, direct_preflight, etsy_listings, listing_readiness, marketplace_media
from vendoo_studio.services.api_job_lock import (
    claim_api_job, job_cancel_requested, job_is_live, release_api_job, request_job_cancel, start_gate,
)
from vendoo_studio.services.listing_result import ERROR_CODES, failure, scrub
from vendoo_studio.services.seller_overrides import price_for
log = logging.getLogger("vendoo_studio.native_marketplaces")


# Why a refusal happened, when the app acts on it: a busy marketplace means the
# seller's confirmed action waits its turn instead of failing.
BUSY = "marketplace_busy"


class NativeMarketplaceError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, reason: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.reason = reason


Marketplace = Literal["ebay", "poshmark", "mercari", "depop", "etsy"]
InspectionMarketplace = Literal["poshmark", "mercari", "depop", "etsy"]
API_MARKETPLACES = frozenset({"ebay", "etsy"})
DRAFT_MARKETPLACES = frozenset({"ebay", "mercari", "depop", "etsy", "poshmark"})
NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}
UNKNOWN = "Anteroom could not confirm what happened. Check the result before trying again."
# How long Chrome has to answer one step, and how long a job then keeps the
# slot waiting for Chrome to report what it did before giving up as unknown.
EXECUTE_TIMEOUT_S = 270
SETTLE_DEADLINE_S = 8 * 60
SETTLE_POLL_S = 10.0
STEP_STATE = {"save_draft": "saving", "publish": "publishing", "list": "publishing", "update": "updating"}
STEP_LABEL = {"save_draft": "Saving the listing", "publish": "Publishing", "list": "Listing", "update": "Updating the live listing",
              "waiting": "Waiting for Chrome to confirm", "checking": "Checking the result"}
PROGRESS_EVENT = "native_progress"
# How often a waiting step looks for the seller's Cancel.
CANCEL_POLL_S = 1.0


def step_label(current_step: str) -> str | None:
    """A direct job's step as the seller reads it, e.g. "Poshmark: Listing…"; None for other jobs."""
    if not current_step.startswith("native_"):
        return None
    _, marketplace, step = (current_step.split("_", 2) + ["", ""])[:3]
    return f"{NAMES.get(marketplace, marketplace)}: {STEP_LABEL.get(step, 'Working')}…"


def is_direct_action(current_step: str) -> bool:
    """A direct marketplace job. It holds only its own marketplace, never the listing."""
    return any(current_step.startswith(f"native_{name}_") for name in NAMES)


class EtsyPackage(BaseModel):
    item_weight: float = Field(allow_inf_nan=False)
    item_length: float = Field(allow_inf_nan=False)
    item_width: float = Field(allow_inf_nan=False)
    item_height: float = Field(allow_inf_nan=False)
    item_weight_unit: str
    item_dimensions_unit: str


class ListRequest(BaseModel):
    revision_id: str
    confirm: bool = False
    shipping_profile_id: str | None = None
    readiness_state_id: str | None = None
    return_policy_id: str | None = None
    etsy_package: EtsyPackage | None = None


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


def _running(db: Session, record: dict) -> bool:
    return bool(record.get("job_id")) and job_is_live(db, record["job_id"])


# Listing steps that may run beside a listing step on another marketplace.
SIDE_BY_SIDE_STEPS = frozenset(STEP_STATE) | {"check"}


def blocking_job(repo: JobRepo, marketplace: str | None):
    """The active job that stops a new action on ``marketplace``, if any.

    Listing actions on different marketplaces run side by side; one
    marketplace runs one action at a time. Delists and page inspections still
    have Chrome to themselves. With no marketplace, only those exclusive jobs
    count.
    """
    for job in repo.get_active():
        step = str(job.current_step or "")
        other = next((name for name in NAMES if step.startswith(f"native_{name}_")), None)
        if other is None or other == marketplace or step.removeprefix(f"native_{other}_") not in SIDE_BY_SIDE_STEPS:
            return job
    return None


def _busy_message(job, marketplace: str) -> str:
    if str(job.current_step or "").startswith(f"native_{marketplace}_"):
        return f"Another {NAMES[marketplace]} action is running. Wait for it to finish."
    return "Another automation job is running. Wait for it to finish."


def running_marketplace(db: Session, conv_id: str) -> str | None:
    """The marketplace a direct action is running on for this item, if any."""
    for marketplace, record in MarketplaceListingRepo(db).for_conversation(conv_id).items():
        if _running(db, record):
            return NAMES.get(marketplace, marketplace)
    return None


def status(conv_id: str, db: Session) -> dict:
    """Each marketplace's record, with what a running action is doing now."""
    _conversation(db, conv_id)
    records = MarketplaceListingRepo(db).for_conversation(conv_id)
    repo = JobRepo(db)
    for record in records.values():
        record["running"] = _running(db, record)
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
        else (r"/edit-listing/[a-f0-9]{24}", "poshmark.com") if marketplace == "poshmark"
        else (r"/sell/draft/[A-Za-z0-9_-]+/", "www.mercari.com")
    )
    if (parsed.scheme != "https" or parsed.netloc != host or not re.fullmatch(pattern, parsed.path)
            or (marketplace == "poshmark" and (parsed.query or parsed.fragment))):
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


def _draft_gone(result: dict) -> bool:
    """Depop saying the saved draft was deleted."""
    text = f"{result.get('error') or ''} {result.get('marketplace_message') or ''}"
    return "could not find draft" in text.lower()


def _outcome(marketplace: str, action: str, record: dict, result: dict) -> dict:
    """The record's new fields for one step's result."""
    reason = str(result.get("error") or "").strip()
    if result.get("ok") is True:
        url = None
        # eBay's saved draft is an unpublished offer, which has no page to link.
        if marketplace != "ebay" and action in {"save_draft", "publish"}:
            url = draft_url(marketplace, str(result.get("draft_url") or ""))
            replaced = marketplace == "depop" and result.get("draft_replaced") is True
            if record.get("draft_url") and url != record["draft_url"] and not replaced:
                raise NativeMarketplaceError(502, "Chrome answered for a different draft. Check the native listing.")
        expected = "draft" if action == "save_draft" else "published"
        state = result.get("state")
        if state != expected:
            return {"state": "unknown", **_CLEARED_ERROR, "error": UNKNOWN, "error_code": "uncertain"}
        values = {"state": state, **_CLEARED_ERROR}
        # Listed, but a setting did not stick (Poshmark's Smart Pricing). Shown on the live row.
        warning = str(result.get("warning") or "").strip()
        if warning and state in {"published", "pending_review"}:
            values["error"] = warning[:300]
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

            answered = listing_id(marketplace, values.get("listing_url"))
            stored = record.get("listing_id")
            if answered != stored:
                # A Depop category change rewrites the public slug. Accept it when
                # the extension loaded the product we already had and Depop returned the new one.
                previous = str(result.get("previous_slug") or "").strip().casefold()
                if marketplace == "depop" and previous and previous == str(stored or "").strip().casefold() and answered:
                    values["listing_id"] = answered
                else:
                    raise NativeMarketplaceError(502, "The marketplace answered for a different listing. The live one was not replaced.")
        return values
    contract = _contract(result)
    if result.get("safe_to_retry") is True:
        if action == "update":
            # A refused update leaves the live listing in place. Another Update is the seller's choice.
            return {"state": "published", **contract, "error": reason or "Could not update the live listing."}
        values = {**contract, "error": reason or "Could not finish."}
        if marketplace == "depop" and _draft_gone(result):
            values["draft_url"] = None
        elif marketplace in {"depop", "etsy"} and result.get("draft_url"):
            candidate = draft_url(marketplace, result["draft_url"])
            replaced = marketplace == "depop" and result.get("draft_replaced") is True
            # A refusal that names some other draft stays on the draft already linked
            # to this listing. The refusal is known, so List can run again.
            if replaced or not record.get("draft_url") or candidate == record["draft_url"]:
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
    if isinstance(result, dict) and result.get("ok") is True and values["state"] in LIVE_STATES:
        # Confirmed live: the marketplace has the price this action sent.
        job = JobRepo(db).get(job_id)
        values["listed_price"] = price_for(job.listing_snapshot if job else None, marketplace)
        if action in {"publish", "list"}:
            # A new listing went up, so its age starts over. An Update keeps the date.
            values["listed_at"] = datetime.now(UTC).replace(tzinfo=None)
    stored = _store(db, conv_id, marketplace, values)
    JobRepo(db).add_event(job_id, "native_result", f"native_{marketplace}_{action}", {
        "marketplace": marketplace, "action": action, "ok": result.get("ok") is True, "state": stored["state"],
        "error": stored.get("error"), "error_code": stored.get("error_code"),
        "marketplace_message": stored.get("marketplace_message"),
    })
    return stored


# --- the List / Update job ----------------------------------------------------

def _progress(db: Session, job_id: str, marketplace: str, step: str, index: int = 0, total: int = 1) -> None:
    JobRepo(db).add_event(job_id, PROGRESS_EVENT, f"native_{marketplace}_{step}", {
        "marketplace": marketplace, "step": step, "label": STEP_LABEL.get(step, step), "index": index, "total": total,
    })


def _plan(marketplace: str, kind: str, record: dict, revision_id: str) -> list[str]:
    if kind == "save_draft":
        return ["save_draft"]
    if kind == "update":
        return ["update"]
    if marketplace in {"ebay", "poshmark"}:
        return ["list"]
    current = record.get("state") == "draft" and record.get("revision_id") == revision_id and record.get("draft_url")
    return ["publish"] if current else ["save_draft", "publish"]


def _preflight(db: Session, conv_id: str, marketplace: str, body: ListRequest) -> tuple[dict, list]:
    from vendoo_studio.services.extension_connection import marketplace_actions_refusal
    from vendoo_studio.services import activity
    from vendoo_studio.services.streaming import active_generation

    conv = _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    if not listing or listing.current_revision_id != body.revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    revision = ListingRepo(db).get_revision(body.revision_id)
    if revision is None:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    snapshot = revision.listing_json
    if not isinstance(snapshot, dict):
        raise NativeMarketplaceError(400, "The saved listing could not be read.")
    snapshot = direct_preflight.prepare(db, conv, snapshot, marketplace)
    from vendoo_studio.services.listing_videos import VIDEO_MARKETPLACES, selected_video

    if marketplace in VIDEO_MARKETPLACES:
        try:
            snapshot["_native_video"] = selected_video(db, conv_id, snapshot, marketplace)
        except ValueError as exc:
            raise NativeMarketplaceError(400, str(exc)) from exc
    if active_generation(conv_id) or activity.running(conv_id):
        raise NativeMarketplaceError(409, "Wait for generation to finish.")
    if marketplace == "etsy":
        try:
            etsy_listings.connected_shop_id()
        except ValueError as exc:
            raise NativeMarketplaceError(409, str(exc)) from exc
    elif marketplace != "ebay" and (refusal := marketplace_actions_refusal()):
        raise NativeMarketplaceError(409, refusal)
    photos, warning = _photos(db, conv_id, snapshot, marketplace)
    snapshot["_approved_photo_warning"] = warning
    return snapshot, photos


async def _require_ebay() -> None:
    from vendoo_studio.services.ebay_client import EbayClient
    from vendoo_studio.services.marketplace_accounts import SignInRequired

    try:
        await EbayClient.connect()
    except SignInRequired as exc:
        raise NativeMarketplaceError(409, "Connect eBay in Settings → Connections before listing.") from exc


def _photos(db: Session, conv_id: str, snapshot: dict, marketplace: str) -> tuple[list, str]:
    """This marketplace's photos for the listing being sent, and a warning when its limit leaves some off."""
    return marketplace_media.photos_for(snapshot, ConversationRepo(db).get_all_photos(conv_id), marketplace)


async def _start(conv_id: str, marketplace: str, body: ListRequest, kind: str) -> dict:
    """Check, ask Chrome or eBay, then open the job. Each database step runs on a worker thread
    with its own session, so no pooled connection is held while Chrome or eBay answers."""
    snapshot, photos, deleted_here = await in_worker_session(_check_start, conv_id, marketplace, body, kind)
    if marketplace == "ebay":
        await _require_ebay()
    from vendoo_studio.services.chrome_catalog import live_preview

    preview = await live_preview(snapshot) if marketplace in {"poshmark", "depop"} else None
    options = await in_worker_session(_after_preview, marketplace, body, snapshot, preview)
    from vendoo_studio.services import approved_photos

    # The copies are made before the start gate, off the event loop: on the web each photo is a
    # Storage download and upload, and neither the gate nor the server should wait on them.
    try:
        snapshot[approved_photos.KEY] = await asyncio.to_thread(approved_photos.capture, photos)
    except (OSError, ValueError) as exc:
        raise NativeMarketplaceError(409, "Could not preserve the approved photos. Review them and try again.") from exc

    async def open_and_launch() -> dict:
        record, job_id, steps = await in_worker_session(
            _open_job, conv_id, marketplace, body, kind, snapshot, photos, deleted_here)
        _launch(run_job(job_id, conv_id, marketplace, steps, options))
        record["running"] = True
        return record

    # A claimed job always gets its worker, even if the request is cancelled while the job opens.
    return await asyncio.shield(open_and_launch())


def _check_start(db: Session, conv_id: str, marketplace: str, body: ListRequest, kind: str) -> tuple[dict, list, bool]:
    from vendoo_studio.services import listing_mode

    listing_mode.require("direct", db)
    name = NAMES[marketplace]
    if kind == "save_draft" and marketplace not in DRAFT_MARKETPLACES:
        raise NativeMarketplaceError(400, f"Saving a private {name} draft is not supported.")
    if kind != "save_draft" and body.confirm is not True:
        raise NativeMarketplaceError(400, f"Confirm updating the live {name} listing." if kind == "update" else f"Confirm listing on {name}.")
    # The seller deleted the listing this record last published, so nothing is live there.
    deleted_here = False
    if kind in {"list", "save_draft"}:
        from vendoo_studio.services.marketplace_delist import listings as live_listings

        conv = _conversation(db, conv_id)
        listings = live_listings(db, conv_id)
        if conv.status == "sold" or any(entry.get("sold_here") for entry in listings.values()):
            raise NativeMarketplaceError(409, "This item has sold. It cannot be listed again.")
        existing = listings.get(marketplace) or {}
        if existing.get("listing_id") and existing.get("state") != "deleted":
            raise NativeMarketplaceError(409, f"This item is already listed on {name}.")
        deleted_here = existing.get("state") == "deleted"
    snapshot, photos = _preflight(db, conv_id, marketplace, body)
    return snapshot, photos, deleted_here


def _after_preview(db: Session, marketplace: str, body: ListRequest, snapshot: dict, preview: dict | None) -> dict:
    """Keep the category ids Chrome resolved, and return the job's marketplace options."""
    from vendoo_studio.services.chrome_catalog import apply_direct_ids, remember_direct_categories

    if preview and apply_direct_ids(snapshot, preview.get("categories") or {}):
        remember_direct_categories(db, ListingRepo(db).get_revision(body.revision_id), snapshot.get("direct_category_ids") or {})
    # Local field checks are advisory. The seller-confirmed attempt goes to
    # the marketplace, whose response is recorded for correction and retry.
    options = {}
    if marketplace == "etsy":
        from vendoo_studio.services import user_settings

        defaults = user_settings.get_etsy_listing_defaults()
        options = {"shipping_profile_id": body.shipping_profile_id or defaults.get("shipping_profile_id"),
                   "readiness_state_id": body.readiness_state_id or defaults.get("readiness_state_id"),
                   "return_policy_id": body.return_policy_id or defaults.get("return_policy_id"),
                   "package": body.etsy_package.model_dump() if body.etsy_package else listing_package(snapshot)}
    return options


def _open_job(db: Session, conv_id: str, marketplace: str, body: ListRequest, kind: str, snapshot: dict,
              photos: list, deleted_here: bool) -> tuple[dict, str, list[str]]:
    from vendoo_studio.services import approved_photos, listing_mode

    name = NAMES[marketplace]
    job = None
    try:
        with listing_mode.guard(db, "direct"):
            record = _record(db, conv_id, marketplace)
            if record.get("state") in UNSETTLED_STATES:
                raise NativeMarketplaceError(409, f"Check the last {name} action first; Anteroom has not confirmed it.")
            extra: dict = {}
            # A Mercari update keeps the photos already on the live item.
            if marketplace in {"poshmark", "depop"} or (marketplace == "mercari" and kind != "update"):
                extra["photo_ids"] = [photo.id for photo in photos]
            if kind == "update":
                from vendoo_studio.services.marketplace_delist import listings as live_listings

                live = live_listings(db, conv_id).get(marketplace) or {}
                if live.get("sold_here") or live.get("state") != "live" or not live.get("listing_id"):
                    raise NativeMarketplaceError(409, f"This item is not live on {name}.")
                extra.update(listing_id=live["listing_id"], listing_url=live.get("url"))
            elif record.get("state") in {"published", "pending_review"} and not deleted_here:
                raise NativeMarketplaceError(409, f"This item is already listed on {name}.")
            steps = _plan(marketplace, kind, record, body.revision_id)
            repo = JobRepo(db)
            if (busy := blocking_job(repo, marketplace)) is not None:
                raise NativeMarketplaceError(409, _busy_message(busy, marketplace), reason=BUSY)
            if steps == ["publish"]:
                previous_job_id = record.get("job_id")
                previous_job = repo.get(previous_job_id) if previous_job_id else None
                previous_photos = (previous_job.listing_snapshot or {}).get(approved_photos.KEY, []) if previous_job else []
                previous_versions = [(row["id"], row["checksum"]) for row in previous_photos]
                current_versions = [(row["id"], row["checksum"]) for row in snapshot[approved_photos.KEY]]
                if previous_versions != current_versions:
                    steps = ["save_draft", "publish"]
            job = repo.create(conv_id, body.revision_id, snapshot, status="dispatched", current_step=f"native_{marketplace}_{steps[0]}")
            claim_api_job(db, job.id)
            record = _store(db, conv_id, marketplace, {
                **extra, **_CLEARED_ERROR, "state": STEP_STATE[steps[0]], "action": steps[0], "job_id": job.id,
                "operation_id": job.id, "previous_revision_id": record.get("revision_id"), "revision_id": body.revision_id,
            })
    except BaseException:
        if job is None:
            # Refused or failed before the job existed: nothing owns the copies.
            approved_photos.release(snapshot)
        raise
    _progress(db, job.id, marketplace, steps[0], 0, len(steps))
    return record, job.id, steps


def _context(db: Session, conv_id: str, marketplace: str, body: ListRequest | None = None) -> direct_preflight.Context:
    return direct_preflight.Context(db=db)


def preflight(conv_id: str, db: Session) -> dict:
    """Read saved listing requirements locally, without Chrome or category changes.

    Synchronous database and form work: routes call it from the threadpool.
    """
    conv = _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    if not revision or not isinstance(revision.listing_json, dict):
        return {"revision_id": None, "marketplaces": {}}
    from vendoo_studio.services.job_snapshot import prepare_listing_snapshot
    from vendoo_studio.services.native_contract import copy_listing
    from vendoo_studio.services.native_form import attach_form

    prepared = prepare_listing_snapshot(db, conv, revision.listing_json)
    body = ListRequest(revision_id=revision.id)
    photos = ConversationRepo(db).get_all_photos(conv_id)
    records = MarketplaceListingRepo(db).for_conversation(conv_id)
    out = {}
    for marketplace in NAMES:
        found = direct_preflight.gaps(attach_form(copy_listing(prepared), marketplace), marketplace,
                                      _context(db, conv_id, marketplace, body))
        left_off = marketplace_media.photos_for(prepared, photos, marketplace)[1]
        if left_off:
            found.append(direct_preflight.gap(f"marketplace_photos.{marketplace}", left_off, blocking=False))
        found = [direct_preflight.with_fix(item) for item in found]
        rejected = listing_readiness.rejected_fields(records.get(marketplace) or {})
        out[marketplace] = {"ready": not direct_preflight.blocking(found), "gaps": found,
                            "missing": listing_readiness.missing(found, rejected)}
    return {"revision_id": revision.id, "marketplaces": out}


async def list_now(conv_id: str, marketplace: Marketplace, body: ListRequest) -> dict:
    """Start listing on one marketplace in one seller-confirmed step. Returns at once; poll status."""
    return await _start(conv_id, marketplace, body, "list")


async def update_live(conv_id: str, marketplace: Marketplace, body: ListRequest) -> dict:
    """Write the listing saved in Anteroom onto the one already live. One confirmation, never a second listing."""
    return await _start(conv_id, marketplace, body, "update")


async def save_draft(conv_id: str, marketplace: Marketplace, revision_id: str) -> dict:
    """Save only. No confirmation, publish step, or automatic publish retry."""
    return await _start(conv_id, marketplace, ListRequest(revision_id=revision_id), "save_draft")


def _active(db: Session, job_id: str, conv_id: str, marketplace: str) -> bool:
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES

    job = JobRepo(db).get(job_id)
    if job:
        db.refresh(job)
    return (bool(job) and job.status in ACTIVE_JOB_STATUSES and not job.cancel_requested
            and not job_cancel_requested(job_id) and _record(db, conv_id, marketplace).get("job_id") == job_id)


def _for_chrome(snapshot: dict) -> dict:
    """The listing as Chrome receives it. The marketplaces' saved form contracts stay on the
    server: Chrome never reads them, and they can be megabytes, past what the Bridge accepts."""
    from vendoo_studio.services.native_contract import KEY

    return {key: value for key, value in snapshot.items() if key != KEY}


def _execute_payload(marketplace: str, step: str, record: dict, snapshot: dict, photos: list, warning: str) -> dict:
    extra: dict = {}
    if marketplace == "depop" and warning:
        extra["photo_warning"] = warning
    return {
        "marketplace": marketplace, "action": step, "confirm": step in {"publish", "list", "update"},
        "operation_id": record["operation_id"], "draft_url": record.get("draft_url"), "listing_id": record.get("listing_id"),
        "listing": _for_chrome(snapshot), "local_draft_id": record.get("local_draft_id"), "revision_id": record.get("revision_id"),
        **({"video": snapshot.get("_native_video")} if marketplace == "poshmark" else {}),
        "photos": [{"id": p.id, "mime_type": p.mime_type} for p in photos],
        **extra,
    }


def _result_context(db: Session, conv_id: str, marketplace: str, record: dict, snapshot: dict, candidate: str | None) -> dict:
    first_save = record.get("action") == "save_draft" and not record.get("previous_revision_id")
    return {"marketplace": marketplace, "action": record.get("action"), "operation_id": record.get("operation_id"),
            "draft_url": candidate, "listing_id": record.get("listing_id"), "local_draft_id": record.get("local_draft_id"),
            "revision_id": record.get("revision_id"), "photo_ids": record.get("photo_ids"),
            "expected_photo_count": (len(snapshot["_approved_photos"]) if "_approved_photos" in snapshot
                                     else len(record["photo_ids"]) if record.get("photo_ids") is not None
                                     else len(_photos(db, conv_id, snapshot, marketplace)[0]) if first_save or marketplace == "poshmark" else None),
            "listing": _for_chrome(snapshot)}


async def _send_cancel(operation_id: str) -> None:
    from vendoo_studio.models.protocol import ProtocolMessage
    from vendoo_studio.services.extension_connection import current_extension

    # Chrome journals the action under its operation id, so that is what it cancels.
    await current_extension().send_message(ProtocolMessage(
        type="job.cancel", job_id=operation_id, payload={"job_id": operation_id},
    ).model_dump(mode="json"))


CHROME_SILENT = ("Chrome has not said whether this {name} action finished. Look for it on {name}: if it is there, "
                 "use Mark as listed; if not, choose “I checked it myself” and List again.")


class _Cancelled(Exception):
    pass


class _JobRef:
    """A job's id without its database row, for the calls made while the job waits on Chrome."""

    def __init__(self, job_id: str):
        self.id = job_id


def _release(db: Session) -> None:
    """End the job's transaction before it waits on Chrome, so the wait does not hold a pooled
    connection. Each running marketplace would otherwise pin one for minutes, and a few at
    once leave none for the seller's own page requests."""
    db.commit()


async def _unless_cancelled(job_id: str, awaitable):
    """Await a Chrome call, giving up as soon as the seller presses Cancel."""
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        if job_cancel_requested(job_id):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the call is abandoned
                pass
            raise _Cancelled
        await asyncio.wait({task}, timeout=CANCEL_POLL_S)
    return task.result()


async def _wait_for_chrome(db: Session, job, conv_id: str, marketplace: str, record: dict, snapshot: dict) -> dict:
    """Chrome's reply was lost or late. Stop the action, then hold the slot until Chrome reports what it did."""
    operation_id = record["operation_id"]
    # Only the id is used while waiting: reading the job row again would reopen a transaction.
    job = _JobRef(job.id)
    await _send_cancel(operation_id)
    deadline = time.monotonic() + SETTLE_DEADLINE_S
    context = _result_context(db, conv_id, marketplace, record, snapshot, record.get("draft_url"))
    try:
        while time.monotonic() < deadline:
            _progress(db, job.id, marketplace, "waiting")
            _release(db)
            try:
                reply = await _unless_cancelled(job.id, browser_bridge.request(
                    job, "marketplace.result", {"marketplace": marketplace, "operation_id": operation_id, "context": context}, timeout=65))
                if reply.get("finished") and isinstance(reply.get("result"), dict):
                    return reply["result"]
            except browser_bridge.BrowserBridgeError:
                pass
            _release(db)
            await _unless_cancelled(job.id, asyncio.sleep(SETTLE_POLL_S))
    except _Cancelled:
        # The seller stopped waiting. Chrome may still have finished, so the result stays uncertain.
        pass
    return failure(CHROME_SILENT.format(name=NAMES[marketplace]), "uncertain", safe=False)


async def _execute_step(db: Session, job, conv_id: str, marketplace: str, step: str, options: dict) -> dict:
    from vendoo_studio.services import approved_photos

    snapshot = job.listing_snapshot if isinstance(job.listing_snapshot, dict) else {}
    try:
        photos = await asyncio.to_thread(approved_photos.load, snapshot)
    except ValueError as exc:
        raise NativeMarketplaceError(409, str(exc)) from exc
    warning = snapshot.get("_approved_photo_warning") or ""
    binding = _record(db, conv_id, marketplace)
    if marketplace in API_MARKETPLACES:
        def record(values: dict) -> None:
            binding.update(values)
            _store(db, conv_id, marketplace, values)

        def active() -> bool:
            return _active(db, job.id, conv_id, marketplace)

        if marketplace == "ebay":
            from vendoo_studio.services import ebay_listings

            result = await ebay_listings.execute(step, snapshot, photos, binding, record, active, confirm=step in {"list", "publish", "update"},
                                                 conversation_id=conv_id)
        else:
            result = await etsy_listings.execute(step, snapshot, photos, binding, record, active, confirm=step in {"list", "publish", "update"}, **options)
        stored = _apply_result(db, conv_id, marketplace, job.id, step, result)
        if stored["state"] == "unknown":
            # A reply was lost. Read what the marketplace has now, while still holding the slot.
            _progress(db, job.id, marketplace, "checking")
            checked = await _read_result(
                marketplace, stored, snapshot, stored.get("draft_url"), job, context=None,
                remember=lambda values: _store(db, conv_id, marketplace, values))
            if checked is not None:
                stored = _apply_result(db, conv_id, marketplace, job.id, step, checked)
        return stored
    payload = _execute_payload(marketplace, step, binding, snapshot, photos, warning)
    job = _JobRef(job.id)
    _release(db)
    try:
        result = await _unless_cancelled(job.id, browser_bridge.request(
            job, "marketplace.execute", payload, timeout=EXECUTE_TIMEOUT_S))
    except (browser_bridge.BrowserBridgeError, _Cancelled):
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
    if (job.cancel_requested or job_cancel_requested(job_id)) and not live:
        repo.update_status(job_id, "cancelled", job.current_step, error="Cancelled.")
        repo.add_event(job_id, "cancelled", job.current_step)
    elif record.get("job_id") == job_id and not record.get("error") and record.get("state") in {"published", "pending_review", "draft"}:
        repo.update_status(job_id, "completed", job.current_step)
    else:
        repo.update_status(job_id, "failed", job.current_step, error=record.get("error") or UNKNOWN)


async def run_job(job_id: str, conv_id: str, marketplace: str, steps: list[str], options: dict) -> None:
    """Run a List or Update job's steps in order, holding the automation slot until the last answer."""
    from vendoo_studio.database import SessionLocal

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
            # Depop's drafts have no Boost; its live edit does. A List the seller set to boost
            # turns it on with one edit of the listing it just posted.
            if (marketplace == "depop" and step == "publish" and "update" not in steps
                    and ((job.listing_snapshot or {}).get("depop_specifics") or {}).get("boost") is True
                    and record.get("state") == "published" and not record.get("error")):
                from vendoo_studio.services.marketplace_delist import listing_id

                if live_id := listing_id(marketplace, record.get("listing_url")):
                    _store(db, conv_id, marketplace, {"listing_id": live_id})
                    steps.append("update")
                else:
                    # Listed, but Boost did not stick: shown on the live row, like Poshmark's Smart Pricing.
                    _store(db, conv_id, marketplace, {"error": "Listed on Depop without Boost: Depop did not return the "
                                                               "listing link. Turn Boost on with Update."})
            # A confirmed List found the saved draft deleted. Save a new one, then list that, in this same job.
            if (marketplace == "depop" and step == "publish" and "save_draft" not in steps
                    and _draft_gone(record)):
                steps.extend(["save_draft", "publish"])
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
            db.close()


async def cancel(conv_id: str, marketplace: Marketplace) -> dict:
    """Stop a running List or Update at its next safe point. The job keeps the slot until the work stops."""
    record = await in_worker_session(_running_record, conv_id, marketplace)
    await request_cancel(record["job_id"], record)
    return (await in_worker_session(lambda db: status(conv_id, db)))[marketplace]


def _running_record(db: Session, conv_id: str, marketplace: str) -> dict:
    record = _record(db, conv_id, marketplace)
    if not _running(db, record):
        raise NativeMarketplaceError(409, f"No {NAMES[marketplace]} action is running.")
    return record


def stop_job(db: Session, job) -> dict | None:
    """Cancel from the jobs list, database step: end a job nothing runs any more and return None, or
    for a live direct job return its record for ``request_cancel``, which the caller awaits holding no session."""
    marketplace = str(job.current_step or "").removeprefix("native_").split("_", 1)[0]
    record = _record(db, job.conversation_id, marketplace) if marketplace in NAMES else {}
    if job_is_live(db, job.id):
        db.commit()
        return record
    JobRepo(db).update_status(job.id, "cancelled", job.current_step, error="Cancelled.")
    mark_interrupted(db, job.id)
    return None


async def request_cancel(job_id: str, record: dict) -> None:
    # Recorded on the job: the Machine running it stops at its next safe point.
    if not await in_worker_session(_record_cancel, job_id, record.get("marketplace")):
        return
    if record.get("operation_id"):
        await _send_cancel(record["operation_id"])


def _record_cancel(db: Session, job_id: str, marketplace: str | None) -> bool:
    if not request_job_cancel(db, job_id):
        return False
    JobRepo(db).add_event(job_id, "native_cancel_requested", None, {"marketplace": marketplace})
    return True


# --- Check, Reset and automatic reconcile --------------------------------------

async def _read_result(marketplace: str, record: dict, snapshot: dict, candidate: str | None, job, *,
                       context: dict | None, remember) -> dict | None:
    """Read the action's result from the marketplace (or Chrome's journal). Never writes the marketplace.

    ``context`` is ``_result_context`` for a Chrome marketplace; ``remember`` keeps what eBay
    reports along the way. None means Chrome has no journal for it: the seller has to look.
    """
    if marketplace == "etsy":
        return await etsy_listings.check(snapshot, record, candidate)
    if marketplace == "ebay":
        from vendoo_studio.services import ebay_listings

        return await ebay_listings.check(record, snapshot, remember)
    try:
        reply = await browser_bridge.request(job, "marketplace.result", {
            "marketplace": marketplace, "operation_id": record.get("operation_id"), "context": context,
        }, timeout=65)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not check the result. Connect Chrome and check again.") from exc
    if not reply.get("finished") or not isinstance(reply.get("result"), dict):
        return None
    return reply["result"]


async def check_result(conv_id: str, marketplace: Marketplace, body: CheckRequest | None = None) -> dict:
    """Read-only: ask the marketplace what the last action did, and record it.

    The database steps run on worker threads; none holds a connection while the marketplace answers.
    """
    record, snapshot, candidate, context, check_job_id = await in_worker_session(
        _open_check, conv_id, marketplace, body)
    remembered: list[dict] = []
    try:
        result = await _read_result(marketplace, record, snapshot, candidate, _JobRef(check_job_id),
                                    context=context, remember=remembered.append)
        return await in_worker_session(_settle_check, conv_id, marketplace, record, check_job_id, remembered, result)
    except BaseException:
        await asyncio.shield(in_worker_session(_fail_check, conv_id, marketplace, check_job_id, remembered))
        raise
    finally:
        release_api_job(check_job_id)


def _open_check(db: Session, conv_id: str, marketplace: str, body: CheckRequest | None):
    from vendoo_studio.services.extension_connection import current_extension

    _conversation(db, conv_id)
    record = _record(db, conv_id, marketplace)
    if not record.get("job_id"):
        raise NativeMarketplaceError(409, "No marketplace action to check.")
    if _running(db, record):
        raise NativeMarketplaceError(409, f"The {NAMES[marketplace]} action is still running. Wait for it to finish.")
    if marketplace not in API_MARKETPLACES and not current_extension().connected:
        raise NativeMarketplaceError(409, "Connect Chrome to check the result.")
    action_job = JobRepo(db).get(record["job_id"])
    snapshot = action_job.listing_snapshot if action_job and isinstance(action_job.listing_snapshot, dict) else {}
    candidate = draft_url(marketplace, body.draft_url) if body and body.draft_url else record.get("draft_url")
    if record.get("draft_url") and candidate != record["draft_url"]:
        raise NativeMarketplaceError(409, "Check the draft already linked to this listing.")
    context = (None if marketplace in API_MARKETPLACES
               else _result_context(db, conv_id, marketplace, record, snapshot, candidate))
    repo = JobRepo(db)
    with start_gate():
        if blocking_job(repo, marketplace) is not None:
            raise NativeMarketplaceError(409, "Another automation job is running. Check again when it finishes.")
        # Reading Chrome's journal can open the marketplace tab: hold the slot like any other job.
        check_job = repo.create(conv_id, record.get("revision_id") or "", snapshot, status="dispatched",
                                current_step=f"native_{marketplace}_check")
        claim_api_job(db, check_job.id)
    return record, snapshot, candidate, context, check_job.id


def _keep_remembered(db: Session, conv_id: str, marketplace: str, remembered: list[dict]) -> None:
    for values in remembered:
        _store(db, conv_id, marketplace, values)


def _settle_check(db: Session, conv_id: str, marketplace: str, record: dict, check_job_id: str,
                  remembered: list[dict], result: dict | None) -> dict:
    _keep_remembered(db, conv_id, marketplace, remembered)
    if result is None:
        stored = _store(db, conv_id, marketplace, {**_CLEARED_ERROR, "state": "unknown", "error_code": "uncertain",
                                                   "error": CHROME_SILENT.format(name=NAMES[marketplace])})
    else:
        stored = _apply_result(db, conv_id, marketplace, record["job_id"], record.get("action") or "", result)
    repo = JobRepo(db)
    action_job = repo.get(record["job_id"])
    settled = stored["state"] not in UNSETTLED_STATES
    if action_job and settled and not stored.get("error"):
        if action_job.status in {"failed", "dispatched"}:
            action_job.status, action_job.last_error = "completed", None
            db.commit()
            repo.add_event(action_job.id, "native_result_verified", action_job.current_step)
    repo.update_status(check_job_id, "completed")
    return stored


def _fail_check(db: Session, conv_id: str, marketplace: str, check_job_id: str, remembered: list[dict]) -> None:
    _keep_remembered(db, conv_id, marketplace, remembered)
    JobRepo(db).update_status(check_job_id, "failed", error="Check did not finish.")


def reset(conv_id: str, marketplace: Marketplace, db: Session) -> dict:
    """The seller looked on the marketplace themselves. Clear an unconfirmed record; never touch the marketplace."""
    _conversation(db, conv_id)
    with start_gate():
        record = _record(db, conv_id, marketplace)
        if record.get("state") not in UNSETTLED_STATES:
            raise NativeMarketplaceError(409, f"Nothing on {NAMES[marketplace]} is waiting to be confirmed.")
        if _running(db, record):
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
    checked = 0
    for conv_id, marketplace in await in_worker_session(_reconcile_targets, chrome):
        try:
            await check_result(conv_id, marketplace)
            checked += 1
        except NativeMarketplaceError as exc:
            log.info("Reconcile skipped %s for %s: %s", marketplace, conv_id, exc)
        except Exception:  # noqa: BLE001 - one record must not stop the rest
            log.exception("Reconcile failed for %s on %s", conv_id, marketplace)
    return checked


def _reconcile_targets(db: Session, chrome: bool) -> list[tuple[str, str]]:
    return [(row.conversation_id, row.marketplace) for row in MarketplaceListingRepo(db).unsettled()
            if row.job_id and not job_is_live(db, row.job_id) and (row.marketplace in API_MARKETPLACES or chrome)]


async def reconcile_after_reconnect() -> None:
    """On Chrome reconnect or a stalled job: settle what Chrome may have finished. Read-only."""
    try:
        await reconcile(chrome=True)
    except Exception:  # noqa: BLE001 - logged; the next reconnect tries again
        log.exception("Reconnect reconcile failed")


async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest):
    """Read a marketplace page's form. Holds the automation slot so no action starts into a busy Chrome."""
    job_id = await in_worker_session(_open_inspection, conv_id, marketplace)
    try:
        reply = await browser_bridge.request(_JobRef(job_id), "marketplace.inspect", {
            "marketplace": marketplace, "script_url": body.script_url, "background": body.background,
        }, timeout=45)
        await in_worker_session(lambda db: JobRepo(db).update_status(job_id, "completed"))
        return reply
    except browser_bridge.BrowserBridgeError as exc:
        await in_worker_session(lambda db: JobRepo(db).update_status(
            job_id, "failed", error="Chrome could not inspect the marketplace page."))
        raise NativeMarketplaceError(502, "Chrome could not inspect the marketplace page.") from exc
    finally:
        release_api_job(job_id)


def _open_inspection(db: Session, conv_id: str, marketplace: str) -> str:
    _conversation(db, conv_id)
    repo = JobRepo(db)
    with start_gate():
        if repo.get_active():
            raise NativeMarketplaceError(409, "Wait for the current Chrome job to finish.")
        job = repo.create(conv_id, "", {}, status="dispatched", current_step=f"native_{marketplace}_inspect")
        claim_api_job(db, job.id)
    return job.id


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



POSHMARK_CONDITIONS = ["New With Tags (NWT)", "Like New", "Good", "Fair"]


