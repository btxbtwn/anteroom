"""Home Command Center API: Today's Summary, Goals Tracker, and Needs Attention Inbox."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.routes.api_contract import (
    CONFLICT,
    GOAL_NOT_FOUND,
    NOT_FOUND,
    AttentionInbox,
    AttentionResolveInput,
    AttentionResolveResult,
    AttentionRestoreInput,
    AttentionRestoreResult,
    AttentionSnoozeInput,
    AttentionSnoozeResult,
    GoalChangeResult,
    GoalInput,
    GoalUpdate,
    HomeSummary,
)
from anteroom.services import home_dashboard, similar_items

router = APIRouter(prefix="/api/home", tags=["home"])


def _mutation_result(db: Session, timezone: str, *, goal_id: str | None = None) -> dict:
    result: dict = {"ok": True}
    if goal_id is not None:
        result["goal_id"] = goal_id
    try:
        result["home"] = home_dashboard.home_summary(db, tz_name=timezone)
    except Exception:
        logging.getLogger(__name__).warning("Could not refresh Today after a saved mutation", exc_info=True)
    return result


@router.get("", response_model=HomeSummary)
def get_home_summary(
    response: Response,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    response.headers["Cache-Control"] = "no-store"
    return home_dashboard.home_summary(db, tz_name=timezone)


@router.post("/goals", response_model=GoalChangeResult)
def upsert_goal(
    body: GoalInput,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    try:
        goal = home_dashboard.upsert_goal(
            db,
            period=body.period,
            metric=body.metric,
            target=body.target,
            label=body.label,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _mutation_result(db, timezone, goal_id=goal)


@router.patch("/goals/{goal_id}", response_model=GoalChangeResult, responses=GOAL_NOT_FOUND)
def update_goal(
    goal_id: str,
    body: GoalUpdate,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    try:
        updated = home_dashboard.update_goal(
            db,
            goal_id,
            period=body.period,
            metric=body.metric,
            target=body.target,
            label=body.label,
            label_provided="label" in body.model_fields_set,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Goal not found")
    return _mutation_result(db, timezone, goal_id=updated)


@router.delete("/goals/{goal_id}", response_model=GoalChangeResult, responses=GOAL_NOT_FOUND)
def delete_goal(
    goal_id: str,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    removed = home_dashboard.delete_goal(db, goal_id)
    if not removed:
        raise HTTPException(404, "Goal not found")
    return _mutation_result(db, timezone)


@router.get("/attention", response_model=AttentionInbox)
def get_attention(response: Response, db: Session = Depends(get_db, scope="function")):
    """Needs Attention alone, without Today and goals, for the iPhone app."""
    response.headers["Cache-Control"] = "no-store"
    return home_dashboard.attention_inbox(db)


# Snooze, resolve and restore skip home_summary: rebuilding Today scans every
# listing and job and stalls the action on large inventories. The client
# patches its copy from the answer.
@router.post("/attention/dismiss", response_model=AttentionSnoozeResult)
def dismiss_attention(
    body: AttentionSnoozeInput,
    db: Session = Depends(get_db, scope="function"),
):
    try:
        dismissal = home_dashboard.dismiss_attention_item(db, item_key=body.item_key, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "dismissal": dismissal}


@router.post("/attention/resolve", response_model=AttentionResolveResult, responses={**NOT_FOUND, **CONFLICT})
def resolve_attention(
    body: AttentionResolveInput,
    db: Session = Depends(get_db, scope="function"),
):
    try:
        resolution = home_dashboard.resolve_attention_item(
            db, item_key=body.item_key, kind=body.kind, fingerprint=body.fingerprint,
        )
    except similar_items.MergeError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "resolution": resolution}


@router.post("/attention/restore", response_model=AttentionRestoreResult)
def restore_attention(
    body: AttentionRestoreInput,
    db: Session = Depends(get_db, scope="function"),
):
    restored = home_dashboard.restore_attention_item(db, item_key=body.item_key)
    return {"ok": True, "restored": restored, "item_key": body.item_key}
