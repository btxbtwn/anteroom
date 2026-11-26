"""Operator-only export of managed OpenRouter and Photoroom usage per seller."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from anteroom.services import api_usage, listing_quality

router = APIRouter(prefix="/api/operator", tags=["operator"])


def _is_operator(request: Request) -> bool:
    operator = os.environ.get("ANTEROOM_OPERATOR_USER_ID")
    seller = getattr(request.state, "seller", None)
    return bool(operator and seller is not None and seller.user_id == operator)


def _require_operator(request: Request) -> None:
    if not _is_operator(request):
        raise HTTPException(status_code=403, detail="Operator access required")


def _parse_instant(value: str | None, *, default: datetime) -> datetime:
    if not value:
        return default
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Use an ISO-8601 timestamp for since/until") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _report_db():
    """Session or engine-backed loader; tests replace this."""
    return None


@router.get("/listing-quality")
def quality_report(request: Request, since: str | None = None, until: str | None = None):
    _require_operator(request)
    from anteroom.database import engine

    if engine.dialect.name != "postgresql":
        raise HTTPException(status_code=503, detail="The cloud database is not configured")
    now = datetime.now(UTC)
    start = _parse_instant(since, default=now - timedelta(days=30))
    end = _parse_instant(until, default=now)
    if end <= start:
        raise HTTPException(status_code=422, detail="until must be after since")
    return listing_quality.operator_report(start, end)


@router.get("/usage")
def usage_report(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    seller_id: str | None = Query(default=None, max_length=36),
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    """Per-seller managed API spend for beta invoicing. Operator only."""
    _require_operator(request)
    now = datetime.now(UTC)
    start = _parse_instant(since, default=now - timedelta(days=30))
    end = _parse_instant(until, default=now)
    if end <= start:
        raise HTTPException(status_code=422, detail="until must be after since")
    db = _report_db()
    if db is not None:
        report = api_usage.operator_report(db, since=start, until=end, seller_id=seller_id or None)
    else:
        report = api_usage.load_operator_report(since=start, until=end, seller_id=seller_id or None)
    if format == "csv":
        return PlainTextResponse(
            api_usage.report_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="anteroom-api-usage.csv"'},
        )
    return report
