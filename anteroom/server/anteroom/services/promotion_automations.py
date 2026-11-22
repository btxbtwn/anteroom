"""Seller-enabled scheduled sharing and automatic offers to likers.

Every automation is saved off and runs only after the seller turns it on; any
edit turns it off again. A round goes one listing at a time through
``listing_promotions.review`` and ``perform``, so each send is journaled, sent
at most once, and holds the single automation slot only while it is sent.

A round stops at the marketplace's first limit, browser check or sign-out and
pauses the automation. For Cloud sign-outs, the session expiry transaction
pauses the rule only if the expired session is still current. An unconfirmed result pauses it too, and that listing
stays blocked until the seller checks it. Offers never cross the listing's
saved individual minimum and optional shared minimum, and go only to listings
with new likers since the last offer. Nothing
here lists, publishes or delists.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from sqlalchemy import CursorResult, func, or_, update
from sqlalchemy.orm import Session

from anteroom.database import SessionLocal, claim_in_worker_session, in_worker_session, run_worker_db_step
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.listing_promotion import (
    ListingPromotion,
    MarketplaceOfferFloor,
)
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.promotion_automation import PromotionAutomation, PromotionRun
from anteroom.repositories.queries import JobRepo
from anteroom.services import listing_promotions as promotions
from anteroom.services import offer_preview_refusals
from anteroom.services.automation_age import load_listed_index, resolve_listed_at
from anteroom.services.automation_schedule import (
    ScheduleError,
    as_utc,
    next_after,
    normalize_schedule,
    require_timezone,
)
from anteroom.services.ebay_api import dollars, money_cents
from anteroom.services.listing_promotions import NAMES, STOP_CODES, PromotionError
from anteroom.services.listing_result import scrub
from anteroom.services.marketplace_delist import listings_for

log = logging.getLogger("anteroom.promotion_automations")

# Pause between shares, before Poshmark's own jitter of up to 3 seconds.
SHARE_SPEEDS = {"slow": 15000, "normal": 7000, "fast": 4000}
SHARE_ORDERS = ("newest", "oldest", "random")
SHARE_MODES = ("self", "party", "community")
HOURLY_INTENSITIES = ("none", "low", "medium", "high")
INTENSITY_GAP_MULTIPLIER = {"high": 1.0, "medium": 1.5, "low": 2.25}
# Poshmark's daily Posh Party windows in Pacific Time (America/Los_Angeles).
POSH_PARTY_TIMES_PT = ("09:00", "12:00", "16:00", "19:00")
POSH_PARTY_HOURS_PT = (9, 12, 16, 19)
POSH_PARTY_DURATION_HOURS = 2
# Peak-hour defaults for the 24-hour Poshmark sharing intensity schedule.
DEFAULT_HOURLY_SCHEDULE: dict[str, str] = {
    **{str(h): "none" for h in range(0, 7)},
    "7": "low",
    "8": "medium",
    **{str(h): "high" for h in (9, 10, 11, 12, 13, 14, 18, 19, 20, 21)},
    **{str(h): "medium" for h in (15, 16, 17, 22)},
    "23": "low",
}
# Safety ceiling for Poshmark sharing; Poshmark stops sooner if it wants to.
MAX_DAILY_SHARES = 8000
MAX_DAILY_FOLLOWS = 1000
MAX_DAILY_UNFOLLOWS = 1000
MAX_TIMES = 6
MAX_LISTED_ITEMS = 500
OFFER_INTERVAL = timedelta(minutes=15)
FOLLOW_INTERVAL = timedelta(hours=1)
CLOUD_DISCONNECTED = "Paused: Reconnect the marketplace cloud session to resume."
# Listing reads per offer check, spaced out; the rest are read in a later check.
OFFER_READS = 60
READ_GAP_S = 2.0
# A listing whose last check found nobody to offer to is not read again until a
# new like is reported or its marketplace's re-check time passes. Poshmark and
# Depop report new likes in the feed check_new_likes reads every minute, so
# their re-check only finds likes that fell out of it (Chrome offline). Mercari
# has no such feed, so its daily re-check is how its new likes are found.
LIKE_RECHECK = {"poshmark": timedelta(days=7), "depop": timedelta(days=7), "mercari": timedelta(days=1)}
WAITING_FOR_LIKES = "no new likes since the last check"
HELD_AFTER_REFUSAL = "refused at the last check; checked again a day later"
# More held listings than this, and over half of a rule's listings, are not held.
BROAD_REFUSALS = 3
TICK_S = 60.0
# How long a due round waits for Chrome to reconnect, e.g. after a restart,
# before it is recorded as unable to run.
CHROME_GRACE = timedelta(minutes=10)
INTERRUPTED = "Anteroom stopped during this round."
_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def active_posh_party_window(moment: datetime | None = None) -> str | None:
    """Return the active PT Posh Party slot ('09:00', '12:00', '16:00', '19:00') if now is inside one."""
    from zoneinfo import ZoneInfo

    current_utc = as_utc(moment or _now())
    local_pt = current_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    for start_hour, label in zip(POSH_PARTY_HOURS_PT, POSH_PARTY_TIMES_PT, strict=True):
        if start_hour <= local_pt.hour < start_hour + POSH_PARTY_DURATION_HOURS:
            return label
    return None


def hourly_intensity_for(hourly_schedule: dict | None, timezone_name: str | None, moment: datetime | None = None) -> str:
    """Return 'none' | 'low' | 'medium' | 'high' for the current local hour."""
    if not hourly_schedule:
        return "high"
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name or "UTC")
    local_hour = str(as_utc(moment or _now()).astimezone(tz).hour)
    level = str(hourly_schedule.get(local_hour) or "none").lower()
    return level if level in HOURLY_INTENSITIES else "none"


def _normalize_share_modes(raw: list[str] | None) -> list[str]:
    if not raw:
        return ["self"]
    modes: list[str] = []
    for mode in raw:
        item = str(mode or "").strip().lower()
        if item not in SHARE_MODES:
            raise PromotionError(400, f"Unsupported share mode: {mode}. Choose self, party, or community.")
        if item not in modes:
            modes.append(item)
    return modes or ["self"]


def _normalize_hourly_schedule(raw: dict | None) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PromotionError(400, "Hourly schedule must map hours 0–23 to none, low, medium, or high.")
    cleaned: dict[str, str] = {}
    for hour in range(24):
        key = str(hour)
        level = str(raw.get(key, raw.get(hour, "none")) or "none").strip().lower()
        if level not in HOURLY_INTENSITIES:
            raise PromotionError(400, f"Invalid hourly intensity '{level}' for hour {key}.")
        cleaned[key] = level
    return cleaned


def _normalize_follow_config(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        raise PromotionError(400, "Configure at least one Poshmark follow or unfollow option.")
    reciprocal = bool(raw.get("reciprocal_24h", False))
    new_cap = int(raw.get("new_active_sellers_cap") or 0)
    unfollow_cap = int(raw.get("unfollow_cap") or 0)
    unsub_live = bool(raw.get("unsubscribe_live_shows", False))
    if not 0 <= new_cap <= MAX_DAILY_FOLLOWS:
        raise PromotionError(400, f"New active seller follows cap must be 0 to {MAX_DAILY_FOLLOWS:,}/day.")
    if not 0 <= unfollow_cap <= MAX_DAILY_UNFOLLOWS:
        raise PromotionError(400, f"Unfollow cap must be 0 to {MAX_DAILY_UNFOLLOWS:,}/day.")
    if not (reciprocal or new_cap > 0 or unfollow_cap > 0 or unsub_live):
        raise PromotionError(400, "Enable reciprocal follows, active seller follows, unfollows, or Live Show unsubscribe.")
    return {
        "reciprocal_24h": reciprocal,
        "new_active_sellers_cap": new_cap,
        "unfollow_cap": unfollow_cap,
        "unsubscribe_live_shows": unsub_live,
    }


def run_out(run: PromotionRun) -> dict:
    return {"id": run.id, "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
            "sent": run.sent, "skipped": run.skipped, "failed": run.failed, "note": run.note}


def out(db: Session, row: PromotionAutomation) -> dict:
    last = db.query(PromotionRun).filter_by(automation_id=row.id).order_by(PromotionRun.started_at.desc()).first()
    return {
        "id": row.id, "marketplace": row.marketplace, "action": row.action, "name": row.name, "enabled": row.enabled,
        "scope": row.scope, "conversation_ids": row.conversation_ids or [], "excluded_ids": row.excluded_ids or [],
        "min_days_listed": row.min_days_listed, "share_order": row.share_order, "speed": row.speed,
        "daily_cap": row.daily_cap, "times": row.times or [], "timezone": row.timezone,
        "share_modes": row.share_modes or (["self"] if row.action == "share" else []),
        "hourly_schedule": row.hourly_schedule,
        "discount_percent": row.discount_percent, "min_likes": row.min_likes, "shipping_label": row.shipping_label,
        "minimum_price": dollars(row.minimum_price_cents) if row.minimum_price_cents is not None else None,
        "waterfall_steps": row.waterfall_steps or [],
        "bundle_pause_minutes": row.bundle_pause_minutes,
        "bundle_min_items": row.bundle_min_items,
        "bundle_comment": row.bundle_comment,
        "follow_config": row.follow_config,
        "paused_reason": row.paused_reason, "next_run_at": _iso(row.next_run_at) if row.enabled else None,
        "last_run": run_out(last) if last else None, "available": promotions.available(row.marketplace, row.action),
    }


def _ids(value, label: str) -> list[str]:
    ids = list(dict.fromkeys(str(item) for item in value or []))
    if len(ids) > MAX_LISTED_ITEMS:
        raise PromotionError(400, f"Choose at most {MAX_LISTED_ITEMS} {label}.")
    return ids


def _default_name(marketplace: str, action: str) -> str:
    labels = {
        "share": "sharing",
        "offer": "offers to likers",
        "bundle_offer": "private bundle offers",
        "follow": "automatic follows",
    }
    return f"{NAMES[marketplace]} {labels.get(action, action)}"


def _validate(body: dict) -> dict:
    marketplace, action = body.get("marketplace") or "", body.get("action")
    if action not in promotions.SUPPORTED.get(marketplace, frozenset()):
        raise PromotionError(400, "Anteroom cannot do this on that marketplace.")
    assert isinstance(marketplace, str)
    scope = body.get("scope") or "all"
    if scope not in ("all", "items"):
        raise PromotionError(400, "Cover all live listings or the items you choose.")
    fields: dict = {
        "marketplace": marketplace, "action": action, "scope": scope,
        "name": str(body.get("name") or "").strip()[:80] or _default_name(marketplace, action),
        "conversation_ids": _ids(body.get("conversation_ids"), "items") if scope == "items" else [],
        "excluded_ids": _ids(body.get("excluded_ids"), "exclusions"),
        "min_days_listed": int(body.get("min_days_listed") or 0),
        "share_order": None, "speed": None, "daily_cap": None, "times": None, "timezone": None,
        "share_modes": None, "hourly_schedule": None,
        "discount_percent": None, "min_likes": None, "shipping_label": None, "minimum_price_cents": None,
        "waterfall_steps": None, "bundle_pause_minutes": None, "bundle_min_items": None, "bundle_comment": None,
        "follow_config": None,
    }
    if scope == "items" and action != "follow" and not fields["conversation_ids"]:
        raise PromotionError(400, "Choose at least one item.")
    if not 0 <= fields["min_days_listed"] <= 365:
        raise PromotionError(400, "Minimum days listed is 0 to 365.")
    if action == "share":
        order, speed, cap = body.get("share_order") or "newest", body.get("speed") or "normal", body.get("daily_cap")
        if order not in SHARE_ORDERS or speed not in SHARE_SPEEDS:
            raise PromotionError(400, "Choose an order and a speed.")
        if not isinstance(cap, int) or not 1 <= cap <= MAX_DAILY_SHARES:
            raise PromotionError(400, f"The daily cap is 1 to {MAX_DAILY_SHARES:,} shares.")
        share_modes = _normalize_share_modes(body.get("share_modes"))
        hourly_schedule = _normalize_hourly_schedule(body.get("hourly_schedule"))
        try:
            times = sorted({normalize_schedule({"kind": "daily", "time": str(value)})["time"] for value in body.get("times") or []})
            timezone = str(body.get("timezone") or "UTC")
            require_timezone(timezone)
        except ScheduleError as exc:
            raise PromotionError(400, str(exc)) from exc
        if hourly_schedule is None and not 1 <= len(times) <= MAX_TIMES:
            raise PromotionError(400, f"Choose 1 to {MAX_TIMES} round times.")
        if hourly_schedule is not None and not any(v != "none" for v in hourly_schedule.values()):
            raise PromotionError(400, "Set at least one hour above 'none' in the 24-hour schedule.")
        fields.update(
            share_order=order, speed=speed, daily_cap=cap, times=times, timezone=timezone,
            share_modes=share_modes, hourly_schedule=hourly_schedule,
        )
    elif action == "follow":
        follow_cfg = _normalize_follow_config(body.get("follow_config"))
        try:
            raw_times = body.get("times") or []
            times = sorted({normalize_schedule({"kind": "daily", "time": str(value)})["time"] for value in raw_times})
            timezone = str(body.get("timezone") or "UTC")
            require_timezone(timezone)
        except ScheduleError as exc:
            raise PromotionError(400, str(exc)) from exc
        if len(times) > MAX_TIMES:
            raise PromotionError(400, f"Choose at most {MAX_TIMES} follow round times.")
        fields.update(
            scope="all",
            conversation_ids=[],
            times=times or None,
            timezone=timezone,
            follow_config=follow_cfg,
            daily_cap=max(follow_cfg["new_active_sellers_cap"], follow_cfg["unfollow_cap"], 1),
        )
    else:
        waterfall_steps = promotions.normalize_waterfall_steps(body.get("waterfall_steps"))
        discount = waterfall_steps[0]["discount_percent"] if waterfall_steps else body.get("discount_percent")
        likes = body.get("min_likes") or 1
        if not isinstance(discount, int) or not 1 <= discount <= 99:
            raise PromotionError(400, "Enter the discount for the offer.")
        if not isinstance(likes, int) or not 1 <= likes <= 1000:
            raise PromotionError(400, "Minimum likes is 1 to 1,000.")
        label = str(body.get("shipping_label") or "").strip()[:80] or None
        if label and marketplace not in promotions.SHIPPING_DISCOUNTS:
            raise PromotionError(400, f"{NAMES[marketplace]} offers carry no shipping discount.")
        try:
            minimum = money_cents(body["minimum_price"]) if body.get("minimum_price") not in (None, "") else None
        except ValueError as exc:
            raise PromotionError(400, "Enter a positive minimum price with at most two decimal places, or leave it blank.") from exc
        fields.update(
            discount_percent=discount,
            min_likes=likes,
            shipping_label=label,
            minimum_price_cents=minimum,
            waterfall_steps=waterfall_steps,
        )
        if action == "bundle_offer":
            pause_min = body.get("bundle_pause_minutes") if body.get("bundle_pause_minutes") is not None else 15
            min_items = body.get("bundle_min_items") if body.get("bundle_min_items") is not None else 1
            if type(pause_min) is not int or not 1 <= pause_min <= 1440:
                raise PromotionError(400, "Bundle activity pause must be 1 to 1,440 minutes.")
            if type(min_items) is not int or not 1 <= min_items <= 20:
                raise PromotionError(400, "Minimum bundle items must be 1 to 20.")
            comment = str(body.get("bundle_comment") or "").strip()[:500] or None
            fields.update(bundle_pause_minutes=pause_min, bundle_min_items=min_items, bundle_comment=comment)
    return fields


def _get(db: Session, automation_id: str) -> PromotionAutomation:
    row = db.get(PromotionAutomation, automation_id)
    if not row:
        raise PromotionError(404, "This automation was not found.")
    return row


def save(db: Session, body: dict, automation_id: str | None = None) -> dict:
    """Create or edit an automation. It is saved off: the seller turns it on separately."""
    fields = _validate(body)
    row = _get(db, automation_id) if automation_id else PromotionAutomation(id=new_id())
    if automation_id and (row.marketplace, row.action) != (fields["marketplace"], fields["action"]):
        raise PromotionError(400, "Create a new automation for another marketplace or action.")
    for key, value in fields.items():
        setattr(row, key, value)
    row.enabled, row.paused_reason, row.next_run_at = False, None, None
    db.add(row)
    db.commit()
    return out(db, row)


def delete(db: Session, automation_id: str) -> None:
    db.delete(_get(db, automation_id))
    db.commit()


def next_share_slot(row: PromotionAutomation, after: datetime) -> datetime:
    if row.hourly_schedule:
        # Advance to the next top-of-hour whose configured intensity is not 'none'.
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(row.timezone or "UTC")
        cursor = as_utc(after).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for _ in range(48):
            local_hour = str(cursor.astimezone(tz).hour)
            if str(row.hourly_schedule.get(local_hour) or "none").lower() != "none":
                return cursor.astimezone(UTC).replace(tzinfo=None)
            cursor += timedelta(hours=1)
    slots = [next_after({"kind": "daily", "time": clock}, row.timezone or "UTC", as_utc(after)) for clock in row.times or []]
    valid = [slot for slot in slots if slot]
    if not valid:
        return (as_utc(after) + timedelta(hours=1)).astimezone(UTC).replace(tzinfo=None)
    return min(valid).astimezone(UTC).replace(tzinfo=None)


def enable(db: Session, automation_id: str) -> dict:
    """Turn one automation on. Nothing is sent now: the first round waits for its time."""
    row = _get(db, automation_id)
    if not promotions.available(row.marketplace, row.action):
        raise PromotionError(409, f"Anteroom cannot send {NAMES[row.marketplace]} offers yet: its offer request still needs "
                                  "to be read from the signed-in site.")
    now = _now()
    _schedule_enabled(row, now)
    db.commit()
    return out(db, row)


def _schedule_enabled(row: PromotionAutomation, now: datetime) -> None:
    if row.action == "share":
        row.next_run_at = next_share_slot(row, now)
    elif row.action == "follow":
        row.next_run_at = next_share_slot(row, now) if row.times else now + FOLLOW_INTERVAL
    else:
        row.next_run_at = now + OFFER_INTERVAL
    row.enabled, row.paused_reason = True, None
    row.like_events_since = now


def pause_for_cloud_disconnect(db: Session, seller: str, marketplace: str) -> None:
    db.execute(update(PromotionAutomation).where(
        PromotionAutomation.user_id == seller,
        PromotionAutomation.marketplace == marketplace,
        PromotionAutomation.enabled.is_(True),
    ).values(enabled=False, paused_reason=CLOUD_DISCONNECTED))


def resume_after_cloud_reconnect(db: Session, seller: str, marketplace: str) -> int:
    rows = db.query(PromotionAutomation).filter(
        PromotionAutomation.user_id == seller,
        PromotionAutomation.marketplace == marketplace,
        PromotionAutomation.enabled.is_(False),
        PromotionAutomation.paused_reason == CLOUD_DISCONNECTED,
    ).with_for_update().all()
    now = _now()
    for row in rows:
        _schedule_enabled(row, now)
    return len(rows)


def pause(db: Session, automation_id: str | None = None, reason: str | None = None) -> int:
    query = db.query(PromotionAutomation).filter(or_(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.paused_reason == CLOUD_DISCONNECTED,
    ))
    if automation_id:
        query = query.filter(PromotionAutomation.id == automation_id)
    # Write both fields even if this session saw the rule disabled before reconnect.
    paused = query.update({"enabled": False, "paused_reason": reason}, synchronize_session="fetch")
    db.commit()
    return paused


def listing(db: Session) -> dict:
    report_overdue(db)
    labels = set()
    for (options,) in db.query(ListingPromotion.shipping_options).filter(
            ListingPromotion.marketplace == "poshmark", ListingPromotion.action.in_(("offer", "bundle_offer"))).order_by(
            ListingPromotion.created_at.desc()).limit(200).all():
        labels.update(option.get("label") for option in options or [] if option.get("label"))
    rows = db.query(PromotionAutomation).order_by(PromotionAutomation.created_at).all()
    return {
        "automations": [out(db, row) for row in rows],
        "supported": {name: sorted(actions) for name, actions in promotions.SUPPORTED.items()},
        "awaiting_capture": {name: sorted(actions) for name, actions in promotions.AWAITING_CAPTURE.items()},
        "speeds": list(SHARE_SPEEDS), "orders": list(SHARE_ORDERS), "share_modes": list(SHARE_MODES),
        "posh_party_times_pt": list(POSH_PARTY_TIMES_PT),
        "default_hourly_schedule": dict(DEFAULT_HOURLY_SCHEDULE),
        "max_daily_cap": MAX_DAILY_SHARES,
        "max_daily_follows": MAX_DAILY_FOLLOWS,
        "max_daily_unfollows": MAX_DAILY_UNFOLLOWS,
        "shipping_labels": sorted(labels),
    }


def _covered(db: Session, row: PromotionAutomation, everything: bool = False) -> list[tuple[Conversation, str, datetime | None]]:
    """Live listings this automation covers (or every live one), with when each was listed."""
    excluded = set() if everything else set(row.excluded_ids or [])
    if row.scope == "items" and not everything:
        # Query rather than db.get: a draft deleted while the like feed was read may still be cached here.
        found = {conv.id: conv for conv in db.query(Conversation).filter(Conversation.id.in_(row.conversation_ids or []))}
        convs = [found[conv_id] for conv_id in row.conversation_ids or [] if conv_id in found]
    else:
        convs = db.query(Conversation).filter(Conversation.status != "sold").all()
    rows_by_item: dict[str, list[MarketplaceListing]] = {}
    for listing_row in db.query(MarketplaceListing).filter_by(marketplace=row.marketplace).all():
        rows_by_item.setdefault(listing_row.conversation_id, []).append(listing_row)
    index = load_listed_index(db)
    convs = [conv for conv in convs if conv.id not in excluded]
    known = listings_for(db, [conv for conv in convs if conv.status != "sold"])
    result = []
    for conv in convs:
        if conv.status == "sold":
            continue
        listing_id, reason = promotions.live_entry(known[conv.id].get(row.marketplace) or {}, row.marketplace)
        if reason:
            continue
        listed_at = resolve_listed_at(index, conv, rows_by_item.get(conv.id, []), {row.marketplace})[0]
        result.append((conv, listing_id, as_utc(listed_at).replace(tzinfo=None) if listed_at else None))
    return result


def coverage(db: Session, automation_id: str) -> dict:
    """Every live listing on the marketplace and whether this automation covers it, read locally."""
    row = _get(db, automation_id)
    floors = {floor.listing_id: floor.floor_cents for floor in db.query(MarketplaceOfferFloor).filter_by(marketplace=row.marketplace)}
    chosen, excluded = set(row.conversation_ids or []), set(row.excluded_ids or [])
    now = _now()
    items = []
    for conv, listing_id, listed_at in _covered(db, row, everything=True)[:MAX_LISTED_ITEMS]:
        days = (now - listed_at).days if listed_at else None
        items.append({"conversation_id": conv.id, "title": conv.title, "listing_id": listing_id, "listed_days": days,
                      "floor": dollars(floors[listing_id]) if listing_id in floors else None,
                      "included": conv.id not in excluded and (row.scope == "all" or conv.id in chosen),
                      "too_new": bool(row.min_days_listed and (days is None or days < row.min_days_listed))})
    included = [item for item in items if item["included"]]
    return {"items": items, "included": len(included)}


def activity(db: Session, automation_id: str, limit: int = 50) -> dict:
    _get(db, automation_id)
    runs = db.query(PromotionRun).filter_by(automation_id=automation_id).order_by(PromotionRun.started_at.desc()).limit(limit).all()
    sends = db.query(ListingPromotion).filter_by(automation_id=automation_id).order_by(ListingPromotion.created_at.desc()).limit(limit).all()
    sends.sort(key=lambda send: send.state not in ("unknown", "sending"))
    return {"runs": [run_out(run) for run in runs], "sends": [promotions.out(send) for send in sends]}


def _shares_today(db: Session, marketplace: str, now: datetime) -> int:
    rows = db.query(
        ListingPromotion.listing_id,
        ListingPromotion.state,
        ListingPromotion.recipient_count,
        ListingPromotion.follow_budget,
    ).filter(
        ListingPromotion.marketplace == marketplace,
        ListingPromotion.action == "share",
        ListingPromotion.sent_at > now - timedelta(hours=24),
    ).all()
    total = 0
    for listing_id, state, recipient_count, follow_budget in rows:
        if listing_id == "__account__":
            if state == "failed":
                if isinstance(follow_budget, dict) and type(follow_budget.get("max_shares")) is int:
                    total += max(0, int(follow_budget["max_shares"]))
                elif recipient_count is not None:
                    total += max(0, int(recipient_count))
            elif recipient_count is not None:
                total += max(0, int(recipient_count))
            elif isinstance(follow_budget, dict) and type(follow_budget.get("max_shares")) is int:
                total += max(0, int(follow_budget["max_shares"]))
            else:
                total += 50
        else:
            total += max(0, int(recipient_count)) if recipient_count is not None else 1
    return total


def _order(db: Session, row: PromotionAutomation, targets: list) -> list:
    if row.action in ("offer", "bundle_offer"):
        # Skipped reviews are journaled too: low-like listings must not keep
        # consuming the bounded scan while other listings never get checked.
        # Persisted checks survive restarts; a new rule starts with unseen items.
        checked = dict(db.query(ListingPromotion.listing_id, func.max(ListingPromotion.created_at)).filter(
            ListingPromotion.automation_id == row.id,
            ListingPromotion.marketplace == row.marketplace,
            ListingPromotion.action == row.action,
        ).group_by(ListingPromotion.listing_id).all())
        return sorted(targets, key=lambda target: (checked.get(target[1], datetime.min), target[1]))
    if row.share_order == "random":
        return random.sample(targets, len(targets))
    oldest = datetime.min
    return sorted(targets, key=lambda target: target[2] or oldest, reverse=row.share_order == "newest")


def _waiting_for_likes(db: Session, row: PromotionAutomation, now: datetime) -> set[str]:
    """Listings whose latest check by this rule, within LIKE_RECHECK, found too few likers to offer to."""
    recheck = LIKE_RECHECK.get(row.marketplace)
    if row.action != "offer" or recheck is None:
        return set()
    latest: dict[str, tuple[str, int | None]] = {}
    for listing_id, state, likes in db.query(ListingPromotion.listing_id, ListingPromotion.state, ListingPromotion.like_count).filter(
        ListingPromotion.automation_id == row.id, ListingPromotion.marketplace == row.marketplace,
        ListingPromotion.action == "offer", ListingPromotion.created_at > now - recheck,
    ).order_by(ListingPromotion.created_at.desc()):
        latest.setdefault(listing_id, (state, likes))
    # The like count when each listing was last offered; the same count means no new likers.
    offered: dict[str, int | None] = dict(db.query(ListingPromotion.listing_id, ListingPromotion.like_count).filter(
        ListingPromotion.marketplace == row.marketplace, ListingPromotion.action == "offer", ListingPromotion.state == "done",
        ListingPromotion.listing_id.in_(list(latest)),
    ).order_by(ListingPromotion.sent_at))
    minimum = row.min_likes or 1
    return {listing_id for listing_id, (state, likes) in latest.items()
            if state == "skipped" and likes is not None
            and (likes < minimum or (offered.get(listing_id) is not None and likes <= cast(int, offered[listing_id])))}


def _held_after_refusal(db: Session, row: PromotionAutomation, now: datetime, covered: set[str]) -> dict[str, datetime]:
    """Listings whose latest check by this rule, within a day, got a refusal that repeats until the listing changes.

    Maps each to when it was refused: a like newer than that still reads it. A
    price Anteroom recorded for the listing after the refusal, different from
    the one the marketplace read then, releases it. So many held listings at
    once looks like a change at the marketplace, so none is held then.
    """
    if row.action != "offer":
        return {}
    latest: dict[str, ListingPromotion] = {}
    for promotion in db.query(ListingPromotion).filter(
        ListingPromotion.automation_id == row.id, ListingPromotion.marketplace == row.marketplace,
        ListingPromotion.action == "offer", ListingPromotion.created_at > now - offer_preview_refusals.HOLD,
    ).order_by(ListingPromotion.created_at.desc()):
        latest.setdefault(promotion.listing_id, promotion)
    refused = {listing_id: promotion for listing_id, promotion in latest.items()
               if listing_id in covered and promotion.state == "skipped" and offer_preview_refusals.held_note(promotion.note, promotion.marketplace_message)}
    priced = {promotion.conversation_id: promotion for promotion in refused.values()
              if promotion.price_cents is not None and promotion.conversation_id}
    if priced:
        for conversation_id, listed_price, price_recorded_at in db.query(
            MarketplaceListing.conversation_id, MarketplaceListing.listed_price, MarketplaceListing.price_recorded_at,
        ).filter(MarketplaceListing.conversation_id.in_(list(priced)), MarketplaceListing.marketplace == row.marketplace):
            promotion = priced[conversation_id]
            if (listed_price is not None and price_recorded_at is not None and price_recorded_at > (promotion.finished_at or promotion.created_at)
                    and round(listed_price * 100) != promotion.price_cents):
                refused.pop(promotion.listing_id, None)
    if len(refused) > BROAD_REFUSALS and len(refused) * 2 > len(covered):
        return {}
    return {listing_id: promotion.created_at for listing_id, promotion in refused.items()}


def _skip_reason(exc: PromotionError, send: ListingPromotion) -> str:
    """The skip text, with the marketplace's own words when it gave them."""
    text = scrub(str(exc)) or "The marketplace did not answer."
    own = scrub(send.marketplace_message) or ""
    if own and own not in text:
        text = f"{text.rstrip('.')}: {own}"
    return text[:1000]


def _skip_note(reasons: Counter) -> str:
    return "; ".join(f"{text[:120]} ({count})" for text, count in reasons.most_common(3))


@dataclass
class _StartedRound:
    rule: dict
    run_id: str
    previous_slot: datetime | None
    claimed_slot: datetime | None


def _begin_round(
    db: Session,
    automation_id: str,
    now: datetime,
    expected_next_run_at: datetime | None,
    like_events: dict[str, datetime] | None,
) -> _StartedRound | None:
    row = db.get(PromotionAutomation, automation_id)
    if row is None:
        return None
    target_next_run = claimed_slot = None
    if like_events is None:
        target_next_run = expected_next_run_at if expected_next_run_at is not None else row.next_run_at
        # Claim the due round: when two Machines read the same due rule, one runs it.
        if row.action == "share":
            claimed_slot = next_share_slot(row, now)
        elif row.action == "follow":
            claimed_slot = next_share_slot(row, now) if row.times else now + FOLLOW_INTERVAL
        else:
            claimed_slot = now + OFFER_INTERVAL
        claimed = db.execute(update(PromotionAutomation).execution_options(synchronize_session=False).where(
            PromotionAutomation.id == row.id, PromotionAutomation.next_run_at == target_next_run,
        ).values(next_run_at=claimed_slot))
        if cast(CursorResult, claimed).rowcount != 1:
            db.rollback()
            return None
        db.refresh(row)
    run = PromotionRun(automation_id=row.id, started_at=now)
    db.add(run)
    row.last_run_at = now
    db.flush()
    rule = {
        "id": row.id,
        "marketplace": row.marketplace,
        "action": row.action,
        "speed": row.speed,
        "daily_cap": row.daily_cap,
        "timezone": row.timezone,
        "share_modes": list(row.share_modes or ["self"]),
        "hourly_schedule": dict(row.hourly_schedule) if isinstance(row.hourly_schedule, dict) else None,
        "min_days_listed": row.min_days_listed,
        "min_likes": row.min_likes,
        "shipping_label": row.shipping_label,
        "discount_percent": row.discount_percent,
        "waterfall_steps": list(row.waterfall_steps or []),
        "bundle_pause_minutes": row.bundle_pause_minutes,
        "bundle_min_items": row.bundle_min_items,
        "bundle_comment": row.bundle_comment,
        "follow_config": dict(row.follow_config) if isinstance(row.follow_config, dict) else None,
    }
    started = _StartedRound(rule, run.id, target_next_run, claimed_slot)
    db.commit()
    return started


def _prepare_round(
    db: Session,
    automation_id: str,
    now: datetime,
    like_events: dict[str, datetime] | None,
) -> tuple[list[tuple[str, str, str]], int | None, set[str], dict[str, datetime]]:
    row = db.get(PromotionAutomation, automation_id)
    if row is None:
        return [], None, set(), {}
    covered = _covered(db, row)
    targets = [
        (conv.id, getattr(conv, "title", "") or "", listing_id)
        for conv, listing_id, listed_at in _order(db, row, covered)
        if (like_events is None or listing_id in like_events)
        if not row.min_days_listed or (listed_at and (now - listed_at).days >= row.min_days_listed)
    ]
    remaining = (row.daily_cap or 0) - _shares_today(db, row.marketplace, now) if row.action == "share" else None
    # Like-feed rounds read their listings; the regular scan skips those still waiting for a like.
    waiting = _waiting_for_likes(db, row, now) if like_events is None and not row.waterfall_steps else set()
    held = _held_after_refusal(db, row, now, {target[1] for target in covered})
    return targets, remaining, waiting, held


def _check_enabled(db: Session, automation_id: str) -> bool:
    row = db.get(PromotionAutomation, automation_id)
    return bool(row and row.enabled)


def _check_offer_target(
    db: Session,
    automation_id: str,
    marketplace: str,
    listing_id: str,
    new_like_at: datetime | None,
    action: str = "offer",
) -> tuple[bool, str | None, dict | None, datetime | None]:
    row = db.get(PromotionAutomation, automation_id)
    if not row or not row.enabled:
        return False, None, None, None
    blocked = promotions.blocking(db, marketplace, listing_id, action, new_like_at=new_like_at)
    if blocked:
        return True, blocked, None, None
    if row.waterfall_steps:
        first_seen = (
            promotions.first_seen_waterfall_like_at(db, marketplace, listing_id, action)
            if new_like_at is None
            else None
        )
        effective_like_at = new_like_at if new_like_at is not None else first_seen
        step, skip_reason = promotions.next_waterfall_step(
            db, marketplace, listing_id, action, row.waterfall_steps, new_like_at=effective_like_at,
        )
        if skip_reason:
            return True, skip_reason, None, first_seen
        return True, None, step, first_seen
    return True, None, None, None


def _record_review_failure(
    db: Session,
    send: ListingPromotion,
    automation_id: str,
    pause_reason: str | None,
    signed_out: bool = False,
) -> str | None:
    # The draft may have been deleted while Chrome was reading.
    # Keep the listing identity, without a dangling draft reference.
    if send.conversation_id and db.get(Conversation, send.conversation_id) is None:
        send.conversation_id = None
    db.add(send)
    db.commit()
    if pause_reason is not None:
        row = db.get(PromotionAutomation, automation_id)
        if row is not None:
            return _pause(db, row, pause_reason, signed_out=signed_out)
    return None


def _save_preview_send(db: Session, send: ListingPromotion) -> str:
    db.add(send)
    db.commit()
    return send.id


def _mark_send_skipped(db: Session, send_id: str, exc: PromotionError) -> str:
    send = db.get(ListingPromotion, send_id)
    if send is None:
        return scrub(str(exc)) or "The marketplace did not answer."
    send.state, send.note = "skipped", str(exc)[:300]
    db.commit()
    return _skip_reason(exc, send)


def _pause_by_id(db: Session, automation_id: str, reason: str, signed_out: bool = False) -> str:
    row = db.get(PromotionAutomation, automation_id)
    if row is None:
        return f"Paused: {reason}"[:300]
    return _pause(db, row, reason, signed_out=signed_out)


def _finish_round(
    db: Session,
    run_id: str,
    counts: Counter,
    stop_note: str | None,
    reasons: Counter,
) -> PromotionRun | None:
    run = db.get(PromotionRun, run_id)
    if run is None:
        return None
    run.sent, run.skipped, run.failed = counts["sent"], counts["skipped"], counts["failed"]
    run.finished_at = _now()
    run.note = " ".join(part for part in (stop_note, f"Skipped: {_skip_note(reasons)}." if reasons else None) if part)[:500] or None
    db.commit()
    db.refresh(run)
    db.expunge(run)
    return run


def _abandon_round(db: Session, started: _StartedRound, now: datetime) -> None:
    # No marketplace work started. Restore the slot only if this run still
    # owns it, so a newer round or a seller's schedule edit wins.
    if started.claimed_slot is not None:
        db.execute(update(PromotionAutomation).where(
            PromotionAutomation.id == started.rule["id"],
            PromotionAutomation.next_run_at == started.claimed_slot,
            PromotionAutomation.last_run_at == now,
        ).values(next_run_at=started.previous_slot))
    if _finish_round(db, started.run_id, Counter(), "Stopped before the round started.", Counter()) is None:
        db.rollback()


async def run_round(automation_id: str, *, expected_next_run_at: datetime | None = None,
                    sleep=asyncio.sleep, like_events: dict[str, datetime] | None = None) -> PromotionRun | None:
    """One round: share listings, run follow/unfollow, or send offers/bundles. Stops at the first limit."""
    now = _now()
    async def abandon(started: _StartedRound | None) -> None:
        if started is not None:
            await in_worker_session(_abandon_round, started, now)

    started = await claim_in_worker_session(_begin_round, abandon, automation_id, now, expected_next_run_at, like_events)
    if started is None:
        return None
    rule, run_id = started.rule, started.run_id
    marketplace, action = rule["marketplace"], rule["action"]
    reasons: Counter = Counter()
    counts: Counter = Counter()
    stop_note = None
    try:
        await run_worker_db_step(promotions.require_chrome, marketplace)
        batch = new_id()
        if action == "follow":
            follow_result = await promotions.perform_account_action(
                marketplace,
                "follow",
                automation_id=automation_id,
                batch_id=batch,
                extra_payload={},
            )
            if follow_result["state"] == "done":
                counts["sent"] += max(int(follow_result.get("recipient_count") or 1), 1)
            else:
                counts["failed"] += 1
                if follow_result["state"] == "unknown" or follow_result.get("stop"):
                    stop_note = await in_worker_session(
                        _pause_by_id, automation_id, follow_result.get("note") or "The marketplace asked Anteroom to stop.",
                    )
                else:
                    reasons[str(follow_result.get("note") or "refused")[:120]] += 1
            return await in_worker_session(_finish_round, run_id, counts, stop_note, reasons)

        share_modes = list(rule.get("share_modes") or ["self"])
        party_window = active_posh_party_window(now) if action == "share" and "party" in share_modes else None
        intensity = hourly_intensity_for(rule.get("hourly_schedule"), rule.get("timezone"), now) if action == "share" else "high"
        if action == "share" and rule.get("hourly_schedule") and intensity == "none":
            stop_note = "Current hour is set to none in the 24-hour sharing schedule."
            return await in_worker_session(_finish_round, run_id, counts, stop_note, reasons)

        targets, remaining, waiting, held = await in_worker_session(_prepare_round, automation_id, now, like_events)
        reads = 0
        for conv_id, conv_title, listing_id in targets:
            new_like_at = like_events.get(listing_id) if like_events is not None else None
            chosen_discount = rule["discount_percent"]
            chosen_waterfall_step: int | None = None
            chosen_step: dict | None = None
            first_seen_like_at: datetime | None = None
            if action in ("offer", "bundle_offer"):
                if listing_id in waiting:
                    if not await in_worker_session(_check_enabled, automation_id):
                        stop_note = "Turned off during the round."
                        break
                    counts["skipped"] += 1
                    reasons[WAITING_FOR_LIKES] += 1
                    continue
                # As in the feed filter, keep millisecond ties eligible until a later check.
                if listing_id in held and (new_like_at is None or new_like_at < held[listing_id].replace(
                        microsecond=held[listing_id].microsecond // 1000 * 1000)):
                    if not await in_worker_session(_check_enabled, automation_id):
                        stop_note = "Turned off during the round."
                        break
                    counts["skipped"] += 1
                    reasons[HELD_AFTER_REFUSAL] += 1
                    continue
                enabled, blocked, step, first_seen_like_at = await in_worker_session(
                    _check_offer_target, automation_id, marketplace, listing_id, new_like_at, action,
                )
                if not enabled:
                    stop_note = "Turned off during the round."
                    break
                if blocked:
                    counts["skipped"] += 1
                    reason = (
                        blocked
                        if blocked == promotions.MERCARI_COOLDOWN_NOTE or "waterfall" in blocked
                        else "offered in the last 24 hours or unconfirmed"
                    )
                    reasons[reason] += 1
                    continue
                if step is not None:
                    chosen_step = step
                    chosen_discount = int(step["discount_percent"])
                    chosen_waterfall_step = int(step["step"])
                if reads >= OFFER_READS:
                    stop_note = f"Checked {OFFER_READS} listings; the rest are checked next time."
                    break
                if reads:
                    await sleep(READ_GAP_S)
                reads += 1
            else:
                if not await in_worker_session(_check_enabled, automation_id):
                    stop_note = "Turned off during the round."
                    break
                if remaining is not None and remaining <= 0:
                    stop_note = f"Reached the daily cap of {rule['daily_cap']:,} shares."
                    break
                if "self" not in share_modes and not ("party" in share_modes and party_window):
                    break
            send = ListingPromotion(
                batch_id=batch, conversation_id=conv_id, marketplace=marketplace, action=action,
                listing_id="", title=conv_title, discount_percent=chosen_discount,
                waterfall_step=chosen_waterfall_step,
                automation_id=automation_id, state="preview", expires_at=_now() + promotions.PREVIEW_TTL,
            )
            # Feed events arriving during the read must remain newer than this check.
            send.created_at = _now()
            try:
                await promotions.review(send, conv_id, new_like_at=new_like_at)
                shipping = None
                if action in ("offer", "bundle_offer"):
                    if cast(int, send.like_count) < (rule["min_likes"] or 1):
                        raise PromotionError(409, "fewer likes than the rule needs")
                    if chosen_waterfall_step is None or chosen_waterfall_step == 1:
                        last = await in_worker_session(promotions.likes_at_last_offer, marketplace, listing_id)
                        if new_like_at is None and last is not None and cast(int, send.like_count) <= last:
                            raise PromotionError(409, "no new likers since the last offer")
                    if (
                        chosen_waterfall_step == 1
                        and chosen_step is not None
                        and new_like_at is None
                        and first_seen_like_at is None
                        and int(chosen_step.get("delay_minutes") or 0) > 0
                    ):
                        raise PromotionError(409, f"waiting {int(chosen_step['delay_minutes'])}m before waterfall step 1")
                    shipping = _shipping_choice_for(marketplace, rule["shipping_label"], send)
            except PromotionError as exc:
                # Review only reads, but its refusal still belongs in the
                # per-listing activity journal. No send timestamp; a recognized
                # Mercari eligibility refusal gates previews only.
                send.listing_id = send.listing_id or listing_id
                skip_reason = _skip_reason(exc, send)
                send.state, send.note = ("failed" if exc.code in ("chrome", "network") else "skipped"), skip_reason
                counts[send.state] += 1
                send.marketplace_message = scrub(send.marketplace_message)
                send.finished_at = _now()
                paused = await in_worker_session(
                    _record_review_failure, send, automation_id,
                    str(exc) if exc.code in STOP_CODES and not exc.cloud_signed_out else None,
                    exc.code == "signed_out",
                )
                if exc.code in STOP_CODES:
                    stop_note = f"Stopped: {exc}" if exc.cloud_signed_out else paused
                    break
                if exc.code in ("chrome", "network"):
                    stop_note = str(exc)
                    break
                reasons[skip_reason] += 1
                continue
            send_id = await in_worker_session(_save_preview_send, send)
            base_gap = SHARE_SPEEDS.get(rule["speed"] or "") if action == "share" else None
            effective_gap = int(base_gap * INTENSITY_GAP_MULTIPLIER.get(intensity, 1.0)) if base_gap else None
            extra_payload: dict | None = None
            if action == "share":
                if share_modes != ["self"]:
                    extra_payload = {"share_modes": share_modes, "party_window": party_window}
            elif action == "bundle_offer":
                extra_payload = {
                    "bundle_pause_minutes": rule.get("bundle_pause_minutes") or 15,
                    "bundle_min_items": rule.get("bundle_min_items") or 1,
                    "bundle_comment": rule.get("bundle_comment"),
                }
            try:
                result = await promotions.perform(
                    send_id,
                    shipping,
                    new_like_at=new_like_at,
                    gap_ms=effective_gap,
                    extra_payload=extra_payload,
                    include_executor=True,
                )
            except PromotionError as exc:
                reason = await in_worker_session(_mark_send_skipped, send_id, exc)
                if exc.code in ("busy", "paused", "chrome"):
                    stop_note = "Stopped: another job needed Chrome." if exc.code == "busy" else str(exc)
                    counts["skipped"] += 1
                    break
                counts["skipped"] += 1
                reasons[reason] += 1
                continue
            if result["state"] == "done":
                counts["sent"] += 1
                if remaining is not None:
                    remaining -= 1
                continue
            counts["failed"] += 1
            if result["state"] == "unknown":
                stop_note = await in_worker_session(
                    _pause_by_id, automation_id, "A send was not confirmed. Check it in the activity log, then turn this back on.",
                )
                break
            if result.get("stop"):
                reason = result.get("note") or "The marketplace asked Anteroom to stop."
                signed_out = result.get("error_code") == "signed_out"
                if signed_out and result.get("executor") == "cloud":
                    stop_note = f"Stopped: {reason}"
                else:
                    stop_note = await in_worker_session(_pause_by_id, automation_id, reason, signed_out)
                break
            reasons[str(result.get("note") or "refused")[:120]] += 1

        if action == "share" and "community" in share_modes and stop_note is None and (remaining is None or remaining > 0):
            if await in_worker_session(_check_enabled, automation_id):
                community_result = await promotions.perform_account_action(
                    marketplace,
                    "share",
                    automation_id=automation_id,
                    batch_id=batch,
                    extra_payload={"share_mode": "community", "max_shares": min(remaining or 50, 50)},
                )
                if community_result["state"] == "done":
                    counts["sent"] += max(int(community_result.get("recipient_count") or 1), 1)
                elif community_result["state"] == "unknown" or community_result.get("stop"):
                    stop_note = await in_worker_session(
                        _pause_by_id, automation_id, community_result.get("note") or "The marketplace asked Anteroom to stop.",
                    )
    except PromotionError as exc:
        stop_note = str(exc)
        log.warning("%s automation %s could not finish its round: %s", marketplace, automation_id, scrub(str(exc)))
    finally:
        finished = await in_worker_session(_finish_round, run_id, counts, stop_note, reasons)
    return finished


def report_overdue(db: Session) -> None:
    """Report missed rounds without running them or changing their approval."""
    cutoff = _now() - timedelta(minutes=30)
    for row in db.query(PromotionAutomation).filter(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.next_run_at < cutoff,
    ).all():
        log.error("%s %s automation %s is overdue (seller %s, due %s, last run %s). Check the scheduler and Chrome connection.",
                  row.marketplace, row.action, row.id, row.user_id, row.next_run_at, row.last_run_at)


def _shipping_choice_for(marketplace: str, shipping_label: str | None, send: ListingPromotion) -> str | None:
    if marketplace not in promotions.SHIPPING_DISCOUNTS:
        return None
    if not shipping_label:
        return "none"
    match = next((option for option in send.shipping_options or [] if option["label"] == shipping_label), None)
    if not match:
        raise PromotionError(409, f"{NAMES[marketplace]} did not offer the shipping discount “{shipping_label}”")
    return match["id"]


def _pause(db: Session, row: PromotionAutomation, reason: str, *, signed_out: bool = False) -> str:
    if signed_out and row.paused_reason == CLOUD_DISCONNECTED:
        return CLOUD_DISCONNECTED
    note = f"Paused: {reason}"[:300]
    row.enabled, row.paused_reason = False, note
    db.commit()
    log.warning("%s automation %s paused: %s", row.marketplace, row.id, scrub(reason))
    return note


def _due_automations(
    db: Session,
    moment: datetime,
    *,
    marketplaces: tuple[str, ...] | None = None,
    skip_cloud: bool = False,
) -> list[tuple[str, datetime | None, str]]:
    from anteroom.services import cloud_sessions
    from anteroom.services.listing_mode import read
    if read(db) == "vendoo":
        return []
    query = db.query(PromotionAutomation).filter(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.next_run_at <= moment,
    )
    if marketplaces is not None:
        query = query.filter(PromotionAutomation.marketplace.in_(marketplaces))
    rows = query.order_by(PromotionAutomation.next_run_at).all()
    if skip_cloud:
        rows = [row for row in rows if not cloud_sessions.has_active_session(row.marketplace, db)]
    return [(row.id, row.next_run_at, row.marketplace) for row in rows]


def _has_active_job(db: Session) -> bool:
    return bool(JobRepo(db).get_active())


async def tick(
    *,
    now: datetime | None = None,
    sleep=asyncio.sleep,
    marketplaces: tuple[str, ...] | None = None,
    skip_cloud: bool = False,
) -> int:
    """Run every due automation, one at a time. A busy slot waits for the next tick."""
    moment = now or _now()
    due = await in_worker_session(
        lambda db: _due_automations(db, moment, marketplaces=marketplaces, skip_cloud=skip_cloud)
    )
    ran = 0
    for automation_id, next_run_at, marketplace in due:
        if await in_worker_session(_has_active_job):
            break
        if await run_worker_db_step(_awaiting_chrome_at, next_run_at, moment, marketplace):
            continue
        if await run_round(automation_id, expected_next_run_at=next_run_at, sleep=sleep) is not None:
            ran += 1
    return ran


def _awaiting_chrome_at(next_run_at: datetime | None, moment: datetime, marketplace: str | None = None) -> bool:
    """Whether a due round should wait for Chrome to reconnect rather than record a round that cannot run."""
    try:
        promotions.require_chrome(marketplace)
    except PromotionError as exc:
        return exc.code == "chrome" and next_run_at is not None and moment - next_run_at < CHROME_GRACE
    return False


def _offer_automations(
    db: Session,
    *,
    marketplaces: tuple[str, ...] | None = None,
    skip_cloud: bool = False,
) -> list[tuple[str, str]]:
    from anteroom.services import cloud_sessions
    from anteroom.services.listing_mode import read
    if read(db) == "vendoo":
        return []
    allowed = tuple(m for m in ("poshmark", "depop") if marketplaces is None or m in marketplaces)
    if not allowed:
        return []
    rows = db.query(PromotionAutomation).filter(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.action == "offer",
        PromotionAutomation.marketplace.in_(allowed),
    ).all()
    if skip_cloud:
        rows = [row for row in rows if not cloud_sessions.has_active_session(row.marketplace, db)]
    return [(row.id, row.marketplace) for row in rows]


def _check_like_rule_state(db: Session, automation_id: str) -> tuple[str, datetime | None]:
    if JobRepo(db).get_active():
        return "busy", None
    row = db.get(PromotionAutomation, automation_id)
    if row is None or not row.enabled:
        return "skip", None
    if row.like_events_since is None:
        # No historical feed replay when adopting the feature.
        row.like_events_since = _now()
        db.commit()
        return "initialized", None
    return "ready", row.like_events_since


def _filter_like_events(
    db: Session,
    automation_id: str,
    marketplace: str,
    events: dict[str, datetime],
) -> dict[str, datetime]:
    row = db.get(PromotionAutomation, automation_id)
    if row is None or not row.enabled:
        return {}
    covered = {target[1] for target in _covered(db, row)}
    # The feed is re-read from the same start every minute, so a like this
    # rule has already checked the listing for would otherwise read it again
    # on every tick. Failed reads must retry. The Bridge truncates events to
    # milliseconds: keep ties eligible until a check in a later millisecond.
    # A count-only skip cannot assess individual likers: a replacement liker
    # may leave the count unchanged. Its journal reason distinguishes it
    # from completed eligibility checks, including existing persisted rows.
    checked = dict(db.query(ListingPromotion.listing_id, func.max(ListingPromotion.created_at)).filter(
        ListingPromotion.automation_id == row.id, ListingPromotion.action == "offer",
        ListingPromotion.listing_id.in_(list(events)),
        or_(ListingPromotion.state != "failed", ListingPromotion.sent_at.is_not(None)),
        or_(ListingPromotion.state != "skipped", ListingPromotion.sent_at.is_not(None),
            ListingPromotion.note.is_(None),
            ~ListingPromotion.note.startswith("no new likers since the last offer")),
    ).group_by(ListingPromotion.listing_id).all()) if events else {}
    return {
        key: at for key, at in events.items()
        if key in covered
        and not (key in checked and at < checked[key].replace(microsecond=checked[key].microsecond // 1000 * 1000))
        and not promotions.blocking(db, marketplace, key, "offer", new_like_at=at)
    }


async def check_new_likes(
    *,
    sleep=asyncio.sleep,
    marketplaces: tuple[str, ...] | None = None,
    skip_cloud: bool = False,
) -> int:
    """Check bounded recent-like feeds once per minute, only for approved rules.

    The regular scan still catches older likes that fell out of these feeds.
    Only events after approval qualify. Re-reading the recent feed also catches
    late notifications; the send journal prevents replaying confirmed offers.
    """
    from anteroom.services import cloud_marketplace, cloud_sessions

    rules = await in_worker_session(
        lambda db: _offer_automations(db, marketplaces=marketplaces, skip_cloud=skip_cloud)
    )
    feeds: dict[str, tuple[datetime, dict, bool]] = {}
    ran = 0
    for automation_id, marketplace in rules:
        state, since = await in_worker_session(_check_like_rule_state, automation_id)
        if state == "busy":
            break
        if state != "ready" or since is None:
            continue
        if marketplace not in feeds:
            try:
                use_cloud = await run_worker_db_step(cloud_sessions.has_active_session, marketplace)
                if not use_cloud:
                    await run_worker_db_step(promotions.require_chrome, marketplace)
                started = _now()
                if use_cloud:
                    answer = await cloud_marketplace.execute(
                        marketplace,
                        {
                            "type": "marketplace.like_events",
                            "job_id": f"likes-{automation_id}",
                            "payload": {"marketplace": marketplace},
                        },
                        timeout=45,
                    )
                else:
                    answer = await promotions.browser_bridge.request(SimpleNamespace(id=f"likes-{automation_id}"),
                        "marketplace.like_events", {"marketplace": marketplace}, timeout=45)
            except (
                promotions.browser_bridge.BrowserBridgeError,
                PromotionError,
                cloud_marketplace.CloudExecutionUnavailable,
                cloud_sessions.CloudSessionError,
            ) as exc:
                log.warning("%s automation %s could not read recent likes: %s", marketplace, automation_id, scrub(str(exc)))
                continue
            feeds[marketplace] = started, answer, use_cloud
        started, answer, use_cloud = feeds[marketplace]
        if answer.get("ok") is not True:
            log.error("%s automation %s recent-like feed failed: %s", marketplace, automation_id,
                      scrub(answer.get("error") or "The marketplace did not answer."))
            code = answer.get("error_code")
            if code in STOP_CODES | {"schema_drift"} and not (use_cloud and code == "signed_out"):
                await in_worker_session(
                    _pause_by_id, automation_id, str(answer.get("error") or "The marketplace asked Anteroom to stop."),
                    code == "signed_out",
                )
            continue
        events: dict[str, datetime] = {}
        try:
            if not isinstance(answer.get("events"), list):
                raise ValueError("Missing events")
            for event in answer["events"]:
                at = datetime.fromisoformat(event["at"].replace("Z", "+00:00"))
                if at.tzinfo is None or not isinstance(event["listing_id"], str):
                    raise ValueError("Invalid event")
                at = at.astimezone(UTC).replace(tzinfo=None)
                if since < at <= started:
                    listing_id = event["listing_id"]
                    events[listing_id] = max(events.get(listing_id, at), at)
        except (ValueError, KeyError, TypeError, AttributeError):
            await in_worker_session(_pause_by_id, automation_id, "The recent-like feed changed. Anteroom needs an update.")
            continue
        filtered = await in_worker_session(_filter_like_events, automation_id, marketplace, events)
        if filtered:
            await run_round(automation_id, sleep=sleep, like_events=filtered)
            ran += 1
    return ran


def recover(db: Session) -> None:
    """Close rounds a stopped Anteroom left open. Their sends were already marked unknown."""
    for run in db.query(PromotionRun).filter(PromotionRun.finished_at.is_(None)).all():
        run.finished_at, run.note = _now(), INTERRUPTED
    # Apply the shorter interval to already-enabled rules without sending at startup.
    next_check = _now() + OFFER_INTERVAL
    db.query(PromotionAutomation).filter(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.action == "offer",
        PromotionAutomation.next_run_at > next_check,
    ).update({"next_run_at": next_check}, synchronize_session="fetch")
    db.commit()


async def _timer() -> None:
    while True:
        # Never send as a side effect of startup: the first check waits a full tick.
        await asyncio.sleep(TICK_S)
        from anteroom.services.mac_workspace import account_shown

        # The account runs them while this Mac's window shows it.
        if account_shown():
            continue
        try:
            await check_new_likes()
            await tick()
        except Exception:  # noqa: BLE001 - one bad round must not stop the clock
            log.exception("promotion automation tick failed; unconfirmed sends are never retried")


def start() -> None:
    global _task
    if _task and not _task.done():
        return
    with SessionLocal() as db:
        recover(db)
    _task = asyncio.create_task(_timer())


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
