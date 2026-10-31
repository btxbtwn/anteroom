"""Seller-enabled scheduled sharing and automatic offers to likers.

Every automation is saved off and runs only after the seller turns it on; any
edit turns it off again. A round goes one listing at a time through
``listing_promotions.review`` and ``perform``, so each send is journaled, sent
at most once, and holds the single automation slot only while it is sent.

A round stops at the marketplace's first limit, browser check or sign-out and
pauses the automation. An unconfirmed result pauses it too, and that listing
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
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.models.conversation import Conversation, new_id
from vendoo_studio.models.listing_promotion import (
    ListingPromotion,
    MarketplaceOfferFloor,
)
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.promotion_automation import PromotionAutomation, PromotionRun
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services import listing_promotions as promotions
from vendoo_studio.services.automation_age import load_listed_index, resolve_listed_at
from vendoo_studio.services.automation_schedule import (
    ScheduleError,
    as_utc,
    next_after,
    normalize_schedule,
    require_timezone,
)
from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.listing_promotions import NAMES, STOP_CODES, PromotionError

log = logging.getLogger("vendoo_studio.promotion_automations")

# Pause between shares, before Poshmark's own jitter of up to 3 seconds.
SHARE_SPEEDS = {"slow": 15000, "normal": 7000, "fast": 4000}
SHARE_ORDERS = ("newest", "oldest", "random")
# Vendoo's ceiling for Poshmark sharing; Poshmark stops sooner if it wants to.
MAX_DAILY_SHARES = 6000
MAX_TIMES = 6
MAX_LISTED_ITEMS = 500
OFFER_INTERVAL = timedelta(minutes=15)
# Listing reads per offer check, spaced out; the rest are read in a later check.
OFFER_READS = 60
READ_GAP_S = 2.0
TICK_S = 60.0
INTERRUPTED = "Anteroom stopped during this round."
_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


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
        "discount_percent": row.discount_percent, "min_likes": row.min_likes, "shipping_label": row.shipping_label,
        "minimum_price": dollars(row.minimum_price_cents) if row.minimum_price_cents is not None else None,
        "paused_reason": row.paused_reason, "next_run_at": _iso(row.next_run_at) if row.enabled else None,
        "last_run": run_out(last) if last else None, "available": promotions.available(row.marketplace, row.action),
    }


def _ids(value, label: str) -> list[str]:
    ids = list(dict.fromkeys(str(item) for item in value or []))
    if len(ids) > MAX_LISTED_ITEMS:
        raise PromotionError(400, f"Choose at most {MAX_LISTED_ITEMS} {label}.")
    return ids


def _validate(body: dict) -> dict:
    marketplace, action = body.get("marketplace"), body.get("action")
    if action not in promotions.SUPPORTED.get(marketplace, frozenset()):
        raise PromotionError(400, "Anteroom cannot do this on that marketplace.")
    scope = body.get("scope") or "all"
    if scope not in ("all", "items"):
        raise PromotionError(400, "Cover all live listings or the items you choose.")
    fields = {
        "marketplace": marketplace, "action": action, "scope": scope,
        "name": str(body.get("name") or "").strip()[:80] or f"{NAMES[marketplace]} {'sharing' if action == 'share' else 'offers to likers'}",
        "conversation_ids": _ids(body.get("conversation_ids"), "items") if scope == "items" else [],
        "excluded_ids": _ids(body.get("excluded_ids"), "exclusions"),
        "min_days_listed": int(body.get("min_days_listed") or 0),
        "share_order": None, "speed": None, "daily_cap": None, "times": None, "timezone": None,
        "discount_percent": None, "min_likes": None, "shipping_label": None, "minimum_price_cents": None,
    }
    if scope == "items" and not fields["conversation_ids"]:
        raise PromotionError(400, "Choose at least one item.")
    if not 0 <= fields["min_days_listed"] <= 365:
        raise PromotionError(400, "Minimum days listed is 0 to 365.")
    if action == "share":
        order, speed, cap = body.get("share_order") or "newest", body.get("speed") or "normal", body.get("daily_cap")
        if order not in SHARE_ORDERS or speed not in SHARE_SPEEDS:
            raise PromotionError(400, "Choose an order and a speed.")
        if not isinstance(cap, int) or not 1 <= cap <= MAX_DAILY_SHARES:
            raise PromotionError(400, f"The daily cap is 1 to {MAX_DAILY_SHARES:,} shares.")
        try:
            times = sorted({normalize_schedule({"kind": "daily", "time": str(value)})["time"] for value in body.get("times") or []})
            timezone = str(body.get("timezone") or "UTC")
            require_timezone(timezone)
        except ScheduleError as exc:
            raise PromotionError(400, str(exc)) from exc
        if not 1 <= len(times) <= MAX_TIMES:
            raise PromotionError(400, f"Choose 1 to {MAX_TIMES} round times.")
        fields.update(share_order=order, speed=speed, daily_cap=cap, times=times, timezone=timezone)
    else:
        discount, likes = body.get("discount_percent"), body.get("min_likes") or 1
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
        fields.update(discount_percent=discount, min_likes=likes, shipping_label=label, minimum_price_cents=minimum)
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
    slots = [next_after({"kind": "daily", "time": clock}, row.timezone or "UTC", as_utc(after)) for clock in row.times or []]
    return min(slot for slot in slots if slot).astimezone(UTC).replace(tzinfo=None)


def enable(db: Session, automation_id: str) -> dict:
    """Turn one automation on. Nothing is sent now: the first round waits for its time."""
    row = _get(db, automation_id)
    if not promotions.available(row.marketplace, row.action):
        raise PromotionError(409, f"Anteroom cannot send {NAMES[row.marketplace]} offers yet: its offer request still needs "
                                  "to be read from the signed-in site.")
    now = _now()
    row.next_run_at = next_share_slot(row, now) if row.action == "share" else now + OFFER_INTERVAL
    row.enabled, row.paused_reason = True, None
    row.like_events_since = now
    db.commit()
    return out(db, row)


def pause(db: Session, automation_id: str | None = None, reason: str | None = None) -> int:
    query = db.query(PromotionAutomation).filter(PromotionAutomation.enabled.is_(True))
    if automation_id:
        query = query.filter(PromotionAutomation.id == automation_id)
    rows = query.all()
    for row in rows:
        row.enabled, row.paused_reason = False, reason
    db.commit()
    return len(rows)


def listing(db: Session) -> dict:
    labels = set()
    for (options,) in db.query(ListingPromotion.shipping_options).filter(
            ListingPromotion.marketplace == "poshmark", ListingPromotion.action == "offer").order_by(
            ListingPromotion.created_at.desc()).limit(200).all():
        labels.update(option.get("label") for option in options or [] if option.get("label"))
    rows = db.query(PromotionAutomation).order_by(PromotionAutomation.created_at).all()
    return {
        "automations": [out(db, row) for row in rows],
        "supported": {name: sorted(actions) for name, actions in promotions.SUPPORTED.items()},
        "awaiting_capture": {name: sorted(actions) for name, actions in promotions.AWAITING_CAPTURE.items()},
        "speeds": list(SHARE_SPEEDS), "orders": list(SHARE_ORDERS), "max_daily_cap": MAX_DAILY_SHARES,
        "shipping_labels": sorted(labels),
    }


def _covered(db: Session, row: PromotionAutomation, everything: bool = False) -> list[tuple[Conversation, str, datetime | None]]:
    """Live listings this automation covers (or every live one), with when each was listed."""
    excluded = set() if everything else set(row.excluded_ids or [])
    if row.scope == "items" and not everything:
        convs = [conv for conv in (db.get(Conversation, conv_id) for conv_id in row.conversation_ids or []) if conv]
    else:
        convs = db.query(Conversation).filter(Conversation.status != "sold").all()
    rows_by_item: dict[str, list[MarketplaceListing]] = {}
    for listing_row in db.query(MarketplaceListing).filter_by(marketplace=row.marketplace).all():
        rows_by_item.setdefault(listing_row.conversation_id, []).append(listing_row)
    index = load_listed_index(db)
    result = []
    for conv in convs:
        if conv.id in excluded:
            continue
        listing_id, reason = promotions.live_listing(db, conv, row.marketplace)
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
    return db.query(ListingPromotion).filter(
        ListingPromotion.marketplace == marketplace, ListingPromotion.action == "share",
        ListingPromotion.sent_at > now - timedelta(hours=24),
    ).count()


def _order(row: PromotionAutomation, targets: list) -> list:
    if row.action == "offer" or row.share_order == "random":
        return random.sample(targets, len(targets))
    oldest = datetime.min
    return sorted(targets, key=lambda target: target[2] or oldest, reverse=row.share_order == "newest")


def _skip_reason(exc: PromotionError, send: ListingPromotion) -> str:
    """The skip text, with the marketplace's own words when it gave them."""
    text = str(exc)
    own = (send.marketplace_message or "").strip()
    if own and own not in text:
        text = f"{text.rstrip('.')}: {own}"
    return text[:120]


def _skip_note(reasons: Counter) -> str:
    return "; ".join(f"{text} ({count})" for text, count in reasons.most_common(3))


async def run_round(db: Session, row: PromotionAutomation, *, sleep=asyncio.sleep,
                    like_events: dict[str, datetime] | None = None) -> PromotionRun:
    """One round: share the next listings, or offer on listings with new likers. Stops at the first limit."""
    now = _now()
    run = PromotionRun(automation_id=row.id, started_at=now)
    db.add(run)
    row.last_run_at = now
    if like_events is None:
        row.next_run_at = next_share_slot(row, now) if row.action == "share" else now + OFFER_INTERVAL
    db.commit()
    reasons: Counter = Counter()
    counts = Counter()
    stop_note = None
    try:
        promotions.require_chrome()
        targets = [target for target in _order(row, _covered(db, row))
                   if (like_events is None or target[1] in like_events)
                   if not row.min_days_listed or (target[2] and (now - target[2]).days >= row.min_days_listed)]
        remaining = (row.daily_cap or 0) - _shares_today(db, row.marketplace, now) if row.action == "share" else None
        reads = 0
        batch = new_id()
        for conv, listing_id, _listed_at in targets:
            new_like_at = like_events.get(listing_id) if like_events is not None else None
            db.refresh(row)
            if not row.enabled:
                stop_note = "Turned off during the round."
                break
            if remaining is not None and remaining <= 0:
                stop_note = f"Reached the daily cap of {row.daily_cap:,} shares."
                break
            if row.action == "offer":
                if promotions.blocking(db, row.marketplace, listing_id, "offer", new_like_at=new_like_at):
                    counts["skipped"] += 1
                    reasons["offered in the last 24 hours or unconfirmed"] += 1
                    continue
                if reads >= OFFER_READS:
                    stop_note = f"Checked {OFFER_READS} listings; the rest are checked next time."
                    break
                if reads:
                    await sleep(READ_GAP_S)
                reads += 1
            send = promotions.new_row(conv, row.marketplace, row.action, batch, row.discount_percent, row.id)
            try:
                await promotions.review(db, send, conv, new_like_at=new_like_at)
                shipping = None
                if row.action == "offer":
                    if cast(int, send.like_count) < (row.min_likes or 1):
                        raise PromotionError(409, "fewer likes than the rule needs")
                    last = promotions.likes_at_last_offer(db, row.marketplace, listing_id)
                    if new_like_at is None and last is not None and cast(int, send.like_count) <= last:
                        raise PromotionError(409, "no new likers since the last offer")
                    shipping = _shipping_choice(row, send)
            except PromotionError as exc:
                db.rollback()
                counts["skipped"] += 1
                if exc.code in STOP_CODES:
                    stop_note = _pause(db, row, str(exc))
                    break
                if exc.code in ("chrome", "network"):
                    stop_note = str(exc)
                    break
                reasons[_skip_reason(exc, send)] += 1
                continue
            db.add(send)
            db.commit()
            try:
                result = await promotions.perform(db, send.id, shipping,
                                                  new_like_at=new_like_at,
                                                  gap_ms=SHARE_SPEEDS.get(row.speed or "") if row.action == "share" else None)
            except PromotionError as exc:
                db.rollback()
                send.state, send.note = "skipped", str(exc)[:300]
                db.commit()
                if exc.code in ("busy", "paused", "chrome"):
                    stop_note = "Stopped: another job needed Chrome." if exc.code == "busy" else str(exc)
                    counts["skipped"] += 1
                    break
                counts["skipped"] += 1
                reasons[_skip_reason(exc, send)] += 1
                continue
            if result["state"] == "done":
                counts["sent"] += 1
                if remaining is not None:
                    remaining -= 1
                continue
            counts["failed"] += 1
            if result["state"] == "unknown":
                stop_note = _pause(db, row, "A send was not confirmed. Check it in the activity log, then turn this back on.")
                break
            if result.get("stop"):
                stop_note = _pause(db, row, result.get("note") or "The marketplace asked Anteroom to stop.")
                break
            reasons[str(result.get("note") or "refused")[:120]] += 1
    except PromotionError as exc:
        db.rollback()
        stop_note = str(exc)
    finally:
        run.sent, run.skipped, run.failed = counts["sent"], counts["skipped"], counts["failed"]
        run.finished_at = _now()
        run.note = " ".join(part for part in (stop_note, f"Skipped: {_skip_note(reasons)}." if reasons else None) if part)[:500] or None
        db.commit()
    return run


def _shipping_choice(row: PromotionAutomation, send: ListingPromotion) -> str | None:
    if row.marketplace not in promotions.SHIPPING_DISCOUNTS:
        return None
    if not row.shipping_label:
        return "none"
    match = next((option for option in send.shipping_options or [] if option["label"] == row.shipping_label), None)
    if not match:
        raise PromotionError(409, f"{NAMES[row.marketplace]} did not offer the shipping discount “{row.shipping_label}”")
    return match["id"]


def _pause(db: Session, row: PromotionAutomation, reason: str) -> str:
    note = f"Paused: {reason}"[:300]
    row.enabled, row.paused_reason = False, note
    db.commit()
    return note


async def tick(db: Session, *, now: datetime | None = None, sleep=asyncio.sleep) -> int:
    """Run every due automation, one at a time. A busy slot waits for the next tick."""
    moment = now or _now()
    ran = 0
    due = db.query(PromotionAutomation).filter(PromotionAutomation.enabled.is_(True), PromotionAutomation.next_run_at <= moment).order_by(
        PromotionAutomation.next_run_at).all()
    for row in due:
        if JobRepo(db).get_active():
            break
        await run_round(db, row, sleep=sleep)
        ran += 1
    return ran


async def check_new_likes(db: Session, *, sleep=asyncio.sleep) -> int:
    """Check bounded recent-like feeds once per minute, only for approved rules.

    The regular scan still catches older likes that fell out of these feeds.
    Only events after approval qualify. Re-reading the recent feed also catches
    late notifications; the send journal prevents replaying confirmed offers.
    """
    rows = db.query(PromotionAutomation).filter(
        PromotionAutomation.enabled.is_(True), PromotionAutomation.action == "offer",
        PromotionAutomation.marketplace.in_(("poshmark", "depop")),
    ).all()
    feeds: dict[str, tuple[datetime, dict]] = {}
    ran = 0
    for row in rows:
        if JobRepo(db).get_active():
            break
        db.refresh(row)
        if not row.enabled:
            continue
        if row.like_events_since is None:
            # No historical feed replay when adopting the feature.
            row.like_events_since = _now()
            db.commit()
            continue
        if row.marketplace not in feeds:
            try:
                promotions.require_chrome()
                started = _now()
                answer = await promotions.browser_bridge.request(SimpleNamespace(id=f"likes-{row.id}"),
                    "marketplace.like_events", {"marketplace": row.marketplace}, timeout=45)
            except (promotions.browser_bridge.BrowserBridgeError, PromotionError):
                continue
            feeds[row.marketplace] = started, answer
        started, answer = feeds[row.marketplace]
        if answer.get("ok") is not True:
            if answer.get("error_code") in STOP_CODES | {"schema_drift"}:
                _pause(db, row, str(answer.get("error") or "The marketplace asked Anteroom to stop."))
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
                if row.like_events_since < at <= started:
                    listing_id = event["listing_id"]
                    events[listing_id] = max(events.get(listing_id, at), at)
        except (ValueError, KeyError, TypeError, AttributeError):
            _pause(db, row, "The recent-like feed changed. Anteroom needs an update.")
            continue
        covered = {target[1] for target in _covered(db, row)}
        events = {key: at for key, at in events.items() if key in covered
                  and not promotions.blocking(db, row.marketplace, key, "offer", new_like_at=at)}
        if events:
            await run_round(db, row, sleep=sleep, like_events=events)
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
        try:
            with SessionLocal() as db:
                await check_new_likes(db)
                await tick(db)
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
