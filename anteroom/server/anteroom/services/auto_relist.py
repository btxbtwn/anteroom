"""Per-marketplace unattended Auto-Relist and eBay Auto-Recreate (OFF by default).

Approved by the seller on 2026-11-22 (see AGENTS.md Invariant):
- Per-marketplace rules for Poshmark, eBay, Mercari, and Depop are OFF by default.
- Minimum 30-day cycle on Poshmark/Mercari/Depop; eBay recreate mode targets ~30 minutes
  before the monthly 30-day GTC renewal.
- Enforces a seller daily cap (max 100/day/marketplace), optional price adjustment
  (% or $ step with floor), and filters (likers/watchers, condition, price range,
  keywords, labels).
- Automatically skips listings with active incoming buyer offers.
- Atomically swaps the old listing ID for the new listing ID across tracking tables
  so post-sale auto-delisting stays pinned.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.orm import Session

from anteroom.database import in_worker_session, run_worker_db_step
from anteroom.models.automation import MarketplaceRelistLog, MarketplaceRelistRule
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from anteroom.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from anteroom.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from anteroom.services import listing_mode, listing_persistence
from anteroom.services.automation_age import load_listed_index, resolve_listed_at
from anteroom.services.marketplace_delist import listings_for
from anteroom.services.marketplace_identity import listing_id as extract_listing_id
from anteroom.services.vendoo_import import merge_notes, parse_notes

log = logging.getLogger("anteroom.auto_relist")

RELIST_MARKETPLACES = ("poshmark", "ebay", "mercari", "depop")
MIN_AGE_DAYS_STANDARD = 30
MAX_DAILY_CAP = 100
DEFAULT_DAILY_CAP = 25
DEFAULT_EBAY_GTC_MINUTES = 30
EBAY_GTC_CYCLE = timedelta(days=30)
RULE_DISABLED_AFTER_DELETE = "The relist rule was turned off after deletion. Review the listing before recreating it."


class AutoRelistError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _relisted_last_24h(db: Session, marketplace: str, moment: datetime) -> int:
    since = moment - timedelta(hours=24)
    return (
        db.query(MarketplaceRelistLog)
        .filter(
            MarketplaceRelistLog.marketplace == marketplace,
            MarketplaceRelistLog.status.in_(("completed", "relist_pending")),
            MarketplaceRelistLog.created_at >= since,
        )
        .count()
    )


def _default_rule_view(marketplace: str, relisted_today: int = 0) -> dict[str, Any]:
    return {
        "marketplace": marketplace,
        "enabled": False,
        "min_age_days": MIN_AGE_DAYS_STANDARD,
        "ebay_recreate_before_gtc_minutes": DEFAULT_EBAY_GTC_MINUTES if marketplace == "ebay" else None,
        "daily_cap": DEFAULT_DAILY_CAP,
        "price_adjust_mode": None,
        "price_adjust_value": None,
        "price_floor": None,
        "filters": {},
        "skip_active_offers": True,
        "relisted_today": relisted_today,
        "remaining_today": max(DEFAULT_DAILY_CAP - relisted_today, 0),
        "last_run_at": None,
    }


def rule_view(db: Session, row: MarketplaceRelistRule, moment: datetime | None = None) -> dict[str, Any]:
    now = _as_naive_utc(moment) or _now()
    done = _relisted_last_24h(db, row.marketplace, now)
    return {
        "id": row.id,
        "marketplace": row.marketplace,
        "enabled": bool(row.enabled),
        "min_age_days": int(row.min_age_days),
        "ebay_recreate_before_gtc_minutes": row.ebay_recreate_before_gtc_minutes,
        "daily_cap": int(row.daily_cap),
        "price_adjust_mode": row.price_adjust_mode,
        "price_adjust_value": row.price_adjust_value,
        "price_floor": row.price_floor,
        "filters": dict(row.filters or {}),
        "skip_active_offers": True,
        "relisted_today": done,
        "remaining_today": max(int(row.daily_cap) - done, 0),
        "last_run_at": _iso(row.last_run_at),
    }


def list_rules(db: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = _as_naive_utc(now) or _now()
    existing = {row.marketplace: row for row in db.query(MarketplaceRelistRule).all()}
    result = []
    for market in RELIST_MARKETPLACES:
        if market in existing:
            result.append(rule_view(db, existing[market], moment))
        else:
            result.append(_default_rule_view(market, _relisted_last_24h(db, market, moment)))
    return result


def list_logs(db: Session, *, marketplace: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    query = db.query(MarketplaceRelistLog, Conversation.title).outerjoin(
        Conversation, Conversation.id == MarketplaceRelistLog.conversation_id
    )
    if marketplace:
        query = query.filter(MarketplaceRelistLog.marketplace == marketplace)
    rows = query.order_by(MarketplaceRelistLog.created_at.desc()).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": entry.id,
            "conversation_id": entry.conversation_id,
            "title": title,
            "marketplace": entry.marketplace,
            "old_listing_id": entry.old_listing_id,
            "new_listing_id": entry.new_listing_id,
            "old_price": entry.old_price,
            "new_price": entry.new_price,
            "status": entry.status,
            "reason": entry.reason,
            "created_at": _iso(entry.created_at),
        }
        for entry, title in rows
    ]


def _clean_filters(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for int_key in ("min_likes", "max_likes"):
        val = raw.get(int_key)
        if val is not None and val != "":
            try:
                parsed = int(val)
            except (TypeError, ValueError) as exc:
                raise AutoRelistError(f"{int_key} must be a whole number.") from exc
            if parsed < 0:
                raise AutoRelistError(f"{int_key} cannot be negative.")
            cleaned[int_key] = parsed
    if "min_likes" in cleaned and "max_likes" in cleaned and cleaned["min_likes"] > cleaned["max_likes"]:
        raise AutoRelistError("Minimum likers/watchers cannot exceed maximum.")
    for num_key in ("min_price", "max_price"):
        val = raw.get(num_key)
        if val is not None and val != "":
            try:
                parsed_f = float(val)
            except (TypeError, ValueError) as exc:
                raise AutoRelistError(f"{num_key} must be a price.") from exc
            if parsed_f < 0:
                raise AutoRelistError(f"{num_key} cannot be negative.")
            cleaned[num_key] = round(parsed_f, 2)
    if "min_price" in cleaned and "max_price" in cleaned and cleaned["min_price"] > cleaned["max_price"]:
        raise AutoRelistError("Minimum price filter cannot exceed maximum price filter.")
    for list_key in ("conditions", "keywords", "labels"):
        val = raw.get(list_key)
        if isinstance(val, list):
            items = [str(item).strip() for item in val if str(item or "").strip()]
            if items:
                cleaned[list_key] = items[:50]
    return cleaned


def save_rule(db: Session, marketplace: str, body: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
    market = str(marketplace or "").strip().lower()
    if market not in RELIST_MARKETPLACES:
        raise AutoRelistError("Auto-relist supports Poshmark, eBay, Mercari, and Depop.")
    enabled = bool(body.get("enabled", False))
    if enabled and confirm is not True:
        raise AutoRelistError("Confirm enabling unattended auto-relist for this marketplace.")

    min_age = int(body.get("min_age_days", MIN_AGE_DAYS_STANDARD))
    if market != "ebay" and min_age < MIN_AGE_DAYS_STANDARD:
        raise AutoRelistError(f"{market.title()} auto-relist requires at least a {MIN_AGE_DAYS_STANDARD}-day minimum age cycle.")
    if not 1 <= min_age <= 365:
        raise AutoRelistError("Minimum age cycle must be between 1 and 365 days.")

    gtc_minutes = body.get("ebay_recreate_before_gtc_minutes")
    if market == "ebay":
        gtc_val = DEFAULT_EBAY_GTC_MINUTES if gtc_minutes is None else int(gtc_minutes)
        if not 5 <= gtc_val <= 1440:
            raise AutoRelistError("eBay GTC recreate window must be between 5 and 1,440 minutes before renewal.")
        gtc_minutes = gtc_val
    else:
        gtc_minutes = None

    daily_cap = int(body.get("daily_cap", DEFAULT_DAILY_CAP))
    if not 1 <= daily_cap <= MAX_DAILY_CAP:
        raise AutoRelistError(f"Daily cap must be between 1 and {MAX_DAILY_CAP} listings per day.")

    price_mode = body.get("price_adjust_mode") or None
    price_value = body.get("price_adjust_value")
    price_floor = body.get("price_floor")
    if price_mode not in (None, "percent", "amount"):
        raise AutoRelistError("Price adjustment mode must be percent, amount, or none.")
    if price_mode is not None:
        if price_value is None:
            raise AutoRelistError("Enter a positive price adjustment step.")
        try:
            price_value = float(price_value)
        except (TypeError, ValueError) as exc:
            raise AutoRelistError("Enter a positive price adjustment step.") from exc
        if price_value <= 0 or (price_mode == "percent" and price_value >= 100):
            raise AutoRelistError("Enter a valid positive price adjustment step.")
        if price_floor is not None and price_floor != "":
            price_floor = round(float(price_floor), 2)
            if price_floor < 0:
                raise AutoRelistError("Price floor cannot be negative.")
        else:
            price_floor = None
    else:
        price_value = None
        price_floor = round(float(price_floor), 2) if price_floor not in (None, "") else None

    filters = _clean_filters(body.get("filters"))
    # Active buyer offers must always be checked and skipped before deleting a live listing.

    with listing_mode.guard(db):
        row = db.query(MarketplaceRelistRule).filter_by(marketplace=market).first()
        if row is None:
            row = MarketplaceRelistRule(id=new_id(), marketplace=market)
            db.add(row)
        row.enabled = enabled
        row.min_age_days = min_age
        row.ebay_recreate_before_gtc_minutes = gtc_minutes
        row.daily_cap = daily_cap
        row.price_adjust_mode = price_mode
        row.price_adjust_value = price_value
        row.price_floor = price_floor
        row.filters = filters
        row.skip_active_offers = True
        row.updated_at = _now()
        db.commit()
    db.refresh(row)
    return rule_view(db, row)


def compute_relist_price(current_price: float | None, rule: MarketplaceRelistRule) -> float | None:
    """Compute adjusted price respecting the seller's floor."""
    if current_price is None or current_price <= 0 or not rule.price_adjust_mode or not rule.price_adjust_value:
        return current_price
    if rule.price_adjust_mode == "percent":
        next_price = current_price * (1.0 - float(rule.price_adjust_value) / 100.0)
    else:
        next_price = current_price - float(rule.price_adjust_value)
    floor = float(rule.price_floor) if rule.price_floor is not None else 1.0
    floor = max(floor, 1.0)
    next_price = max(next_price, floor)
    return round(next_price, 2)


def is_due_for_relist(marketplace: str, listed_at: datetime | None, rule: MarketplaceRelistRule, now: datetime) -> bool:
    stamp = _as_naive_utc(listed_at)
    if stamp is None:
        return False
    age = now - stamp
    if marketplace == "ebay":
        before_mins = int(rule.ebay_recreate_before_gtc_minutes or DEFAULT_EBAY_GTC_MINUTES)
        threshold = EBAY_GTC_CYCLE - timedelta(minutes=before_mins)
        return threshold <= age < EBAY_GTC_CYCLE
    return age >= timedelta(days=max(int(rule.min_age_days or MIN_AGE_DAYS_STANDARD), MIN_AGE_DAYS_STANDARD))


def matches_filters(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    likes = item.get("likes")
    if "min_likes" in filters and (likes is None or likes < filters["min_likes"]):
        return False
    if "max_likes" in filters and likes is not None and likes > filters["max_likes"]:
        return False
    price = item.get("price")
    if "min_price" in filters and (price is None or price < filters["min_price"]):
        return False
    if "max_price" in filters and (price is None or price > filters["max_price"]):
        return False
    conditions = filters.get("conditions")
    if conditions:
        cond = str(item.get("condition") or "").strip().lower()
        if cond not in {str(c).strip().lower() for c in conditions}:
            return False
    keywords = filters.get("keywords")
    if keywords:
        haystack = f"{item.get('title') or ''} {item.get('description') or ''}".lower()
        if not any(str(kw).strip().lower() in haystack for kw in keywords if str(kw).strip()):
            return False
    labels = filters.get("labels")
    if labels:
        item_labels = {str(lbl).strip().lower() for lbl in (item.get("labels") or []) if str(lbl).strip()}
        if not item_labels.intersection({str(lbl).strip().lower() for lbl in labels}):
            return False
    return True


def has_active_buyer_offer_local(db: Session, conv_id: str, marketplace: str, listing_id: str) -> bool:
    """Check local observations and recent active offers for pending/open buyer offers."""
    ml = db.query(MarketplaceListing).filter_by(conversation_id=conv_id, marketplace=marketplace).first()
    if ml is not None and isinstance(ml.inventory_observation, dict):
        obs = ml.inventory_observation
        if obs.get("has_active_offer") is True or (isinstance(obs.get("active_offers_count"), int) and obs["active_offers_count"] > 0):
            return True
    recent_cutoff = _now() - timedelta(hours=24)
    if marketplace == "ebay":
        active_ebay = (
            db.query(EbayOfferAttempt.id)
            .join(EbayOfferRule, EbayOfferRule.id == EbayOfferAttempt.rule_id)
            .filter(
                EbayOfferRule.listing_id == listing_id,
                EbayOfferAttempt.state.in_(("sending", "sent", "pending")),
                EbayOfferAttempt.created_at >= recent_cutoff,
            )
            .first()
        )
        if active_ebay is not None:
            return True
    else:
        active_promo = (
            db.query(ListingPromotion.id)
            .filter(
                ListingPromotion.marketplace == marketplace,
                ListingPromotion.listing_id == listing_id,
                ListingPromotion.action.in_(("offer", "bundle_offer")),
                ListingPromotion.state.in_(("sending", "done")),
                ListingPromotion.sent_at >= recent_cutoff,
            )
            .first()
        )
        if active_promo is not None:
            return True
    return False


def swap_tracked_listing_id(
    db: Session,
    conv_id: str,
    marketplace: str,
    *,
    old_listing_id: str | None,
    new_listing_id: str,
    new_listing_url: str | None = None,
    new_price: float | None = None,
    now: datetime,
) -> None:
    """Atomically replace old_listing_id with new_listing_id so post-sale auto-delisting stays pinned."""
    row = db.query(MarketplaceListing).filter_by(conversation_id=conv_id, marketplace=marketplace).first()
    if row is None:
        row = MarketplaceListing(id=new_id(), conversation_id=conv_id, marketplace=marketplace)
        db.add(row)
    row.state = "published"
    row.listing_id = new_listing_id
    if new_listing_url:
        row.listing_url = new_listing_url
    if marketplace == "ebay":
        row.ebay_listing_id = new_listing_id
    row.listed_at = now
    if new_price is not None:
        row.listed_price = new_price
        row.price_recorded_at = now
    row.error = None
    row.error_code = None

    # Update offer floors and eBay offer rules that referenced the old listing ID.
    if old_listing_id and old_listing_id != new_listing_id:
        floor_row = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=old_listing_id).first()
        if floor_row is not None:
            existing_new = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=new_listing_id).first()
            if existing_new is None:
                floor_row.listing_id = new_listing_id
        if marketplace == "ebay":
            ebay_rule = db.query(EbayOfferRule).filter_by(listing_id=old_listing_id).first()
            if ebay_rule is not None:
                existing_rule = db.query(EbayOfferRule).filter_by(account_id=ebay_rule.account_id, listing_id=new_listing_id).first()
                if existing_rule is None:
                    ebay_rule.listing_id = new_listing_id

    # Clear stale delist record and refresh seller mark if present so marketplace_delist.listings sees the new ID cleanly.
    conv = db.get(Conversation, conv_id)
    if conv is not None:
        notes = parse_notes(conv.notes)
        delists = dict(notes.get("marketplaceDelists") or {})
        delists.pop(marketplace, None)
        marks = dict(notes.get("marketplaceMarks") or {})
        if marketplace in marks and isinstance(marks[marketplace], dict) and marks[marketplace].get("state") == "listed":
            marks[marketplace] = {
                **marks[marketplace],
                "url": new_listing_url or marks[marketplace].get("url"),
                "listing_id": new_listing_id,
                "at": now.isoformat() + "Z",
            }
        conv.notes = merge_notes(conv.notes, {"marketplaceDelists": delists, "marketplaceMarks": marks})


def _eligible_candidates(db: Session, rule: MarketplaceRelistRule, moment: datetime) -> list[dict[str, Any]]:
    done_today = _relisted_last_24h(db, rule.marketplace, moment)
    remaining = max(min(int(rule.daily_cap), MAX_DAILY_CAP) - done_today, 0)
    if remaining <= 0:
        return []

    sold_convs = {
        row[0]
        for row in db.query(MarketplaceSale.conversation_id)
        .filter(
            MarketplaceSale.conversation_id.isnot(None),
            MarketplaceSale.order_status.in_((*PAID_STATUSES, "cancellation_pending")),
        )
        .all()
    }
    convs = db.query(Conversation).filter(Conversation.status != "sold").all()
    if not convs:
        return []
    eligible_convs = [c for c in convs if c.id not in sold_convs]
    conv_ids = [c.id for c in eligible_convs]
    if not conv_ids:
        return []
    known_by_conv = listings_for(db, eligible_convs)
    listed_index = load_listed_index(db)
    listings_by_conv = {row.conversation_id: row for row in db.query(Listing).filter(Listing.conversation_id.in_(conv_ids)).all()}
    rev_ids = [row.current_revision_id for row in listings_by_conv.values() if row.current_revision_id]
    revs_by_id = {row.id: row for row in db.query(ListingRevision).filter(ListingRevision.id.in_(rev_ids)).all()} if rev_ids else {}
    ml_by_conv = {
        row.conversation_id: row
        for row in db.query(MarketplaceListing)
        .filter(MarketplaceListing.conversation_id.in_(conv_ids), MarketplaceListing.marketplace == rule.marketplace)
        .all()
    }

    candidates: list[dict[str, Any]] = []
    for conv in convs:
        if conv.id not in conv_ids:
            continue
        entry = (known_by_conv.get(conv.id) or {}).get(rule.marketplace) or {}
        if entry.get("sold_here") or entry.get("state") not in (None, "live", "failed") or not entry.get("listing_id"):
            continue
        ml = ml_by_conv.get(conv.id)
        if ml is not None and ml.state not in LIVE_STATES and ml.state != "not_saved":
            continue
        listed_at_raw, _src = resolve_listed_at(listed_index, conv, [ml] if ml is not None else [], {rule.marketplace})
        # Prefer marketplace_listing.listed_at when available for accurate per-marketplace relist age.
        listed_at = ml.listed_at if (ml is not None and ml.listed_at is not None) else listed_at_raw
        if not is_due_for_relist(rule.marketplace, listed_at, rule, moment):
            continue

        listing_row = listings_by_conv.get(conv.id)
        rev = revs_by_id.get(listing_row.current_revision_id) if (listing_row and listing_row.current_revision_id) else None
        data = dict(rev.listing_json) if (rev and isinstance(rev.listing_json, dict)) else {}
        notes = parse_notes(conv.notes)
        raw_labels = notes.get("vendooLabels") or ""
        labels = [part.strip() for part in str(raw_labels).split(",") if part.strip()]
        obs = ml.inventory_observation if (ml and isinstance(ml.inventory_observation, dict)) else {}
        likes = obs.get("likes") if isinstance(obs.get("likes"), int) else obs.get("watchers")
        price_raw = ml.listed_price if (ml and ml.listed_price is not None) else data.get("price")
        try:
            price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None

        item = {
            "conversation_id": conv.id,
            "title": conv.title or data.get("title") or "",
            "description": data.get("description") or "",
            "condition": data.get("condition") or "",
            "price": price,
            "likes": likes,
            "labels": labels,
            "listing_id": str(entry["listing_id"]),
            "revision_id": rev.id if rev else None,
            "listed_at": _as_naive_utc(listed_at),
        }
        if not matches_filters(item, rule.filters or {}):
            continue
        candidates.append(item)

    # Oldest listed items go first, bounded by daily cap.
    candidates.sort(key=lambda item: item["listed_at"] or datetime.min)
    return candidates[:remaining]


def _prepare_price_revision(db: Session, conv_id: str, new_price: float | None, current_revision_id: str | None) -> str | None:
    if new_price is None or current_revision_id is None:
        return current_revision_id
    rev = db.get(ListingRevision, current_revision_id)
    if rev is None or not isinstance(rev.listing_json, dict):
        return current_revision_id
    current_val = rev.listing_json.get("price")
    try:
        if current_val is not None and round(float(current_val), 2) == round(new_price, 2):
            return current_revision_id
    except (TypeError, ValueError):
        pass
    updated = dict(rev.listing_json)
    updated["price"] = round(new_price, 2)
    saved = listing_persistence.save_revision(
        db,
        conv_id,
        updated,
        source="auto_relist",
        parent_revision_id=current_revision_id,
        expected_revision_id=current_revision_id,
        commit=True,
    )
    return saved.id


PENDING_STAGE_DELETING = "stage:deleting"
PENDING_STAGE_PUBLISHING = "stage:publishing"
PENDING_NO_JOB_GRACE = timedelta(minutes=15)


def _has_delete_receipt(db: Session, attempt: MarketplaceRelistLog) -> bool:
    conv = db.get(Conversation, attempt.conversation_id)
    if conv is None:
        return False
    delists = parse_notes(conv.notes).get("marketplaceDelists")
    receipt = delists.get(attempt.marketplace) if isinstance(delists, dict) else None
    if (not isinstance(receipt, dict) or receipt.get("state") != "deleted"
            or not attempt.old_listing_id or receipt.get("listing_id") != attempt.old_listing_id):
        return False
    try:
        deleted_at = _as_naive_utc(datetime.fromisoformat(str(receipt.get("at"))))
    except ValueError:
        return False
    return deleted_at is not None and deleted_at >= attempt.created_at


def _reconcile_pending_relists(
    db: Session,
    moment: datetime,
    marketplace: str | None = None,
) -> set[tuple[str, str]]:
    """Reconcile any ``relist_pending`` logs against settled marketplace records.

    Returns the set of ``(conversation_id, marketplace)`` pairs still waiting on
    an active or unverified publish so subsequent sweeps never trigger a duplicate relist.
    """
    from anteroom.models.job import ACTIVE_JOB_STATUSES, Job
    from anteroom.repositories.queries import JobRepo

    query = db.query(MarketplaceRelistLog).filter(MarketplaceRelistLog.status == "relist_pending")
    if marketplace:
        query = query.filter(MarketplaceRelistLog.marketplace == marketplace)
    pending_rows = query.all()
    if not pending_rows:
        return set()

    still_pending: set[tuple[str, str]] = set()
    changed = False
    job_repo = JobRepo(db)
    for row in pending_rows:
        ml = (
            db.query(MarketplaceListing)
            .filter_by(conversation_id=row.conversation_id, marketplace=row.marketplace)
            .first()
        )
        if row.native_job_id:
            job = job_repo.get(row.native_job_id)
            if (ml is None or job is None or job.conversation_id != row.conversation_id
                    or ml.job_id != row.native_job_id or job.status in ACTIVE_JOB_STATUSES):
                still_pending.add((row.conversation_id, row.marketplace))
                continue
            if job.status == "completed" and ml.state in LIVE_STATES and ml.listing_id and ml.listing_id != row.old_listing_id:
                swap_tracked_listing_id(
                    db,
                    row.conversation_id,
                    row.marketplace,
                    old_listing_id=row.old_listing_id,
                    new_listing_id=str(ml.listing_id),
                    new_listing_url=ml.listing_url,
                    new_price=row.new_price,
                    now=moment,
                )
                row.status = "completed"
                row.new_listing_id = str(ml.listing_id)
                row.reason = None
                changed = True
                continue
            row.status = "down_needs_relist"
            if ml is not None and ml.error:
                row.reason = str(ml.error)[:500]
            changed = True
            continue

        # No native_job_id recorded yet (e.g. crashed during delete or before list_now returned):
        # verify marketplace state before clearing the hold or offering recovery; never retry publish.
        active_conv_job = (
            db.query(Job.id)
            .filter(Job.conversation_id == row.conversation_id, Job.status.in_(ACTIVE_JOB_STATUSES))
            .first()
        )
        if active_conv_job is not None:
            still_pending.add((row.conversation_id, row.marketplace))
            continue
        ml_listed_at = _as_naive_utc(ml.listed_at) if (ml is not None and ml.listed_at is not None) else None
        if (
            ml is not None
            and ml.state in LIVE_STATES
            and ml.listing_id
            and ml.listing_id != row.old_listing_id
            and ml_listed_at is not None
            and ml_listed_at >= row.created_at
        ):
            swap_tracked_listing_id(
                db,
                row.conversation_id,
                row.marketplace,
                old_listing_id=row.old_listing_id,
                new_listing_id=str(ml.listing_id),
                new_listing_url=ml.listing_url,
                new_price=row.new_price,
                now=moment,
            )
            row.status = "completed"
            row.new_listing_id = str(ml.listing_id)
            row.reason = None
            changed = True
            continue
        reason_text = str(row.reason or "")
        aged_out = row.created_at <= moment - PENDING_NO_JOB_GRACE
        if (aged_out and reason_text.startswith(PENDING_STAGE_DELETING)
                and (ml is None or ml.state not in LIVE_STATES) and _has_delete_receipt(db, row)):
            row.status = "down_needs_relist"
            row.reason = "The old listing is confirmed absent and no replacement was recorded. Verify the marketplace before relisting."
            changed = True
            continue
        if aged_out and reason_text.startswith(PENDING_STAGE_PUBLISHING) and (
            ml is None or ml.state not in LIVE_STATES or ml.listing_id != row.old_listing_id
        ):
            row.status = "down_needs_relist"
            row.reason = "Old listing was delisted before a replacement job ID was recorded; verify marketplace before relisting."
            changed = True
            continue
        still_pending.add((row.conversation_id, row.marketplace))
    if changed:
        db.commit()
    return still_pending


def _load_enabled_rules_and_candidates(
    db: Session,
    moment: datetime,
    marketplace: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(MarketplaceRelistRule).filter(MarketplaceRelistRule.enabled.is_(True))
    market: str | None = None
    if marketplace:
        market = str(marketplace).strip().lower()
        if market not in RELIST_MARKETPLACES:
            raise AutoRelistError("Auto-relist supports Poshmark, eBay, Mercari, and Depop.")
        query = query.filter(MarketplaceRelistRule.marketplace == market)
    still_pending = _reconcile_pending_relists(db, moment, market)
    rules = query.all()
    plan: list[dict[str, Any]] = []
    for rule in rules:
        candidates = _eligible_candidates(db, rule, moment)
        for item in candidates:
            if (item["conversation_id"], rule.marketplace) in still_pending:
                continue
            # Always check local active buyer offers regardless of rule.skip_active_offers.
            local_offer = has_active_buyer_offer_local(db, item["conversation_id"], rule.marketplace, item["listing_id"])
            new_price = compute_relist_price(item["price"], rule)
            plan.append({
                "rule_id": rule.id,
                "marketplace": rule.marketplace,
                "local_active_offer": local_offer,
                "new_price": new_price,
                **item,
            })
    return plan


def _record_log(
    db: Session,
    *,
    conv_id: str,
    marketplace: str,
    old_listing_id: str | None,
    new_listing_id: str | None,
    old_price: float | None,
    new_price: float | None,
    status: str,
    reason: str | None,
    moment: datetime,
    new_listing_url: str | None = None,
    native_job_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    if status == "completed" and new_listing_id:
        swap_tracked_listing_id(
            db,
            conv_id,
            marketplace,
            old_listing_id=old_listing_id,
            new_listing_id=new_listing_id,
            new_listing_url=new_listing_url,
            new_price=new_price,
            now=moment,
        )
    row = db.get(MarketplaceRelistLog, attempt_id) if attempt_id else None
    if row is None:
        row = MarketplaceRelistLog(id=attempt_id or new_id(), conversation_id=conv_id,
                                  marketplace=marketplace, old_listing_id=old_listing_id,
                                  old_price=old_price, new_price=new_price, created_at=moment)
        db.add(row)
    if native_job_id is not None:
        row.native_job_id = native_job_id
    row.new_listing_id = new_listing_id
    row.status = status
    row.reason = reason
    rule = db.query(MarketplaceRelistRule).filter_by(marketplace=marketplace).first()
    if rule is not None:
        rule.last_run_at = moment
    db.commit()
    return {
        "conversation_id": conv_id,
        "marketplace": marketplace,
        "old_listing_id": old_listing_id,
        "new_listing_id": new_listing_id,
        "status": status,
        "reason": reason,
    }


def _rule_enabled(db: Session, rule_id: str) -> bool:
    rule = db.get(MarketplaceRelistRule, rule_id)
    return bool(rule and rule.enabled)


def _record_log_payload(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return _record_log(db, **payload)


def _reserve_relist_attempt(
    db: Session,
    entry: dict[str, Any],
    moment: datetime,
) -> dict[str, Any]:
    """Serialize rule-enabled check, daily-cap reservation, candidate freshness, and revision prep."""
    rule_id = entry["rule_id"]
    conv_id = entry["conversation_id"]
    marketplace = entry["marketplace"]
    old_listing_id = entry["listing_id"]
    old_price = entry["price"]
    new_price = entry["new_price"]
    current_revision_id = entry["revision_id"]
    with listing_mode.guard(db):
        rule = (
            db.query(MarketplaceRelistRule)
            .filter(MarketplaceRelistRule.id == rule_id)
            .with_for_update()
            .first()
        )
        if rule is None or not rule.enabled:
            outcome = _record_log(
                db,
                conv_id=conv_id,
                marketplace=marketplace,
                old_listing_id=old_listing_id,
                new_listing_id=None,
                old_price=old_price,
                new_price=old_price,
                status="skipped_rule_disabled",
                reason="Skipped because auto-relist was turned off before delisting.",
                moment=moment,
            )
            return {"reserved": False, "outcome": outcome}

        existing_pending = (
            db.query(MarketplaceRelistLog.id)
            .filter(
                MarketplaceRelistLog.conversation_id == conv_id,
                MarketplaceRelistLog.marketplace == marketplace,
                MarketplaceRelistLog.status == "relist_pending",
            )
            .first()
        )
        if existing_pending is not None:
            return {"reserved": False, "outcome": None}

        done_today = _relisted_last_24h(db, marketplace, moment)
        cap = min(int(rule.daily_cap), MAX_DAILY_CAP)
        if done_today >= cap:
            return {"reserved": False, "outcome": None}

        candidate = next(
            (item for item in _eligible_candidates(db, rule, moment) if item["conversation_id"] == conv_id),
            None,
        )
        if (
            candidate is None
            or candidate["listing_id"] != old_listing_id
            or candidate["revision_id"] != current_revision_id
            or compute_relist_price(candidate["price"], rule) != new_price
        ):
            return {"reserved": False, "outcome": None}

        revision_id = _prepare_price_revision(db, conv_id, new_price, current_revision_id)
        if not revision_id:
            outcome = _record_log(
                db,
                conv_id=conv_id,
                marketplace=marketplace,
                old_listing_id=old_listing_id,
                new_listing_id=None,
                old_price=old_price,
                new_price=new_price,
                status="failed",
                reason="Listing has no saved revision to relist.",
                moment=moment,
            )
            return {"reserved": False, "outcome": outcome}

        attempt_id = new_id()
        _record_log(
            db,
            attempt_id=attempt_id,
            conv_id=conv_id,
            marketplace=marketplace,
            old_listing_id=old_listing_id,
            new_listing_id=None,
            old_price=old_price,
            new_price=new_price,
            status="relist_pending",
            reason=f"{PENDING_STAGE_DELETING} | Delisting old listing before relist.",
            moment=moment,
        )
        return {"reserved": True, "attempt_id": attempt_id, "revision_id": revision_id}


def _update_attempt_stage(
    db: Session,
    attempt_id: str,
    reason: str | None = None,
    native_job_id: str | None = None,
) -> None:
    row = db.get(MarketplaceRelistLog, attempt_id)
    if row is None or row.status != "relist_pending":
        return
    if reason is not None:
        row.reason = reason
    if native_job_id is not None:
        row.native_job_id = native_job_id
    db.commit()


async def check_active_buyer_offer(marketplace: str, listing_id: str) -> bool | None:
    """Verify whether ``listing_id`` has an active incoming buyer offer.

    Returns:
      - ``True``: verified active incoming buyer offer exists (must skip delist/relist)
      - ``False``: verified NO active incoming buyer offer exists (safe to delist/relist)
      - ``None``: could not verify (must fail closed with ``skipped_offer_unverified``)
    """
    market = str(marketplace or "").strip().lower()
    lid = str(listing_id or "").strip()
    if not lid:
        return None

    if market == "ebay":
        from anteroom.services.ebay_client import NS, EbayClient, EbayFailure
        from anteroom.services.marketplace_accounts import SignInRequired

        try:
            client = await EbayClient.connect()
            result = await client.trading("GetBestOffers", {"ItemID": lid, "BestOfferStatus": "Active"})
        except EbayFailure as exc:
            combined = f"{exc} {exc.marketplace_message or ''}".lower()
            if "21916260" in combined or "best offer is not enabled" in combined or "not enabled for best offer" in combined:
                return False
            log.warning("eBay GetBestOffers check failed for %s: %s", lid, exc)
            return None
        except (SignInRequired, Exception) as exc:  # noqa: BLE001 - fail closed when offer status cannot be verified
            log.warning("eBay active offer check unavailable for %s: %s", lid, exc)
            return None
        root = result.get("xml")
        if root is None:
            return None
        offers = root.findall(".//e:BestOfferArray/e:BestOffer", NS) or root.findall(".//e:BestOffer", NS)
        # The request explicitly filters for active offers. A new status name
        # must not turn a returned offer into clearance to delete the listing.
        return bool(offers) if root.tag == f"{{{NS['e']}}}GetBestOffersResponse" else None

    if market in ("poshmark", "depop", "mercari"):
        from anteroom.services import browser_bridge, cloud_marketplace, cloud_sessions
        from anteroom.services.extension_connection import current_extension
        from anteroom.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, relay_for
        from anteroom.services.service_context import current

        payload = {"marketplace": market, "listing_id": lid}
        job_id = f"offer-check-{new_id()}"
        try:
            use_cloud = await run_worker_db_step(cloud_sessions.has_active_session, market)
            if use_cloud:
                reply = await cloud_marketplace.execute(
                    market,
                    {"type": "marketplace.active_offers", "job_id": job_id, "payload": payload},
                    timeout=20,
                )
            else:
                ctx = current()
                if ctx is not None:
                    if relay_for(ctx.seller_id).refusal(MARKETPLACE_PROMOTIONS):
                        return None
                elif not current_extension().connected:
                    return None
                reply = await browser_bridge.request(
                    SimpleNamespace(id=job_id),
                    "marketplace.active_offers",
                    payload,
                    timeout=20,
                )
        except Exception as exc:  # noqa: BLE001 - fail closed when marketplace cannot be queried
            log.warning("%s active offer check failed for %s: %s", market, lid, exc)
            return None
        if not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("verified") is not True:
            return None
        active = reply.get("has_active_offer")
        return active if isinstance(active, bool) else None

    return None


async def run_auto_relist(
    *,
    now: datetime | None = None,
    marketplace: str | None = None,
    dry_run: bool = False,
    delister=None,
    lister=None,
    offer_checker=None,
) -> list[dict[str, Any]]:
    """Run per-marketplace unattended auto-relist / eBay auto-recreate for opted-in marketplaces.

    Releases DB connections before every external marketplace/extension await.
    """
    moment = _as_naive_utc(now) or _now()
    plan = await in_worker_session(_load_enabled_rules_and_candidates, moment, marketplace)
    if not plan:
        return []
    if dry_run:
        return [
            {
                "conversation_id": entry["conversation_id"],
                "title": entry["title"],
                "marketplace": entry["marketplace"],
                "old_listing_id": entry["listing_id"],
                "new_listing_id": None,
                "old_price": entry["price"],
                "new_price": entry["new_price"],
                "status": "skipped_active_offer" if entry["local_active_offer"] else "dry_run",
                "reason": "Active incoming buyer offer." if entry["local_active_offer"] else None,
            }
            for entry in plan
        ]

    async def _run_delist(
        conv_id: str, marketplace: str, expected_listing_id: str, rule_id: str
    ) -> dict[str, Any]:
        if not await in_worker_session(_rule_enabled, rule_id):
            return {
                "ok": False,
                "skipped": True,
                "error": "The relist rule was turned off. Nothing was delisted.",
            }
        if delister is not None:
            return await delister(conv_id, marketplace)
        from anteroom.services.marketplace_delist import DelistError, delist as default_delist

        try:
            res = await default_delist(
                conv_id,
                [marketplace],
                confirm=True,
                expected={marketplace: expected_listing_id},
                relist_rule_id=rule_id,
            )
        except DelistError as exc:
            if exc.reason == "relist_rule_disabled":
                return {"ok": False, "skipped": True, "error": str(exc)}
            return {"ok": False, "error": str(exc)}
        entry_res = res.get(marketplace) or {}
        return {"ok": entry_res.get("state") == "deleted", "error": entry_res.get("error"), "detail": entry_res}

    async def _run_list(
        conv_id: str, marketplace: str, revision_id: str, attempt_id: str, rule_id: str
    ) -> dict[str, Any]:
        if lister is not None:
            if not await in_worker_session(_rule_enabled, rule_id):
                return {"ok": False, "skipped": True, "error": RULE_DISABLED_AFTER_DELETE}
            return await lister(conv_id, marketplace, revision_id)
        from anteroom.services.automation_actions import _turn, wait_for_native_result
        from anteroom.services.native_marketplaces import ListRequest, NativeMarketplaceError, list_now

        try:
            async with _turn(marketplace):
                if not await in_worker_session(_rule_enabled, rule_id):
                    return {"ok": False, "skipped": True, "error": RULE_DISABLED_AFTER_DELETE}
                try:
                    res = await list_now(
                        conv_id,
                        cast(Any, marketplace),
                        ListRequest(revision_id=revision_id, confirm=True),
                        relist_rule_id=rule_id,
                    )
                except NativeMarketplaceError as exc:
                    if exc.reason == "relist_rule_disabled":
                        return {"ok": False, "skipped": True, "error": RULE_DISABLED_AFTER_DELETE}
                    return {"ok": False, "error": str(exc)}
                dispatched_job_id = res.get("job_id")
                if dispatched_job_id:
                    await in_worker_session(
                        _update_attempt_stage,
                        attempt_id,
                        None,
                        str(dispatched_job_id),
                    )
                res = await wait_for_native_result(conv_id, marketplace, res)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        ok = res.get("state") in LIVE_STATES and not res.get("error")
        new_id_val = res.get("listing_id") or extract_listing_id(marketplace, res.get("listing_url"))
        return {
            "ok": ok,
            "listing_id": new_id_val,
            "listing_url": res.get("listing_url"),
            "error": res.get("error"),
            "timed_out": bool(res.get("timed_out")),
            "native_job_id": res.get("job_id"),
        }

    active_offer_checker = offer_checker if offer_checker is not None else check_active_buyer_offer

    outcomes: list[dict[str, Any]] = []
    for entry in plan:
        conv_id = entry["conversation_id"]
        market = entry["marketplace"]
        old_id = entry["listing_id"]
        rule_id = entry["rule_id"]
        step_now = moment if now is not None else _now()

        # Always verify no active incoming buyer offer before deleting a live listing.
        offer_status = True if entry["local_active_offer"] else None
        if offer_status is None:
            try:
                offer_status = await asyncio.wait_for(active_offer_checker(market, old_id), timeout=20)
            except Exception:  # noqa: BLE001 - no positive clearance to delist
                pass
        if offer_status is not False:
            outcomes.append(await in_worker_session(_record_log_payload, {
                "conv_id": conv_id,
                "marketplace": market,
                "old_listing_id": old_id,
                "new_listing_id": None,
                "old_price": entry["price"],
                "new_price": entry["price"],
                "status": "skipped_active_offer" if offer_status is True else "skipped_offer_unverified",
                "reason": ("Skipped because this listing has an active incoming buyer offer." if offer_status is True
                           else "Skipped because Anteroom could not verify incoming buyer offers."),
                "moment": step_now,
            }))
            continue

        # Atomically lock the rule, re-check rule.enabled and daily cap, prepare revision,
        # and commit a relist_pending row before dispatching either external write.
        reservation = await in_worker_session(_reserve_relist_attempt, entry, step_now)
        if not reservation["reserved"]:
            if reservation.get("outcome") is not None:
                outcomes.append(reservation["outcome"])
            continue

        attempt_id = reservation["attempt_id"]
        revision_id = reservation["revision_id"]

        delist_res = await _run_delist(conv_id, market, old_id, rule_id)
        if not delist_res.get("ok"):
            delist_status = "skipped_rule_disabled" if delist_res.get("skipped") else "failed"
            outcomes.append(await in_worker_session(_record_log_payload, {
                "attempt_id": attempt_id, "conv_id": conv_id,
                "marketplace": market,
                "old_listing_id": old_id,
                "new_listing_id": None,
                "old_price": entry["price"],
                "new_price": entry["price"] if delist_status == "skipped_rule_disabled" else entry["new_price"],
                "status": delist_status,
                "reason": str(delist_res.get("error") or "Could not delist the old listing."),
                "moment": moment if now is not None else _now(),
            }))
            continue

        await in_worker_session(
            _update_attempt_stage,
            attempt_id,
            f"{PENDING_STAGE_PUBLISHING} | Old listing delisted; publishing replacement listing.",
        )

        list_res = await _run_list(conv_id, market, revision_id, attempt_id, rule_id)
        new_id_val = list_res.get("listing_id") or extract_listing_id(market, list_res.get("listing_url"))
        if not list_res.get("ok") or not new_id_val:
            error_msg = str(list_res.get("error") or "Old listing was delisted, but relist did not confirm.")
            if list_res.get("skipped"):
                status = "skipped_rule_disabled"
            elif list_res.get("timed_out"):
                status = "relist_pending"
            else:
                status = "down_needs_relist"
            outcomes.append(await in_worker_session(_record_log_payload, {
                "attempt_id": attempt_id, "conv_id": conv_id,
                "marketplace": market,
                "old_listing_id": old_id,
                "new_listing_id": None,
                "old_price": entry["price"],
                "new_price": entry["new_price"],
                "status": status,
                "native_job_id": list_res.get("native_job_id"),
                "reason": error_msg,
                "moment": moment if now is not None else _now(),
            }))
            if status == "relist_pending":
                log.warning("Auto-relist publish timed out on %s for %s; marked relist_pending for reconciliation: %s", market, conv_id, error_msg)
            elif status == "skipped_rule_disabled":
                log.info("Auto-relist stopped recreating %s on %s because rule was turned off: %s", conv_id, market, error_msg)
            else:
                log.error("Auto-relist left item %s down on %s: %s", conv_id, market, error_msg)
            continue

        outcomes.append(await in_worker_session(_record_log_payload, {
            "attempt_id": attempt_id, "conv_id": conv_id,
            "marketplace": market,
            "old_listing_id": old_id,
            "new_listing_id": str(new_id_val),
            "old_price": entry["price"],
            "new_price": entry["new_price"],
            "status": "completed",
            "reason": None,
            "moment": moment if now is not None else _now(),
            "new_listing_url": list_res.get("listing_url"),
        }))

    return outcomes
