"""Scheduled automations: list, draft, preview, confirm, and run them.

Activating a task permits local draft preparation. Marketplace writes always
wait for a separate confirmation of each item, action and named marketplace.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from anteroom.database import claim_in_worker_session, get_db, in_worker_session
from anteroom.models.automation import AutomationApproval
from anteroom.repositories.queries import JobRepo
from anteroom.services import automations
from anteroom.services.automation_scheduler import _execute
from anteroom.services.automations import AutomationBusy, AutomationError

router = APIRouter(prefix="/api/automations", tags=["automations"])


class ActionBody(BaseModel):
    type: str
    mode: str | None = None
    value: float | None = None
    floor: float | None = None
    floor_percent: float | None = None
    repeat: str | None = Field(default=None, description="step walks the original price down each cycle. once drops a single time.")
    fields: dict[str, str] | None = None


class ScheduleBody(BaseModel):
    kind: str | None = None
    at: str | None = None
    time: str | None = None
    weekdays: list[int] | None = None
    cron: str | None = None


class FiltersBody(BaseModel):
    category: str | None = None
    department: str | None = None
    brand: str | None = None
    min_age_days: int | None = Field(default=None, ge=1, le=3650)
    max_age_days: int | None = Field(default=None, ge=1, le=3650)
    status: str | None = None
    conversation_ids: list[str] | None = None


class AutomationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    prompt: str | None = Field(default=None, max_length=8000)
    timezone: str | None = None
    schedule: ScheduleBody | None = None
    filters: FiltersBody | None = None
    actions: list[ActionBody] | None = None
    marketplaces: list[str] | None = None
    price_repeat: Literal["step", "once"] | None = Field(default=None, description="step walks the original price down each cycle. once drops a single time.")
    activate: bool = Field(default=False, description="Confirm this automation and let it run. Leave this off to save a draft.")


class DraftBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    timezone: str | None = None


class RunBody(BaseModel):
    dry_run: bool = Field(default=False, description="Show the items and the price change without delisting, relisting, or saving.")


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool = False
    action: Literal["publish", "delist", "relist"]
    marketplaces: list[str] = Field(min_length=1, max_length=7)
    revision_id: str | None
    review_token: str = Field(pattern=r"^[a-f0-9]{64}$")


def _fail(exc: AutomationError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def _payload(body: AutomationBody) -> dict:
    data = body.model_dump(exclude_none=True)
    return data


@router.get("")
def list_automations(db: Session = Depends(get_db, scope="function")):
    """List scheduled automations, newest first."""
    return automations.list_automations(db)


@router.post("/draft")
def draft_automation(body: DraftBody):
    """Turn a sentence into a structured automation. Nothing is saved."""
    return automations.draft_prompt(body.prompt, timezone=body.timezone or "UTC")


@router.post("/preview")
def preview_automation(body: AutomationBody, db: Session = Depends(get_db, scope="function")):
    """Which items a task would touch, and the price each would become. Nothing is saved or sent."""
    try:
        return automations.preview(db, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db, scope="function")):
    """Item-specific marketplace actions waiting for seller approval."""
    return automations.list_pending_approvals(db)


def _claim_approval(db: Session, approval_id: str, body: ConfirmBody) -> dict:
    """Check the reviewed action still matches and nothing else runs, then claim it. One session."""
    from anteroom.services.api_job_lock import start_gate

    with start_gate():
        row = automations.get_approval(db, approval_id)
        if JobRepo(db).get_active():
            raise AutomationError("A marketplace job is running. Wait for it to finish.", 409)
        automations.claim_approval(db, row, review_token=body.review_token, action=body.action,
                                  marketplaces=body.marketplaces, revision_id=body.revision_id)
        return {"automation_id": row.automation_id, "run_id": row.run_id, "conversation_id": row.conversation_id,
                "title": row.title, "action": row.action, "marketplaces": list(row.marketplaces),
                "revision_id": row.revision_id}


def _settle_approval(db: Session, approval_id: str, claimed: dict, item: dict, result: dict) -> dict:
    row = db.get(AutomationApproval, approval_id)
    assert row is not None
    try:
        automations.note_relist_result(db, claimed["automation_id"], claimed["run_id"], item, result)
        view = automations.mark_approval(db, row, "approved" if result.get("ok") else "failed")
        automations.record_approval_result(db, row, result)
    except BaseException:
        db.rollback()
        _fail_executing(db, approval_id)
        raise
    view["result"] = result
    return view


def _fail_executing(db: Session, approval_id: str) -> None:
    row = db.get(AutomationApproval, approval_id)
    if row is not None and row.status == "executing":
        automations.mark_approval(db, row, "failed")


@router.post("/approvals/{approval_id}/approve")
async def approve_listing(approval_id: str, body: ConfirmBody):
    """Perform the one item/action/revision/marketplace set the seller reviewed."""
    from anteroom.services.automation_actions import perform, publish_revision
    from anteroom.services.pricing_rules import active_rules, freeze_active_rules

    if body.confirm is not True:
        raise HTTPException(400, "Confirm this item's marketplace action.")
    with freeze_active_rules(await asyncio.to_thread(active_rules)):
        try:
            # A request dropped while claiming ends the claimed approval as failed rather than executing.
            claimed = await claim_in_worker_session(
                _claim_approval, lambda _claimed: in_worker_session(_fail_executing, approval_id), approval_id, body)
        except AutomationError as exc:
            raise _fail(exc) from exc
        # Claimed: each database step below has its own short session, none held while the marketplaces answer.
        try:
            item = {"conversation_id": claimed["conversation_id"], "title": claimed["title"]}
            if claimed["action"] == "publish":
                result = await publish_revision(claimed["conversation_id"], claimed["marketplaces"], claimed["revision_id"])
            else:
                result = await perform(item, {"type": claimed["action"]}, claimed["marketplaces"], claimed["revision_id"], confirm=True)
        except AutomationBusy as exc:
            await asyncio.shield(in_worker_session(_fail_executing, approval_id))
            raise HTTPException(409, str(exc) or "A marketplace job is running. Review a new action after it finishes.") from exc
        except AutomationError as exc:
            raise _fail(exc) from exc
        except BaseException:
            await asyncio.shield(in_worker_session(_fail_executing, approval_id))
            raise
    return await asyncio.shield(in_worker_session(_settle_approval, approval_id, claimed, item, result))


class BulkScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=200)
    publish_at: str = Field(min_length=1)
    marketplaces: list[str] = Field(min_length=1, max_length=7)
    confirm: bool = False


class BulkUnscheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class AutoRelistFiltersBody(BaseModel):
    min_likes: int | None = Field(default=None, ge=0)
    max_likes: int | None = Field(default=None, ge=0)
    conditions: list[str] | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    keywords: list[str] | None = None
    labels: list[str] | None = None


class AutoRelistRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    confirm: bool = False
    min_age_days: int = Field(default=30, ge=1, le=365)
    ebay_recreate_before_gtc_minutes: int | None = Field(default=30, ge=5, le=1440)
    daily_cap: int = Field(default=25, ge=1, le=100)
    price_adjust_mode: Literal["percent", "amount"] | None = None
    price_adjust_value: float | None = None
    price_floor: float | None = Field(default=None, ge=0)
    skip_active_offers: bool = True
    filters: AutoRelistFiltersBody | None = None


class AutoRelistRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marketplace: str | None = None
    dry_run: bool = False


@router.get("/scheduled-drafts")
def list_scheduled_drafts(db: Session = Depends(get_db, scope="function")):
    """List drafts marked ready-to-list or scheduled for future publishing."""
    from anteroom.services import draft_scheduling

    return {"drafts": draft_scheduling.list_scheduled_drafts(db)}


@router.get("/scheduled-drafts/logs")
def list_scheduled_draft_logs(limit: int = 100, db: Session = Depends(get_db, scope="function")):
    """Audit log of scheduled draft publish attempts and failures."""
    from anteroom.services import draft_scheduling

    return {"logs": draft_scheduling.list_publish_logs(db, limit=min(max(limit, 1), 200))}


@router.post("/scheduled-drafts/bulk-schedule")
def bulk_schedule_drafts(body: BulkScheduleBody, db: Session = Depends(get_db, scope="function")):
    """Schedule multiple validated drafts for future publishing to named marketplaces."""
    from anteroom.services import draft_scheduling

    try:
        return draft_scheduling.bulk_schedule(
            db,
            body.conversation_ids,
            publish_at=body.publish_at,
            marketplaces=body.marketplaces,
            confirm=body.confirm,
        )
    except draft_scheduling.SchedulePublishError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/scheduled-drafts/bulk-unschedule")
def bulk_unschedule_drafts(body: BulkUnscheduleBody, db: Session = Depends(get_db, scope="function")):
    """Cancel scheduled future publishes for multiple drafts."""
    from anteroom.services import draft_scheduling

    return draft_scheduling.bulk_unschedule(db, body.conversation_ids, reason=body.reason)


@router.get("/auto-relist")
def list_auto_relist_rules(db: Session = Depends(get_db, scope="function")):
    """Per-marketplace unattended Auto-Relist and eBay Auto-Recreate settings (OFF by default)."""
    from anteroom.services import auto_relist

    return {"rules": auto_relist.list_rules(db)}


@router.get("/auto-relist/logs")
def list_auto_relist_logs(marketplace: str | None = None, limit: int = 100, db: Session = Depends(get_db, scope="function")):
    """Audit log of unattended auto-relist and eBay recreate runs."""
    from anteroom.services import auto_relist

    return {"logs": auto_relist.list_logs(db, marketplace=marketplace, limit=min(max(limit, 1), 200))}


@router.put("/auto-relist/{marketplace}")
def save_auto_relist_rule(marketplace: str, body: AutoRelistRuleBody, db: Session = Depends(get_db, scope="function")):
    """Update one marketplace's Auto-Relist / eBay Auto-Recreate rule."""
    from anteroom.services import auto_relist

    try:
        return auto_relist.save_rule(db, marketplace, body.model_dump(exclude={"confirm"}), confirm=body.confirm)
    except auto_relist.AutoRelistError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/auto-relist/run")
async def run_auto_relist_now(body: AutoRelistRunBody | None = None):
    """Run an immediate auto-relist pass (or dry-run preview) for enabled marketplace rules."""
    from anteroom.services import auto_relist

    marketplace = body.marketplace if body else None
    dry_run = bool(body.dry_run) if body else False
    try:
        results = await auto_relist.run_auto_relist(
            marketplace=marketplace,
            dry_run=dry_run,
            respect_backoff=False,
        )
    except auto_relist.AutoRelistError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"ok": True, "outcomes": results, "dry_run": dry_run}


@router.post("")
def create_automation(body: AutomationBody, db: Session = Depends(get_db, scope="function")):
    """Save an automation. It runs only when activate is set and the task names its actions and marketplaces."""
    try:
        return automations.create_automation(db, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.patch("/{automation_id}")
def update_automation(automation_id: str, body: AutomationBody, db: Session = Depends(get_db, scope="function")):
    """Change a saved automation. Pass activate to confirm an edited draft."""
    try:
        return automations.update_automation(db, automation_id, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("/{automation_id}/pause")
def pause_automation(automation_id: str, db: Session = Depends(get_db, scope="function")):
    """Stop an automation from starting new runs. A run already going finishes."""
    try:
        return automations.pause_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("/{automation_id}/resume")
def resume_automation(automation_id: str, db: Session = Depends(get_db, scope="function")):
    """Let a paused automation run again at its next time. A missed time runs once."""
    try:
        return automations.resume_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.delete("/{automation_id}")
def delete_automation(automation_id: str, db: Session = Depends(get_db, scope="function")):
    """Delete an automation and its run history. Listing jobs it already wrote stay in the job log."""
    try:
        return automations.delete_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


def _open_run(db: Session, automation_id: str, dry_run: bool) -> str:
    from anteroom.models.automation import AutomationRun

    row = automations.get_automation(db, automation_id)
    if db.query(AutomationRun).filter(AutomationRun.status.in_(("running", "waiting"))).first():
        raise AutomationError("Another automation is already running.", 409)
    if JobRepo(db).get_active() and not dry_run:
        raise AutomationError("A marketplace job is running. Wait for it to finish.", 409)
    return automations.open_manual_run(db, row, dry_run=dry_run, now=datetime.now(UTC)).id


def _run_view(db: Session, run_id: str) -> dict:
    from anteroom.models.automation import AutomationRun

    run = db.get(AutomationRun, run_id)
    assert run is not None
    return automations.run_view(run)


@router.post("/{automation_id}/run")
async def run_automation(automation_id: str, body: RunBody | None = None):
    """Run a confirmed automation now. A dry run only records what would change."""
    dry_run = bool(body.dry_run) if body else False
    try:
        run_id = await in_worker_session(_open_run, automation_id, dry_run)
        await _execute(run_id, automation_id, datetime.now(UTC), None if not dry_run else _dry_executor)
    except AutomationBusy as exc:
        raise HTTPException(409, str(exc) or "A marketplace job is running. Wait for it to finish.") from exc
    except AutomationError as exc:
        raise _fail(exc) from exc
    return await in_worker_session(_run_view, run_id)


async def _dry_executor(*_args, **_kwargs):
    return {"ok": True, "dry_run": True}


@router.get("/{automation_id}/history")
def automation_history(automation_id: str, db: Session = Depends(get_db, scope="function")):
    """Past runs, with each item's result and any error."""
    try:
        return automations.history(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc
