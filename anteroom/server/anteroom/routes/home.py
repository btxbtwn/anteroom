"""Home Command Center API: Today's Summary, Goals Tracker, and Needs Attention Inbox."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import home_dashboard

router = APIRouter(prefix="/api/home", tags=["home"])


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
    return {"ok": True, "goal_id": goal.id, "home": home_dashboard.home_summary(db, tz_name=timezone)}


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
    return {"ok": True, "goal_id": updated.id, "home": home_dashboard.home_summary(db, tz_name=timezone)}


@router.delete("/goals/{goal_id}")
def delete_goal(
    goal_id: str,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    removed = home_dashboard.delete_goal(db, goal_id)
    if not removed:
        raise HTTPException(404, "Goal not found")
    return {"ok": True, "home": home_dashboard.home_summary(db, tz_name=timezone)}


@router.post("/attention/dismiss")
def dismiss_attention(
    body: AttentionDismissInput,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    try:
        home_dashboard.dismiss_attention_item(db, item_key=body.item_key, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "home": home_dashboard.home_summary(db, tz_name=timezone)}


@router.post("/attention/restore")
def restore_attention(
    body: AttentionRestoreInput,
    timezone: str = Query("UTC", max_length=100),
    db: Session = Depends(get_db, scope="function"),
):
    home_dashboard.restore_attention_item(db, item_key=body.item_key)
    return {"ok": True, "home": home_dashboard.home_summary(db, tz_name=timezone)}
