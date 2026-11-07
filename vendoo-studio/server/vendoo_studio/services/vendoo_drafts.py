"""Seller-started Vendoo drafts. Writes are sent once and checked by their reserved ID."""
from __future__ import annotations

import copy
import asyncio
import re
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import load_only
from starlette.concurrency import run_in_threadpool

from vendoo_studio.database import SessionLocal, in_worker_session
from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import approved_photos, browser_bridge, listing_mode
from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job
from vendoo_studio.services.streaming import spawn
from vendoo_studio.services.vendoo_item_build import build_vendoo_item


def _bridge_ready() -> None:
    from vendoo_studio.services.extension_connection import current_extension

    manager = current_extension()
    refusal = getattr(manager, "refusal", None)
    if callable(refusal):
        reason = refusal("vendoo.drafts")
        if reason:
            raise HTTPException(409, reason)
    elif not manager.connected:
        raise HTTPException(409, "Connect Chrome before sending to Vendoo.")
    elif "vendoo.drafts" not in manager.capabilities:
        raise HTTPException(409, "Update Anteroom Bridge to 0.2.55 or later before sending to Vendoo.")


def status(db, conv_id: str) -> dict:
    if not ConversationRepo(db).get(conv_id):
        raise HTTPException(404, "Conversation not found")
    job = db.scalar(select(Job).options(load_only(Job.id, Job.status, Job.current_step, Job.last_error,
        Job.vendoo_item_id, Job.approved_revision_id)).where(Job.conversation_id == conv_id,
        or_(Job.current_step.like("vendoo_%"), Job.vendoo_item_id.is_not(None)))
        .order_by(Job.created_at.desc()).limit(1))
    return _response(job)


def _response(job) -> dict:
    item_id = job.vendoo_item_id if job else None
    url = f"https://web.vendoo.co/app/item/{item_id}" if item_id and re.fullmatch(r"[A-Za-z0-9]{20}", item_id) else None
    return {"job_id": job.id if job else None, "status": job.status if job else None,
            "step": job.current_step if job else None, "error": job.last_error if job else None,
            "url": url, "revision_id": job.approved_revision_id if job else None}


async def _ops(job_id: str, ops: list[dict], *, active: bool = True) -> dict:
    if active:
        with SessionLocal() as db:
            state = db.scalar(select(Job.status).where(Job.id == job_id))
        if state != "dispatched":
            raise asyncio.CancelledError("The Vendoo send stopped.")
    _bridge_ready()
    reply = await browser_bridge.request(SimpleNamespace(id=job_id), "job.vendoo_api",
        {"listing_mode": "vendoo", "ops": ops}, timeout=240)
    if reply.get("ok") is not True:
        # Browser errors never carry request/response bodies, headers or tokens.
        errors = [row.get("error") for row in reply.get("results", []) if not row.get("ok")]
        raise ValueError(str(next((error for error in errors if error), None)
            or reply.get("error") or "Vendoo did not confirm this request.")[:400])
    return reply


def _result(reply: dict, op: str):
    return next(row for row in reply["results"] if row.get("op") == op)


async def start(conv_id: str, revision_id: str) -> dict:
    response = await in_worker_session(_prepare, conv_id, revision_id)
    spawn(run(response["job_id"]))
    return response


def _prepare(db, conv_id: str, revision_id: str) -> dict:
    listing_mode.require("vendoo", db)
    conversation = ConversationRepo(db).get(conv_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(revision_id)
    if not listing or listing.current_revision_id != revision_id or not revision:
        raise HTTPException(409, "The listing changed. Save and review it before sending to Vendoo.")
    if conversation.status == "sold":
        raise HTTPException(409, "This item has sold.")
    _bridge_ready()
    from vendoo_studio.services.streaming import active_generation
    if active_generation(conv_id):
        raise HTTPException(409, "Wait for generation to finish before sending the draft.")
    photos = [SimpleNamespace(id=photo.id, stored_filename=photo.stored_filename,
        checksum=photo.checksum, mime_type=photo.mime_type)
        for photo in ConversationRepo(db).get_all_photos(conv_id)]
    if not photos:
        raise HTTPException(400, "Add photos before sending to Vendoo.")
    snapshot = copy.deepcopy(revision.listing_json)
    db.commit()
    # Storage can take time. It holds neither an account claim nor a pooled connection.
    snapshot[approved_photos.KEY] = approved_photos.capture(photos)
    try:
        with listing_mode.guard(db, "vendoo"):
            current = ListingRepo(db).get_current(conv_id)
            if current is None or current.current_revision_id != revision_id:
                raise HTTPException(409, "The listing changed during photo preparation. Review it before sending.")
            if active_generation(conv_id):
                raise HTTPException(409, "Wait for generation to finish before sending the draft.")
            previous = db.scalar(select(Job).where(Job.conversation_id == conv_id,
                Job.vendoo_item_id.is_not(None)).order_by(Job.created_at.desc()).limit(1))
            if previous and previous.status != "completed" and not previous.listing_snapshot.get("_vendoo_checked"):
                raise HTTPException(409, "Check the existing Vendoo draft before sending again.")
            if db.scalar(select(Job.id).where(Job.conversation_id == conv_id,
                    Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)):
                raise HTTPException(409, "A listing action is already running for this item.")
            if previous:
                snapshot["_vendoo_previous"] = {
                    "item_id": previous.vendoo_item_id,
                    "uid": previous.listing_snapshot.get("_vendoo_uid"),
                    "sent": previous.listing_snapshot.get("_vendoo_sent"),
                    "retry": previous.listing_snapshot.get("_vendoo_retry") or {},
                }
            job = JobRepo(db).create(conv_id, revision_id, snapshot,
                status="dispatched", current_step="vendoo_session")
            claim_api_job(job.id)
    except BaseException:
        db.rollback()
        approved_photos.release(snapshot)
        raise
    return _response(job)


def _save(db, job, step: str, **snapshot) -> None:
    job.listing_snapshot = {**job.listing_snapshot, **snapshot}
    if "_vendoo_sent" in snapshot:
        job.listing_snapshot.pop("_vendoo_previous", None)
    job.current_step = step
    db.commit()


def _updates(before: dict, after: dict, prefix: str = "") -> dict:
    """Only fields Anteroom changed; Vendoo's other fields and listing states stay owned there."""
    changed = {}
    for key in sorted(before.keys() | after.keys()):
        value = after.get(key)
        path = f"{prefix}.{key}" if prefix else key
        old = before.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            changed.update(_updates(old, value, path))
        elif old != value:
            changed[path] = value
    return changed


def _owned(item: dict) -> dict:
    return {"generalDetails": item.get("generalDetails", {}), "labels": item.get("labels", []),
            "listings": {market: {key: value for key, value in section.items()
                if key in {"overrides", "categorySpecifics", "marketplaceSpecifics"}}
                for market, section in item.get("listings", {}).items()}}


def _at(item: dict, path: str):
    value = item
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _fields(item: dict, prefix: str = "") -> dict:
    result = {}
    for key, value in item.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_fields(value, path))
        else:
            result[path] = value
    return result


def _verify(item: dict, remote: dict, updates: dict | None = None) -> None:
    if not remote or remote.get("userID") != item["userID"] or remote.get("itemID") != item["itemID"]:
        raise ValueError("Vendoo did not return the saved draft. Check it before sending again.")
    def contains(actual, expected):
        # A newly created draft may acquire Vendoo defaults in fields the
        # seller left empty. Explicit clears on later updates are checked below.
        if expected in (None, "", [], {}):
            return True
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(contains(actual.get(key), value) for key, value in expected.items())
        return actual == expected
    mismatch = (not contains(_owned(remote), _owned(item)) if updates is None
                else any(_at(remote, path) != value for path, value in updates.items()))
    if mismatch:
        raise ValueError("Vendoo returned different draft fields. Review the draft in Vendoo before sending again.")


def _check_target(db, conv_id: str) -> tuple[str, str, dict]:
    listing_mode.require("vendoo", db)
    found = status(db, conv_id)
    job = JobRepo(db).get(found["job_id"]) if found["job_id"] else None
    if not job or not job.vendoo_item_id:
        raise HTTPException(409, "There is no Vendoo draft to check.")
    if job.status in ACTIVE_JOB_STATUSES:
        raise HTTPException(409, "Wait for the current send to finish.")
    return job.id, job.vendoo_item_id, job.listing_snapshot


def _record_check(db, job_id: str, changes: dict) -> dict:
    job = JobRepo(db).get(job_id)
    for name, value in changes.items():
        setattr(job, name, value)
    db.commit()
    return _response(job)


async def check(conv_id: str) -> dict:
    """Read the draft back from Vendoo. No session is open while Chrome reads it."""
    job_id, item_id, snapshot = await in_worker_session(_check_target, conv_id)
    job = SimpleNamespace()
    try:
        reply = await _ops(job_id, [{"op": "session", "expected_uid": snapshot.get("_vendoo_uid")},
            {"op": "get_item", "item_id": item_id, "raw": True, "allow_missing": True}], active=False)
        remote = _result(reply, "get_item")["item"]
        if remote is None:
            job.vendoo_item_id = None
            job.vendoo_url = None
            raise ValueError("Vendoo confirmed there is no draft at the reserved ID. You can send it again.")
        sent = snapshot.get("_vendoo_sent") or (snapshot.get("_vendoo_previous") or {}).get("sent")
        if remote.get("userID") != snapshot.get("_vendoo_uid") or remote.get("itemID") != item_id:
            raise ValueError("Vendoo did not return this account's draft.")
        try:
            if not sent:
                raise ValueError("This send stopped before writing its fields.")
            _verify(sent, remote, snapshot.get("_vendoo_verify"))
        except ValueError:
            # This read proves the item exists under the original account. A
            # seller may explicitly save again to this ID, without duplicating
            # it or treating a lost write response as a success.
            intended = snapshot.get("_vendoo_verify")
            if intended is None:
                intended = {path: value for path, value in _fields(_owned(sent or {})).items()
                            if value not in (None, "", [], {})}
            retry = {path: value for path, value in intended.items() if _at(remote, path) != value}
            job.listing_snapshot = {**snapshot, "_vendoo_sent": sent, "_vendoo_checked": True,
                                    "_vendoo_retry": retry}
            raise ValueError("Vendoo has this draft with different fields. Review it, or press Save changes to Vendoo again.") from None
        job.status, job.current_step, job.last_error = "completed", "vendoo_saved", None
    except (ValueError, browser_bridge.BrowserBridgeError) as exc:
        job.status, job.last_error = "failed", str(exc)[:400]
    return await in_worker_session(_record_check, job_id, vars(job))


async def run(job_id: str) -> None:
    db = SessionLocal()
    job = JobRepo(db).get(job_id)
    if job is None:
        db.close()
        release_api_job(job_id)
        return
    snapshot = job.listing_snapshot
    try:
        previous = snapshot.get("_vendoo_previous") or {}
        db.commit()  # No database connection stays checked out while Chrome works.
        session = await _ops(job_id, [{"op": "session", "expected_uid": previous.get("uid")},
                                    {"op": "new_item_id"}, {"op": "subscription"}])
        uid = _result(session, "session")["uid"]
        item_id = previous.get("item_id") or _result(session, "new_item_id")["item_id"]
        if not re.fullmatch(r"[A-Za-z0-9]{20}", item_id):
            raise ValueError("Vendoo returned an invalid draft ID.")
        job.vendoo_item_id = item_id
        job.vendoo_url = f"https://web.vendoo.co/app/item/{item_id}"
        _save(db, job, "vendoo_photos", _vendoo_uid=uid)
        uploaded = await _ops(job_id, [{"op": "session", "expected_uid": uid}, *[
            {"op": "upload_photo", "photo": {"id": photo["id"], "mime_type": photo["mime_type"],
                "extension": "png" if photo["mime_type"] == "image/png" else "jpg"}}
            for photo in snapshot[approved_photos.KEY]]])
        images = [row["image"] for row in uploaded["results"] if row["op"] == "upload_photo"]
        item, unresolved = await run_in_threadpool(build_vendoo_item, snapshot,
            images=images, user_id=uid, item_id=item_id)
        if item["labels"]:
            labels = await _ops(job_id, [{"op": "session", "expected_uid": uid},
                {"op": "resolve_labels", "names": item["labels"]}])
            item["labels"] = _result(labels, "resolve_labels")["ids"]
        _save(db, job, "vendoo_saving", _vendoo_sent=item, _vendoo_warnings=unresolved)
        updates = None
        if previous:
            before = previous.get("sent")
            if not before:
                raise ValueError("Review this older draft in Vendoo; Anteroom has no verified copy to update safely.")
            remote = _result(await _ops(job_id, [{"op": "get_item", "item_id": item_id,
                "with_version": True, "raw": True}]), "get_item")["item"]
            updates = {**(previous.get("retry") or {}), **_updates(_owned(before), _owned(item))}
            _save(db, job, "vendoo_saving", _vendoo_verify=updates)
            if updates:
                await _ops(job_id, [{"op": "session", "expected_uid": uid}, {"op": "update_item", "item_id": item_id,
                    "updates": updates, "expected_update_time": remote["_studio_update_time"]}])
        else:
            await _ops(job_id, [{"op": "session", "expected_uid": uid}, {"op": "create_item", "item": item,
                "subscription_version": _result(session, "subscription").get("version")}])
        _save(db, job, "vendoo_verifying", _vendoo_verify=updates)
        remote = _result(await _ops(job_id, [{"op": "session", "expected_uid": uid},
            {"op": "get_item", "item_id": item_id, "raw": True}]), "get_item")["item"]
        _verify(item, remote, updates)
        JobRepo(db).update_status(job.id, "completed", "vendoo_saved")
    except (ValueError, browser_bridge.BrowserBridgeError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        JobRepo(db).update_status(job.id, "failed", "vendoo_check_required", error=str(detail)[:400])
    except BaseException:
        JobRepo(db).update_status(job.id, "failed", "vendoo_check_required",
            error="The Vendoo send stopped. Check the draft in Vendoo before sending again.")
        raise
    finally:
        db.close()
        try:
            await run_in_threadpool(approved_photos.release, snapshot)
        finally:
            release_api_job(job_id)
