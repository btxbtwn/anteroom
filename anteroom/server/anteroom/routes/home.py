"""Home Command Center API: Today's Summary, Goals Tracker, and Needs Attention Inbox."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import home_dashboard

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


class GoalCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Literal["weekly", "monthly", "quarterly", "yearly"]
    metric: Literal["profit", "sales", "listings", "sell_through_rate"]
    target: float = Field(gt=0, le=100_000_000)
    label: str | None = Field(default=None, max_length=80)


class GoalUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Literal["weekly", "monthly", "quarterly", "yearly"] | None = None
    metric: Literal["profit", "sales", "listings", "sell_through_rate"] | None = None
    target: float | None = Field(default=None, gt=0, le=100_000_000)
    label: str | None = Field(default=None, max_length=80)


class AttentionDismissInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="general", max_length=40)


class AttentionRestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_key: str = Field(min_length=1, max_length=200)


@router.get("")
def get_home_summary(
    response: Response,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    response.headers["Cache-Control"] = "no-store"
    return home_dashboard.home_summary(db, tz_name=timezone)


@router.post("/goals")
def upsert_goal(
    body: GoalCreateInput,
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


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: str,
    body: GoalUpdateInput,
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


@router.delete("/goals/{goal_id}")
def delete_goal(
    goal_id: str,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    removed = home_dashboard.delete_goal(db, goal_id)
    if not removed:
        raise HTTPException(404, "Goal not found")
    return _mutation_result(db, timezone)


@router.post("/attention/dismiss")
def dismiss_attention(
    body: AttentionDismissInput,
    db: Session = Depends(get_db, scope="function"),
):
    # Skip home_summary: rebuilding Today scans every listing/job and stalls
    # dismiss on large inventories. The client patches from `dismissal`.
    try:
        dismissal = home_dashboard.dismiss_attention_item(
            db, item_key=body.item_key, kind=body.kind,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "dismissal": dismissal}


@router.post("/attention/restore")
def restore_attention(
    body: AttentionRestoreInput,
    db: Session = Depends(get_db, scope="function"),
):
    restored = home_dashboard.restore_attention_item(db, item_key=body.item_key)
    return {"ok": True, "restored": restored, "item_key": body.item_key}
