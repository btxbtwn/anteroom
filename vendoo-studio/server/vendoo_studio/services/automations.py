"""Create, preview, and run scheduled automations.

A task the seller confirmed is approval for the actions it names, on the
marketplaces it names, and for nothing else. Sold items are left alone.

Listing age is the earliest real listed date: the first successful publish,
the date on the Vendoo job, then the item's own listed timestamp.
``marketplace_listings.created_at`` counts only when nothing earlier exists,
because a backfill stamp is not when the item was listed.

Runs are claimed by ``(automation, scheduled slot)`` so a restart cannot
fire the same slot twice. Missed slots catch up once. One run is in
progress at a time, and it waits while any marketplace job is already
using the Bridge. Each item's outcome is a row in ``jobs``. A dry run is
recorded as ``dry_run``, not as a completed send.
"""
from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.models.automation import Automation, AutomationApplication, AutomationRun, ItemPriceBaseline
from vendoo_studio.models.conversation import Conversation, new_id, utcnow
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.services.automation_age import listed_markets, load_listed_index, resolve_listed_at
from vendoo_studio.services.automation_pricing import PriceError, drop_price
from vendoo_studio.services.vendoo_import import parse_notes
from vendoo_studio.services.automation_prompt import MARKETPLACES, draft_from_prompt
from vendoo_studio.services.automation_schedule import (
    ScheduleError,
    advance_after_run,
    as_utc,
    describe,
    first_due,
    normalize_schedule,
    require_timezone,
)

log = logging.getLogger("vendoo_studio.automations")

ACTION_ORDER = ("regenerate", "update_fields", "price", "delist", "relist")
FIELD_NAMES = {"title", "description", "brand", "condition", "size", "sku", "department", "category_path"}
MAX_ATTEMPTS = 3
RETRY_WAIT = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
BUSY_WAIT = timedelta(seconds=30)
STALE_RUN = timedelta(minutes=20)


class AutomationError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AutomationBusy(RuntimeError):
    """Another marketplace job holds the single Bridge slot."""


def list_automations(db: Session) -> list[dict]:
    rows = db.query(Automation).order_by(Automation.created_at.desc()).all()
    return [view(row) for row in rows]


def get_automation(db: Session, automation_id: str) -> Automation:
    row = db.get(Automation, automation_id)
    if row is None:
        raise AutomationError("That automation is gone.", 404)
    return row


def create_automation(db: Session, body: dict, *, now: datetime | None = None) -> dict:
    spec = compile_spec(body, now=now)
    if body.get("activate"):
        _require_ready(spec)
    row = _insert(db, spec, activate=bool(body.get("activate")), now=now or utcnow())
    return view(row)


def update_automation(db: Session, automation_id: str, body: dict, *, now: datetime | None = None) -> dict:
    row = get_automation(db, automation_id)
    merged = {
        "name": body.get("name", row.name),
        "prompt": body.get("prompt", row.prompt),
        "timezone": body.get("timezone", row.timezone),
        "schedule": body.get("schedule", row.schedule),
        "filters": body.get("filters", row.filters),
        "actions": body.get("actions", row.actions),
        "marketplaces": body.get("marketplaces", row.marketplaces),
        "activate": body.get("activate", False),
    }
    # A prompt on an update replaces the structured task, then explicit fields win.
    if body.get("prompt") and not any(body.get(key) for key in ("schedule", "actions", "filters", "marketplaces")):
        spec = compile_spec({"prompt": body["prompt"], "timezone": merged["timezone"], "name": merged["name"]}, now=now)
    else:
        spec = compile_spec(merged, now=now)
    activating = bool(body.get("activate")) or row.status == "active"
    if activating:
        _require_ready(spec)
    row.name = spec["name"]
    row.prompt = spec["prompt"]
    row.timezone = spec["timezone"]
    row.schedule = spec["schedule"]
    row.filters = spec["filters"]
    row.actions = spec["actions"]
    row.marketplaces = spec["marketplaces"]
    moment = now or utcnow()
    if body.get("activate"):
        row.status = "active"
        row.confirmed_at = _naive(moment)
        row.next_run_at = _naive(first_due(spec["schedule"], spec["timezone"], moment))
    elif row.status == "active":
        row.next_run_at = _naive(first_due(spec["schedule"], spec["timezone"], moment))
    row.updated_at = _naive(moment)
    db.commit()
    db.refresh(row)
    return view(row)


def pause_automation(db: Session, automation_id: str) -> dict:
    row = get_automation(db, automation_id)
    if row.status != "active":
        raise AutomationError("Only a running automation can be paused.")
    row.status = "paused"
    row.updated_at = _naive(utcnow())
    db.commit()
    return view(row)


def resume_automation(db: Session, automation_id: str, *, now: datetime | None = None) -> dict:
    row = get_automation(db, automation_id)
    if row.status != "paused":
        raise AutomationError("That automation is not paused.")
    moment = now or utcnow()
    row.status = "active"
    if row.next_run_at is None or as_utc(row.next_run_at) is None:
        row.next_run_at = _naive(first_due(row.schedule, row.timezone, moment))
    row.updated_at = _naive(moment)
    db.commit()
    return view(row)


def delete_automation(db: Session, automation_id: str) -> dict:
    row = get_automation(db, automation_id)
    db.delete(row)
    db.commit()
    return {"ok": True, "id": automation_id}


def draft_prompt(prompt: str, *, timezone: str = "UTC", now: datetime | None = None) -> dict:
    draft = draft_from_prompt(prompt, timezone=timezone or "UTC", now=now)
    draft["summary"] = _summary(draft) if draft.get("schedule") else ""
    return draft


def preview(db: Session, body: dict, *, now: datetime | None = None) -> dict:
    spec = compile_spec(body, now=now, allow_incomplete=True)
    moment = now or utcnow()
    items = select_items(db, spec, moment, persist_price=False)
    affected = [item for item in items if item["include"]]
    return {
        "ready": spec["ready"],
        "issues": spec["issues"],
        "summary": _summary(spec) if spec.get("schedule") else "",
        "count": len(affected),
        "items": items,
    }


def history(db: Session, automation_id: str) -> dict:
    row = get_automation(db, automation_id)
    runs = (
        db.query(AutomationRun)
        .filter(AutomationRun.automation_id == row.id)
        .order_by(AutomationRun.started_at.desc())
        .limit(50)
        .all()
    )
    return {"automation_id": row.id, "runs": [run_view(item) for item in runs]}


def proposal_from_block(db: Session, block: str, *, now: datetime | None = None) -> dict:
    """The assistant tool: a fenced JSON block becomes a draft the seller still has to confirm."""
    import json

    try:
        data = json.loads(block)
    except ValueError:
        return {"op": "create", "ready": False, "issues": ["The automation block was not valid JSON."], "actions": []}
    if not isinstance(data, dict):
        return {"op": "create", "ready": False, "issues": ["The automation block must be an object."], "actions": []}
    op = str(data.get("op") or "create").lower()
    if op == "pause":
        task = _find_named(db, data)
        return {
            "op": "pause",
            "id": task.id if task else data.get("id"),
            "name": task.name if task else None,
            "ready": task is not None and task.status == "active",
            "issues": [] if task and task.status == "active" else ["That automation is not an active task you can pause."],
            "actions": [],
        }
    if op == "update":
        task = _find_named(db, data)
        if task is None:
            return {"op": "update", "ready": False, "issues": ["That automation was not found."], "actions": []}
        body = {"prompt": data.get("prompt"), "timezone": data.get("timezone") or task.timezone, "name": data.get("name") or task.name}
        for key in ("schedule", "filters", "actions", "marketplaces"):
            if data.get(key) is not None:
                body[key] = data[key]
        if not data.get("prompt") and not any(data.get(key) is not None for key in ("schedule", "filters", "actions", "marketplaces")):
            return {"op": "update", "id": task.id, "ready": False, "issues": ["Say what to change."], "actions": []}
        spec = compile_spec(body, now=now, allow_incomplete=True)
        spec.update(op="update", id=task.id)
        return spec
    body = dict(data)
    body.setdefault("op", "create")
    return compile_spec(body, now=now, allow_incomplete=True)


def confirm_proposal(db: Session, proposal: dict, *, now: datetime | None = None) -> dict:
    """Save a draft the seller just confirmed. Refuses anything still ambiguous."""
    op = proposal.get("op") or "create"
    if op == "pause":
        return pause_automation(db, str(proposal.get("id") or ""))
    if op == "update":
        body = {key: proposal.get(key) for key in ("name", "prompt", "timezone", "schedule", "filters", "actions", "marketplaces")}
        body["activate"] = True
        return update_automation(db, str(proposal["id"]), body, now=now)
    body = {key: proposal.get(key) for key in ("name", "prompt", "timezone", "schedule", "filters", "actions", "marketplaces")}
    body["activate"] = True
    return create_automation(db, body, now=now)


def brief_lines(db: Session) -> str:
    rows = db.query(Automation).order_by(Automation.created_at.desc()).limit(20).all()
    if not rows:
        return "--- Scheduled automations ---\nNone yet."
    lines = [f"- {row.id}: {row.name} ({row.status})" for row in rows]
    return "--- Scheduled automations ---\n" + "\n".join(lines)


def compile_spec(body: dict, *, now: datetime | None = None, allow_incomplete: bool = False) -> dict:
    """Merge a prompt and any structured fields into one spec. Invalid shapes raise; gaps are ``issues``."""
    prompt = str(body.get("prompt") or "").strip()
    timezone = str(body.get("timezone") or "").strip()
    base: dict[str, Any] = {
        "name": "", "prompt": prompt, "timezone": timezone or "UTC", "schedule": None,
        "filters": {}, "actions": [], "marketplaces": [], "issues": [], "ready": False, "op": "create",
    }
    if prompt and not body.get("actions") and not body.get("schedule"):
        base = draft_from_prompt(prompt, timezone=base["timezone"], now=now)
    elif prompt:
        drafted = draft_from_prompt(prompt, timezone=base["timezone"], now=now)
        base.update(name=drafted["name"], filters=drafted["filters"], actions=drafted["actions"],
                    marketplaces=drafted["marketplaces"], schedule=drafted["schedule"], issues=list(drafted["issues"]))
    if body.get("name"):
        base["name"] = str(body["name"]).strip()[:120]
    if body.get("timezone"):
        base["timezone"] = str(body["timezone"]).strip()
    if body.get("schedule") is not None:
        base["schedule"] = body["schedule"]
    if body.get("filters") is not None:
        base["filters"] = body["filters"] if isinstance(body["filters"], dict) else {}
    if body.get("actions") is not None:
        base["actions"] = body["actions"]
    if body.get("marketplaces") is not None:
        base["marketplaces"] = body["marketplaces"]
    issues = []
    try:
        require_timezone(base["timezone"])
    except ScheduleError as exc:
        raise AutomationError(str(exc)) from exc
    try:
        schedule = normalize_schedule(base["schedule"]) if base.get("schedule") else None
    except ScheduleError as exc:
        if allow_incomplete:
            schedule = None
            issues.append(str(exc))
        else:
            raise AutomationError(str(exc)) from exc
    try:
        actions = _normalize_actions(base.get("actions") or [], schedule)
    except AutomationError:
        if allow_incomplete:
            actions = []
            issues.append("The actions could not be read.")
        else:
            raise
    marketplaces = _normalize_marketplaces(base.get("marketplaces") or [])
    filters = _normalize_filters(base.get("filters") or {})
    if not base["name"]:
        base["name"] = _fallback_name(actions, filters)
    issues.extend(_readiness(actions, marketplaces, schedule, filters))
    # Drop parser issues that the structured fields have since answered.
    issues = list(dict.fromkeys(issues))
    return {
        "op": base.get("op") or "create",
        "name": base["name"],
        "prompt": prompt or base.get("prompt") or "",
        "timezone": base["timezone"],
        "schedule": schedule,
        "filters": filters,
        "actions": actions,
        "marketplaces": marketplaces,
        "issues": issues,
        "ready": not issues and schedule is not None and bool(actions),
        "summary": describe(schedule, base["timezone"]) if schedule else "",
    }


def select_items(db: Session, spec: dict, now: datetime, *, automation_id: str | None = None, persist_price: bool = False) -> list[dict]:
    """Every listing the filters can see, with whether this run would touch it and why not."""
    moment = as_utc(now)
    wanted = set(spec.get("marketplaces") or [])
    filters = spec.get("filters") or {}
    actions = spec.get("actions") or []
    needs_live = any(action["type"] in {"delist", "relist"} for action in actions)
    conversations = db.query(Conversation).all()
    listings = _listings_by_conversation(db)
    rows = db.query(MarketplaceListing).all()
    by_item: dict[str, list[MarketplaceListing]] = {}
    for row in rows:
        by_item.setdefault(row.conversation_id, []).append(row)
    listed = load_listed_index(db)
    items = []
    for conv in conversations:
        listing = listings.get(conv.id) or {}
        notes = parse_notes(conv.notes)
        live = [row for row in by_item.get(conv.id, []) if row.state in LIVE_STATES]
        sold_on = _sold_marketplaces(db, conv.id)
        if wanted:
            live_here = [row for row in live if row.marketplace in wanted and row.marketplace not in sold_on]
        else:
            live_here = [row for row in live if row.marketplace not in sold_on]
        markets = sorted(
            name for name in listed_markets(notes, live_here)
            if (not wanted or name in wanted) and name not in sold_on
        )
        listed_at, listed_source = resolve_listed_at(listed, conv, live_here, wanted)
        age = _age_days(listed_at, moment)
        price = _number(listing.get("price"))
        original = _original_price(db, conv.id, price, persist=persist_price and price is not None)
        planned = _plan_price(actions, original, price)
        item = {
            "conversation_id": conv.id,
            "title": (listing.get("title") or conv.title or "Untitled listing"),
            "status": conv.status,
            "listed_at": listed_at.isoformat() if listed_at else None,
            "listed_source": listed_source,
            "age_days": age,
            "marketplaces": markets,
            "sold_marketplaces": sorted(sold_on & wanted) if wanted else sorted(sold_on),
            "price": price,
            "original_price": original,
            "next_price": planned.get("next_price"),
            "floor": planned.get("floor"),
            "include": False,
            "skip_reason": None,
        }
        reason = _skip_reason(
            db, conv, listing, item, filters, wanted, needs_live, moment, automation_id, actions, planned,
        )
        item["skip_reason"] = reason
        item["include"] = reason is None
        items.append(item)
    items.sort(key=lambda item: (not item["include"], item["title"].lower()))
    return items


def apply_local(db: Session, conv_id: str, actions: list[dict], *, dry_run: bool) -> dict:
    """Regenerate is not local. Price and field edits are saved as a new revision and do not publish."""
    revision, listing = _current(db, conv_id)
    if listing is None:
        return {"ok": False, "error": "It has no listing yet.", "revision_id": None}
    updated = copy.deepcopy(listing)
    notes = []
    for action in actions:
        if action["type"] == "update_fields":
            for key, value in (action.get("fields") or {}).items():
                if key in FIELD_NAMES and value is not None:
                    updated[key] = value
                    notes.append(f"set {key}")
        elif action["type"] == "price":
            current = _number(updated.get("price"))
            if current is None:
                return {"ok": False, "error": "It has no price.", "revision_id": revision.id if revision else None}
            original = _original_price(db, conv_id, current, persist=not dry_run)
            try:
                _basis, target = drop_price(
                    original, current, mode=action["mode"], value=action["value"],
                    floor=action.get("floor"), floor_percent=action.get("floor_percent"),
                    repeat=action.get("repeat") or "once",
                )
            except PriceError as exc:
                return {"ok": False, "error": str(exc), "revision_id": revision.id if revision else None}
            updated["price"] = target
            notes.append(f"price {current:g} -> {target:g} from original {original:g}")
    changed = updated != listing
    if dry_run or not changed:
        return {"ok": True, "revision_id": revision.id if revision else None, "changed": changed, "notes": notes, "price": updated.get("price")}
    saved = ListingRepo(db).save_revision(conv_id, updated, "automation", revision.id if revision else None)
    return {"ok": True, "revision_id": saved.id, "changed": True, "notes": notes, "price": updated.get("price")}


def log_item_job(db: Session, conv_id: str, revision_id: str | None, payload: dict, *, ok: bool, error: str | None) -> str:
    """One terminal job per item. It is not queued, so it never takes the Bridge slot."""
    if payload.get("dry_run"):
        status = "dry_run"
    else:
        status = "completed" if ok else "failed"
    job = JobRepo(db).create(
        conv_id,
        revision_id or "automation",
        payload,
        status=status,
        current_step="automation",
    )
    if error and not ok:
        job.last_error = error[:500]
        db.commit()
    JobRepo(db).add_event(job.id, "automation_item", "automation", {
        "automation_id": payload.get("automation_id"),
        "run_id": payload.get("run_id"),
        "ok": ok,
        "error": error,
        "dry_run": payload.get("dry_run"),
        "actions": payload.get("actions"),
        "price": payload.get("price"),
        "next_price": payload.get("next_price"),
        "original_price": payload.get("original_price"),
    })
    return job.id


def record_application(db: Session, automation_id: str, conv_id: str, listed_at: datetime | None, when: datetime) -> None:
    row = (
        db.query(AutomationApplication)
        .filter(AutomationApplication.automation_id == automation_id, AutomationApplication.conversation_id == conv_id)
        .one_or_none()
    )
    if row is None:
        row = AutomationApplication(automation_id=automation_id, conversation_id=conv_id)
        db.add(row)
    row.listed_at = _naive(listed_at) if listed_at else None
    row.applied_at = _naive(when)
    db.commit()


def view(row: Automation) -> dict:
    schedule = row.schedule or {}
    return {
        "id": row.id,
        "name": row.name,
        "prompt": row.prompt,
        "status": row.status,
        "timezone": row.timezone,
        "schedule": schedule,
        "filters": row.filters or {},
        "actions": row.actions or [],
        "marketplaces": row.marketplaces or [],
        "summary": describe(schedule, row.timezone) if schedule.get("kind") else "",
        "confirmed_at": _iso(row.confirmed_at),
        "next_run_at": _iso(row.next_run_at),
        "last_run_at": _iso(row.last_run_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def run_view(row: AutomationRun) -> dict:
    summary = row.summary or {}
    return {
        "id": row.id,
        "automation_id": row.automation_id,
        "origin": row.origin,
        "status": row.status,
        "dry_run": bool(row.dry_run),
        "attempt_count": row.attempt_count,
        "scheduled_for": _iso(row.scheduled_for),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "next_attempt_at": _iso(row.next_attempt_at),
        "error": row.error,
        "items": summary.get("items") or [],
        "job_ids": summary.get("job_ids") or [],
    }


def open_manual_run(db: Session, row: Automation, *, dry_run: bool, now: datetime) -> AutomationRun:
    if row.status == "draft":
        raise AutomationError("Confirm the automation before it runs.", 409)
    moment = as_utc(now)
    for _ in range(5):
        run = AutomationRun(
            id=new_id(), automation_id=row.id, scheduled_for=_naive(moment), origin="manual",
            status="running", dry_run=dry_run, attempt_count=1, started_at=_naive(moment), summary={"items": [], "job_ids": []},
        )
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            moment = moment + timedelta(microseconds=1000)
            continue
        db.refresh(run)
        return run
    raise AutomationError("Could not start a run. Try again.")


def claim_scheduled_run(db: Session, row: Automation, now: datetime) -> AutomationRun | None:
    slot = as_utc(row.next_run_at)
    if slot is None:
        return None
    run = AutomationRun(
        id=new_id(), automation_id=row.id, scheduled_for=_naive(slot), origin="schedule",
        status="running", dry_run=False, attempt_count=1, started_at=_naive(now), summary={"items": [], "job_ids": []},
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("automation %s slot %s already claimed", row.id, slot.isoformat())
        return None
    db.refresh(run)
    return run


def finish_run(db: Session, run: AutomationRun, automation: Automation, now: datetime, *, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _naive(now) if status in {"completed", "failed"} else None
    automation.last_run_at = _naive(now)
    if status == "completed" and run.origin == "schedule":
        nxt = advance_after_run(automation.schedule, automation.timezone, run.scheduled_for, now)
        automation.next_run_at = _naive(nxt)
        if automation.schedule.get("kind") == "once":
            automation.status = "completed"
    elif status == "failed" and run.attempt_count >= MAX_ATTEMPTS and run.origin == "schedule":
        nxt = advance_after_run(automation.schedule, automation.timezone, run.scheduled_for, now)
        automation.next_run_at = _naive(nxt)
        if automation.schedule.get("kind") == "once":
            automation.status = "paused"
    automation.updated_at = _naive(now)
    db.commit()


def mark_waiting(db: Session, run: AutomationRun, now: datetime) -> None:
    run.status = "waiting"
    run.next_attempt_at = _naive(as_utc(now) + BUSY_WAIT)
    run.error = "Another marketplace job is running. This one will start when that finishes."
    db.commit()


def mark_retry(db: Session, run: AutomationRun, now: datetime, error: str) -> None:
    """Count this failure. The claim itself was attempt 1; each failure after the last allowed one stops."""
    current = int(run.attempt_count or 1)
    run.error = error[:500]
    if current >= MAX_ATTEMPTS:
        run.status = "failed"
        run.finished_at = _naive(now)
    else:
        run.attempt_count = current + 1
        run.status = "waiting"
        wait = RETRY_WAIT[min(current - 1, len(RETRY_WAIT) - 1)]
        run.next_attempt_at = _naive(as_utc(now) + wait)
    db.commit()


# --- internals ---


def _insert(db: Session, spec: dict, *, activate: bool, now: datetime) -> Automation:
    moment = as_utc(now)
    row = Automation(
        name=spec["name"], prompt=spec["prompt"] or None, status="active" if activate else "draft",
        timezone=spec["timezone"], schedule=spec["schedule"] or {}, filters=spec["filters"],
        actions=spec["actions"], marketplaces=spec["marketplaces"],
        confirmed_at=_naive(moment) if activate else None,
        next_run_at=_naive(first_due(spec["schedule"], spec["timezone"], moment)) if activate else None,
        created_at=_naive(moment), updated_at=_naive(moment),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _require_ready(spec: dict) -> None:
    if not spec.get("ready"):
        detail = " ".join(spec.get("issues") or ["This automation is not ready to confirm."])
        raise AutomationError(detail)


def _readiness(actions: list[dict], marketplaces: list[str], schedule: dict | None, filters: dict) -> list[str]:
    issues = []
    if schedule is None:
        issues.append("Choose when it runs.")
    if not actions:
        issues.append("Choose at least one action.")
    if any(action["type"] in {"delist", "relist"} for action in actions) and not marketplaces:
        issues.append("Name the marketplaces. An automation does not run on every marketplace by default.")
    if filters.get("status") == "sold":
        issues.append("Sold items are never changed.")
    for action in actions:
        if action["type"] == "price" and not action.get("value"):
            issues.append("Say how much to drop the price, as an amount or a percent.")
    return issues


def _normalize_actions(actions: list, schedule: dict | None = None) -> list[dict]:
    if not isinstance(actions, list):
        raise AutomationError("Actions must be a list.")
    found: dict[str, dict] = {}
    for raw in actions:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(exclude_none=True)
        if not isinstance(raw, dict):
            raise AutomationError("Each action must be an object.")
        kind = str(raw.get("type") or "").lower()
        if kind not in ACTION_ORDER:
            raise AutomationError(f"Unknown action {kind or '(blank)'}.")
        action = {"type": kind}
        if kind == "price":
            mode = str(raw.get("mode") or "").lower()
            if mode not in {"amount", "percent"}:
                raise AutomationError("A price change is an amount or a percent.")
            action["mode"] = mode
            action["value"] = raw.get("value")
            if raw.get("floor") is not None:
                action["floor"] = float(raw["floor"])
            if raw.get("floor_percent") is not None:
                action["floor_percent"] = float(raw["floor_percent"])
            action["repeat"] = _price_repeat(raw.get("repeat"), schedule)
        if kind == "update_fields":
            fields = raw.get("fields") or {}
            if not isinstance(fields, dict):
                raise AutomationError("Field updates must be an object of field names.")
            clean = {key: str(value) for key, value in fields.items() if key in FIELD_NAMES and str(value).strip()}
            if not clean:
                raise AutomationError("Name a field to update: title, description, brand, condition, size, sku, department, or category.")
            action["fields"] = clean
        found[kind] = action
    # Relist already takes the listing down. Doing both would delist twice.
    if "relist" in found:
        found.pop("delist", None)
    return [found[name] for name in ACTION_ORDER if name in found]


def _price_repeat(raw, schedule: dict | None) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"once", "one", "one_time", "single"}:
        return "once"
    if text in {"step", "stepped", "recurring", "each"}:
        return "step"
    if text:
        raise AutomationError("A price drop happens once, or it steps down each cycle.")
    if schedule and schedule.get("kind") in {"daily", "weekly"}:
        return "step"
    return "once"


def _normalize_marketplaces(values) -> list[str]:
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",") if part.strip()]
    if not isinstance(values, list):
        raise AutomationError("Marketplaces must be a list.")
    names = []
    for value in values:
        name = str(value or "").strip().lower()
        if not name:
            continue
        if name not in MARKETPLACES:
            raise AutomationError(f"Unknown marketplace {name}.")
        if name not in names:
            names.append(name)
    return names


def _normalize_filters(raw: dict) -> dict:
    filters: dict[str, Any] = {}
    if raw.get("category"):
        filters["category"] = str(raw["category"]).strip().lower()
    if raw.get("department"):
        filters["department"] = str(raw["department"]).strip().lower()
    if raw.get("brand"):
        filters["brand"] = str(raw["brand"]).strip().lower()
    if raw.get("min_age_days") not in (None, ""):
        filters["min_age_days"] = int(raw["min_age_days"])
        if filters["min_age_days"] < 1:
            raise AutomationError("Age has to be at least one day.")
    if raw.get("max_age_days") not in (None, ""):
        filters["max_age_days"] = int(raw["max_age_days"])
    if raw.get("status"):
        filters["status"] = str(raw["status"]).strip().lower()
    ids = raw.get("conversation_ids") or []
    if isinstance(ids, str):
        ids = [part.strip() for part in ids.split(",") if part.strip()]
    if ids:
        filters["conversation_ids"] = [str(item) for item in ids if str(item).strip()]
    return filters


def _fallback_name(actions: list[dict], filters: dict) -> str:
    if filters.get("min_age_days"):
        return f"After {filters['min_age_days']} days listed"
    kinds = [action["type"].replace("_", " ") for action in actions]
    return ", ".join(kinds).capitalize() or "Automation"


def _summary(spec: dict) -> str:
    schedule = describe(spec["schedule"], spec["timezone"]) if spec.get("schedule") else "No schedule yet"
    actions = ", ".join(action["type"].replace("_", " ") for action in spec.get("actions") or []) or "no actions"
    markets = ", ".join(spec.get("marketplaces") or []) or "no marketplaces named"
    return f"{spec.get('name') or 'Automation'}: {schedule}. {actions}. {markets}."


def _listings_by_conversation(db: Session) -> dict[str, dict]:
    rows = (
        db.query(Listing.conversation_id, ListingRevision.listing_json)
        .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
        .all()
    )
    return {conv_id: body for conv_id, body in rows if isinstance(body, dict)}


def _current(db: Session, conv_id: str) -> tuple[ListingRevision | None, dict | None]:
    listing = ListingRepo(db).get_current(conv_id)
    if listing is None or not listing.current_revision_id:
        return None, None
    revision = ListingRepo(db).get_revision(listing.current_revision_id)
    if revision is None or not isinstance(revision.listing_json, dict):
        return None, None
    return revision, revision.listing_json


def _original_price(db: Session, conv_id: str, current: float | None, *, persist: bool) -> float | None:
    row = db.get(ItemPriceBaseline, conv_id)
    if row is not None:
        return float(row.original_price)
    first = (
        db.query(ListingRevision)
        .filter(ListingRevision.conversation_id == conv_id)
        .order_by(ListingRevision.created_at.asc(), ListingRevision.id.asc())
        .all()
    )
    original = None
    for revision in first:
        if isinstance(revision.listing_json, dict) and _number(revision.listing_json.get("price")):
            original = _number(revision.listing_json.get("price"))
            break
    if original is None:
        original = current
    if original is None:
        return None
    if persist:
        db.add(ItemPriceBaseline(conversation_id=conv_id, original_price=original, created_at=_naive(utcnow())))
        db.commit()
    return original


def _plan_price(actions: list[dict], original: float | None, current: float | None) -> dict:
    action = next((item for item in actions if item["type"] == "price"), None)
    if action is None or original is None or current is None or not action.get("value"):
        return {}
    try:
        basis, target = drop_price(
            original, current, mode=action["mode"], value=float(action["value"]),
            floor=action.get("floor"), floor_percent=action.get("floor_percent"),
            repeat=action.get("repeat") or "once",
        )
    except PriceError as exc:
        return {"error": str(exc)}
    return {"original_price": basis, "next_price": target, "floor": action.get("floor")}


def _skip_reason(db, conv, listing, item, filters, wanted, needs_live, now, automation_id, actions, planned) -> str | None:
    if (conv.status or "") == "sold":
        return "Sold items are never changed."
    if filters.get("status") and (conv.status or "") != filters["status"]:
        return f"Status is {conv.status or 'unset'}, not {filters['status']}."
    if filters.get("conversation_ids") and conv.id not in filters["conversation_ids"]:
        return "Not one of the items this automation names."
    haystack = _haystack(conv, listing)
    if filters.get("department") and not _department_matches(haystack, filters["department"]):
        return f"Department is not {filters['department']}."
    if filters.get("category") and not _category_matches(haystack, filters["category"]):
        return f"Category is not {filters['category']}."
    if filters.get("brand") and filters["brand"] not in haystack:
        return f"Brand is not {filters['brand']}."
    if needs_live and wanted and not item["marketplaces"]:
        if item.get("sold_marketplaces"):
            return "It sold on " + ", ".join(item["sold_marketplaces"]) + ". Sold listings are left as they are."
        return "It is not listed on " + ", ".join(wanted) + "."
    if filters.get("min_age_days") is not None:
        if item["age_days"] is None:
            return "It has no listed date."
        if item["age_days"] < int(filters["min_age_days"]):
            return f"Listed {item['age_days']} days, under {filters['min_age_days']}."
        if automation_id and _cooled_down(db, automation_id, conv.id, int(filters["min_age_days"]), now):
            return f"Already handled in the last {filters['min_age_days']} days."
    if filters.get("max_age_days") is not None and item["age_days"] is not None and item["age_days"] > int(filters["max_age_days"]):
        return f"Listed {item['age_days']} days, over {filters['max_age_days']}."
    if any(action["type"] == "price" for action in actions):
        if item["price"] is None:
            return "It has no price."
        if planned.get("error"):
            return planned["error"]
    if any(action["type"] in {"price", "update_fields", "regenerate", "relist"} for action in actions) and not listing:
        return "It has no listing yet."
    return None


def _sold_marketplaces(db: Session, conv_id: str) -> set[str]:
    """Marketplaces where this item is sold out. Those listings are never delisted or relisted."""
    try:
        from vendoo_studio.models.marketplace_sale import MarketplaceSale
        from vendoo_studio.services.sale_inventory import stock_after_sale
    except Exception:
        return set()
    sold = set()
    for sale in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id == conv_id).all():
        try:
            stock = stock_after_sale(db, conv_id, sale.marketplace, sale.listing_id)
        except Exception:
            continue
        if stock.get("status") == "sold_out":
            sold.add(sale.marketplace)
    return sold


def _cooled_down(db: Session, automation_id: str, conv_id: str, days: int, now: datetime) -> bool:
    row = (
        db.query(AutomationApplication)
        .filter(AutomationApplication.automation_id == automation_id, AutomationApplication.conversation_id == conv_id)
        .one_or_none()
    )
    if row is None or row.applied_at is None:
        return False
    return as_utc(row.applied_at) + timedelta(days=days) > as_utc(now)


def _haystack(conv: Conversation, listing: dict) -> str:
    parts = [conv.title or "", listing.get("title"), listing.get("brand"), listing.get("department"), listing.get("category_path"), listing.get("category")]
    for key in ("ebay_specifics", "poshmark_specifics", "mercari_specifics", "depop_specifics", "etsy_specifics"):
        spec = listing.get(key)
        if isinstance(spec, dict):
            path = spec.get("categoryPath") or spec.get("category_path")
            if isinstance(path, list):
                parts.extend(str(piece) for piece in path)
            elif path:
                parts.append(str(path))
    tags = listing.get("tags") or []
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    return " ".join(str(part) for part in parts if part).lower()


def _department_matches(text: str, want: str) -> bool:
    want = want.lower()
    if want in {"men", "mens", "men's", "male"}:
        return bool(re.search(r"\bmens?\b|men's|\bmale\b", text)) and not re.search(r"\bwomens?\b|women's", text)
    if want in {"women", "womens", "women's", "female"}:
        return bool(re.search(r"\bwomens?\b|women's|\bfemale\b", text))
    return want in text


def _category_matches(text: str, want: str) -> bool:
    want = want.lower()
    if want in {"t-shirt", "tshirt", "tee", "tees"}:
        return bool(re.search(r"t-?\s*shirts?|\btees?\b", text))
    return want in text


def _age_days(listed_at: datetime | None, now: datetime) -> int | None:
    if listed_at is None:
        return None
    return max(0, (as_utc(now) - as_utc(listed_at)).days)


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _find_named(db: Session, data: dict) -> Automation | None:
    if data.get("id"):
        return db.get(Automation, str(data["id"]))
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    return db.query(Automation).filter(Automation.name == name).order_by(Automation.created_at.desc()).first()


def _naive(value: datetime | None) -> datetime | None:
    """SQLite drops timezone info. Store UTC wall time and put the zone back when reading."""
    if value is None:
        return None
    return as_utc(value).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    stamp = as_utc(value)
    return stamp.isoformat() if stamp else None


def jobs_for_run(db: Session, run_id: str) -> list[Job]:
    """Jobs whose snapshot names this run. History reads the run summary; this is the check that the log exists."""
    rows = db.query(Job).filter(Job.current_step == "automation").all()
    return [row for row in rows if isinstance(row.listing_snapshot, dict) and row.listing_snapshot.get("run_id") == run_id]
