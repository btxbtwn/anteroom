"""Scheduled automations: list, draft, preview, confirm, and run them.

Creating a task does not run it. ``activate`` is the seller confirming the
actions and marketplaces by name. A draft that is still ambiguous is refused
at that point. Run now only works after that confirmation.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services import automations
from vendoo_studio.services.automation_scheduler import _execute
from vendoo_studio.services.automations import AutomationBusy, AutomationError

router = APIRouter(prefix="/api/automations", tags=["automations"])


class ActionBody(BaseModel):
    type: str
    mode: str | None = None
    value: float | None = None
    floor: float | None = None
    floor_percent: float | None = None
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
    name: str | None = None
    prompt: str | None = Field(default=None, max_length=8000)
    timezone: str | None = None
    schedule: ScheduleBody | None = None
    filters: FiltersBody | None = None
    actions: list[ActionBody] | None = None
    marketplaces: list[str] | None = None
    activate: bool = Field(default=False, description="Confirm this automation and let it run. Leave this off to save a draft.")


class DraftBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    timezone: str | None = None


class RunBody(BaseModel):
    dry_run: bool = Field(default=False, description="Show the items and the price change without delisting, relisting, or saving.")


def _fail(exc: AutomationError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def _payload(body: AutomationBody) -> dict:
    data = body.model_dump(exclude_none=True)
    return data


@router.get("")
def list_automations(db: Session = Depends(get_db)):
    """List scheduled automations, newest first."""
    return automations.list_automations(db)


@router.post("/draft")
def draft_automation(body: DraftBody):
    """Turn a sentence into a structured automation. Nothing is saved."""
    return automations.draft_prompt(body.prompt, timezone=body.timezone or "UTC")


@router.post("/preview")
def preview_automation(body: AutomationBody, db: Session = Depends(get_db)):
    """Which items a task would touch, and the price each would become. Nothing is saved or sent."""
    try:
        return automations.preview(db, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("")
def create_automation(body: AutomationBody, db: Session = Depends(get_db)):
    """Save an automation. It runs only when activate is set and the task names its actions and marketplaces."""
    try:
        return automations.create_automation(db, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.patch("/{automation_id}")
def update_automation(automation_id: str, body: AutomationBody, db: Session = Depends(get_db)):
    """Change a saved automation. Pass activate to confirm an edited draft."""
    try:
        return automations.update_automation(db, automation_id, _payload(body))
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("/{automation_id}/pause")
def pause_automation(automation_id: str, db: Session = Depends(get_db)):
    """Stop an automation from starting new runs. A run already going finishes."""
    try:
        return automations.pause_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("/{automation_id}/resume")
def resume_automation(automation_id: str, db: Session = Depends(get_db)):
    """Let a paused automation run again at its next time. A missed time runs once."""
    try:
        return automations.resume_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.delete("/{automation_id}")
def delete_automation(automation_id: str, db: Session = Depends(get_db)):
    """Delete an automation and its run history. Listing jobs it already wrote stay in the job log."""
    try:
        return automations.delete_automation(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc


@router.post("/{automation_id}/run")
async def run_automation(automation_id: str, body: RunBody | None = None, db: Session = Depends(get_db)):
    """Run a confirmed automation now. A dry run only records what would change."""
    from vendoo_studio.models.automation import AutomationRun

    try:
        row = automations.get_automation(db, automation_id)
        if db.query(AutomationRun).filter(AutomationRun.status.in_(("running", "waiting"))).first():
            raise AutomationError("Another automation is already running.", 409)
        if JobRepo(db).get_active() and not (body and body.dry_run):
            raise AutomationError("A marketplace job is running. Wait for it to finish.", 409)
        dry_run = bool(body.dry_run) if body else False
        run = automations.open_manual_run(db, row, dry_run=dry_run, now=datetime.now(UTC))
        await _execute(db, run, row, datetime.now(UTC), None if not dry_run else _dry_executor)
    except AutomationBusy as exc:
        raise HTTPException(409, str(exc) or "A marketplace job is running. Wait for it to finish.") from exc
    except AutomationError as exc:
        raise _fail(exc) from exc
    db.refresh(run)
    return automations.run_view(run)


async def _dry_executor(*_args, **_kwargs):
    return {"ok": True, "dry_run": True}


@router.get("/{automation_id}/history")
def automation_history(automation_id: str, db: Session = Depends(get_db)):
    """Past runs, with each item's result and any error."""
    try:
        return automations.history(db, automation_id)
    except AutomationError as exc:
        raise _fail(exc) from exc
