"""Hosted subscription tiers, Smart Credit weights, and per-seller usage metering.

Desktop execution (where ``service_context.current()`` is ``None``) uses the
seller's own provider keys and is never metered or limited by subscription
tiers. Hosted accounts default to the Free tier unless assigned another plan
in ``seller_plans`` or matched by ``ANTEROOM_UNLIMITED_EMAILS``.

Monthly cap enforcement stays OFF by default (``PLAN_LIMITS_ENFORCED = False``)
across hosted web, iPhone/API, MCP, and background processing until an
explicitly approved future release ships with a working upgrade path. Tiers,
assignments, usage metering, and photo-credit refunds remain active while
enforcement is off.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.models.subscription import PlanUsageEvent, SellerPlan
from anteroom.services.photoroom import StudioOptions
from anteroom.services.service_context import current

_EXEMPT_LISTED_ITEM: ContextVar[bool] = ContextVar("anteroom_exempt_listed_item", default=False)

# Cap enforcement stays off until an explicitly approved release adds a working
# self-serve upgrade path (see deploy/fly/README.md for activation prerequisites).
PLAN_LIMITS_ENFORCED: bool = False


def limits_enforced() -> bool:
    """Return whether hosted Smart Credit and Free listed-item caps block requests."""
    return PLAN_LIMITS_ENFORCED


@contextmanager
def exempt_listed_item_count():
    """Exempt relists (take down + re-list of existing live items) from Free monthly item counting."""
    token = _EXEMPT_LISTED_ITEM.set(True)
    try:
        yield
    finally:
        _EXEMPT_LISTED_ITEM.reset(token)


def is_listed_item_exempt() -> bool:
    return _EXEMPT_LISTED_ITEM.get()

REASON_CREDIT_LIMIT = "plan_credit_limit"
REASON_LISTING_LIMIT = "plan_listing_limit"

# Smart Credit weights (1 credit ≈ $0.02 COGS).
AI_LISTING_CREDITS = 5
PHOTO_CUTOUT_CREDITS = 1
PHOTO_STUDIO_CREDITS = 5

KIND_AI_LISTING = "ai_listing"
KIND_PHOTO_CUTOUT = "photo_cutout"
KIND_PHOTO_STUDIO = "photo_studio"
KIND_PHOTO_REFUND = "photo_refund"
KIND_LISTED_ITEM = "listed_item"

BILLING_INTERVALS = frozenset({"monthly", "annual"})
UNLIMITED_EMAILS_ENV = "ANTEROOM_UNLIMITED_EMAILS"
DEFAULT_UNLIMITED_EMAILS = frozenset({
    "cristianarielcanizares@gmail.com",
    "xyzcanizarez@gmail.com",
})


@dataclass(frozen=True)
class PlanDefinition:
    id: str
    name: str
    tagline: str
    monthly_price_cents: int
    annual_price_cents: int
    monthly_credits: int | None
    monthly_listed_items: int | None
    public: bool = True
    recommended: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "monthly_price_cents": self.monthly_price_cents,
            "annual_price_cents": self.annual_price_cents,
            "monthly_credits": self.monthly_credits,
            "monthly_listed_items": self.monthly_listed_items,
            "recommended": self.recommended,
        }


PLANS: tuple[PlanDefinition, ...] = (
    PlanDefinition(
        id="free",
        name="Free",
        tagline="Try Anteroom with real listings",
        monthly_price_cents=0,
        annual_price_cents=0,
        monthly_credits=50,
        monthly_listed_items=10,
        public=True,
    ),
    PlanDefinition(
        id="lister",
        name="Lister",
        tagline="Unlimited manual & bulk cross-listing",
        monthly_price_cents=1499,
        annual_price_cents=14990,
        monthly_credits=100,
        monthly_listed_items=None,
        public=True,
    ),
    PlanDefinition(
        id="starter",
        name="Starter",
        tagline="For part-time resellers using AI every week",
        monthly_price_cents=2999,
        annual_price_cents=29990,
        monthly_credits=500,
        monthly_listed_items=None,
        public=True,
    ),
    PlanDefinition(
        id="pro",
        name="Pro",
        tagline="Full-time resellers with AI drafting & AI Studio",
        monthly_price_cents=4999,
        annual_price_cents=49990,
        monthly_credits=1200,
        monthly_listed_items=None,
        public=True,
        recommended=True,
    ),
    PlanDefinition(
        id="business",
        name="Business",
        tagline="High-volume shops and teams",
        monthly_price_cents=8999,
        annual_price_cents=89990,
        monthly_credits=2500,
        monthly_listed_items=None,
        public=True,
    ),
    PlanDefinition(
        id="unlimited",
        name="Unlimited",
        tagline="Unmetered internal account",
        monthly_price_cents=0,
        annual_price_cents=0,
        monthly_credits=None,
        monthly_listed_items=None,
        public=False,
    ),
)

PLANS_BY_ID: dict[str, PlanDefinition] = {plan.id: plan for plan in PLANS}
PUBLIC_PLANS: tuple[PlanDefinition, ...] = tuple(plan for plan in PLANS if plan.public)


class PlanLimitError(HTTPException):
    """Raised when a hosted seller exhausts a monthly Smart Credit or Free listed-item limit."""

    def __init__(self, detail: str, *, reason: str):
        super().__init__(status_code=402, detail=detail, headers={"X-Anteroom-Reason": reason})
        self.reason = reason


def unlimited_emails() -> frozenset[str]:
    """Emails granted the unmetered internal Unlimited plan.

    Defaults to the two approved owner accounts when ``ANTEROOM_UNLIMITED_EMAILS``
    is unset. Setting the env var overrides the list (an empty string disables it).
    """
    raw = os.environ.get(UNLIMITED_EMAILS_ENV)
    if raw is None:
        return DEFAULT_UNLIMITED_EMAILS
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def current_period(now: datetime | None = None) -> str:
    """Current UTC billing period key (``YYYY-MM``)."""
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    return f"{instant.year:04d}-{instant.month:02d}"


def next_reset_iso(now: datetime | None = None) -> str:
    """ISO-8601 timestamp when the current UTC calendar month resets."""
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    if instant.month == 12:
        nxt = datetime(instant.year + 1, 1, 1, tzinfo=UTC)
    else:
        nxt = datetime(instant.year, instant.month + 1, 1, tzinfo=UTC)
    return nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


def photo_credit_cost(options: StudioOptions) -> tuple[str, int]:
    """Smart Credit kind and cost for an AI Studio edit.

    Plain solid-background removal costs 1 credit ($0.02 COGS via Photoroom Basic);
    generative edits (shadows, ghost mannequin, flat lay, virtual model, or
    text-guided keep/exclude) cost 5 credits ($0.10 COGS via Photoroom Plus).
    """
    if options.plain_cutout:
        return KIND_PHOTO_CUTOUT, PHOTO_CUTOUT_CREDITS
    return KIND_PHOTO_STUDIO, PHOTO_STUDIO_CREDITS


def _lock_seller_usage(db: Session, user_id: str) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"anteroom-plan-usage:{user_id}"},
        )


def _active_seller() -> tuple[str, str | None] | None:
    ctx = current()
    if ctx is None:
        return None
    return ctx.seller_id, ctx.seller_email


def resolve_plan(db: Session, user_id: str, email: str | None = None) -> tuple[PlanDefinition, str]:
    """Return ``(PlanDefinition, billing_interval)`` for ``user_id``."""
    resolved_email = email
    if resolved_email is None:
        active = _active_seller()
        if active is not None and active[0] == user_id:
            resolved_email = active[1]
    if resolved_email and resolved_email.strip().lower() in unlimited_emails():
        return PLANS_BY_ID["unlimited"], "monthly"
    row = db.scalar(select(SellerPlan).where(SellerPlan.user_id == user_id))
    if row is not None and row.plan_id in PLANS_BY_ID:
        interval = row.billing_interval if row.billing_interval in BILLING_INTERVALS else "monthly"
        return PLANS_BY_ID[row.plan_id], interval
    return PLANS_BY_ID["free"], "monthly"


def credits_used(db: Session, user_id: str, period: str) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(PlanUsageEvent.credits), 0)).where(
            PlanUsageEvent.user_id == user_id,
            PlanUsageEvent.period == period,
            PlanUsageEvent.kind != KIND_LISTED_ITEM,
        )
    )
    return max(0, int(total or 0))


def listed_items_used(db: Session, user_id: str, period: str) -> int:
    total = db.scalar(
        select(func.count(func.distinct(PlanUsageEvent.conversation_id))).where(
            PlanUsageEvent.user_id == user_id,
            PlanUsageEvent.period == period,
            PlanUsageEvent.kind == KIND_LISTED_ITEM,
            PlanUsageEvent.conversation_id.is_not(None),
        )
    )
    return int(total or 0)


def _credit_limit_message(plan: PlanDefinition, *, needed: int, remaining: int, action: str) -> str:
    if remaining <= 0:
        return (
            f"You've used all {plan.monthly_credits:,} Smart Credits on your {plan.name} plan this month. "
            f"Manual listing and editing still work; upgrade in Settings → Usage for more AI {action}."
        )
    return (
        f"This {action} needs {needed} Smart Credits, and you have {remaining} left on your {plan.name} plan this month. "
        f"Upgrade in Settings → Usage or choose fewer items."
    )


def _listing_limit_message(plan: PlanDefinition) -> str:
    return (
        f"You've reached the {plan.monthly_listed_items}-item monthly listing limit on the {plan.name} plan. "
        f"You can still cross-list those {plan.monthly_listed_items} items to more marketplaces, update, or delist; "
        f"upgrade to Lister ($14.99/mo) in Settings → Usage for unlimited manual & bulk listing."
    )


def charge_ai_listing(
    db: Session,
    conversation_id: str,
    *,
    reference_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Record (and enforce when active) Smart Credits for an AI listing generation in the caller's transaction.

    Returns the credits charged (``0`` on desktop). Does not commit; the caller
    commits atomically alongside enqueuing the draft task.
    """
    active = _active_seller()
    if active is None:
        return 0
    user_id, email = active
    _lock_seller_usage(db, user_id)
    plan, _ = resolve_plan(db, user_id, email)
    period = current_period(now)
    if limits_enforced() and plan.monthly_credits is not None:
        used = credits_used(db, user_id, period)
        remaining = max(0, plan.monthly_credits - used)
        if used + AI_LISTING_CREDITS > plan.monthly_credits:
            raise PlanLimitError(
                _credit_limit_message(plan, needed=AI_LISTING_CREDITS, remaining=remaining, action="listing generation"),
                reason=REASON_CREDIT_LIMIT,
            )
    db.add(
        PlanUsageEvent(
            user_id=user_id,
            period=period,
            kind=KIND_AI_LISTING,
            credits=AI_LISTING_CREDITS,
            conversation_id=conversation_id,
            reference_id=reference_id,
            created_at=now or datetime.now(UTC),
        )
    )
    return AI_LISTING_CREDITS


def check_photo_batch(
    db: Session,
    count: int,
    options: StudioOptions,
    *,
    now: datetime | None = None,
) -> None:
    """Pre-flight check that the seller can afford ``count`` AI Studio photos when enforcement is active."""
    active = _active_seller()
    if active is None or count <= 0 or not limits_enforced():
        return
    user_id, email = active
    plan, _ = resolve_plan(db, user_id, email)
    if plan.monthly_credits is None:
        return
    _kind, per_photo = photo_credit_cost(options)
    needed = count * per_photo
    used = credits_used(db, user_id, current_period(now))
    remaining = max(0, plan.monthly_credits - used)
    if used + needed > plan.monthly_credits:
        raise PlanLimitError(
            _credit_limit_message(plan, needed=needed, remaining=remaining, action="AI Studio edit"),
            reason=REASON_CREDIT_LIMIT,
        )


def reserve_photo_credit(
    db: Session,
    conversation_id: str,
    photo_id: str,
    options: StudioOptions,
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Atomically meter (and enforce when active) credits for one AI Studio photo call before hitting Photoroom."""
    active = _active_seller()
    if active is None:
        return 0, ""
    user_id, email = active
    _lock_seller_usage(db, user_id)
    plan, _ = resolve_plan(db, user_id, email)
    kind, cost = photo_credit_cost(options)
    period = current_period(now)
    if limits_enforced() and plan.monthly_credits is not None:
        used = credits_used(db, user_id, period)
        remaining = max(0, plan.monthly_credits - used)
        if used + cost > plan.monthly_credits:
            raise PlanLimitError(
                _credit_limit_message(plan, needed=cost, remaining=remaining, action="AI Studio edit"),
                reason=REASON_CREDIT_LIMIT,
            )
    db.add(
        PlanUsageEvent(
            user_id=user_id,
            period=period,
            kind=kind,
            credits=cost,
            conversation_id=conversation_id,
            reference_id=photo_id,
            created_at=now or datetime.now(UTC),
        )
    )
    db.commit()
    return cost, period


def refund_photo_credit(
    db: Session,
    conversation_id: str,
    photo_id: str,
    credits: int,
    period: str | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Refund reserved photo credits into the period originally charged when Photoroom fails."""
    if credits <= 0:
        return
    active = _active_seller()
    if active is None:
        return
    user_id, _ = active
    db.add(
        PlanUsageEvent(
            user_id=user_id,
            period=period or current_period(now),
            kind=KIND_PHOTO_REFUND,
            credits=-credits,
            conversation_id=conversation_id,
            reference_id=photo_id,
            created_at=now or datetime.now(UTC),
        )
    )
    db.commit()


def _item_already_counted(db: Session, user_id: str, period: str, conversation_id: str) -> bool:
    existing = db.scalar(
        select(PlanUsageEvent.id)
        .where(
            PlanUsageEvent.user_id == user_id,
            PlanUsageEvent.period == period,
            PlanUsageEvent.kind == KIND_LISTED_ITEM,
            PlanUsageEvent.conversation_id == conversation_id,
        )
        .limit(1)
    )
    return existing is not None


def check_listed_item_allowance(
    db: Session,
    conversation_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Unlocked pre-check before Chrome preview / photo capture for Free-tier item cap when active."""
    active = _active_seller()
    if active is None or not limits_enforced():
        return
    user_id, email = active
    plan, _ = resolve_plan(db, user_id, email)
    if plan.monthly_listed_items is None:
        return
    period = current_period(now)
    if _item_already_counted(db, user_id, period, conversation_id):
        return
    if listed_items_used(db, user_id, period) >= plan.monthly_listed_items:
        raise PlanLimitError(_listing_limit_message(plan), reason=REASON_LISTING_LIMIT)


def require_and_record_listed_item(
    db: Session,
    conversation_id: str,
    *,
    marketplace: str,
    now: datetime | None = None,
) -> None:
    """Atomically meter (and enforce when active) the Free-tier monthly distinct-item cap in the caller's tx."""
    active = _active_seller()
    if active is None:
        return
    user_id, email = active
    _lock_seller_usage(db, user_id)
    plan, _ = resolve_plan(db, user_id, email)
    period = current_period(now)
    if _item_already_counted(db, user_id, period, conversation_id):
        return
    if limits_enforced() and plan.monthly_listed_items is not None:
        if listed_items_used(db, user_id, period) >= plan.monthly_listed_items:
            raise PlanLimitError(_listing_limit_message(plan), reason=REASON_LISTING_LIMIT)
    db.add(
        PlanUsageEvent(
            user_id=user_id,
            period=period,
            kind=KIND_LISTED_ITEM,
            credits=0,
            conversation_id=conversation_id,
            reference_id=marketplace,
            created_at=now or datetime.now(UTC),
        )
    )


def seller_subscription_summary(
    db: Session,
    user_id: str,
    *,
    email: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Current plan, monthly Smart Credit balance, listed items, and public catalog for Settings → Usage."""
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    period = current_period(instant)
    plan, billing_interval = resolve_plan(db, user_id, email)
    rows = db.execute(
        select(
            PlanUsageEvent.kind,
            func.count(PlanUsageEvent.id),
            func.coalesce(func.sum(PlanUsageEvent.credits), 0),
        )
        .where(
            PlanUsageEvent.user_id == user_id,
            PlanUsageEvent.period == period,
            PlanUsageEvent.kind != KIND_LISTED_ITEM,
        )
        .group_by(PlanUsageEvent.kind)
    ).all()

    by_kind: dict[str, tuple[int, int]] = {
        str(kind): (int(count or 0), int(credits or 0))
        for kind, count, credits in rows
    }
    used_credits = max(0, sum(credits for _count, credits in by_kind.values()))
    used_items = listed_items_used(db, user_id, period)

    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "tagline": plan.tagline,
            "monthly_price_cents": plan.monthly_price_cents,
            "annual_price_cents": plan.annual_price_cents,
            "monthly_credits": plan.monthly_credits,
            "monthly_listed_items": plan.monthly_listed_items,
            "billing_interval": billing_interval,
            "unlimited": plan.monthly_credits is None and plan.monthly_listed_items is None,
        },
        "period": period,
        "resets_at": next_reset_iso(instant),
        "limits_enforced": limits_enforced(),
        "credits": {
            "used": used_credits,
            "limit": plan.monthly_credits,
            "remaining": None if plan.monthly_credits is None else max(0, plan.monthly_credits - used_credits),
            "breakdown": {
                KIND_AI_LISTING: {
                    "count": by_kind.get(KIND_AI_LISTING, (0, 0))[0],
                    "credits": by_kind.get(KIND_AI_LISTING, (0, 0))[1],
                },
                KIND_PHOTO_CUTOUT: {
                    "count": by_kind.get(KIND_PHOTO_CUTOUT, (0, 0))[0],
                    "credits": by_kind.get(KIND_PHOTO_CUTOUT, (0, 0))[1],
                },
                KIND_PHOTO_STUDIO: {
                    "count": by_kind.get(KIND_PHOTO_STUDIO, (0, 0))[0],
                    "credits": by_kind.get(KIND_PHOTO_STUDIO, (0, 0))[1],
                },
                KIND_PHOTO_REFUND: {
                    "count": by_kind.get(KIND_PHOTO_REFUND, (0, 0))[0],
                    "credits": by_kind.get(KIND_PHOTO_REFUND, (0, 0))[1],
                },
            },
        },
        "listed_items": {
            "used": used_items,
            "limit": plan.monthly_listed_items,
            "remaining": None if plan.monthly_listed_items is None else max(0, plan.monthly_listed_items - used_items),
        },
        "credit_weights": {
            KIND_AI_LISTING: AI_LISTING_CREDITS,
            KIND_PHOTO_CUTOUT: PHOTO_CUTOUT_CREDITS,
            KIND_PHOTO_STUDIO: PHOTO_STUDIO_CREDITS,
            "manual_listing": 0,
        },
        "plans": [item.to_public_dict() for item in PUBLIC_PLANS],
    }


def _normalize_target_user(user_id: str) -> str:
    try:
        normalized = str(UUID(user_id))
    except (ValueError, TypeError) as exc:
        raise ValueError("user_id must be a valid UUID") from exc
    if normalized == LOCAL_USER_ID:
        raise ValueError("Cannot assign a subscription plan to the local desktop owner")
    return normalized


def _validate_assignment(plan_id: str, billing_interval: str) -> tuple[str, str]:
    pid = plan_id.strip().lower()
    if pid not in PLANS_BY_ID:
        allowed = ", ".join(PLANS_BY_ID)
        raise ValueError(f"Unknown plan_id '{plan_id}'. Choose one of: {allowed}")
    interval = billing_interval.strip().lower()
    if interval not in BILLING_INTERVALS:
        raise ValueError("billing_interval must be 'monthly' or 'annual'")
    return pid, interval


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def set_seller_plan(
    user_id: str,
    plan_id: str,
    *,
    billing_interval: str = "monthly",
    assigned_by: str = "operator",
    note: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Assign or update a seller's subscription tier (operator-only)."""
    owner = _normalize_target_user(user_id)
    pid, interval = _validate_assignment(plan_id, billing_interval)
    clean_note = note.strip()[:256] if isinstance(note, str) and note.strip() else None

    if db is not None:
        now = datetime.now(UTC)
        row = db.scalar(select(SellerPlan).where(SellerPlan.user_id == owner))
        if row is None:
            row = SellerPlan(
                user_id=owner,
                plan_id=pid,
                billing_interval=interval,
                assigned_by=assigned_by,
                note=clean_note,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.plan_id = pid
            row.billing_interval = interval
            row.assigned_by = assigned_by
            row.note = clean_note
            row.updated_at = now
        db.commit()
        return {
            "user_id": owner,
            "plan_id": row.plan_id,
            "billing_interval": row.billing_interval,
            "assigned_by": row.assigned_by,
            "note": row.note,
            "created_at": _iso_or_none(row.created_at),
            "updated_at": _iso_or_none(row.updated_at),
        }

    from anteroom import database

    if database.engine.dialect.name == "postgresql":
        with database.engine.begin() as conn:
            result = conn.execute(
                text(
                    "SELECT user_id, plan_id, billing_interval, assigned_by, note, created_at, updated_at "
                    "FROM anteroom_private.set_seller_plan(:uid, :pid, :interval, :assigned_by, :note)"
                ),
                {
                    "uid": owner,
                    "pid": pid,
                    "interval": interval,
                    "assigned_by": assigned_by,
                    "note": clean_note,
                },
            ).mappings().one()
        return {
            "user_id": str(result["user_id"]),
            "plan_id": str(result["plan_id"]),
            "billing_interval": str(result["billing_interval"]),
            "assigned_by": result["assigned_by"],
            "note": result["note"],
            "created_at": _iso_or_none(result["created_at"]),
            "updated_at": _iso_or_none(result["updated_at"]),
        }

    with database.SessionLocal() as session:
        return set_seller_plan(
            owner,
            pid,
            billing_interval=interval,
            assigned_by=assigned_by,
            note=clean_note,
            db=session,
        )


def list_seller_plans(
    *,
    period: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """List every assigned seller plan and active-month usage for operator administration."""
    target_period = period or current_period()

    if db is not None:
        plans = {row.user_id: row for row in db.scalars(select(SellerPlan)).all()}
        credit_rows = db.execute(
            select(PlanUsageEvent.user_id, func.coalesce(func.sum(PlanUsageEvent.credits), 0))
            .where(PlanUsageEvent.period == target_period, PlanUsageEvent.kind != KIND_LISTED_ITEM)
            .group_by(PlanUsageEvent.user_id)
        ).all()
        item_rows = db.execute(
            select(PlanUsageEvent.user_id, func.count(func.distinct(PlanUsageEvent.conversation_id)))
            .where(
                PlanUsageEvent.period == target_period,
                PlanUsageEvent.kind == KIND_LISTED_ITEM,
                PlanUsageEvent.conversation_id.is_not(None),
            )
            .group_by(PlanUsageEvent.user_id)
        ).all()
        credits_by_user = {str(uid): max(0, int(total or 0)) for uid, total in credit_rows}
        items_by_user = {str(uid): int(total or 0) for uid, total in item_rows}
        owners = sorted(set(plans) | set(credits_by_user) | set(items_by_user))
        sellers = []
        for uid in owners:
            row = plans.get(uid)
            pid = row.plan_id if row is not None and row.plan_id in PLANS_BY_ID else "free"
            plan_def = PLANS_BY_ID[pid]
            sellers.append({
                "user_id": uid,
                "plan_id": pid,
                "plan_name": plan_def.name,
                "billing_interval": row.billing_interval if row is not None else "monthly",
                "assigned_by": row.assigned_by if row is not None else None,
                "note": row.note if row is not None else None,
                "created_at": _iso_or_none(row.created_at) if row is not None else None,
                "updated_at": _iso_or_none(row.updated_at) if row is not None else None,
                "credits_used": credits_by_user.get(uid, 0),
                "monthly_credits": plan_def.monthly_credits,
                "listed_items_used": items_by_user.get(uid, 0),
                "monthly_listed_items": plan_def.monthly_listed_items,
            })
        return {
            "period": target_period,
            "limits_enforced": limits_enforced(),
            "unlimited_emails": sorted(unlimited_emails()),
            "sellers": sellers,
            "plans": [item.to_public_dict() for item in PUBLIC_PLANS],
        }

    from anteroom import database

    if database.engine.dialect.name == "postgresql":
        with database.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT user_id, plan_id, billing_interval, assigned_by, note, "
                    "created_at, updated_at, credits_used, listed_items_used "
                    "FROM anteroom_private.list_seller_plans(:period)"
                ),
                {"period": target_period},
            ).mappings().all()
        sellers = []
        for row in rows:
            pid = str(row["plan_id"]) if row["plan_id"] in PLANS_BY_ID else "free"
            plan_def = PLANS_BY_ID[pid]
            sellers.append({
                "user_id": str(row["user_id"]),
                "plan_id": pid,
                "plan_name": plan_def.name,
                "billing_interval": str(row["billing_interval"]),
                "assigned_by": row["assigned_by"],
                "note": row["note"],
                "created_at": _iso_or_none(row["created_at"]),
                "updated_at": _iso_or_none(row["updated_at"]),
                "credits_used": max(0, int(row["credits_used"] or 0)),
                "monthly_credits": plan_def.monthly_credits,
                "listed_items_used": int(row["listed_items_used"] or 0),
                "monthly_listed_items": plan_def.monthly_listed_items,
            })
        return {
            "period": target_period,
            "limits_enforced": limits_enforced(),
            "unlimited_emails": sorted(unlimited_emails()),
            "sellers": sellers,
            "plans": [item.to_public_dict() for item in PUBLIC_PLANS],
        }

    with database.SessionLocal() as session:
        return list_seller_plans(period=target_period, db=session)
