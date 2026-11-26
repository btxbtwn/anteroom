"""Record and report managed OpenRouter / Photoroom spend per seller.

Hosted beta accounts share Anteroom's provider keys. Each successful managed
call writes one row so the operator can invoice the exact API cost. Seller-
supplied keys are never recorded: those bills land on the seller's own accounts.
Recording is best-effort and never raises into listing or photo flows.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anteroom.models.api_usage import ApiUsageEvent
from anteroom.models.ownership import LOCAL_USER_ID

log = logging.getLogger("anteroom.api_usage")

PHOTOROOM_COST_ENV = "ANTEROOM_PHOTOROOM_COST_USD"
DEFAULT_PHOTOROOM_COST_USD = 0.10


def photoroom_cost_usd() -> float:
    raw = os.environ.get(PHOTOROOM_COST_ENV, "").strip()
    if not raw:
        return DEFAULT_PHOTOROOM_COST_USD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PHOTOROOM_COST_USD
    if not math.isfinite(value) or value < 0:
        return DEFAULT_PHOTOROOM_COST_USD
    return value


def _seller_id() -> str | None:
    from anteroom.services.service_context import current

    try:
        context = current()
    except RuntimeError:
        return None
    if context is None or context.seller_id == LOCAL_USER_ID:
        return None
    return context.seller_id


def _finite_cost(value: float | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _write(event: ApiUsageEvent) -> None:
    from anteroom.database import SessionLocal

    db = SessionLocal()
    try:
        if getattr(db, "user_id", None) is None and event.user_id:
            # Desktop SessionLocal has no owner stamp; keep the seller id we set.
            pass
        db.add(event)
        db.commit()
    except Exception:
        log.exception("could not record %s usage for seller %s", event.provider, event.user_id)
        db.rollback()
    finally:
        db.close()


def record_openrouter(
    *,
    cost_usd: float | None,
    model: str,
    kind: str = "chat",
    provider_request_id: str | None = None,
    conversation_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Persist one managed OpenRouter charge when the provider reported a cost."""
    from anteroom.services.hosted_provider import managed_ai_enabled

    if not managed_ai_enabled():
        return
    amount = _finite_cost(cost_usd)
    if amount is None:
        return
    seller = _seller_id()
    if seller is None:
        return
    _write(ApiUsageEvent(
        user_id=seller,
        provider="openrouter",
        kind=(kind or "chat")[:32],
        model_or_edit=(model or "")[:256],
        cost_usd=amount,
        cost_source="provider_reported",
        units=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        conversation_id=conversation_id,
        provider_request_id=(provider_request_id or None) and str(provider_request_id)[:128],
    ))


def record_photoroom(*, edit_label: str, basic: bool, conversation_id: str | None = None) -> None:
    """Record one managed call. A Basic removal uses one fifth of a Plus image credit."""
    from anteroom.services.photoroom import managed_photoroom_enabled

    if not managed_photoroom_enabled():
        return
    seller = _seller_id()
    if seller is None:
        return
    _write(ApiUsageEvent(
        user_id=seller,
        provider="photoroom",
        kind="edit",
        model_or_edit=(edit_label or "")[:256],
        cost_usd=photoroom_cost_usd() / (5 if basic else 1),
        cost_source="rate_card",
        units=1,
        conversation_id=conversation_id,
    ))


def note_openrouter_call(record: dict, *, provider_request_id: str | None = None) -> None:
    """Record from a finished ``model_usage`` call dict when cost is present."""
    if not isinstance(record, dict) or record.get("provider") != "openrouter":
        return
    if record.get("outcome") in {"failed", "cancelled"}:
        return
    record_openrouter(
        cost_usd=record.get("cost_usd"),
        model=str(record.get("model") or ""),
        kind="chat",
        provider_request_id=provider_request_id,
        input_tokens=record.get("input_tokens"),
        output_tokens=record.get("output_tokens"),
    )


def list_events(db: Session, *, since: datetime, until: datetime, seller_id: str | None = None) -> list[ApiUsageEvent]:
    query = select(ApiUsageEvent).where(
        ApiUsageEvent.created_at >= since,
        ApiUsageEvent.created_at < until,
    )
    if seller_id:
        query = query.where(ApiUsageEvent.user_id == seller_id)
    query = query.order_by(ApiUsageEvent.created_at.asc(), ApiUsageEvent.id.asc())
    return list(db.scalars(query).all())


USAGE_PERIOD_PATTERN = r"^(30d|20\d{2}-(0[1-9]|1[0-2]))$"
USAGE_MONTHS_LISTED = 12
_SERVICES = ("openrouter", "photoroom")


def usage_period(period: str, now: datetime) -> tuple[datetime, datetime]:
    """UTC bounds, end exclusive, of ``30d`` (today and the 29 days before it) or a ``YYYY-MM`` month."""
    if period == "30d":
        today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return today - timedelta(days=29), today + timedelta(days=1)
    year, month = (int(part) for part in period.split("-"))
    start = datetime(year, month, 1, tzinfo=UTC)
    return start, datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)


def _months_with_usage(first: datetime | None, now: datetime) -> list[str]:
    """Months a seller can pick, newest first: this month back to their first charge."""
    months = []
    year, month = now.year, now.month
    oldest = min((first.year, first.month), (year, month)) if first else (year, month)
    while (year, month) >= oldest and len(months) < USAGE_MONTHS_LISTED:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return months


def seller_usage_summary(db: Session, seller_id: str, period: str = "30d") -> dict[str, Any]:
    """The signed-in seller's own recorded costs for one period: totals, what they went on, and each UTC day."""
    now = datetime.now(UTC)
    start, end = usage_period(period, now)
    mine = (
        ApiUsageEvent.user_id == seller_id,
        ApiUsageEvent.provider.in_(_SERVICES),
    )
    in_period = (*mine, ApiUsageEvent.created_at >= start, ApiUsageEvent.created_at < end)
    cost = func.sum(ApiUsageEvent.cost_usd)

    services: dict[str, dict[str, Any]] = {
        provider: {"count": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0, "items": []}
        for provider in _SERVICES
    }
    by_item = (
        select(ApiUsageEvent.provider, ApiUsageEvent.model_or_edit, func.count(), cost,
               func.sum(ApiUsageEvent.input_tokens), func.sum(ApiUsageEvent.output_tokens))
        .where(*in_period)
        .group_by(ApiUsageEvent.provider, ApiUsageEvent.model_or_edit)
    )
    for provider, label, count, usd, tokens_in, tokens_out in db.execute(by_item):
        service = services[provider]
        service["count"] += count
        service["usd"] += float(usd)
        service["input_tokens"] += tokens_in or 0
        service["output_tokens"] += tokens_out or 0
        service["items"].append({
            "label": label, "count": count, "usd": round(float(usd), 6),
            "input_tokens": tokens_in or 0, "output_tokens": tokens_out or 0,
        })
    for service in services.values():
        service["usd"] = round(service["usd"], 6)
        service["items"].sort(key=lambda item: (-item["usd"], item["label"]))

    day = func.date(ApiUsageEvent.created_at)
    spent = {
        (str(on), provider): float(usd)
        for on, provider, usd in db.execute(
            select(day, ApiUsageEvent.provider, cost).where(*in_period).group_by(day, ApiUsageEvent.provider))
    }
    days = []
    for offset in range((end - start).days):
        on = (start + timedelta(days=offset)).date().isoformat()
        days.append({"date": on, **{provider: round(spent.get((on, provider), 0.0), 6) for provider in _SERVICES}})

    first = db.scalar(select(func.min(ApiUsageEvent.created_at)).where(*mine))
    return {
        "period": {"id": period, "start": start.isoformat(), "end": end.isoformat()},
        "months": _months_with_usage(first, now),
        "total_usd": round(sum(service["usd"] for service in services.values()), 6),
        **services,
        "days": days,
    }


def seller_usage_csv(db: Session, seller_id: str, period: str = "30d") -> str:
    """Every charge the signed-in seller ran up in one period, oldest first."""
    from anteroom.services import ledger_exports

    start, end = usage_period(period, datetime.now(UTC))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["time_utc", "service", "item", "input_tokens", "output_tokens", "cost_usd"])
    for event in list_events(db, since=start, until=end, seller_id=seller_id):
        writer.writerow([
            f"{event.created_at:%Y-%m-%d %H:%M:%S}", event.provider, ledger_exports.text(event.model_or_edit),
            event.input_tokens, event.output_tokens, event.cost_usd,
        ])
    return out.getvalue()


def operator_report(
    db: Session,
    *,
    since: datetime,
    until: datetime,
    seller_id: str | None = None,
) -> dict[str, Any]:
    """Per-seller totals for the operator invoice window."""
    events = list_events(db, since=since, until=until, seller_id=seller_id)
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "seller_id": "",
        "openrouter_usd": 0.0,
        "photoroom_usd": 0.0,
        "total_usd": 0.0,
        "openrouter_calls": 0,
        "photoroom_calls": 0,
    })
    for event in events:
        row = buckets[event.user_id]
        row["seller_id"] = event.user_id
        if event.provider == "openrouter":
            row["openrouter_usd"] += float(event.cost_usd)
            row["openrouter_calls"] += 1
        elif event.provider == "photoroom":
            row["photoroom_usd"] += float(event.cost_usd)
            row["photoroom_calls"] += 1
        row["total_usd"] = row["openrouter_usd"] + row["photoroom_usd"]
    sellers = sorted(buckets.values(), key=lambda row: row["seller_id"])
    for row in sellers:
        for key in ("openrouter_usd", "photoroom_usd", "total_usd"):
            row[key] = round(row[key], 6)
    total = round(sum(row["total_usd"] for row in sellers), 6)
    return {
        "since": since.astimezone(UTC).isoformat(),
        "until": until.astimezone(UTC).isoformat(),
        "sellers": sellers,
        "total_usd": total,
        "event_count": len(events),
        "events": [_event_dict(event) for event in events],
    }


def _event_dict(event: ApiUsageEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "seller_id": event.user_id,
        "provider": event.provider,
        "kind": event.kind,
        "model_or_edit": event.model_or_edit,
        "cost_usd": event.cost_usd,
        "cost_source": event.cost_source,
        "units": event.units,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "conversation_id": event.conversation_id,
        "provider_request_id": event.provider_request_id,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def report_csv(report: dict[str, Any]) -> str:
    """One CSV of per-seller totals, then a detail section of every event."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["section", "seller_id", "openrouter_usd", "photoroom_usd", "total_usd",
                     "openrouter_calls", "photoroom_calls"])
    for row in report["sellers"]:
        writer.writerow([
            "seller", row["seller_id"], row["openrouter_usd"], row["photoroom_usd"], row["total_usd"],
            row["openrouter_calls"], row["photoroom_calls"],
        ])
    writer.writerow([])
    writer.writerow(["section", "seller_id", "provider", "kind", "model_or_edit", "cost_usd",
                     "cost_source", "units", "input_tokens", "output_tokens", "created_at",
                     "provider_request_id", "conversation_id"])
    for event in report["events"]:
        writer.writerow([
            "event", event["seller_id"], event["provider"], event["kind"], event["model_or_edit"],
            event["cost_usd"], event["cost_source"], event["units"], event["input_tokens"],
            event["output_tokens"], event["created_at"], event["provider_request_id"],
            event["conversation_id"],
        ])
    return out.getvalue()


def _report_from_rows(rows: list[dict[str, Any]], *, since: datetime, until: datetime) -> dict[str, Any]:
    """Build the operator report from plain event dicts (Postgres definer path)."""
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "seller_id": "",
        "openrouter_usd": 0.0,
        "photoroom_usd": 0.0,
        "total_usd": 0.0,
        "openrouter_calls": 0,
        "photoroom_calls": 0,
    })
    events: list[dict[str, Any]] = []
    for row in rows:
        seller = row["seller_id"]
        bucket = buckets[seller]
        bucket["seller_id"] = seller
        cost = float(row["cost_usd"])
        if row["provider"] == "openrouter":
            bucket["openrouter_usd"] += cost
            bucket["openrouter_calls"] += 1
        elif row["provider"] == "photoroom":
            bucket["photoroom_usd"] += cost
            bucket["photoroom_calls"] += 1
        bucket["total_usd"] = bucket["openrouter_usd"] + bucket["photoroom_usd"]
        events.append(row)
    sellers = sorted(buckets.values(), key=lambda item: item["seller_id"])
    for item in sellers:
        for key in ("openrouter_usd", "photoroom_usd", "total_usd"):
            item[key] = round(item[key], 6)
    return {
        "since": since.astimezone(UTC).isoformat(),
        "until": until.astimezone(UTC).isoformat(),
        "sellers": sellers,
        "total_usd": round(sum(item["total_usd"] for item in sellers), 6),
        "event_count": len(events),
        "events": events,
    }


def _postgres_operator_rows(engine, *, since: datetime, until: datetime, seller_id: str | None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT id, user_id AS seller_id, provider, kind, model_or_edit, cost_usd, cost_source, "
                "units, input_tokens, output_tokens, conversation_id, provider_request_id, created_at "
                "FROM anteroom_private.api_usage_events(:since, :until, :seller)"
            ),
            {"since": since, "until": until, "seller": seller_id},
        )
        rows = []
        for mapping in result.mappings():
            created = mapping["created_at"]
            rows.append({
                "id": mapping["id"],
                "seller_id": mapping["seller_id"],
                "provider": mapping["provider"],
                "kind": mapping["kind"],
                "model_or_edit": mapping["model_or_edit"],
                "cost_usd": float(mapping["cost_usd"]),
                "cost_source": mapping["cost_source"],
                "units": mapping["units"],
                "input_tokens": mapping["input_tokens"],
                "output_tokens": mapping["output_tokens"],
                "conversation_id": mapping["conversation_id"],
                "provider_request_id": mapping["provider_request_id"],
                "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            })
        return rows


def load_operator_report(*, since: datetime, until: datetime, seller_id: str | None = None) -> dict[str, Any]:
    """Cross-seller report for the operator route (bypasses seller-scoped SessionLocal)."""
    from anteroom.database import engine, _local_sessions

    if engine.dialect.name == "postgresql":
        return _report_from_rows(
            _postgres_operator_rows(engine, since=since, until=until, seller_id=seller_id),
            since=since, until=until,
        )
    db = _local_sessions()
    try:
        return operator_report(db, since=since, until=until, seller_id=seller_id)
    finally:
        db.close()
