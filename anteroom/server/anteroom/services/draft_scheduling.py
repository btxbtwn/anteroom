"""Seller opt-in scheduled draft publishing (OFF by default).

Approved by the seller on 2026-11-22 (see AGENTS.md Invariant):
- A validated draft marked "Ready to list" can be scheduled for a specific
  future UTC time on explicitly named marketplace(s), with ``confirm=True``
  recorded at schedule time and re-checked before execution.
- Editing a scheduled draft automatically unschedules it so stale unreviewed
  changes are never published.
- No code path publishes everywhere by default, on a retry, or without a
  recorded confirmation matching the current revision.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from anteroom.database import in_worker_session
from anteroom.models.automation import ScheduledPublishLog
from anteroom.models.conversation import Conversation, Photo, new_id
from anteroom.models.listing import Listing, ListingRevision
from anteroom.services.marketplace_delist import MARKETPLACES
from anteroom.services.validation import validate_listing

log = logging.getLogger("anteroom.draft_scheduling")

SCHEDULABLE_MARKETPLACES = MARKETPLACES
MAX_BULK_SCHEDULE = 100


class SchedulePublishError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_naive_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SchedulePublishError("Enter a valid ISO date and time to schedule publishing.") from exc
    else:
        parsed = value
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _normalize_marketplaces(marketplaces: list[str] | None) -> list[str]:
    if not marketplaces:
        raise SchedulePublishError("Choose at least one marketplace. Scheduled publishing never publishes everywhere by default.")
    seen: list[str] = []
    for raw in marketplaces:
        name = str(raw or "").strip().lower()
        if name not in SCHEDULABLE_MARKETPLACES:
            raise SchedulePublishError(f"Unsupported marketplace: {raw}")
        if name not in seen:
            seen.append(name)
    return seen


def _validate_ready(db: Session, conv_id: str) -> tuple[Listing, ListingRevision]:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise SchedulePublishError("Listing not found.", 404)
    if conv.status == "sold":
        raise SchedulePublishError("Sold items cannot be scheduled for publishing.", 409)
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or not listing.current_revision_id:
        raise SchedulePublishError("Save a complete draft before marking it Ready to list.")
    revision = db.get(ListingRevision, listing.current_revision_id)
    if revision is None or not isinstance(revision.listing_json, dict):
        raise SchedulePublishError("Save a complete draft before marking it Ready to list.")
    photo_count = db.query(Photo).filter(Photo.conversation_id == conv_id, Photo.marketplace.is_(None)).count()
    validation = validate_listing(revision.listing_json, photo_count, require_photos=True)
    if not validation.valid:
        first = validation.errors[0].get("message") if validation.errors and isinstance(validation.errors[0], dict) else "Fix validation errors first."
        raise SchedulePublishError(f"Draft is not valid yet: {first}")
    listing.validation_status = "valid"
    listing.validation_errors = validation.errors + validation.warnings
    return listing, revision


def schedule_view(listing: Listing | None, conv: Conversation | None = None) -> dict[str, Any]:
    if listing is None:
        return {
            "conversation_id": conv.id if conv else None,
            "title": conv.title if conv else None,
            "ready_to_list": False,
            "scheduled_publish_at": None,
            "scheduled_marketplaces": [],
            "scheduled_revision_id": None,
            "scheduled_confirmed_at": None,
            "scheduled_status": None,
            "scheduled_error": None,
        }
    return {
        "conversation_id": listing.conversation_id,
        "title": conv.title if conv else None,
        "ready_to_list": bool(listing.ready_to_list),
        "scheduled_publish_at": _iso(listing.scheduled_publish_at),
        "scheduled_marketplaces": list(listing.scheduled_marketplaces or []),
        "scheduled_revision_id": listing.scheduled_revision_id,
        "scheduled_confirmed_at": _iso(listing.scheduled_confirmed_at),
        "scheduled_status": listing.scheduled_status,
        "scheduled_error": listing.scheduled_error,
    }


def log_view(row: ScheduledPublishLog, title: str | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "title": title,
        "revision_id": row.revision_id,
        "marketplaces": list(row.marketplaces or []),
        "scheduled_for": _iso(row.scheduled_for),
        "status": row.status,
        "results": row.results,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
    }


def set_ready_to_list(db: Session, conv_id: str, ready: bool) -> dict[str, Any]:
    """Toggle Ready to list on a draft. Turning it off also clears any pending schedule."""
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise SchedulePublishError("Listing not found.", 404)
    if ready:
        listing, _revision = _validate_ready(db, conv_id)
        listing.ready_to_list = True
    else:
        listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
        if listing is None:
            return schedule_view(None, conv)
        if listing.scheduled_status == "scheduled":
            _clear_schedule(db, listing, reason="Unscheduled because Ready to list was turned off.")
        listing.ready_to_list = False
    db.commit()
    db.refresh(listing)
    return schedule_view(listing, conv)


def _clear_schedule(db: Session, listing: Listing, *, reason: str | None = None) -> bool:
    was_scheduled = listing.scheduled_status == "scheduled" or listing.scheduled_publish_at is not None
    if was_scheduled:
        db.add(ScheduledPublishLog(
            id=new_id(),
            conversation_id=listing.conversation_id,
            revision_id=listing.scheduled_revision_id or listing.current_revision_id,
            marketplaces=list(listing.scheduled_marketplaces or []),
            scheduled_for=listing.scheduled_publish_at or _now(),
            status="unscheduled",
            error=reason,
            created_at=_now(),
            finished_at=_now(),
        ))
    listing.scheduled_publish_at = None
    listing.scheduled_marketplaces = None
    listing.scheduled_revision_id = None
    listing.scheduled_confirmed_at = None
    listing.scheduled_status = None
    listing.scheduled_error = None
    return was_scheduled


def schedule_draft(
    db: Session,
    conv_id: str,
    *,
    publish_at: datetime | str,
    marketplaces: list[str],
    confirm: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Schedule a validated draft for future publishing to named marketplaces."""
    if confirm is not True:
        raise SchedulePublishError("Confirm the marketplaces and publish time to schedule this draft.")
    markets = _normalize_marketplaces(marketplaces)
    moment = _as_naive_utc(now) if now is not None else _now()
    when = _as_naive_utc(publish_at)
    if when <= moment:
        raise SchedulePublishError("Choose a future date and time to schedule publishing.")
    listing, revision = _validate_ready(db, conv_id)
    conv = db.get(Conversation, conv_id)
    listing.ready_to_list = True
    listing.scheduled_publish_at = when
    listing.scheduled_marketplaces = markets
    listing.scheduled_revision_id = revision.id
    listing.scheduled_confirmed_at = moment
    listing.scheduled_status = "scheduled"
    listing.scheduled_error = None
    db.add(ScheduledPublishLog(
        id=new_id(),
        conversation_id=conv_id,
        revision_id=revision.id,
        marketplaces=markets,
        scheduled_for=when,
        status="scheduled",
        created_at=moment,
    ))
    db.commit()
    db.refresh(listing)
    return schedule_view(listing, conv)


def unschedule_draft(db: Session, conv_id: str, *, reason: str | None = None) -> dict[str, Any]:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise SchedulePublishError("Listing not found.", 404)
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None:
        return schedule_view(None, conv)
    if listing.scheduled_status == "publishing":
        raise SchedulePublishError("This draft is currently publishing and cannot be unscheduled.", 409)
    _clear_schedule(db, listing, reason=reason or "Unscheduled by seller.")
    db.commit()
    db.refresh(listing)
    return schedule_view(listing, conv)


def unschedule_on_edit(db: Session, conv_id: str) -> bool:
    """Automatically unschedule a pending draft when edited so unreviewed changes never publish.

    Does not commit: participates in the caller's save transaction.
    """
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or listing.scheduled_status != "scheduled":
        return False
    return _clear_schedule(
        db,
        listing,
        reason="Draft was edited after scheduling; review your changes and reschedule before publishing.",
    )


def bulk_schedule(
    db: Session,
    conv_ids: list[str],
    *,
    publish_at: datetime | str,
    marketplaces: list[str],
    confirm: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    if confirm is not True:
        raise SchedulePublishError("Confirm the marketplaces and publish time to schedule drafts.")
    unique_ids = list(dict.fromkeys(str(item) for item in conv_ids if item))[:MAX_BULK_SCHEDULE]
    if not unique_ids:
        raise SchedulePublishError("Select at least one draft to schedule.")
    scheduled: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for conv_id in unique_ids:
        try:
            scheduled.append(schedule_draft(db, conv_id, publish_at=publish_at, marketplaces=marketplaces, confirm=True, now=now))
        except SchedulePublishError as exc:
            db.rollback()
            failed.append({"conversation_id": conv_id, "error": exc.detail})
    return {"scheduled": scheduled, "failed": failed}


def bulk_unschedule(db: Session, conv_ids: list[str], *, reason: str | None = None) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(str(item) for item in conv_ids if item))[:MAX_BULK_SCHEDULE]
    unscheduled: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for conv_id in unique_ids:
        try:
            unscheduled.append(unschedule_draft(db, conv_id, reason=reason))
        except SchedulePublishError as exc:
            db.rollback()
            failed.append({"conversation_id": conv_id, "error": exc.detail})
    return {"unscheduled": unscheduled, "failed": failed}


def list_scheduled_drafts(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(Listing, Conversation)
        .join(Conversation, Conversation.id == Listing.conversation_id)
        .filter(
            (Listing.ready_to_list.is_(True))
            | (Listing.scheduled_status.isnot(None))
            | (Listing.scheduled_publish_at.isnot(None))
        )
        .order_by(Listing.scheduled_publish_at.asc(), Listing.updated_at.desc())
        .all()
    )
    return [schedule_view(listing, conv) for listing, conv in rows]


def list_publish_logs(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(ScheduledPublishLog, Conversation.title)
        .outerjoin(Conversation, Conversation.id == ScheduledPublishLog.conversation_id)
        .order_by(ScheduledPublishLog.created_at.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [log_view(entry, title) for entry, title in rows]


def _claim_due_drafts(db: Session, moment: datetime) -> list[dict[str, Any]]:
    """Atomically claim due drafts whose confirmation and revision still match."""
    candidates = (
        db.query(Listing)
        .filter(
            Listing.ready_to_list.is_(True),
            Listing.scheduled_status == "scheduled",
            Listing.scheduled_confirmed_at.isnot(None),
            Listing.scheduled_publish_at.isnot(None),
            Listing.scheduled_publish_at <= moment,
        )
        .order_by(Listing.scheduled_publish_at.asc())
        .limit(20)
        .with_for_update(skip_locked=True)
        .all()
    )
    claimed: list[dict[str, Any]] = []
    for row in candidates:
        markets = [m for m in (row.scheduled_marketplaces or []) if m in SCHEDULABLE_MARKETPLACES]
        # Re-check confirm, revision match, and explicit marketplace list before execution.
        if not markets or not row.scheduled_confirmed_at or not row.scheduled_revision_id:
            _clear_schedule(db, row, reason="Scheduled publish refused: missing confirmation or marketplaces.")
            continue
        if row.current_revision_id != row.scheduled_revision_id:
            _clear_schedule(db, row, reason="Draft changed after scheduling; review and reschedule before publishing.")
            continue
        result = db.execute(
            update(Listing)
            .execution_options(synchronize_session=False)
            .where(
                Listing.conversation_id == row.conversation_id,
                Listing.scheduled_status == "scheduled",
                Listing.scheduled_revision_id == row.scheduled_revision_id,
                Listing.current_revision_id == row.scheduled_revision_id,
                Listing.scheduled_confirmed_at == row.scheduled_confirmed_at,
                Listing.scheduled_publish_at == row.scheduled_publish_at,
                Listing.ready_to_list.is_(True),
            )
            .values(scheduled_status="publishing", scheduled_error=None)
        )
        if getattr(result, "rowcount", 0) != 1:
            continue
        claimed.append({
            "conversation_id": row.conversation_id,
            "revision_id": row.scheduled_revision_id,
            "marketplaces": markets,
            "scheduled_for": row.scheduled_publish_at,
            "scheduled_confirmed_at": row.scheduled_confirmed_at,
        })
    db.commit()
    return claimed


def _settle_publish(db: Session, entry: dict[str, Any], outcome: dict[str, Any], moment: datetime) -> dict[str, Any]:
    ok = bool(outcome.get("ok"))
    error = None if ok else str(outcome.get("error") or "Scheduled publish did not confirm.")
    status = "published" if ok else "failed"
    values: dict[str, Any] = {
        "scheduled_status": status,
        "scheduled_error": error,
    }
    if ok:
        values["scheduled_publish_at"] = None
        values["scheduled_confirmed_at"] = None
    # Guard by the exact claimed schedule identity so a replacement schedule saved
    # while this publish was in flight is never overwritten.
    db.execute(
        update(Listing)
        .execution_options(synchronize_session=False)
        .where(
            Listing.conversation_id == entry["conversation_id"],
            Listing.scheduled_status == "publishing",
            Listing.scheduled_revision_id == entry["revision_id"],
            Listing.scheduled_publish_at == entry["scheduled_for"],
            Listing.scheduled_confirmed_at == entry.get("scheduled_confirmed_at"),
        )
        .values(**values)
    )
    audit = ScheduledPublishLog(
        id=new_id(),
        conversation_id=entry["conversation_id"],
        revision_id=entry["revision_id"],
        marketplaces=entry["marketplaces"],
        scheduled_for=entry["scheduled_for"],
        status=status,
        results=outcome,
        error=error,
        created_at=moment,
        finished_at=moment,
    )
    db.add(audit)
    db.commit()
    if not ok:
        log.error(
            "Scheduled publish failed for item %s on %s: %s",
            entry["conversation_id"],
            ", ".join(entry["marketplaces"]),
            error,
        )
    return {
        "conversation_id": entry["conversation_id"],
        "marketplaces": entry["marketplaces"],
        "ok": ok,
        "status": status,
        "error": error,
    }


async def run_due_scheduled_publishes(*, now: datetime | None = None, publisher=None) -> list[dict[str, Any]]:
    """Publish due drafts whose seller confirmation and revision are still valid.

    Releases DB connections before any external marketplace await.
    """
    moment = _as_naive_utc(now) if now is not None else _now()
    due = await in_worker_session(_claim_due_drafts, moment)
    if not due:
        return []
    if publisher is None:
        from anteroom.services.automation_actions import publish_revision

        publisher = publish_revision
    outcomes: list[dict[str, Any]] = []
    for entry in due:
        try:
            result = await publisher(entry["conversation_id"], entry["marketplaces"], entry["revision_id"])
        except Exception as exc:  # noqa: BLE001 - isolate one draft's publish failure
            result = {"ok": False, "error": str(exc) or "Scheduled publish failed."}
        settled = await in_worker_session(_settle_publish, entry, result, _now())
        outcomes.append(settled)
    return outcomes
