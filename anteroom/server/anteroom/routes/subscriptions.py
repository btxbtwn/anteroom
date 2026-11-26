"""Hosted seller subscription summary and operator plan-assignment routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from anteroom.routes.operator_usage import _require_operator
from anteroom.services import subscriptions
from anteroom.services.hosted_database import get_seller_db

router = APIRouter(prefix="/api/settings", tags=["settings"])
operator_router = APIRouter(prefix="/api/operator", tags=["operator"])


def _operator_db():
    """Optional session override for SQLite unit tests."""
    return None


@router.get("/subscription")
def get_subscription(request: Request, db=Depends(get_seller_db, scope="function")):
    """Current seller's plan, monthly Smart Credit usage, listed-item count, and tier catalog."""
    seller = getattr(request.state, "seller", None)
    email = getattr(seller, "email", None)
    return subscriptions.seller_subscription_summary(db, db.user_id, email=email)


class AssignPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(..., min_length=1, max_length=32)
    billing_interval: str = Field(default="monthly", pattern="^(monthly|annual)$")
    note: str | None = Field(default=None, max_length=256)


@operator_router.get("/plans")
def list_plans(
    request: Request,
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
):
    """List every assigned seller plan and monthly usage. Operator only."""
    _require_operator(request)
    return subscriptions.list_seller_plans(period=period, db=_operator_db())


@operator_router.put("/plans/{user_id}")
def assign_plan(user_id: str, body: AssignPlanRequest, request: Request):
    """Assign or update a seller's subscription tier. Operator only."""
    _require_operator(request)
    try:
        return subscriptions.set_seller_plan(
            user_id,
            body.plan_id,
            billing_interval=body.billing_interval,
            assigned_by="operator",
            note=body.note,
            db=_operator_db(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
