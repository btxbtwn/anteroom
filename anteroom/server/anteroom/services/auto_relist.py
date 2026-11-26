"""Per-marketplace unattended Auto-Relist and eBay Auto-Recreate (OFF by default).

Approved by the seller on 2026-11-22 (see AGENTS.md Invariant):
- Per-marketplace rules for Poshmark, eBay, Mercari, and Depop are OFF by default.
- Minimum 30-day cycle on Poshmark/Mercari/Depop; eBay recreate mode targets ~30 minutes
  before the monthly 30-day GTC renewal.
- Enforces a seller daily cap (max 100/day/marketplace), optional price adjustment
  (% or $ step with floor), and filters (likers/watchers, condition, price range,
  keywords, labels).
- Atomically swaps the old listing ID for the new listing ID across tracking tables
  so post-sale auto-delisting stays pinned.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy.orm import Session

from anteroom.database import in_worker_session
from anteroom.models.automation import MarketplaceRelistLog, MarketplaceRelistRule
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.ebay_offer import EbayOfferRule
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_promotion import MarketplaceOfferFloor
from anteroom.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from anteroom.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from anteroom.services import listing_mode, listing_persistence
from anteroom.services.automation_age import load_listed_index, resolve_listed_at
from anteroom.services.chrome_listing import (
    mercari_smart_pricing_meets_price,
    mercari_whole_dollars,
    poshmark_fractional_price_error,
)
from anteroom.services.marketplace_delist import listings_for
from anteroom.services.marketplace_identity import listing_id as extract_listing_id
from anteroom.services.seller_overrides import (
    CANONICAL_PRICE,
    _write_locks,
    locked_paths,
    price_for,
    set_path,
)
from anteroom.services.vendoo_import import merge_notes, parse_notes

log = logging.getLogger("anteroom.auto_relist")

RELIST_MARKETPLACES = ("poshmark", "ebay", "mercari", "depop")
MIN_AGE_DAYS_STANDARD = 30
MAX_DAILY_CAP = 100
DEFAULT_DAILY_CAP = 25
DEFAULT_EBAY_GTC_MINUTES = 30
EBAY_GTC_CYCLE = timedelta(days=30)
EBAY_CYCLE_DEDUPE_WINDOW = timedelta(hours=25)
RELIST_RETRY_BACKOFF = timedelta(hours=6)
AUTO_RELIST_TICK_INTERVAL = timedelta(minutes=5)
BACKOFF_STATUSES = (
    "failed",
    "down_needs_relist",
)
CAP_COUNTED_STATUSES = ("completed", "relist_pending", "failed", "down_needs_relist")
DELIST_BLOCKERS = ("signed_out", "challenge", "chrome_unavailable", "rate_limited")
RULE_DISABLED_AFTER_DELETE = "The relist rule was turned off after deletion. Review the listing before recreating it."
# How long a relist cancelled mid-delete waits to put its price back.
RESTORE_PRICE_DEADLINE_S = 30.0
# Marketplaces whose replacement is first saved as a native draft, which the marketplace
# can refuse. Relisting deletes the old listing first, so a refused draft left the item
# down (production 2026-11-25: Mercari refused native_mercari_save_draft after the delete).
# A replacement draft cannot be saved and verified before that delete: the item's one
# marketplace_listings row holds the live listing, and a draft is refused while it is live.
# Until it can, Auto-Relist leaves these listings up rather than delete one first.
REPLACEMENT_UNVERIFIED = {
    "mercari": ("Mercari Auto-Relist is paused: Mercari can refuse the replacement after the old listing is "
                "deleted, so Anteroom leaves Mercari listings up with their price unchanged."),
}
# A paused marketplace logs why at most once a day. The row neither counts toward the
# daily cap nor starts the retry wait, since nothing was tried.
PAUSED_STATUS = "skipped_paused"
PAUSED_LOG_INTERVAL = timedelta(hours=24)


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


def has_due_relist_rules(db: Session, moment: datetime | None = None) -> bool:
    """Return True when any enabled rule is due for a scheduled sweep."""
    now = _as_naive_utc(moment) or _now()
    cutoff = now - AUTO_RELIST_TICK_INTERVAL
    return (
        db.query(MarketplaceRelistRule.id)
        .filter(
            MarketplaceRelistRule.enabled.is_(True),
            (MarketplaceRelistRule.last_run_at.is_(None))
            | (MarketplaceRelistRule.last_run_at <= cutoff),
        )
        .first()
        is not None
    )


def _relisted_last_24h(db: Session, marketplace: str, moment: datetime) -> int:
    since = moment - timedelta(hours=24)
    return (
        db.query(MarketplaceRelistLog)
        .filter(
            MarketplaceRelistLog.marketplace == marketplace,
            MarketplaceRelistLog.status.in_(CAP_COUNTED_STATUSES),
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
        "relisted_today": relisted_today,
        "remaining_today": max(DEFAULT_DAILY_CAP - relisted_today, 0),
        "last_run_at": None,
        "paused_reason": REPLACEMENT_UNVERIFIED.get(marketplace),
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
        "relisted_today": done,
        "remaining_today": max(int(row.daily_cap) - done, 0),
        "last_run_at": _iso(row.last_run_at),
        "paused_reason": REPLACEMENT_UNVERIFIED.get(row.marketplace),
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
        row.updated_at = _now()
        db.commit()
    db.refresh(row)
    return rule_view(db, row)


# Poshmark and Mercari take whole-dollar prices only.
WHOLE_DOLLAR_RELIST = frozenset({"poshmark", "mercari"})


def compute_relist_price(current_price: float | None, rule: MarketplaceRelistRule) -> float | None:
    """Compute adjusted price respecting the seller's floor without ever raising the price."""
    if current_price is None or current_price <= 0 or not rule.price_adjust_mode or not rule.price_adjust_value:
        return current_price
    floor = float(rule.price_floor) if rule.price_floor is not None else 1.0
    floor = max(floor, 1.0)
    if current_price <= floor:
        return round(current_price, 2)
    if rule.price_adjust_mode == "percent":
        next_price = current_price * (1.0 - float(rule.price_adjust_value) / 100.0)
    else:
        next_price = current_price - float(rule.price_adjust_value)
    next_price = round(min(max(next_price, floor), current_price), 2)
    if rule.marketplace in WHOLE_DOLLAR_RELIST:
        # Round the drop down so it is still a drop, but never under the floor.
        whole = math.floor(next_price)
        if whole < floor:
            whole = math.ceil(floor)
        return float(min(whole, current_price))
    return next_price


def is_due_for_relist(marketplace: str, listed_at: datetime | None, rule: MarketplaceRelistRule, now: datetime) -> bool:
    stamp = _as_naive_utc(listed_at)
    if stamp is None:
        return False
    age = now - stamp
    if age < timedelta(0):
        return False
    if marketplace == "ebay":
        before_mins = int(rule.ebay_recreate_before_gtc_minutes or DEFAULT_EBAY_GTC_MINUTES)
        threshold = EBAY_GTC_CYCLE - timedelta(minutes=before_mins)
        if age < threshold:
            return False
        # Match every 30-day GTC renewal cycle (29d23h30m, 59d23h30m, ...), not only the first cycle.
        return (age % EBAY_GTC_CYCLE) >= threshold
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


def _sql_age_cutoff(rule: MarketplaceRelistRule, moment: datetime) -> datetime:
    if rule.marketplace == "ebay":
        before_mins = int(rule.ebay_recreate_before_gtc_minutes or DEFAULT_EBAY_GTC_MINUTES)
        return moment - (EBAY_GTC_CYCLE - timedelta(minutes=before_mins))
    return moment - timedelta(days=max(int(rule.min_age_days or MIN_AGE_DAYS_STANDARD), MIN_AGE_DAYS_STANDARD))


def _eligible_candidates(
    db: Session,
    rule: MarketplaceRelistRule,
    moment: datetime,
    *,
    conv_id: str | None = None,
    respect_backoff: bool = True,
    preview: bool = False,
) -> list[dict[str, Any]]:
    remaining = 0
    if not preview:
        done_today = _relisted_last_24h(db, rule.marketplace, moment)
        remaining = max(min(int(rule.daily_cap), MAX_DAILY_CAP) - done_today, 0)
        if remaining <= 0:
            return []

    age_cutoff = _sql_age_cutoff(rule, moment)
    query = (
        db.query(MarketplaceListing, Conversation)
        .select_from(Conversation)
        .outerjoin(
            MarketplaceListing,
            (MarketplaceListing.conversation_id == Conversation.id)
            & (MarketplaceListing.marketplace == rule.marketplace),
        )
        .filter(
            Conversation.status != "sold",
            (
                (
                    MarketplaceListing.id.isnot(None)
                    & MarketplaceListing.state.in_(LIVE_STATES)
                    & (MarketplaceListing.listing_id.isnot(None) | MarketplaceListing.listing_url.isnot(None))
                    & ((MarketplaceListing.listed_at.is_(None)) | (MarketplaceListing.listed_at <= age_cutoff))
                )
                | (
                    (
                        MarketplaceListing.id.is_(None)
                        | (
                            MarketplaceListing.state.in_(("not_saved", *LIVE_STATES))
                            & MarketplaceListing.listing_id.is_(None)
                            & MarketplaceListing.listing_url.is_(None)
                        )
                    )
                    & Conversation.notes.isnot(None)
                    & (Conversation.notes != "")
                )
            ),
        )
    )
    if conv_id is not None:
        query = query.filter(Conversation.id == conv_id)
    rows = query.all()
    if not rows:
        return []

    matched_conv_ids = [conv.id for _ml, conv in rows]
    excluded_convs: set[str] = {
        excluded_id
        for row in db.query(MarketplaceSale.conversation_id)
        .filter(
            MarketplaceSale.conversation_id.in_(matched_conv_ids),
            MarketplaceSale.order_status.in_((*PAID_STATUSES, "cancellation_pending")),
        )
        .all()
        if (excluded_id := row[0]) is not None
    }
    in_backoff = set()
    if respect_backoff:
        backoff_since = moment - RELIST_RETRY_BACKOFF
        in_backoff = {
            excluded_id
            for row in db.query(MarketplaceRelistLog.conversation_id)
            .filter(
                MarketplaceRelistLog.marketplace == rule.marketplace,
                MarketplaceRelistLog.conversation_id.in_(matched_conv_ids),
                MarketplaceRelistLog.status.in_(BACKOFF_STATUSES),
                MarketplaceRelistLog.created_at >= backoff_since,
            )
            .all()
            if (excluded_id := row[0]) is not None
        }
        if not preview:
            excluded_convs.update(in_backoff)
    if rule.marketplace == "ebay":
        cycle_since = moment - EBAY_CYCLE_DEDUPE_WINDOW
        excluded_convs.update(
            excluded_id
            for row in db.query(MarketplaceRelistLog.conversation_id)
            .filter(
                MarketplaceRelistLog.marketplace == "ebay",
                MarketplaceRelistLog.conversation_id.in_(matched_conv_ids),
                MarketplaceRelistLog.status == "completed",
                MarketplaceRelistLog.created_at >= cycle_since,
            )
            .all()
            if (excluded_id := row[0]) is not None
        )

    eligible_rows = [(ml, conv) for ml, conv in rows if conv.id not in excluded_convs]
    if not eligible_rows:
        return []
    eligible_convs = [conv for _ml, conv in eligible_rows]
    conv_ids = [conv.id for conv in eligible_convs]

    known_by_conv = listings_for(db, eligible_convs)
    listed_index = None
    listings_by_conv = {row.conversation_id: row for row in db.query(Listing).filter(Listing.conversation_id.in_(conv_ids)).all()}
    rev_ids = [row.current_revision_id for row in listings_by_conv.values() if row.current_revision_id]
    revs_by_id = {row.id: row for row in db.query(ListingRevision).filter(ListingRevision.id.in_(rev_ids)).all()} if rev_ids else {}

    candidates: list[dict[str, Any]] = []
    for ml, conv in eligible_rows:
        entry = (known_by_conv.get(conv.id) or {}).get(rule.marketplace) or {}
        if entry.get("sold_here") or entry.get("state") not in (None, "live", "failed") or not entry.get("listing_id"):
            continue
        selected_id = str(entry["listing_id"])
        ml_id = (
            str(ml.listing_id)
            if (ml is not None and ml.listing_id)
            else (extract_listing_id(rule.marketplace, ml.listing_url) if (ml is not None and ml.listing_url) else None)
        )
        matched_ml = ml if (ml is not None and ml_id == selected_id) else None
        if matched_ml is not None and matched_ml.listed_at is not None:
            listed_at = matched_ml.listed_at
        else:
            if listed_index is None:
                listed_index = load_listed_index(db)
            listed_at, _src = resolve_listed_at(
                listed_index, conv, [matched_ml] if matched_ml is not None else [], {rule.marketplace},
            )
        if not is_due_for_relist(rule.marketplace, listed_at, rule, moment):
            continue

        listing_row = listings_by_conv.get(conv.id)
        rev = revs_by_id.get(listing_row.current_revision_id) if (listing_row and listing_row.current_revision_id) else None
        data = dict(rev.listing_json) if (rev and isinstance(rev.listing_json, dict)) else {}
        notes = parse_notes(conv.notes)
        raw_labels = notes.get("vendooLabels") or ""
        labels = [part.strip() for part in str(raw_labels).split(",") if part.strip()]
        obs = (
            matched_ml.inventory_observation
            if (matched_ml is not None and isinstance(matched_ml.inventory_observation, dict))
            else {}
        )
        likes = obs.get("likes") if isinstance(obs.get("likes"), int) else obs.get("watchers")
        # Start from what this marketplace shows, never the General price: with
        # pricing rules on, the two differ.
        price_raw = (
            matched_ml.listed_price
            if (matched_ml is not None and matched_ml.listed_price is not None)
            else price_for(data, rule.marketplace)
        )
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
        if preview:
            item.update(in_backoff=conv.id in in_backoff, conversation=conv, listing_fields=data)
        candidates.append(item)

    # Oldest listed items go first, bounded by daily cap.
    candidates.sort(key=lambda item: item["listed_at"] or datetime.min)
    return candidates if preview else candidates[:remaining]


def preview_status(db: Session, *, marketplace: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Read rule state and advisory readiness without entering the relist runner."""
    from anteroom.services.cloud_sessions import CLOUD_WRITE_MARKETPLACES

    moment = _now()
    markets = (marketplace,) if marketplace else RELIST_MARKETPLACES
    rules = {rule.marketplace: rule for rule in db.query(MarketplaceRelistRule)
             .filter(MarketplaceRelistRule.marketplace.in_(markets)).all()}
    pending_rows = (db.query(MarketplaceRelistLog.marketplace, MarketplaceRelistLog.conversation_id)
                    .filter(MarketplaceRelistLog.marketplace.in_(markets), MarketplaceRelistLog.status == "relist_pending")
                    .all())
    pending = {market: {conv_id for name, conv_id in pending_rows if name == market} for market in markets}
    previews = []
    for market in markets:
        rule = rules.get(market)
        view = rule_view(db, rule, moment) if rule else _default_rule_view(market, _relisted_last_24h(db, market, moment))
        if rule and rule.enabled:
            candidates = _eligible_candidates(db, rule, moment, preview=True)
            details, counts = _preview_candidates(db, rule, view["remaining_today"], candidates, pending[market], limit)
        else:
            details, counts = [], {"within_cap": 0, "over_cap": 0, "retry_backoff": 0, "pending": 0}
        connection = "Signed-in Chrome with Anteroom Bridge"
        if market in CLOUD_WRITE_MARKETPLACES:
            connection = f"Active marketplace cloud session or {connection}"
        if market == "ebay":
            connection = "Connected eBay API account"
        previews.append({
            **view, "candidate_counts": counts, "pending_items": len(pending[market]), "candidates": details,
            "connection_requirement": connection,
        })
    return {"as_of": _iso(moment), "rules": previews, "connection_verified": False, "listing_mode": listing_mode.read(db),
            "note": "Eligibility and listing checks use saved records. The next pass rechecks the rule, listing and connection."}


def _preview_candidates(db: Session, rule: MarketplaceRelistRule, remaining: int, candidates: list[dict],
                        pending: set[str], limit: int) -> tuple[list[dict], dict[str, int]]:
    from anteroom.services import direct_preflight

    market = rule.marketplace
    counts = {"within_cap": 0, "over_cap": 0, "retry_backoff": 0, "pending": 0}
    within_cap, blocked = [], []
    rank = 0
    for item in candidates:
        conv_id = item["conversation_id"]
        if not item["in_backoff"]:
            # The scheduled planner applies its cap before excluding unsettled attempts.
            rank += 1
        state = "within_cap" if rank <= remaining else "over_cap"
        if item["in_backoff"]:
            state = "retry_backoff"
        if conv_id in pending:
            state = "pending"
        counts[state] += 1
        selected = within_cap if state == "within_cap" else blocked
        if len(selected) < limit:
            selected.append((item, state))
    details = []
    for item, state in (within_cap + blocked)[:limit]:
        new_price = compute_relist_price(item["price"], rule)
        detail = {
            "conversation_id": item["conversation_id"], "title": item["title"], "listing_id": item["listing_id"],
            "revision_id": item["revision_id"], "listed_at": _iso(item["listed_at"]),
            "price": item["price"], "proposed_price": new_price, "status": state,
        }
        if state == "within_cap":
            if item["revision_id"] is None:
                gaps = [direct_preflight.gap("listing", "Save the listing's fields before relisting.")]
            else:
                original_data = item["listing_fields"]
                data = original_data
                current_val = price_for(original_data, market)
                price_changes = new_price is not None and (
                    current_val is None or round(float(current_val), 2) != round(new_price, 2)
                )
                if price_changes and new_price is not None:
                    data = _snapshot_with_relist_price(data, market, new_price)
                prepared = direct_preflight.prepare(db, item["conversation"], data, market)
                gaps = direct_preflight.gaps(prepared, market, direct_preflight.Context(db=db))
                if market == "mercari" and (note := _mercari_smart_pricing_disabled_note(original_data, data, future=True)):
                    gaps.append(direct_preflight.gap("mercari_specifics.smartPricing.enabled", note, blocking=False))
            detail.update(local_fields_ready=not direct_preflight.blocking(gaps), listing_gaps=gaps)
        details.append(detail)
    return details, counts


def _mercari_smart_pricing_disabled_note(before: dict, after: dict, *, future: bool = False) -> str | None:
    """Seller-facing explanation when a relist price drop turned Mercari Smart Pricing off."""
    before_smart = (before.get("mercari_specifics") or {}).get("smartPricing") if isinstance(before, dict) else None
    after_smart = (after.get("mercari_specifics") or {}).get("smartPricing") if isinstance(after, dict) else None
    if not (isinstance(before_smart, dict) and before_smart.get("enabled") is True
            and isinstance(after_smart, dict) and after_smart.get("enabled") is False):
        return None
    price_dollars = mercari_whole_dollars(price_for(after, "mercari"))
    min_dollars = mercari_whole_dollars(after_smart.get("minimumPrice"))
    if price_dollars is not None and price_dollars < 2:
        if future:
            return f"Relisting at ${price_dollars} will turn off Mercari Smart Pricing because Mercari requires $2 or more."
        return "Turned off Mercari Smart Pricing because Mercari requires $2 or more."
    if price_dollars is not None and min_dollars is not None:
        if future:
            return (f"Relisting at ${price_dollars} reaches your ${min_dollars} Smart Pricing minimum, so Mercari "
                    f"Smart Pricing will be turned off (your ${min_dollars} minimum stays saved).")
        return (f"Turned off Mercari Smart Pricing because the new price (${price_dollars}) reached its "
                f"${min_dollars} minimum (your ${min_dollars} minimum stays saved).")
    return "Mercari Smart Pricing will be turned off." if future else "Turned off Mercari Smart Pricing."


def _disable_mercari_smart_pricing_at_floor(updated: dict) -> None:
    """Turn Mercari Smart Pricing off when a relist drop reaches or crosses its floor.

    The saved ``minimumPrice`` stays untouched so we never lower the seller's floor
    behind their back; disabling Smart Pricing keeps the relist draft valid on Mercari.
    Any captured ``_native_forms.mercari`` contract is updated in lockstep so toggling
    Smart Pricing off does not trigger a stale-form reload gap in preview or readiness.
    """
    if not mercari_smart_pricing_meets_price(updated):
        return
    specifics = updated.get("mercari_specifics")
    if not (isinstance(specifics, dict) and isinstance(specifics.get("smartPricing"), dict)):
        return
    specifics["smartPricing"]["enabled"] = False
    native_forms = updated.get("_native_forms")
    mercari_contract = native_forms.get("mercari") if isinstance(native_forms, dict) else None
    if isinstance(mercari_contract, dict):
        contract_inputs = mercari_contract.get("inputs")
        if isinstance(contract_inputs, dict) and "mercari_specifics.smartPricing.enabled" in contract_inputs:
            contract_inputs["mercari_specifics.smartPricing.enabled"] = False
        for row in mercari_contract.get("fields") or []:
            if isinstance(row, dict) and (row.get("path") or row.get("key")) == "mercari_specifics.smartPricing.minimumPrice":
                row["required"] = False


def _snapshot_with_relist_price(data: dict, marketplace: str, price: float) -> dict:
    updated = copy.deepcopy(data)
    path = CANONICAL_PRICE[marketplace]
    set_path(updated, path, round(price, 2))
    _write_locks(updated, locked_paths(updated) | {path})
    if marketplace == "mercari":
        _disable_mercari_smart_pricing_at_floor(updated)
    return updated


def _general_with_relist_price(data: dict, marketplace: str, price: float, live_elsewhere: set[str]) -> dict:
    """The listing with the relist price on this marketplace and General.

    The other marketplaces the item is live on keep their price. The ones it is
    not listed on keep following General, so a later cross-list sends the new price.
    """
    others = {market: price_for(data, market) for market in live_elsewhere if market in CANONICAL_PRICE}
    mercari_before = price_for(data, "mercari")
    updated = _snapshot_with_relist_price(data, marketplace, price)
    updated["price"] = round(price, 2)
    held = set()
    for market, before in others.items():
        if before is not None and price_for(updated, market) != before:
            set_path(updated, CANONICAL_PRICE[market], before)
            held.add(CANONICAL_PRICE[market])
    if held:
        _write_locks(updated, locked_paths(updated) | held)
    if marketplace != "mercari" and "mercari" not in live_elsewhere and price_for(updated, "mercari") != mercari_before:
        _disable_mercari_smart_pricing_at_floor(updated)
    return updated


def _prepare_price_revision(
    db: Session, conv_id: str, marketplace: str, new_price: float | None, current_revision_id: str | None,
) -> str | None:
    """Save the relist price as this marketplace's own price and as the General price.

    The relisted marketplace's price is locked, so the pricing rules cannot
    raise it again from the new General price. Every other marketplace the item
    is live on keeps the price it had: one that would move with General is held
    where it was.
    """
    if new_price is None or current_revision_id is None:
        return current_revision_id
    rev = db.get(ListingRevision, current_revision_id)
    if rev is None or not isinstance(rev.listing_json, dict):
        return current_revision_id
    current_val = price_for(rev.listing_json, marketplace)
    if current_val is not None and round(float(current_val), 2) == round(new_price, 2):
        return current_revision_id
    live_elsewhere = {
        market for (market,) in db.query(MarketplaceListing.marketplace).filter(
            MarketplaceListing.conversation_id == conv_id,
            MarketplaceListing.marketplace != marketplace,
            MarketplaceListing.state.in_(LIVE_STATES),
        )
    }
    updated = _general_with_relist_price(rev.listing_json, marketplace, new_price, live_elsewhere)
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


def _restore_price_revision(db: Session, conv_id: str, price_revision_id: str, original_revision_id: str) -> None:
    """Put back the prices from before a relist that never delisted, unless the listing changed since."""
    if price_revision_id == original_revision_id:
        return
    listing_row = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    original = db.get(ListingRevision, original_revision_id)
    if listing_row is None or listing_row.current_revision_id != price_revision_id or original is None:
        return
    listing_persistence.save_revision(
        db,
        conv_id,
        copy.deepcopy(original.listing_json),
        source="auto_relist",
        parent_revision_id=price_revision_id,
        expected_revision_id=price_revision_id,
        commit=True,
    )


def _restore_attempt_price(db: Session, attempt: MarketplaceRelistLog) -> None:
    """Put back the price an unsettled attempt saved, unless the listing changed since."""
    if not attempt.price_revision_id or not attempt.original_revision_id:
        return
    _restore_price_revision(db, attempt.conversation_id, attempt.price_revision_id, attempt.original_revision_id)


async def _restore_price_after_cancel(conv_id: str, price_revision_id: str, original_revision_id: str) -> None:
    """Put the price back for a relist cancelled while deleting; the cancellation still goes on."""
    restore = asyncio.ensure_future(
        in_worker_session(_restore_price_revision, conv_id, price_revision_id, original_revision_id)
    )
    try:
        await asyncio.wait_for(asyncio.shield(restore), RESTORE_PRICE_DEADLINE_S)
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 - reconcile settles the attempt later
        log.warning("Auto-relist could not put back the prices of %s after a cancelled delist", conv_id, exc_info=True)


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
        if (aged_out and reason_text.startswith(PENDING_STAGE_DELETING) and ml is not None
                and ml.state in LIVE_STATES and ml.listing_id == row.old_listing_id
                and not _has_delete_receipt(db, row)):
            # The delete never finished and the listing is still up: nothing was relisted.
            _restore_attempt_price(db, row)
            row.status = "failed"
            row.reason = "The old listing is still live; its delete never finished. Nothing was relisted."
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
    *,
    scheduled: bool = False,
    respect_backoff: bool = True,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    query = db.query(MarketplaceRelistRule).filter(MarketplaceRelistRule.enabled.is_(True))
    market: str | None = None
    if marketplace:
        market = str(marketplace).strip().lower()
        if market not in RELIST_MARKETPLACES:
            raise AutoRelistError("Auto-relist supports Poshmark, eBay, Mercari, and Depop.")
        query = query.filter(MarketplaceRelistRule.marketplace == market)
    if scheduled:
        throttle_cutoff = moment - AUTO_RELIST_TICK_INTERVAL
        query = query.filter(
            (MarketplaceRelistRule.last_run_at.is_(None))
            | (MarketplaceRelistRule.last_run_at <= throttle_cutoff)
        )
    still_pending = _reconcile_pending_relists(db, moment, market)
    rules = query.all()
    if not rules:
        return []
    plan: list[dict[str, Any]] = []
    stamped = False
    for rule in rules:
        candidates = _eligible_candidates(db, rule, moment, respect_backoff=respect_backoff)
        if scheduled and not dry_run:
            rule.last_run_at = moment
            stamped = True
        for item in candidates:
            if (item["conversation_id"], rule.marketplace) in still_pending:
                continue
            new_price = compute_relist_price(item["price"], rule)
            plan.append({
                "rule_id": rule.id,
                "marketplace": rule.marketplace,
                "new_price": new_price,
                **item,
            })
    if stamped:
        db.commit()
    # Oldest first across marketplaces, so one marketplace's backlog cannot use
    # every pass's time budget while another's listings wait.
    plan.sort(key=lambda entry: entry["listed_at"] or datetime.min)
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
    price_revision_id: str | None = None,
    original_revision_id: str | None = None,
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
    if price_revision_id is not None:
        row.price_revision_id = price_revision_id
        row.original_revision_id = original_revision_id
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


def _relist_skip(
    db: Session, rule: MarketplaceRelistRule | None, conv_id: str, moment: datetime,
) -> Literal["marketplace", "listing"] | None:
    """A disabled or capped rule stops this marketplace. A pending attempt skips one listing."""
    if rule is None or not rule.enabled:
        return "marketplace"
    if _relisted_last_24h(db, rule.marketplace, moment) >= min(int(rule.daily_cap), MAX_DAILY_CAP):
        return "marketplace"
    pending = (
        db.query(MarketplaceRelistLog.id)
        .filter(
            MarketplaceRelistLog.conversation_id == conv_id,
            MarketplaceRelistLog.marketplace == rule.marketplace,
            MarketplaceRelistLog.status == "relist_pending",
        )
        .first()
    )
    return "listing" if pending is not None else None


def _shipping_check_skip(
    db: Session, rule_id: str, conv_id: str, moment: datetime,
) -> Literal["marketplace", "listing"] | None:
    return _relist_skip(db, db.get(MarketplaceRelistRule, rule_id), conv_id, moment)


def _reserve_relist_attempt(
    db: Session,
    entry: dict[str, Any],
    moment: datetime,
    readiness_error: str | None = None,
) -> dict[str, Any]:
    """Serialize rule-enabled check, daily-cap reservation, candidate freshness, and revision prep."""
    rule_id = entry["rule_id"]
    conv_id = entry["conversation_id"]
    marketplace = entry["marketplace"]
    old_listing_id = entry["listing_id"]
    old_price = entry["price"]
    new_price = entry["new_price"]
    current_revision_id = entry["revision_id"]
    with listing_mode.guard(db, "direct"):
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

        if _relist_skip(db, rule, conv_id, moment) is not None:
            return {"reserved": False, "outcome": None}

        # Re-check only this single item under the rule lock instead of scanning the catalog.
        candidates = _eligible_candidates(db, rule, moment, conv_id=conv_id, respect_backoff=False)
        candidate = candidates[0] if candidates else None
        if (
            candidate is None
            or candidate["listing_id"] != old_listing_id
            or candidate["revision_id"] != current_revision_id
            or compute_relist_price(candidate["price"], rule) != new_price
        ):
            return {"reserved": False, "outcome": None}

        price_error = poshmark_fractional_price_error(new_price) if marketplace == "poshmark" else None
        if (paused := REPLACEMENT_UNVERIFIED.get(marketplace)) is not None:
            return {"reserved": False, "outcome": _record_pause(db, entry, paused, moment)}

        preflight_error = readiness_error or price_error
        revision_id = None if preflight_error else _prepare_price_revision(
            db, conv_id, marketplace, new_price, current_revision_id,
        )
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
                reason=preflight_error or "Listing has no saved revision to relist.",
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
            price_revision_id=revision_id,
            original_revision_id=current_revision_id,
        )
        completion_note = None
        if revision_id != current_revision_id and current_revision_id is not None:
            orig_rev = db.get(ListingRevision, current_revision_id)
            prep_rev = db.get(ListingRevision, revision_id)
            if orig_rev is not None and prep_rev is not None and isinstance(orig_rev.listing_json, dict) and isinstance(prep_rev.listing_json, dict):
                completion_note = _mercari_smart_pricing_disabled_note(orig_rev.listing_json, prep_rev.listing_json)
        return {"reserved": True, "attempt_id": attempt_id, "revision_id": revision_id,
                "original_revision_id": current_revision_id, "completion_note": completion_note}


def _record_pause(db: Session, entry: dict[str, Any], reason: str, moment: datetime) -> dict[str, Any]:
    """Log that a paused marketplace left this listing up, at most once a day.

    Nothing is deleted, priced or sent. A pass within the interval reports the
    reason without writing another row.
    """
    marketplace = entry["marketplace"]
    outcome = {
        "conversation_id": entry["conversation_id"], "marketplace": marketplace,
        "old_listing_id": entry["listing_id"], "new_listing_id": None, "status": PAUSED_STATUS, "reason": reason,
    }
    logged = (
        db.query(MarketplaceRelistLog.id)
        .filter(MarketplaceRelistLog.marketplace == marketplace, MarketplaceRelistLog.status == PAUSED_STATUS,
                MarketplaceRelistLog.created_at > moment - PAUSED_LOG_INTERVAL)
        .first()
    )
    if logged is not None:
        return outcome
    return _record_log(
        db, conv_id=entry["conversation_id"], marketplace=marketplace, old_listing_id=entry["listing_id"],
        new_listing_id=None, old_price=entry["price"], new_price=entry["price"], status=PAUSED_STATUS,
        reason=reason, moment=moment,
    )


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


async def run_auto_relist(
    *,
    now: datetime | None = None,
    marketplace: str | None = None,
    dry_run: bool = False,
    scheduled: bool = False,
    respect_backoff: bool = True,
    delister=None,
    lister=None,
    time_budget: timedelta | None = None,
) -> list[dict[str, Any]]:
    """Run per-marketplace unattended auto-relist / eBay auto-recreate for opted-in marketplaces.

    Releases DB connections before every external marketplace/extension await.
    Once ``time_budget`` is spent, no further relist starts; the next pass takes
    the rest, oldest first.
    """
    moment = _as_naive_utc(now) or _now()
    plan = await in_worker_session(
        lambda db: _load_enabled_rules_and_candidates(
            db,
            moment,
            marketplace,
            scheduled=scheduled,
            respect_backoff=respect_backoff,
            dry_run=dry_run,
        )
    )
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
                "status": "dry_run",
                "reason": REPLACEMENT_UNVERIFIED.get(entry["marketplace"]),
            }
            for entry in plan
        ]

    async def _run_delist(
        conv_id: str, marketplace: str, expected_listing_id: str, rule_id: str, revision_id: str
    ) -> dict[str, Any]:
        if marketplace in REPLACEMENT_UNVERIFIED:
            return {"ok": False, "error": REPLACEMENT_UNVERIFIED[marketplace]}
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
                expected_revision_id=revision_id if marketplace == "depop" else None,
            )
        except DelistError as exc:
            if exc.reason == "relist_rule_disabled":
                return {"ok": False, "skipped": True, "error": str(exc)}
            return {"ok": False, "error": str(exc), "error_code": exc.reason}
        entry_res = res.get(marketplace) or {}
        return {
            "ok": entry_res.get("state") == "deleted",
            "error": entry_res.get("error"),
            "error_code": entry_res.get("error_code"),
        }

    async def _run_list(
        conv_id: str, marketplace: str, revision_id: str, attempt_id: str, rule_id: str
    ) -> dict[str, Any]:
        if lister is not None:
            if not await in_worker_session(_rule_enabled, rule_id):
                return {"ok": False, "skipped": True, "error": RULE_DISABLED_AFTER_DELETE}
            return await lister(conv_id, marketplace, revision_id)
        from anteroom.services.automation_actions import IDLE_TIMEOUT_S, _turn, _wait_until_free, wait_for_native_result
        from anteroom.services.native_marketplaces import BUSY, ListRequest, NativeMarketplaceError, list_now

        try:
            async with _turn(marketplace):
                loop = asyncio.get_running_loop()
                deadline = loop.time() + IDLE_TIMEOUT_S
                while loop.time() < deadline and await _wait_until_free(marketplace, deadline=deadline):
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
                        if exc.reason != BUSY:
                            return {"ok": False, "error": str(exc)}
                        # The job gate refused before any marketplace write.
                        await asyncio.sleep(0.5)
                    else:
                        break
                else:
                    return {"ok": False, "error": f"The {marketplace} publish slot stayed busy after delisting."}
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

    from anteroom.services import process_handoff
    from anteroom.services.native_marketplaces import NativeMarketplaceError, check_depop_relist_shipping

    outcomes: list[dict[str, Any]] = []
    blocked_markets: set[str] = set()
    started = time.monotonic()
    for index, entry in enumerate(plan):
        if process_handoff.draining():
            log.info("Auto-relist stops for this Machine's shutdown; %d due listings wait for the next one.", len(plan) - index)
            break
        if time_budget is not None and index and time.monotonic() - started >= time_budget.total_seconds():
            log.info("Auto-relist pass used its %s; %d due listings wait for the next pass.", time_budget, len(plan) - index)
            break
        conv_id = entry["conversation_id"]
        market = entry["marketplace"]
        if market in blocked_markets:
            continue
        old_id = entry["listing_id"]
        rule_id = entry["rule_id"]
        step_now = moment if now is not None else _now()

        # A shipping read can be cancelled without leaving a pending delete or new price.
        shipping_error = None
        if delister is None and market == "depop" and entry["revision_id"]:
            skip = await in_worker_session(_shipping_check_skip, rule_id, conv_id, step_now)
            if skip is not None:
                if skip == "marketplace":
                    blocked_markets.add(market)
                continue
            try:
                await check_depop_relist_shipping(conv_id, entry["revision_id"], entry["new_price"])
            except NativeMarketplaceError as exc:
                shipping_error = str(exc)
                if exc.reason in DELIST_BLOCKERS:
                    blocked_markets.add(market)

        # Atomically lock the rule, re-check rule.enabled, daily cap and freshness, prepare revision,
        # and commit a relist_pending row before dispatching either external write.
        reservation = await in_worker_session(_reserve_relist_attempt, entry, step_now, shipping_error)
        if not reservation["reserved"]:
            if reservation.get("outcome") is not None:
                outcomes.append(reservation["outcome"])
                if market in REPLACEMENT_UNVERIFIED:
                    # One refusal per pass says why; the rest of this marketplace waits.
                    blocked_markets.add(market)
            continue

        attempt_id = reservation["attempt_id"]
        revision_id = reservation["revision_id"]

        try:
            delist_res = await _run_delist(conv_id, market, old_id, rule_id, revision_id)
        except asyncio.CancelledError:
            # Nothing was relisted. Reconcile settles the attempt once the delete is known.
            await _restore_price_after_cancel(conv_id, revision_id, reservation["original_revision_id"])
            raise
        except Exception as exc:  # noqa: BLE001 - any refusal still settles the attempt and its price
            log.warning("Auto-relist delist of %s on %s stopped", conv_id, market, exc_info=True)
            delist_res = {"ok": False, "error": str(getattr(exc, "detail", None) or exc)
                          or "Could not delist the old listing."}
        if not delist_res.get("ok"):
            if delist_res.get("error_code") in DELIST_BLOCKERS:
                blocked_markets.add(market)
            # Nothing was relisted, so the relist price must not stay saved.
            try:
                await in_worker_session(
                    _restore_price_revision, conv_id, revision_id, reservation["original_revision_id"],
                )
            except Exception:  # noqa: BLE001 - record the failed delist and go on with the pass
                log.warning("Auto-relist could not put back the prices of %s after a failed delist", conv_id,
                            exc_info=True)
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
            # The old listing is already gone. Leave the next listing live if
            # this replacement could not be confirmed.
            blocked_markets.add(market)
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
            "reason": reservation.get("completion_note"),
            "moment": moment if now is not None else _now(),
            "new_listing_url": list_res.get("listing_url"),
        }))

    return outcomes
