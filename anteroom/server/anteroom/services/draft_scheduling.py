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

import asyncio
import contextlib
import hashlib
import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from anteroom.database import in_worker_session
from anteroom.models.automation import ScheduledPublishLog
from anteroom.models.conversation import Conversation, Photo, new_id
from anteroom.models.job import ACTIVE_JOB_STATUSES, Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_video import ListingVideo
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from anteroom.services import process_handoff
from anteroom.services.marketplace_delist import MARKETPLACES
from anteroom.services.validation import validate_listing
from anteroom.services.vendoo_import import parse_notes

log = logging.getLogger("anteroom.draft_scheduling")

SCHEDULABLE_MARKETPLACES = MARKETPLACES
MAX_BULK_SCHEDULE = 100
ABANDONED_PUBLISH_GRACE = timedelta(minutes=15)
LIVE_PUBLISH_STATES = frozenset({"published", "pending_review"})
_active_claim: ContextVar[dict[str, Any] | None] = ContextVar("scheduled_publish_claim", default=None)


class SchedulePublishError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def scheduled_requester() -> str | None:
    """The MCP agent that scheduled the publish running now (``requested_by``), if one did."""
    return (_active_claim.get() or {}).get("requested_by")


def scheduled_ebay_ad_rate() -> str | None:
    return (_active_claim.get() or {}).get("confirmed_ebay_ad_rate")


def _confirmed_ebay_rate(snapshot: dict, markets: list[str], rate: str | None) -> str | None:
    if "ebay" not in markets:
        return None
    from anteroom.services.ebay_listing_promotion import confirmed_selection
    from anteroom.services.listing_result import ListingProblem

    try:
        selected = confirmed_selection(snapshot, rate)
    except ListingProblem as exc:
        raise SchedulePublishError(str(exc), 409) from exc
    return format(selected[1].normalize(), "f") if selected else None


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


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _revision_quantity(revision: ListingRevision) -> int | None:
    from anteroom.services.ebay_variations import total_quantity

    if not isinstance(revision.listing_json, dict):
        return None
    var_total = total_quantity(revision.listing_json)
    if isinstance(var_total, int) and var_total >= 0:
        return var_total
    raw = revision.listing_json.get("quantity")
    if type(raw) is int and raw >= 0:
        return raw
    return None


def _quantity_baseline(db: Session, conv_id: str) -> tuple[int | None, datetime | None, bool]:
    """Return ``(available_qty, baseline_at, from_quantity_sync)`` for the current listing.

    Walks past private ``native_schema`` saves and non-quantity edits to the revision
    that established the current quantity (or a ``sale_quantity_sync`` revision).
    For an initial revision (``parent_revision_id is None``), ``baseline_at`` is ``None``
    so all paid sales count against that starting quantity. For a subsequent
    quantity-changing revision (seller restock or ``sale_quantity_sync``), ``baseline_at``
    is that revision's creation time because its quantity already reflects prior sales.
    """
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or not listing.current_revision_id:
        return None, None, False
    revision = db.get(ListingRevision, listing.current_revision_id)
    seen: set[str] = set()
    while (
        revision is not None
        and revision.source == "native_schema"
        and revision.parent_revision_id
        and revision.parent_revision_id not in seen
    ):
        seen.add(revision.id)
        parent = db.get(ListingRevision, revision.parent_revision_id)
        if parent is None:
            break
        revision = parent
    if revision is None or not isinstance(revision.listing_json, dict):
        return None, None, False
    qty = _revision_quantity(revision)
    effective_qty = qty if qty is not None else 1
    while (
        revision.source != "sale_quantity_sync"
        and revision.parent_revision_id
        and revision.parent_revision_id not in seen
    ):
        parent = db.get(ListingRevision, revision.parent_revision_id)
        if parent is None:
            break
        parent_qty = _revision_quantity(parent)
        if (parent_qty if parent_qty is not None else 1) != effective_qty:
            break
        seen.add(revision.id)
        revision = parent
    baseline_at = _as_utc(revision.created_at) if revision.parent_revision_id is not None else None
    return qty, baseline_at, revision.source == "sale_quantity_sync"


def _sale_timestamp(sale: MarketplaceSale) -> datetime | None:
    stamps = [t for t in (_as_utc(sale.received_at), _as_utc(sale.order_observed_at)) if t is not None]
    return max(stamps) if stamps else None


def _is_sold_or_has_sale(db: Session, conv: Conversation | None) -> bool:
    """Return True if the item is marked sold or its stock is exhausted by sales.

    Multi-quantity items with stock still remaining stay schedulable/publishable:
    - A marketplace stock observation of ``remaining > 0`` suppresses the cumulative
      check only when it is at or after its own marketplace's paid sales and either
      no other marketplace has paid sales, or a ``sale_quantity_sync`` revision made
      after those sales already pushed them into that marketplace's quantity.
    - Otherwise, paid sales occurring at or after the current quantity revision's
      baseline (or all paid sales for an initial revision) are compared against the
      revision's declared available quantity so neither cross-marketplace sales nor
      seller restocks / ``sale_quantity_sync`` updates are misclassified.
    """
    if conv is None:
        return False
    if conv.status == "sold":
        return True
    marks = parse_notes(conv.notes).get("marketplaceMarks")
    if isinstance(marks, dict) and any(
        isinstance(entry, dict) and entry.get("state") == "sold"
        for entry in marks.values()
    ):
        return True
    declared_qty, baseline_at, from_quantity_sync = _quantity_baseline(db, conv.id)
    if declared_qty == 0:
        return True
    sales = (
        db.query(MarketplaceSale)
        .filter(
            MarketplaceSale.conversation_id == conv.id,
            MarketplaceSale.order_status.in_((*PAID_STATUSES, "cancellation_pending")),
        )
        .all()
    )
    if not sales:
        return False

    from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
    from anteroom.services.sale_inventory import stock_after_sale

    paid_sales = [sale for sale in sales if sale.order_status in PAID_STATUSES]
    ml_repo = MarketplaceListingRepo(db)

    checked_pairs: set[tuple[str, str | None]] = set()
    has_fresh_remaining = False
    for sale in paid_sales:
        pair = (sale.marketplace, sale.listing_id)
        if pair in checked_pairs:
            continue
        checked_pairs.add(pair)
        stock = stock_after_sale(db, conv.id, sale.marketplace, sale.listing_id)
        record = ml_repo.get(conv.id, sale.marketplace) or {}
        observed = record.get("inventory_observation") or {}
        obs_matches = isinstance(observed, dict) and observed.get("listing_id") == sale.listing_id
        obs_checked = _as_utc(observed.get("checked_at")) if obs_matches else None

        if stock["status"] == "sold_out":
            if obs_checked is not None:
                if baseline_at is None or obs_checked >= baseline_at:
                    return True
            elif baseline_at is None and (declared_qty is None or declared_qty <= 1):
                return True
        if stock["status"] == "remaining" and isinstance(stock.get("remaining"), int) and stock["remaining"] > 0:
            if obs_checked is not None:
                other_sales = [s for s in paid_sales if s.marketplace != sale.marketplace]
                other_stamps = [t for s in other_sales if (t := _sale_timestamp(s)) is not None]
                same_stamps = [
                    t for s in paid_sales if s.marketplace == sale.marketplace and (t := _sale_timestamp(s)) is not None
                ]
                same_ok = not same_stamps or obs_checked >= max(same_stamps)
                # One marketplace's stock only reflects other marketplaces' sales once a
                # quantity sync (Auto-adjust quantity) has run after them; otherwise the
                # cumulative check below decides.
                other_ok = not other_sales or (
                    from_quantity_sync
                    and baseline_at is not None
                    and len(other_stamps) == len(other_sales)
                    and max(other_stamps) <= baseline_at
                    and obs_checked > max(other_stamps)
                )
                if same_ok and other_ok:
                    has_fresh_remaining = True

    if has_fresh_remaining:
        return False

    unaccounted_paid = sum(
        sale.quantity if type(sale.quantity) is int and sale.quantity > 0 else 1
        for sale in paid_sales
        if baseline_at is None or (sale_ts := _sale_timestamp(sale)) is None or sale_ts >= baseline_at
    )
    if declared_qty is None:
        return True
    if declared_qty <= 1:
        unaccounted_blocking = sum(
            sale.quantity if type(sale.quantity) is int and sale.quantity > 0 else 1
            for sale in sales
            if baseline_at is None or (sale_ts := _sale_timestamp(sale)) is None or sale_ts >= baseline_at
        )
        return unaccounted_blocking >= 1
    return unaccounted_paid >= declared_qty


is_sold_or_has_sale = _is_sold_or_has_sale


def _schedule_content_digest(
    db: Session,
    conv_id: str,
    markets: list[str],
    *,
    rules: dict | None = None,
    use_given_rules: bool = False,
    photos: list[Photo] | None = None,
    videos: list[ListingVideo] | None = None,
) -> tuple[str, dict | None]:
    """Hash photos, videos, and active pricing rules so unreviewed changes never publish."""
    from anteroom.services.pricing_rules import active_rules

    current_rules = rules if use_given_rules else active_rules()
    if photos is None:
        photos = _schedule_photos(db, conv_id)
    if videos is None:
        videos = _schedule_videos(db, conv_id)
    payload = {
        "markets": sorted(markets),
        "photos": [
            (p.id, p.marketplace, p.display_order, p.stored_filename, p.checksum, p.size_bytes, p.edits)
            for p in photos
        ],
        "videos": [
            (v.id, v.checksum, v.stored_filename)
            for v in videos
        ],
        "pricing_rules": current_rules,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return digest, current_rules


def _schedule_photos(db: Session, conv_id: str) -> list[Photo]:
    return (
        db.query(Photo).filter(Photo.conversation_id == conv_id)
        .order_by(Photo.display_order, Photo.created_at, Photo.id).all()
    )


def _schedule_videos(db: Session, conv_id: str) -> list[ListingVideo]:
    return (
        db.query(ListingVideo).filter(ListingVideo.conversation_id == conv_id)
        .order_by(ListingVideo.id).all()
    )


def verified_scheduled_media(
    db: Session, conv_id: str, marketplace: str,
) -> tuple[list[Photo], list[ListingVideo]] | None:
    """Verify the exact media rows native preflight will use for this claim."""
    entry = _active_claim.get()
    if entry is None or entry["conversation_id"] != conv_id:
        return None
    reason = "Scheduled draft changed after confirmation; review and reschedule before publishing."
    # Serialize verification with settlement and schedule changes for this draft.
    listing = db.get(Listing, conv_id, with_for_update=True, populate_existing=True)
    if (
        listing is None or listing.scheduled_status != "publishing"
        or listing.scheduled_revision_id != entry["revision_id"]
        or listing.current_revision_id != entry["revision_id"]
        or listing.scheduled_publish_at != entry["scheduled_for"]
        or listing.scheduled_confirmed_at != entry["scheduled_confirmed_at"]
        or listing.scheduled_ebay_ad_rate != entry.get("confirmed_ebay_ad_rate")
        or marketplace not in entry["marketplaces"]
    ):
        raise SchedulePublishError(reason, 409)
    photos, videos = _schedule_photos(db, conv_id), _schedule_videos(db, conv_id)
    digest, _ = _schedule_content_digest(
        db, conv_id, entry["marketplaces"], rules=entry["pricing_rules"], use_given_rules=True,
        photos=photos, videos=videos,
    )
    if digest != entry["content_digest"] or digest != listing.scheduled_content_digest:
        _clear_schedule(db, listing, reason=reason)
        db.commit()
        raise SchedulePublishError(reason, 409)
    return photos, videos


def _validate_ready(
    db: Session,
    conv_id: str,
    *,
    marketplaces: list[str] | None = None,
) -> tuple[Listing, ListingRevision]:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise SchedulePublishError("Listing not found.", 404)
    if _is_sold_or_has_sale(db, conv):
        raise SchedulePublishError("Sold items cannot be scheduled for publishing.", 409)
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or not listing.current_revision_id:
        raise SchedulePublishError("Save a complete draft before marking it Ready to list.")
    revision = db.get(ListingRevision, listing.current_revision_id)
    if revision is None or not isinstance(revision.listing_json, dict):
        raise SchedulePublishError("Save a complete draft before marking it Ready to list.")
    photo_count = db.query(Photo).filter(Photo.conversation_id == conv_id, Photo.marketplace.is_(None)).count()
    validation = validate_listing(
        revision.listing_json,
        photo_count,
        require_photos=True,
        selected_marketplaces=marketplaces,
    )
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
        listing = db.query(Listing).filter(Listing.conversation_id == conv_id).with_for_update().first()
        if listing is None:
            return schedule_view(None, conv)
        if listing.scheduled_status == "publishing":
            raise SchedulePublishError("This draft is currently publishing and cannot be turned off yet.", 409)
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
    listing.scheduled_content_digest = None
    listing.scheduled_requested_by = None
    listing.scheduled_ebay_ad_rate = None
    return was_scheduled


def schedule_draft(
    db: Session,
    conv_id: str,
    *,
    publish_at: datetime | str,
    marketplaces: list[str],
    confirm: bool,
    now: datetime | None = None,
    requested_by: str | None = None,
    confirmed_ebay_ad_rate: str | None = None,
) -> dict[str, Any]:
    """Schedule a validated draft for future publishing to named marketplaces.

    ``requested_by`` names the agent the seller allowed to schedule it; the log keeps it.
    """
    if confirm is not True:
        raise SchedulePublishError("Confirm the marketplaces and publish time to schedule this draft.")
    markets = _normalize_marketplaces(marketplaces)
    moment = _as_naive_utc(now) if now is not None else _now()
    when = _as_naive_utc(publish_at)
    if when <= moment:
        raise SchedulePublishError("Choose a future date and time to schedule publishing.")
    listing, revision = _validate_ready(db, conv_id, marketplaces=markets)
    rate = _confirmed_ebay_rate(revision.listing_json, markets, confirmed_ebay_ad_rate)
    if listing.scheduled_status == "publishing":
        raise SchedulePublishError("This draft is currently publishing and cannot be rescheduled yet.", 409)
    conv = db.get(Conversation, conv_id)
    digest, _rules = _schedule_content_digest(db, conv_id, markets)
    listing.ready_to_list = True
    listing.scheduled_publish_at = when
    listing.scheduled_marketplaces = markets
    listing.scheduled_revision_id = revision.id
    listing.scheduled_confirmed_at = moment
    listing.scheduled_status = "scheduled"
    listing.scheduled_error = None
    listing.scheduled_content_digest = digest
    listing.scheduled_requested_by = requested_by
    listing.scheduled_ebay_ad_rate = rate
    db.add(ScheduledPublishLog(
        id=new_id(),
        conversation_id=conv_id,
        revision_id=revision.id,
        marketplaces=markets,
        scheduled_for=when,
        status="scheduled",
        results={key: value for key, value in {
            "requested_by": requested_by, "confirmed_ebay_ad_rate": rate,
        }.items() if value is not None} or None,
        created_at=moment,
    ))
    db.commit()
    db.refresh(listing)
    return schedule_view(listing, conv)


def unschedule_draft(db: Session, conv_id: str, *, reason: str | None = None) -> dict[str, Any]:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise SchedulePublishError("Listing not found.", 404)
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).with_for_update().first()
    if listing is None:
        return schedule_view(None, conv)
    if listing.scheduled_status == "publishing":
        raise SchedulePublishError("This draft is currently publishing and cannot be unscheduled.", 409)
    _clear_schedule(db, listing, reason=reason or "Unscheduled by seller.")
    db.commit()
    db.refresh(listing)
    return schedule_view(listing, conv)


def unschedule_on_edit(db: Session, conv_id: str, *, reason: str | None = None) -> bool:
    """Automatically unschedule a pending draft when edited so unreviewed changes never publish.

    Does not commit: participates in the caller's save transaction.
    """
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or listing.scheduled_status != "scheduled":
        return False
    return _clear_schedule(
        db,
        listing,
        reason=reason or "Draft was edited after scheduling; review your changes and reschedule before publishing.",
    )


def unschedule_on_sale(db: Session, conv_id: str | None) -> bool:
    """Automatically unschedule a pending draft when the item sells out or is marked sold.

    Acquires a row lock on the Conversation so concurrent native list/save_draft job
    creation serializes with sale recording and observes the committed sale before
    opening a job. Multi-quantity items with stock still remaining stay scheduled.
    Does not commit: participates in the caller's save transaction.
    """
    if not conv_id:
        return False
    conv = db.get(Conversation, conv_id, with_for_update=True, populate_existing=True)
    if conv is None:
        return False
    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if listing is None or listing.scheduled_status != "scheduled":
        return False
    if not _is_sold_or_has_sale(db, conv):
        return False
    return _clear_schedule(
        db,
        listing,
        reason="Scheduled publish cancelled because this item already sold.",
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


def has_due_or_abandoned_publishes(db: Session, moment: datetime) -> bool:
    """Return True when any scheduled draft is due or an abandoned publishing claim needs recovery."""
    cutoff = _as_naive_utc(moment)
    abandoned_cutoff = cutoff - ABANDONED_PUBLISH_GRACE
    return (
        db.query(Listing.conversation_id)
        .filter(
            (
                (Listing.ready_to_list.is_(True))
                & (Listing.scheduled_status == "scheduled")
                & (Listing.scheduled_publish_at.isnot(None))
                & (Listing.scheduled_publish_at <= cutoff)
                & (Listing.scheduled_confirmed_at.isnot(None))
            )
            | (
                (Listing.scheduled_status == "publishing")
                & (Listing.updated_at.isnot(None))
                & (Listing.updated_at <= abandoned_cutoff)
            )
        )
        .first()
        is not None
    )


def _claim_due_drafts(db: Session, moment: datetime) -> list[dict[str, Any]]:
    """Atomically claim due drafts whose confirmation, revision, sold state, and content digest still match."""
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
        # Re-check confirm, revision match, sold state, content digest, and explicit marketplace list before execution.
        if not markets or not row.scheduled_confirmed_at or not row.scheduled_revision_id:
            _clear_schedule(db, row, reason="Scheduled publish refused: missing confirmation or marketplaces.")
            continue
        conv = db.get(Conversation, row.conversation_id)
        if conv is None:
            _clear_schedule(db, row, reason="Scheduled publish refused: listing not found.")
            continue
        if _is_sold_or_has_sale(db, conv):
            _clear_schedule(db, row, reason="Scheduled publish cancelled because this item already sold.")
            continue
        if row.current_revision_id != row.scheduled_revision_id:
            _clear_schedule(db, row, reason="Draft changed after scheduling; review and reschedule before publishing.")
            continue
        if "ebay" in markets:
            revision = db.get(ListingRevision, row.scheduled_revision_id)
            if revision is None:
                _clear_schedule(db, row, reason="Scheduled draft is missing; review and reschedule before publishing.")
                continue
            try:
                _confirmed_ebay_rate(revision.listing_json, markets, row.scheduled_ebay_ad_rate)
            except SchedulePublishError as exc:
                _clear_schedule(db, row, reason=f"Scheduled publish refused: {exc.detail}")
                continue
        if not row.scheduled_content_digest:
            _clear_schedule(db, row, reason="Schedule has no verified content; review and reschedule before publishing.")
            continue
        digest, rules = _schedule_content_digest(db, row.conversation_id, markets)
        if row.scheduled_content_digest != digest:
            _clear_schedule(
                db,
                row,
                reason="Photos, video, or pricing rules changed after scheduling; review and reschedule before publishing.",
            )
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
            .values(scheduled_status="publishing", scheduled_error=None, updated_at=moment)
        )
        if getattr(result, "rowcount", 0) != 1:
            continue
        claimed.append({
            "conversation_id": row.conversation_id,
            "revision_id": row.scheduled_revision_id,
            "marketplaces": markets,
            "scheduled_for": row.scheduled_publish_at,
            "scheduled_confirmed_at": row.scheduled_confirmed_at,
            "pricing_rules": rules,
            "content_digest": digest,
            "requested_by": row.scheduled_requested_by,
            "confirmed_ebay_ad_rate": row.scheduled_ebay_ad_rate,
        })
        break
    db.commit()
    return claimed


def _settle_publish(db: Session, entry: dict[str, Any], outcome: dict[str, Any], moment: datetime) -> dict[str, Any]:
    ok = bool(outcome.get("ok"))
    status_override = outcome.get("status_override")
    error = None if ok else str(outcome.get("error") or "Scheduled publish did not confirm.")
    status = "published" if ok else (str(status_override) if status_override else "failed")
    values: dict[str, Any] = {
        "scheduled_status": status,
        "scheduled_error": error,
        "updated_at": moment,
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
        scheduled_for=entry["scheduled_for"] or moment,
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
            "Scheduled publish %s for item %s on %s: %s",
            status,
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


def _inspect_marketplace_outcome(db: Session, conv_id: str, markets: list[str]) -> dict[str, Any]:
    """Check persisted MarketplaceListing states for an interrupted/abandoned publish claim."""
    if not markets:
        return {
            "ok": False,
            "status_override": "needs_review",
            "error": "Scheduled publish stopped before confirming outcome. Review each marketplace before rescheduling.",
        }
    rows = {
        row.marketplace: row
        for row in db.query(MarketplaceListing)
        .filter(MarketplaceListing.conversation_id == conv_id, MarketplaceListing.marketplace.in_(markets))
        .all()
    }
    summary = {
        market: {
            "state": rows[market].state if market in rows else None,
            "listing_id": rows[market].listing_id if market in rows else None,
            "error": rows[market].error if market in rows else None,
        }
        for market in markets
    }
    if all(market in rows and rows[market].state in LIVE_PUBLISH_STATES for market in markets):
        return {"ok": True, "recovered": True, "marketplaces": summary}
    return {
        "ok": False,
        "status_override": "needs_review",
        "recovered": True,
        "marketplaces": summary,
        "error": "Scheduled publish stopped before confirming every marketplace. Check each marketplace before rescheduling.",
    }


def _recover_entry(db: Session, entry: dict[str, Any], moment: datetime) -> dict[str, Any] | None:
    """Reconcile an interrupted in-flight publish claim without retrying publication."""
    active_job = (
        db.query(Job.id)
        .filter(Job.conversation_id == entry["conversation_id"], Job.status.in_(ACTIVE_JOB_STATUSES))
        .first()
    )
    if active_job is not None:
        return None
    listing = (
        db.query(Listing)
        .filter(
            Listing.conversation_id == entry["conversation_id"],
            Listing.scheduled_status == "publishing",
            Listing.scheduled_revision_id == entry["revision_id"],
            Listing.scheduled_publish_at == entry["scheduled_for"],
            Listing.scheduled_confirmed_at == entry.get("scheduled_confirmed_at"),
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if listing is None:
        return None
    outcome = _inspect_marketplace_outcome(db, entry["conversation_id"], list(entry.get("marketplaces") or []))
    return _settle_publish(db, entry, outcome, moment)


def _recover_abandoned_publishes(db: Session, moment: datetime) -> list[dict[str, Any]]:
    """Recover abandoned ``publishing`` claims older than the grace window with no active job."""
    cutoff = moment - ABANDONED_PUBLISH_GRACE
    stuck = (
        db.query(Listing)
        .filter(
            Listing.scheduled_status == "publishing",
            Listing.updated_at.isnot(None),
            Listing.updated_at <= cutoff,
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    recovered: list[dict[str, Any]] = []
    for row in stuck:
        active_job = (
            db.query(Job.id)
            .filter(Job.conversation_id == row.conversation_id, Job.status.in_(ACTIVE_JOB_STATUSES))
            .first()
        )
        if active_job is not None:
            continue
        markets = [m for m in (row.scheduled_marketplaces or []) if m in SCHEDULABLE_MARKETPLACES]
        entry = {
            "conversation_id": row.conversation_id,
            "revision_id": row.scheduled_revision_id,
            "marketplaces": markets,
            "scheduled_for": row.scheduled_publish_at,
            "scheduled_confirmed_at": row.scheduled_confirmed_at,
        }
        outcome = _inspect_marketplace_outcome(db, row.conversation_id, markets)
        recovered.append(_settle_publish(db, entry, outcome, moment))
    return recovered


async def run_due_scheduled_publishes(*, now: datetime | None = None, publisher=None) -> list[dict[str, Any]]:
    """Publish due drafts whose seller confirmation and revision are still valid.

    Recovers any abandoned ``publishing`` claims before permitting new attempts,
    and releases DB connections before any external marketplace await.
    """
    from anteroom.services import pricing_rules

    moment = _as_naive_utc(now) if now is not None else _now()
    await in_worker_session(_recover_abandoned_publishes, moment)
    if publisher is None:
        from anteroom.services.automation_actions import publish_revision

        publisher = publish_revision
    outcomes: list[dict[str, Any]] = []
    for _ in range(20):
        if process_handoff.draining():
            # This Machine is stopping: leave the rest for the one that takes the lease.
            break
        due = await in_worker_session(_claim_due_drafts, moment)
        if not due:
            break
        entry = due[0]
        settled_done = False
        try:
            try:
                token = _active_claim.set(entry)
                try:
                    with pricing_rules.freeze_active_rules(entry.get("pricing_rules")):
                        result = await publisher(entry["conversation_id"], entry["marketplaces"], entry["revision_id"])
                finally:
                    _active_claim.reset(token)
            except Exception as exc:  # noqa: BLE001 - isolate one draft's publish failure
                result = {"ok": False, "error": str(exc) or "Scheduled publish failed."}
            settled = await asyncio.shield(in_worker_session(_settle_publish, entry, result, _now()))
            settled_done = True
            outcomes.append(settled)
        except BaseException:
            if not settled_done:
                with contextlib.suppress(BaseException):
                    await asyncio.shield(in_worker_session(_recover_entry, entry, _now()))
            raise
    return outcomes
