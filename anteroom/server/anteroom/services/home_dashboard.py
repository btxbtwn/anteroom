"""Home command center: today's snapshot, seller goals tracker, and Needs Attention inbox.

Every read in this module is strictly read-only on marketplace state and never
publishes, updates live listings, or delists anything.
"""
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from anteroom.models.automation import AutomationApproval, AutomationRelistGap
from anteroom.models.cloud_session import CloudMarketplaceSession
from anteroom.models.conversation import Conversation
from anteroom.models.ebay_offer import EbayOfferAttempt
from anteroom.models.home import GOAL_METRICS, GOAL_PERIODS, AttentionDismissal, SellerGoal
from anteroom.models.job import Job
from anteroom.models.listing import Listing
from anteroom.models.listing_promotion import ListingPromotion
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.ownership import LOCAL_USER_ID

DISMISS_TTL_DAYS = 7
_SENT_PROMOTION_STATES = ("sent", "confirmed", "completed")
_DISCONNECTED_CLOUD_STATES = ("expired", "error", "revoked")
_FAILED_LISTING_STATES = ("failed", "error")
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_MARKETPLACE_LABELS = {
    "ebay": "eBay",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
    "depop": "Depop",
    "etsy": "Etsy",
}


class HomeGoalError(ValueError):
    """Validation failure for seller goals."""


def _market_name(slug: str | None) -> str:
    key = str(slug or "").strip().lower()
    return _MARKETPLACE_LABELS.get(key, key.title() if key else "Marketplace")


def _zone(tz_name: str | None) -> ZoneInfo:
    raw = (tz_name or "").strip() or "UTC"
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo is not None else moment


def _iso(moment: datetime | None) -> str | None:
    aware = _utc(moment)
    return aware.isoformat() if aware is not None else None


def _day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def period_window(period: str, today: date) -> tuple[date, date]:
    """Inclusive start/end calendar dates for ``weekly``, ``monthly``, ``quarterly``, or ``yearly``."""
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if period == "monthly":
        last_day = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, 1), date(today.year, today.month, last_day)
    if period == "quarterly":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_end_month = quarter_start_month + 2
        last_day = calendar.monthrange(today.year, quarter_end_month)[1]
        return date(today.year, quarter_start_month, 1), date(today.year, quarter_end_month, last_day)
    if period == "yearly":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    raise HomeGoalError(f"Unsupported goal period: {period}")


def _default_goal_label(period: str, metric: str) -> str:
    period_label = period.capitalize()
    metric_labels = {
        "profit": "profit",
        "sales": "sales",
        "listings": "listings",
        "sell_through_rate": "sell-through rate",
    }
    return f"{period_label} {metric_labels.get(metric, metric)}"


def _listed_conversation_ids_in_window(
    db: Session,
    analytics_rows: list,
    start_utc: datetime,
    end_utc: datetime,
) -> set[str]:
    ids: set[str] = set()
    for row in analytics_rows:
        listed_at = _utc(getattr(row, "listed_at", None))
        if listed_at is not None and start_utc <= listed_at < end_utc:
            ids.add(str(row.conversation_id))
    start_naive = _naive_utc(start_utc)
    end_naive = _naive_utc(end_utc)
    direct_rows = (
        db.query(MarketplaceListing.conversation_id)
        .filter(
            MarketplaceListing.listed_at.is_not(None),
            MarketplaceListing.listed_at >= start_naive,
            MarketplaceListing.listed_at < end_naive,
        )
        .distinct()
        .all()
    )
    for (conv_id,) in direct_rows:
        if conv_id:
            ids.add(str(conv_id))
    return ids


def _today_summary(
    db: Session,
    analytics_rows: list,
    *,
    now_utc: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
    local_today = now_utc.astimezone(tz).date()
    start_utc, end_utc = _day_bounds_utc(local_today, tz)
    start_naive = _naive_utc(start_utc)
    end_naive = _naive_utc(end_utc)

    listed_ids = _listed_conversation_ids_in_window(db, analytics_rows, start_utc, end_utc)
    drafts_created = int(
        db.query(func.count(Conversation.id))
        .filter(Conversation.created_at >= start_naive, Conversation.created_at < end_naive)
        .scalar()
        or 0
    )

    today_sales = [
        row
        for row in analytics_rows
        if row.sold_at is not None and start_utc <= (_utc(row.sold_at) or start_utc) < end_utc
    ]
    units_sold = len(today_sales)
    revenue = round(sum(float(row.sold_price or 0.0) for row in today_sales), 2)
    known_profit_rows = [row for row in today_sales if row.sold_price is not None and row.has_cost]
    if units_sold == 0:
        profit: float | None = 0.0
    elif known_profit_rows:
        profit = round(sum(float(row.profit) for row in known_profit_rows), 2)
    else:
        profit = None

    promo_rows = (
        db.query(ListingPromotion.action, func.count(ListingPromotion.id), func.count(ListingPromotion.automation_id))
        .filter(
            ListingPromotion.state.in_(_SENT_PROMOTION_STATES),
            ListingPromotion.sent_at.is_not(None),
            ListingPromotion.sent_at >= start_naive,
            ListingPromotion.sent_at < end_naive,
        )
        .group_by(ListingPromotion.action)
        .all()
    )
    shares_sent = 0
    promo_offers_sent = 0
    automated_promotions = 0
    for action, count, automated_count in promo_rows:
        if action == "share":
            shares_sent += int(count or 0)
            automated_promotions += automated_count
        elif action == "offer":
            promo_offers_sent += int(count or 0)
            automated_promotions += automated_count

    ebay_offers_sent = int(
        db.query(func.count(EbayOfferAttempt.id))
        .filter(
            EbayOfferAttempt.state == "sent",
            func.coalesce(EbayOfferAttempt.sent_at, EbayOfferAttempt.created_at) >= start_naive,
            func.coalesce(EbayOfferAttempt.sent_at, EbayOfferAttempt.created_at) < end_naive,
        )
        .scalar()
        or 0
    )
    offers_sent = promo_offers_sent + ebay_offers_sent

    return {
        "date": local_today.isoformat(),
        "timezone": tz.key,
        "units_listed": len(listed_ids),
        "drafts_created": drafts_created,
        "units_sold": units_sold,
        "revenue": revenue,
        "profit": profit,
        "profit_known": len(known_profit_rows),
        "shares_sent": shares_sent,
        "offers_sent": offers_sent,
        "automation_actions": automated_promotions + ebay_offers_sent,
    }


def compute_goal_progress(
    db: Session,
    goal: SellerGoal,
    analytics_rows: list,
    *,
    now_utc: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
    local_today = now_utc.astimezone(tz).date()
    start_day, end_day = period_window(goal.period, local_today)
    start_utc, _ = _day_bounds_utc(start_day, tz)
    _, end_utc = _day_bounds_utc(end_day, tz)

    days_total = (end_day - start_day).days + 1
    days_elapsed = max(1, min(days_total, (local_today - start_day).days + 1))
    days_remaining = max(0, (end_day - local_today).days)
    elapsed_fraction = days_elapsed / days_total

    period_sales = [
        row
        for row in analytics_rows
        if row.sold_at is not None and start_utc <= (_utc(row.sold_at) or start_utc) < end_utc
    ]
    active_count = sum(1 for row in analytics_rows if row.stock and row.status == "active")

    current: float | None
    note: str | None = None
    if goal.metric == "profit":
        known = [row for row in period_sales if row.sold_price is not None and row.has_cost]
        if not period_sales:
            current = 0.0
        elif known:
            current = round(sum(float(row.profit) for row in known), 2)
            if len(known) < len(period_sales):
                note = f"Based on {len(known)} of {len(period_sales)} sales with cost recorded."
        else:
            current = None
            note = "Add item cost on sold listings to track profit."
    elif goal.metric == "sales":
        current = round(sum(float(row.sold_price or 0.0) for row in period_sales), 2)
    elif goal.metric == "listings":
        current = float(len(_listed_conversation_ids_in_window(db, analytics_rows, start_utc, end_utc)))
    elif goal.metric == "sell_through_rate":
        sold_count = len(period_sales)
        denom = sold_count + active_count
        current = round((sold_count / denom) * 100.0, 1) if denom > 0 else 0.0
    else:
        current = 0.0

    target = float(goal.target)
    current_value = float(current) if current is not None else 0.0
    raw_percent = round((current_value / target) * 100.0, 1) if target > 0 else 0.0
    progress_percent = min(100.0, max(0.0, raw_percent))
    remaining = round(max(0.0, target - current_value), 2)
    expected_percent = (
        100.0
        if goal.metric == "sell_through_rate"
        else round(elapsed_fraction * 100.0, 1)
    )
    expected_by_now = (
        round(target, 2)
        if goal.metric == "sell_through_rate"
        else round(target * elapsed_fraction, 2)
    )

    if current is None:
        pace = "behind"
    elif current_value >= target:
        pace = "achieved"
    elif expected_by_now <= 0 or current_value >= expected_by_now * 1.05:
        pace = "ahead"
    elif current_value >= expected_by_now * 0.90:
        pace = "on_track"
    else:
        pace = "behind"

    unit = (
        "currency"
        if goal.metric in ("profit", "sales")
        else "percent"
        if goal.metric == "sell_through_rate"
        else "count"
    )

    return {
        "id": goal.id,
        "period": goal.period,
        "metric": goal.metric,
        "label": goal.label or _default_goal_label(goal.period, goal.metric),
        "custom_label": goal.label,
        "target": target,
        "current": current_value,
        "remaining": remaining,
        "progress_percent": progress_percent,
        "expected_percent": expected_percent,
        "pace": pace,
        "window_start": start_day.isoformat(),
        "window_end": end_day.isoformat(),
        "days_elapsed": days_elapsed,
        "days_total": days_total,
        "days_remaining": days_remaining,
        "unit": unit,
        "note": note,
    }


def _duplicate_key(first: str, second: str) -> str:
    a, b = sorted((first, second))
    return f"duplicate:{a}:{b}"


def _collect_attention_items(db: Session, *, now_utc: datetime, dismissed_keys: set[str]) -> list[dict[str, Any]]:
    from anteroom.services import marketplace_accounts, sale_delist_followup, similar_items
    from anteroom.services.unsent_edits import unsent_edits

    items: list[dict[str, Any]] = []

    # 1. Sold elsewhere / still listed alerts
    try:
        with db.begin_nested():
            alerts = sale_delist_followup.still_listed(db)
        for alert in alerts:
            conv_id = str(alert["conversationId"])
            sale_id = str(alert["saleId"])
            markets = ", ".join(_market_name(entry.get("marketplace")) for entry in alert.get("stillListed") or [])
            sold_on = _market_name(alert.get("soldOn"))
            items.append({
                "key": f"still-listed:{conv_id}:{sale_id}",
                "kind": "still_listed",
                "priority": "high",
                "title": str(alert.get("title") or "Untitled listing"),
                "detail": f"Sold on {sold_on}, still listed on {markets}.",
                "action_label": "Open listing",
                "target": {"view": "listings", "conversation_id": conv_id},
                "occurred_at": alert.get("soldAt"),
            })
    except Exception:
        pass

    # 2. Expired or disconnected marketplace sessions/connections
    seen_session_markets: set[str] = set()
    try:
        with db.begin_nested():
            cloud_rows = db.query(CloudMarketplaceSession).all()
        for row in cloud_rows:
            expires = _utc(row.expires_at)
            is_expired = row.state == "connected" and expires is not None and expires <= now_utc
            if row.state in _DISCONNECTED_CLOUD_STATES or is_expired:
                market = str(row.marketplace).lower()
                seen_session_markets.add(market)
                reason = row.last_error or (
                    "Session expired. Reconnect in Settings → Connections."
                    if row.state == "expired" or is_expired
                    else "Marketplace session disconnected. Reconnect in Settings → Connections."
                )
                items.append({
                    "key": f"session:{market}",
                    "kind": "expired_session",
                    "priority": "high",
                    "title": f"{_market_name(market)} session needs reconnect",
                    "detail": reason,
                    "action_label": "Open Connections",
                    "target": {"view": "settings", "settings_section": "connections"},
                    "occurred_at": _iso(row.updated_at),
                })
    except Exception:
        pass

    try:
        with db.begin_nested():
            account_status = marketplace_accounts.status()
        for market, info in (account_status.get("accounts") or {}).items():
            if not isinstance(info, dict):
                continue
            error = info.get("error")
            if error and market not in seen_session_markets:
                seen_session_markets.add(market)
                connected_at = info.get("connectedAt")
                items.append({
                    "key": f"session:{market}",
                    "kind": "expired_session",
                    "priority": "high",
                    "title": f"{_market_name(market)} connection needs sign-in",
                    "detail": str(error),
                    "action_label": "Open Connections",
                    "target": {"view": "settings", "settings_section": "connections"},
                    "occurred_at": (
                        datetime.fromtimestamp(connected_at, UTC).isoformat()
                        if isinstance(connected_at, (int, float))
                        else None
                    ),
                })
    except Exception:
        pass

    conversations = db.query(Conversation).all()
    conv_by_id = {conv.id: conv for conv in conversations}

    # 3. Failed jobs / listing publish errors. Job snapshots can be megabytes
    # each, and the inbox only needs the latest status and error text.
    seen_failed_convs: set[str] = set()
    latest_jobs = (
        db.query(Job.id, Job.conversation_id, Job.status, Job.last_error, Job.updated_at, Job.created_at)
        .order_by(Job.created_at.desc())
    )
    latest_job_by_conv = {}
    for job in latest_jobs:
        if job.conversation_id not in latest_job_by_conv:
            latest_job_by_conv[job.conversation_id] = job
    for conv_id, job in latest_job_by_conv.items():
        if job.status == "failed" and conv_id in conv_by_id:
            seen_failed_convs.add(conv_id)
            conv = conv_by_id[conv_id]
            detail = (job.last_error or "A recent listing job failed.").strip()
            items.append({
                "key": f"failed-job:{conv_id}:{job.id}",
                "kind": "failed_job",
                "priority": "high",
                "title": conv.title or "Untitled listing",
                "detail": detail[:200],
                "action_label": "Inspect listing",
                "target": {"view": "listings", "conversation_id": conv_id},
                "occurred_at": _iso(job.updated_at or job.created_at),
            })

    failed_listings = (
        db.query(MarketplaceListing)
        .filter(
            (MarketplaceListing.state.in_(_FAILED_LISTING_STATES))
            | (MarketplaceListing.error.is_not(None))
        )
        .all()
    )
    for ml in failed_listings:
        if not ml.error and ml.state not in _FAILED_LISTING_STATES:
            continue
        conv = conv_by_id.get(ml.conversation_id)
        if conv is None:
            continue
        if ml.conversation_id in seen_failed_convs and ml.state not in _FAILED_LISTING_STATES:
            continue
        detail = (ml.error or f"{_market_name(ml.marketplace)} listing error.").strip()
        items.append({
            "key": f"publish-error:{ml.conversation_id}:{ml.marketplace}",
            "kind": "failed_job",
            "priority": "high",
            "title": conv.title or "Untitled listing",
            "detail": f"{_market_name(ml.marketplace)}: {detail[:180]}",
            "action_label": "Fix listing",
            "target": {"view": "listings", "conversation_id": ml.conversation_id},
            "occurred_at": _iso(ml.updated_at),
        })

    # 4. Listings with unsent live edits
    try:
        with db.begin_nested():
            revisions = {
                conv_id: rev_id
                for conv_id, rev_id in db.query(Listing.conversation_id, Listing.current_revision_id).all()
                if rev_id
            }
            unsent_ids = unsent_edits(db, conversations, revisions, commit_backfill=False)
        for conv_id in sorted(unsent_ids):
            conv = conv_by_id.get(conv_id)
            if conv is None:
                continue
            items.append({
                "key": f"unsent:{conv_id}",
                "kind": "unsent_edits",
                "priority": "medium",
                "title": conv.title or "Untitled listing",
                "detail": "Saved local edits have not been sent to live marketplaces yet.",
                "action_label": "Review edits",
                "target": {"view": "listings", "conversation_id": conv_id},
                "occurred_at": _iso(conv.updated_at),
            })
    except Exception:
        pass

    # 5a. Pending automation approvals (waiting for approval)
    pending_approvals = (
        db.query(AutomationApproval)
        .filter(AutomationApproval.status == "pending")
        .order_by(AutomationApproval.created_at.desc())
        .all()
    )
    for approval in pending_approvals:
        markets = ", ".join(_market_name(m) for m in (approval.marketplaces or []))
        items.append({
            "key": f"approval:{approval.id}",
            "kind": "pending_approval",
            "priority": "medium",
            "title": approval.title or "Automation change waiting for approval",
            "detail": approval.review_basis or f"Waiting for approval{f' on {markets}' if markets else ''}.",
            "action_label": "Review approval",
            "target": {"view": "automations", "conversation_id": approval.conversation_id},
            "occurred_at": _iso(approval.created_at),
        })

    # 5b. Unresolved relist gaps (down, needs relist)
    relist_gaps = (
        db.query(AutomationRelistGap)
        .filter(AutomationRelistGap.resolved_at.is_(None))
        .order_by(AutomationRelistGap.created_at.desc())
        .all()
    )
    for gap in relist_gaps:
        items.append({
            "key": f"relist-gap:{gap.id}",
            "kind": "relist_gap",
            "priority": "high",
            "title": gap.title or "Listing down, needs relist",
            "detail": f"Down on {_market_name(gap.marketplace)} — {gap.error or gap.state or 'needs relist'}.",
            "action_label": "Open Automations",
            "target": {"view": "automations", "conversation_id": gap.conversation_id},
            "occurred_at": _iso(gap.created_at),
        })

    # 6. Potential duplicate / similar listings (read-only, no Storage fetches)
    try:
        with db.begin_nested():
            similar = similar_items.find_similar(
                db, compute_fingerprints=False, limit=25,
                include_beyond_limit=lambda a, b: _duplicate_key(a, b) in dismissed_keys,
            )
        for pair in similar.get("pairs") or []:
            first = pair.get("first") or {}
            second = pair.get("second") or {}
            first_id = str(first.get("id") or "")
            second_id = str(second.get("id") or "")
            if not first_id or not second_id:
                continue
            reasons = ", ".join(str(r).replace("_", " ") for r in (pair.get("reasons") or []))
            items.append({
                "key": _duplicate_key(first_id, second_id),
                "kind": "duplicate_listing",
                "priority": "low",
                "title": f"Possible duplicate: {first.get('title') or 'Untitled'}",
                "detail": f"Matches “{second.get('title') or 'Untitled'}” ({reasons or 'similar item'}).",
                "action_label": "Review duplicates",
                "target": {"view": "settings", "settings_section": "listings", "target_id": "similar-items"},
                "occurred_at": None,
            })
    except Exception:
        pass

    items.sort(
        key=lambda item: (
            _PRIORITY_ORDER.get(item["priority"], 9),
            "" if item.get("occurred_at") is None else item["occurred_at"],
            item["title"].casefold(),
        ),
    )
    return items


def home_summary(
    db: Session,
    *,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from anteroom.services.inventory_analytics import load_rows

    clock = _utc(now) or datetime.now(UTC)
    tz = _zone(tz_name)
    analytics_rows = load_rows(db)

    today = _today_summary(db, analytics_rows, now_utc=clock, tz=tz)

    goal_rows = (
        db.query(SellerGoal)
        .order_by(SellerGoal.created_at.asc(), SellerGoal.id.asc())
        .all()
    )
    goals = [compute_goal_progress(db, goal, analytics_rows, now_utc=clock, tz=tz) for goal in goal_rows]

    now_naive = _naive_utc(clock)
    dismissals = {
        row.item_key: row
        for row in db.query(AttentionDismissal).filter(AttentionDismissal.expires_at > now_naive).all()
    }

    all_items = _collect_attention_items(db, now_utc=clock, dismissed_keys=set(dismissals))

    active_items: list[dict[str, Any]] = []
    dismissed_items: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}

    for item in all_items:
        dismissal = dismissals.get(item["key"])
        if dismissal is not None:
            expires_utc = _utc(dismissal.expires_at) or clock
            remaining_seconds = max(0.0, (expires_utc - clock).total_seconds())
            reappears_in_days = max(1, int((remaining_seconds + 86399) // 86400))
            dismissed_items.append({
                **item,
                "dismissed_at": _iso(dismissal.dismissed_at),
                "expires_at": _iso(dismissal.expires_at),
                "reappears_in_days": reappears_in_days,
            })
        else:
            active_items.append(item)
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1

    return {
        "today": today,
        "goals": goals,
        "needs_attention": active_items,
        "dismissed": dismissed_items,
        "counts": {
            "active": len(active_items),
            "dismissed": len(dismissed_items),
            "by_kind": by_kind,
        },
    }


def upsert_goal(
    db: Session,
    *,
    period: str,
    metric: str,
    target: float,
    label: str | None = None,
) -> str:
    if period not in GOAL_PERIODS:
        raise HomeGoalError(f"Period must be one of: {', '.join(GOAL_PERIODS)}.")
    if metric not in GOAL_METRICS:
        raise HomeGoalError(f"Metric must be one of: {', '.join(GOAL_METRICS)}.")
    if target <= 0:
        raise HomeGoalError("Goal target must be greater than zero.")
    if metric == "sell_through_rate" and target > 100:
        raise HomeGoalError("Sell-through rate target cannot exceed 100%.")
    cleaned_label = " ".join((label or "").split()).strip() or None
    if cleaned_label and len(cleaned_label) > 80:
        raise HomeGoalError("Goal label must be 80 characters or fewer.")

    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    values = {"target": float(target), "label": cleaned_label, "updated_at": _naive_utc(datetime.now(UTC))}
    statement = insert(SellerGoal).values(
        user_id=getattr(db, "user_id", LOCAL_USER_ID), period=period, metric=metric, **values,
    )
    goal_id = db.execute(statement.on_conflict_do_update(
        index_elements=[SellerGoal.user_id, SellerGoal.period, SellerGoal.metric], set_=values,
    ).returning(SellerGoal.id)).scalar_one()
    db.commit()
    return goal_id


def update_goal(
    db: Session,
    goal_id: str,
    *,
    period: str | None = None,
    metric: str | None = None,
    target: float | None = None,
    label: str | None = None,
    label_provided: bool = False,
) -> str | None:
    if db.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Reserve the writer before reading fields
        # that a concurrent partial edit could change.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    row = db.get(SellerGoal, goal_id, with_for_update=True, populate_existing=True)
    if row is None:
        return None
    next_period = period if period is not None else row.period
    next_metric = metric if metric is not None else row.metric
    next_target = float(target) if target is not None else row.target
    if next_period not in GOAL_PERIODS:
        raise HomeGoalError(f"Period must be one of: {', '.join(GOAL_PERIODS)}.")
    if next_metric not in GOAL_METRICS:
        raise HomeGoalError(f"Metric must be one of: {', '.join(GOAL_METRICS)}.")
    if next_target <= 0:
        raise HomeGoalError("Goal target must be greater than zero.")
    if next_metric == "sell_through_rate" and next_target > 100:
        raise HomeGoalError("Sell-through rate target cannot exceed 100%.")
    conflict = (
        db.query(SellerGoal)
        .filter(
            SellerGoal.id != goal_id,
            SellerGoal.period == next_period,
            SellerGoal.metric == next_metric,
        )
        .first()
    )
    if conflict is not None:
        raise HomeGoalError("A goal for that period and metric already exists.")
    row.period = next_period
    row.metric = next_metric
    row.target = next_target
    if label_provided:
        cleaned = " ".join((label or "").split()).strip() or None
        if cleaned and len(cleaned) > 80:
            raise HomeGoalError("Goal label must be 80 characters or fewer.")
        row.label = cleaned
    row.updated_at = _naive_utc(datetime.now(UTC))
    db.commit()
    return goal_id


def delete_goal(db: Session, goal_id: str) -> bool:
    row = db.get(SellerGoal, goal_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def dismiss_attention_item(
    db: Session,
    *,
    item_key: str,
    kind: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    cleaned_key = (item_key or "").strip()
    cleaned_kind = (kind or "").strip() or "general"
    if not cleaned_key or len(cleaned_key) > 200:
        raise ValueError("Invalid attention item key.")
    clock = _utc(now) or datetime.now(UTC)
    dismissed_at = _naive_utc(clock)
    expires_at = _naive_utc(clock + timedelta(days=DISMISS_TTL_DAYS))
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    values = {"kind": cleaned_kind[:40], "dismissed_at": dismissed_at, "expires_at": expires_at}
    statement = insert(AttentionDismissal).values(
        user_id=getattr(db, "user_id", LOCAL_USER_ID), item_key=cleaned_key, **values,
    )
    db.execute(statement.on_conflict_do_update(
        index_elements=[AttentionDismissal.user_id, AttentionDismissal.item_key], set_=values,
    ))
    db.commit()
    return {
        "item_key": cleaned_key,
        "dismissed_at": _iso(dismissed_at),
        "expires_at": _iso(expires_at),
        "reappears_in_days": DISMISS_TTL_DAYS,
    }


def attention_item_kinds(db: Session, *, now: datetime | None = None) -> dict[str, str]:
    """Every Needs Attention item shown right now, dismissed or not, by key."""
    clock = _utc(now) or datetime.now(UTC)
    now_naive = _naive_utc(clock)
    dismissed = {
        row.item_key
        for row in db.query(AttentionDismissal).filter(AttentionDismissal.expires_at > now_naive).all()
    }
    return {
        item["key"]: item["kind"]
        for item in _collect_attention_items(db, now_utc=clock, dismissed_keys=dismissed)
    }


def restore_attention_item(db: Session, *, item_key: str) -> bool:
    cleaned_key = (item_key or "").strip()
    if not cleaned_key:
        return False
    row = db.query(AttentionDismissal).filter(AttentionDismissal.item_key == cleaned_key).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
