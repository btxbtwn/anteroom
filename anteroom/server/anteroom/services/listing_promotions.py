"""Seller-confirmed shares and offers to likers on live marketplace listings.

The seller selects items, names the marketplace and the action, and reviews
what each item would do. Every reviewed row is then sent at most once, one item
at a time, holding the single automation slot: ``sending`` is committed before
Chrome is asked, so a reply that never arrives leaves ``unknown`` and nothing is
sent again. Checking an unknown row only reads what the extension journaled.

Offers honor saved individual minimums and an automation's optional shared
minimum. Manual offers require an individual floor. A share or offer whose outcome is
unknown blocks that action on that listing until the seller checks it.
Seller-enabled automations (``promotion_automations``) review and send through
the same functions. Nothing here lists, publishes or delists.
"""
from __future__ import annotations
from typing import cast as type_cast
from sqlalchemy.engine import CursorResult

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from anteroom.database import claim_in_worker_session, in_worker_session, run_worker_db_step
from anteroom.models.conversation import Conversation, new_id
from anteroom.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from anteroom.models.promotion_automation import PromotionAutomation
from anteroom.repositories.queries import JobRepo, ListingRepo
from anteroom.services import browser_bridge, listing_mode, mercari_offer_cooldown, offer_preview_refusals
from anteroom.services.depop_offer_eligibility import expected_preview_refusal
from anteroom.services.api_job_lock import claim_api_job, release_api_job
from anteroom.services.ebay_api import dollars, money_cents
from anteroom.services.listing_result import scrub
from anteroom.services.marketplace_diagnostics import failure_context

log = logging.getLogger("anteroom.listing_promotions")

# What each marketplace's own site lets Anteroom do. See docs/direct-form-parity.md.
SUPPORTED: dict[str, frozenset[str]] = {
    "poshmark": frozenset({"share", "offer", "bundle_offer", "follow"}),
    "mercari": frozenset({"offer"}),
    "depop": frozenset({"offer"}),
}
# Built against fakes; the signed-in web client's request has not been captured
# yet (docs/direct-form-parity.md, "Mercari and Depop offer requests"). Nothing is sent while listed here.
AWAITING_CAPTURE: dict[str, frozenset[str]] = {}
# Marketplaces whose offers carry a seller shipping discount the seller picks.
SHIPPING_DISCOUNTS = frozenset({"poshmark"})
MAX_ITEMS = 100
MAX_WATERFALL_STEPS = 5
MAX_WATERFALL_DELAY_MINUTES = 30 * 24 * 60
PREVIEW_TTL = timedelta(minutes=15)
OFFER_COOLDOWN = timedelta(hours=24)
MERCARI_COOLDOWN_NOTE = "Mercari's offer eligibility cooldown is still active."

STEP = "native_promote"
UNKNOWN_NOTE = "Chrome did not confirm this. Check the listing on the marketplace, then mark it checked. It will not be sent again."
# A refusal that applies to every item that follows: stop the batch.
STOP_CODES = frozenset({"rate_limited", "challenge", "signed_out"})
NAMES = {"poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}



class PromotionError(RuntimeError):
    def __init__(self, status_code: int, detail: str, code: str | None = None, *, cloud_signed_out: bool = False):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.cloud_signed_out = cloud_signed_out


def normalize_waterfall_steps(steps: list[dict] | None, *, min_discount: int = 1) -> list[dict] | None:
    """Validate and normalize multi-step waterfall offer definitions.

    Example: [{"discount_percent": 10, "delay_minutes": 15},
              {"discount_percent": 15, "delay_minutes": 1440},
              {"discount_percent": 20, "delay_minutes": 2880}]
    """
    if not steps:
        return None
    if not isinstance(steps, list) or len(steps) > MAX_WATERFALL_STEPS:
        raise PromotionError(400, f"Configure between 1 and {MAX_WATERFALL_STEPS} waterfall steps.")
    normalized: list[dict] = []
    prev_discount = 0
    prev_delay = -1
    for idx, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise PromotionError(400, "Each waterfall step must specify a discount and delay.")
        discount = raw.get("discount_percent")
        delay = raw.get("delay_minutes")
        if type(discount) is not int or not min_discount <= discount <= 99:
            raise PromotionError(400, f"Waterfall step {idx} discount must be between {min_discount}% and 99%.")
        if type(delay) is not int or not 0 <= delay <= MAX_WATERFALL_DELAY_MINUTES:
            raise PromotionError(400, f"Waterfall step {idx} delay must be between 0 and {MAX_WATERFALL_DELAY_MINUTES} minutes.")
        if discount <= prev_discount:
            raise PromotionError(400, "Each waterfall step must increase the discount over the previous step.")
        if delay < prev_delay:
            raise PromotionError(400, "Waterfall step delays cannot go backward.")
        normalized.append({"step": idx, "discount_percent": discount, "delay_minutes": delay})
        prev_discount = discount
        prev_delay = delay
    return normalized


def next_waterfall_step(
    db: Session,
    marketplace: str,
    listing_id: str,
    action: str,
    steps: list[dict] | None,
    *,
    now: datetime | None = None,
    new_like_at: datetime | None = None,
) -> tuple[dict | None, str | None]:
    """Return (next_step_dict, skip_reason) for a listing under a waterfall sequence.

    Always respects both the step's delay and the marketplace 24-hour cooldown
    between consecutive steps on the same listing.
    """
    if not steps:
        return None, None
    moment = now or _now()
    last = (
        db.query(ListingPromotion)
        .filter(
            ListingPromotion.marketplace == marketplace,
            ListingPromotion.listing_id == listing_id,
            ListingPromotion.action == action,
            ListingPromotion.state == "done",
            ListingPromotion.sent_at.isnot(None),
        )
        .order_by(ListingPromotion.sent_at.desc())
        .first()
    )
    if last is None:
        first = steps[0]
        if new_like_at is not None and first["delay_minutes"] > 0:
            due_at = new_like_at + timedelta(minutes=first["delay_minutes"])
            if moment < due_at:
                return None, f"waiting {first['delay_minutes']}m before waterfall step 1"
        return first, None

    last_step = int(last.waterfall_step or 1)
    # If a new buyer liked the listing after the final step completed, restart at step 1.
    if last_step >= len(steps):
        if new_like_at is not None and last.sent_at and new_like_at > last.sent_at:
            first = steps[0]
            if first["delay_minutes"] > 0 and moment < new_like_at + timedelta(minutes=first["delay_minutes"]):
                return None, f"waiting {first['delay_minutes']}m before waterfall step 1"
            return first, None
        return None, "all waterfall offer steps already sent"

    upcoming = steps[last_step]
    assert last.sent_at is not None
    step_delta = max(OFFER_COOLDOWN, timedelta(minutes=int(upcoming["delay_minutes"])))
    if moment < last.sent_at + step_delta:
        return None, f"waiting for waterfall step {upcoming['step']} cooldown"
    return upcoming, None


def first_seen_waterfall_like_at(
    db: Session,
    marketplace: str,
    listing_id: str,
    action: str,
) -> datetime | None:
    """Earliest persisted timestamp where a regular scan saw eligible likes waiting for waterfall step 1."""
    last_done_at = (
        db.query(func.max(ListingPromotion.sent_at))
        .filter(
            ListingPromotion.marketplace == marketplace,
            ListingPromotion.listing_id == listing_id,
            ListingPromotion.action == action,
            ListingPromotion.state == "done",
            ListingPromotion.sent_at.isnot(None),
        )
        .scalar()
    )
    query = db.query(func.min(ListingPromotion.created_at)).filter(
        ListingPromotion.marketplace == marketplace,
        ListingPromotion.listing_id == listing_id,
        ListingPromotion.action == action,
        ListingPromotion.state == "skipped",
        ListingPromotion.waterfall_step == 1,
        ListingPromotion.note.like("waiting % before waterfall step 1%"),
    )
    if last_done_at is not None:
        query = query.filter(ListingPromotion.created_at > last_done_at)
    return query.scalar()


def available(marketplace: str, action: str) -> bool:
    return action in SUPPORTED.get(marketplace, frozenset()) and action not in AWAITING_CAPTURE.get(marketplace, frozenset())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _money(cents: int | None) -> str | None:
    return dollars(cents) if cents is not None else None


def out(row: ListingPromotion) -> dict:
    return {
        "id": row.id, "batch_id": row.batch_id, "conversation_id": row.conversation_id, "marketplace": row.marketplace,
        "action": row.action, "listing_id": row.listing_id, "title": row.title, "price": _money(row.price_cents),
        "offer_price": _money(row.offer_price_cents), "floor": _money(row.floor_cents), "discount_percent": row.discount_percent,
        "waterfall_step": row.waterfall_step, "buyer_handle": row.buyer_handle,
        "like_count": row.like_count, "shipping_options": row.shipping_options or [],
        "confirmation": row.confirmation, "recipient_count": row.recipient_count,
        "shipping_discount_id": row.shipping_discount_id, "shipping_discount_label": row.shipping_discount_label,
        "state": row.state, "note": row.note, "marketplace_message": row.marketplace_message,
        "automation_id": row.automation_id, "created_at": _iso(row.created_at), "expires_at": _iso(row.expires_at), "finished_at": _iso(row.finished_at),
    }


def live_listing(db: Session, conv: Conversation, marketplace: str) -> tuple[str | None, str | None]:
    """The marketplace listing ID to act on, or why there is none."""
    from anteroom.services.marketplace_delist import listings

    if conv.status == "sold":
        return None, "This item sold. Nothing is shared or offered."
    return live_entry(listings(db, conv.id).get(marketplace) or {}, marketplace)


def live_entry(entry: dict, marketplace: str) -> tuple[str | None, str | None]:
    """``live_listing`` for an item whose listings are already read and which has not sold."""
    name = NAMES.get(marketplace, marketplace.title())
    if not entry.get("listing_id"):
        return None, f"Anteroom has no live {name} listing link for this item."
    if entry.get("sold_here"):
        return None, f"This item sold on {name}."
    if entry.get("state") not in (None, "live", "failed"):
        return None, f"This {name} listing was delisted."
    return str(entry["listing_id"]), None


def blocking(db: Session, marketplace: str, listing_id: str, action: str, *, new_like_at: datetime | None = None) -> str | None:
    """Why this action may not be sent on this listing now, if anything stops it."""
    now = _now()
    eligible = ListingPromotion.state.in_(("sending", "unknown", "done"))
    if (marketplace, action) == ("mercari", "offer"):
        # Do not load the listing's lifetime of skipped previews. Recognized
        # hints last at most 72 hours plus the rounding allowance.
        eligible = or_(eligible, and_(ListingPromotion.state == "skipped",
                                      ListingPromotion.finished_at > now - timedelta(hours=73),
                                      ListingPromotion.finished_at <= now))
    rows = db.query(ListingPromotion).filter(
        ListingPromotion.marketplace == marketplace, ListingPromotion.listing_id == listing_id,
        ListingPromotion.action == action, eligible,
    ).all()
    if any(row.state in ("sending", "unknown") for row in rows):
        return f"An earlier {action} on this listing is unconfirmed. Check it first."
    if marketplace == "mercari" and action == "offer":
        for row in rows:
            # Only refusals classified by the read-only preview path carry this
            # note. Keep unknown/sending above this gate; new likes cannot waive it.
            if (row.state == "skipped" and row.note
                    and row.note in (MERCARI_COOLDOWN_NOTE, f"{MERCARI_COOLDOWN_NOTE.rstrip('.')}: {row.marketplace_message}")
                    and (wait := mercari_offer_cooldown.message_wait(row.marketplace_message)) is not None
                    and isinstance(row.finished_at, datetime) and row.finished_at.tzinfo is None
                    and timedelta(0) <= now - row.finished_at < wait):
                return MERCARI_COOLDOWN_NOTE
    if action in ("offer", "bundle_offer") and any(row.sent_at and row.sent_at > _now() - OFFER_COOLDOWN
                                 and (new_like_at is None or row.sent_at >= new_like_at) for row in rows):
        return "An offer was sent on this listing in the last 24 hours."
    return None


def likes_at_last_offer(db: Session, marketplace: str, listing_id: str) -> int | None:
    """Pre-send like snapshot for the last completed action, not delivered offers."""
    row = db.query(ListingPromotion).filter_by(marketplace=marketplace, listing_id=listing_id, action="offer", state="done").order_by(
        ListingPromotion.sent_at.desc()).first()
    return row.like_count if row else None


def floors(db: Session, conversation_ids: list[str], marketplace: str) -> dict[str, dict]:
    """Each selected item's live listing and its saved offer floor."""
    result = {}
    for conv_id in conversation_ids[:MAX_ITEMS]:
        conv = db.get(Conversation, conv_id)
        if not conv:
            continue
        listing_id, reason = live_listing(db, conv, marketplace)
        saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).first() if listing_id else None
        result[conv_id] = {"title": conv.title, "listing_id": listing_id, "reason": reason,
                           "floor": _money(saved.floor_cents) if saved else None}
    return result


def save_floors(db: Session, marketplace: str, prices: dict[str, str]) -> dict[str, dict]:
    """Save the lowest accepted price for each item's live listing. Sends nothing."""
    for conv_id, given in list(prices.items())[:MAX_ITEMS]:
        conv = db.get(Conversation, conv_id)
        listing_id = live_listing(db, conv, marketplace)[0] if conv else None
        if not listing_id or given is None:
            continue
        if given == "":
            db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).delete()
            db.commit()
            continue
        try:
            _save_floor(db, marketplace, listing_id, conv_id, money_cents(given))
        except ValueError as exc:
            raise PromotionError(400, "Enter each floor as a price, such as 25 or 25.00.") from exc
    return floors(db, list(prices), marketplace)


def _save_floor(db: Session, marketplace: str, listing_id: str, conv_id: str, cents: int) -> None:
    row = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).first()
    if row:
        row.floor_cents, row.conversation_id = cents, conv_id
    else:
        db.add(MarketplaceOfferFloor(marketplace=marketplace, listing_id=listing_id, conversation_id=conv_id, floor_cents=cents))
    db.commit()


def require_chrome(marketplace: str | None = None, *, db: Session | None = None) -> None:
    from anteroom.services import cloud_sessions
    from anteroom.services.extension_connection import current_extension
    from anteroom.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, relay_for
    from anteroom.services.service_context import current

    context = current()
    if context is not None:
        if marketplace and cloud_sessions.has_active_session(marketplace, db):
            return
        if refusal := relay_for(context.seller_id).refusal(MARKETPLACE_PROMOTIONS):
            raise PromotionError(409, refusal, "chrome")
        return
    if not current_extension().connected:
        raise PromotionError(409, "Connect Chrome first.", "chrome")


def new_row(conv: Conversation, marketplace: str, action: str, batch_id: str, discount_percent: int | None = None,
            automation_id: str | None = None, waterfall_step: int | None = None) -> ListingPromotion:
    return ListingPromotion(batch_id=batch_id, conversation_id=conv.id, marketplace=marketplace, action=action, listing_id="",
                            title=conv.title, discount_percent=discount_percent, waterfall_step=waterfall_step,
                            automation_id=automation_id, state="preview", expires_at=_now() + PREVIEW_TTL)


def offer_floor(db: Session, row: ListingPromotion) -> int:
    """Automation minimums are optional; a saved individual minimum can only raise them."""
    saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=row.marketplace, listing_id=row.listing_id).first()
    if row.automation_id:
        automation = db.query(PromotionAutomation).filter_by(id=row.automation_id).populate_existing().first()
        if not automation or automation.action not in ("offer", "bundle_offer") or automation.action != row.action or automation.marketplace != row.marketplace:
            raise PromotionError(409, "This offer rule changed. Review it again.")
        return max(automation.minimum_price_cents or 1, saved.floor_cents if saved else 1)
    if not saved:
        raise PromotionError(409, "Set the lowest price you accept for this listing before sending offers.")
    return saved.floor_cents


async def review(row: ListingPromotion, conv_id: str, floor_price: str | None = None,
                 *, new_like_at: datetime | None = None) -> None:
    """Read one listing now and fill in what would be sent. Raises PromotionError when it would not be."""
    listing_id = await in_worker_session(_review_local_by_id, row, conv_id, floor_price, new_like_at)
    await _review_remote(row, listing_id)


def _review_local_by_id(db: Session, row: ListingPromotion, conv_id: str, floor_price: str | None,
                        new_like_at: datetime | None) -> str:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise PromotionError(409, "This listing was deleted.")
    listing_id = _review_local(db, row, conv, floor_price, new_like_at=new_like_at)
    db.commit()
    return listing_id


def _review_local(db: Session, row: ListingPromotion, conv: Conversation, floor_price: str | None,
                  *, new_like_at: datetime | None) -> str:
    """The checks ``review`` makes before asking Chrome. Returns the listing ID to read."""
    listing_id, reason = live_listing(db, conv, row.marketplace)
    row.listing_id = listing_id or ""
    if reason:
        raise PromotionError(409, reason)
    assert listing_id is not None
    if row.action in ("offer", "bundle_offer"):
        if floor_price not in (None, ""):
            try:
                _save_floor(db, row.marketplace, listing_id, conv.id, money_cents(floor_price))
            except ValueError as exc:
                raise PromotionError(400, "Enter the floor as a price, such as 25 or 25.00.") from exc
        row.floor_cents = offer_floor(db, row)
    if blocked := blocking(db, row.marketplace, listing_id, row.action, new_like_at=new_like_at):
        raise PromotionError(409, blocked)
    return listing_id


async def _review_remote(row: ListingPromotion, listing_id: str) -> None:
    """Ask Cloud or Chrome for the listing's live facts and fill them in. Uses no database session."""
    from anteroom.services import cloud_marketplace, cloud_sessions

    name = NAMES.get(row.marketplace, row.marketplace)
    check_action = "offer" if row.action == "bundle_offer" else row.action
    payload = {
        "marketplace": row.marketplace, "action": check_action, "listing_id": listing_id,
        **({"discount_percent": row.discount_percent} if row.action in ("offer", "bundle_offer") else {}),
    }
    diagnostic_inputs = {**payload, "floor_cents": row.floor_cents}
    started = time.monotonic()
    use_cloud = await run_worker_db_step(cloud_sessions.has_active_session, row.marketplace)
    try:
        if use_cloud:
            facts = await cloud_marketplace.execute(
                row.marketplace,
                {"type": "marketplace.promotion_check", "job_id": f"promotion-{row.batch_id}", "payload": payload},
                timeout=90,
            )
        else:
            facts = await browser_bridge.request(SimpleNamespace(id=f"promotion-{row.batch_id}"), "marketplace.promotion_check", payload, timeout=90)
    except (
        browser_bridge.BrowserBridgeError,
        cloud_marketplace.CloudExecutionUnavailable,
        cloud_sessions.CloudSessionError,
    ) as exc:
        log.error("%s %s preview failed for listing %s: %s", row.marketplace, row.action, listing_id, scrub(str(exc)), exc_info=True,
                  extra={"marketplace_context": failure_context("marketplace.promotion_check", diagnostic_inputs, None,
                                                               time.monotonic() - started, 90)})
        raise PromotionError(502, str(exc), "network") from exc
    if facts.get("ok") is not True:
        row.marketplace_message = scrub(facts.get("marketplace_message"))
        # A refusal can still say how many likers could get an offer (Depop's "nobody to offer to").
        if isinstance(facts.get("like_count"), int):
            row.like_count = facts["like_count"]
        cooldown = mercari_offer_cooldown.preview_wait(
            row.marketplace, "marketplace.promotion_check", row.action, facts) is not None
        expected_skip = expected_preview_refusal(row.marketplace, "marketplace.promotion_check", row.action, facts)
        report = log.info if cooldown or expected_skip else log.error
        report("%s %s preview rejected for listing %s, automation %s: %s; %s",
                  row.marketplace, row.action, listing_id, row.automation_id,
                  scrub(str(facts.get("error") or "The marketplace did not answer.")), row.marketplace_message or "-",
                  extra={"marketplace_context": failure_context("marketplace.promotion_check", diagnostic_inputs, facts,
                                                               time.monotonic() - started, 90)})
        if cooldown:
            row.finished_at = _now()
            raise PromotionError(409, MERCARI_COOLDOWN_NOTE, "eligibility_cooldown")
        if held := offer_preview_refusals.recognized(row.marketplace, "marketplace.promotion_check", row.action, facts):
            # The price the marketplace's item read returned before refusing, when the Bridge gives it.
            if isinstance(price := facts.get("price_cents"), int) and not isinstance(price, bool) and price > 0:
                row.price_cents = price
            reason = held
        else:
            reason = str(facts.get("error") or "The marketplace did not answer.")
        code = facts.get("error_code")
        raise PromotionError(409, reason, code, cloud_signed_out=use_cloud and code == "signed_out")
    row.price_cents = int(facts["price_cents"])
    row.like_count = int(facts.get("like_count") or 0)
    if row.action not in ("offer", "bundle_offer"):
        return
    low, high = facts.get("minimum_discount_percent"), facts.get("maximum_discount_percent")
    if facts.get("offer_price_cents") is None:
        if low is None or high is None:
            raise PromotionError(409, f"{name}'s offer limits leave no discount to send on this listing.")
        raise PromotionError(409, f"{name} needs offers to likers between {low}% and {high}% off.")
    row.offer_price_cents = int(facts["offer_price_cents"])
    row.shipping_options = [
        {"id": str(option["id"]), "label": str(option.get("label") or "")[:80], "min_offer_cents": option.get("min_offer_cents")}
        for option in facts.get("shipping_discounts") or [] if option.get("id")
    ]
    if row.like_count <= 0:
        raise PromotionError(409, "Nobody has liked this listing yet.")
    assert row.floor_cents is not None
    if row.offer_price_cents < row.floor_cents:
        raise PromotionError(409, f"{row.discount_percent}% off is ${dollars(row.offer_price_cents)}, below your floor "
                                  f"of ${dollars(row.floor_cents)}. Lower the discount or skip this item.")


async def preview(conversation_ids: list[str], marketplace: str, action: str,
                  discount_percent: int | None = None, floor_prices: dict[str, str] | None = None) -> dict:
    """Read each listing now and record what would be sent. Sends nothing.

    Each database step runs with its own short session; none is held while Chrome reads the listing.
    """
    if action not in SUPPORTED.get(marketplace, frozenset()):
        raise PromotionError(400, "Anteroom cannot do this on that marketplace.")
    if not available(marketplace, action):
        raise PromotionError(409, f"Anteroom cannot send {NAMES[marketplace]} offers yet: its offer request still needs "
                                  "to be read from the signed-in site.")
    ids = list(dict.fromkeys(conversation_ids))
    if not ids or len(ids) > MAX_ITEMS:
        raise PromotionError(400, f"Select between 1 and {MAX_ITEMS} items.")
    if action == "offer" and not (isinstance(discount_percent, int) and 1 <= discount_percent <= 99):
        raise PromotionError(400, "Enter the discount for the offer.")
    await run_worker_db_step(require_chrome, marketplace)
    batch = new_id()
    rows = []
    for conv_id in ids:
        started = await in_worker_session(_preview_local, conv_id, marketplace, action, batch, discount_percent,
                                          (floor_prices or {}).get(conv_id))
        if started is None:
            continue
        row, listing_id, saved = started
        if saved is None:
            try:
                await _review_remote(row, listing_id)
            except PromotionError as exc:
                row.state, row.note = "skipped", str(exc)[:300]
            saved = await in_worker_session(_save_row, row)
        rows.append(saved)
    return {"batch_id": batch, "marketplace": marketplace, "action": action, "rows": rows}


def _preview_local(db: Session, conv_id: str, marketplace: str, action: str, batch: str, discount_percent: int | None,
                   floor_price: str | None) -> tuple[ListingPromotion, str, dict | None] | None:
    """A new review row and its listing, or the saved row when it is already skipped. None for a missing item."""
    conv = db.get(Conversation, conv_id)
    if not conv:
        return None
    row = new_row(conv, marketplace, action, batch, discount_percent)
    try:
        listing_id = _review_local(db, row, conv, floor_price, new_like_at=None)
    except PromotionError as exc:
        db.rollback()
        row.state, row.note = "skipped", str(exc)[:300]
        return row, "", _save_row(db, row)
    db.commit()
    return row, listing_id, None


def _save_row(db: Session, row: ListingPromotion) -> dict:
    db.add(row)
    db.commit()
    return out(row)


def _get(db: Session, promotion_id: str) -> ListingPromotion:
    row = db.get(ListingPromotion, promotion_id)
    if not row:
        raise PromotionError(404, "This review was not found.")
    return row


def _choose_shipping(row: ListingPromotion, shipping_discount_id: str | None) -> None:
    if row.marketplace not in SHIPPING_DISCOUNTS:
        if shipping_discount_id not in (None, "none"):
            raise PromotionError(400, f"{NAMES[row.marketplace]} offers carry no shipping discount.")
        return
    options = {option["id"]: option for option in row.shipping_options or []}
    if shipping_discount_id is None:
        raise PromotionError(400, "Choose a shipping discount, or no shipping discount, for this offer.")
    if shipping_discount_id == "none":
        row.shipping_discount_id, row.shipping_discount_label = None, "No shipping discount"
        return
    option = options.get(shipping_discount_id)
    if not option:
        raise PromotionError(400, "Choose one of the shipping discounts shown in the review.")
    if option.get("min_offer_cents") and row.offer_price_cents < option["min_offer_cents"]:
        raise PromotionError(409, f"That shipping discount needs an offer of at least ${dollars(option['min_offer_cents'])}.")
    row.shipping_discount_id, row.shipping_discount_label = shipping_discount_id, option.get("label")


async def perform(promotion_id: str, shipping_discount_id: str | None = None, *, gap_ms: int | None = None,
                  new_like_at: datetime | None = None, extra_payload: dict | None = None,
                  include_executor: bool = False) -> dict:
    """Send one reviewed row, once. The caller has the seller's explicit confirmation or enabled automation.

    The checks, the job claim and the durable ``sending`` mark are committed before Cloud or Chrome is asked;
    no database connection is held while it sends.
    """
    from anteroom.services import cloud_marketplace, cloud_sessions

    # A request dropped while claiming sent nothing: the row fails as safe to retry and the job is released.
    job_id, payload, use_cloud = await claim_in_worker_session(
        _start_send, partial(_abandon_send, promotion_id), promotion_id, shipping_discount_id, new_like_at)
    if gap_ms and payload["action"] == "share":
        payload["gap_ms"] = gap_ms
    if extra_payload:
        for key, value in extra_payload.items():
            if value is not None and key not in ("action_id", "marketplace", "action", "listing_id", "confirm", "floor_cents"):
                payload[key] = value
    finish: asyncio.Future | None = None
    try:
        try:
            if use_cloud:
                result = await cloud_marketplace.execute(
                    payload["marketplace"],
                    {"type": "marketplace.promote", "job_id": job_id, "payload": payload},
                    timeout=150,
                )
            else:
                result = await browser_bridge.request(SimpleNamespace(id=job_id), "marketplace.promote", payload, timeout=150)
        except (
            browser_bridge.BrowserBridgeError,
            cloud_marketplace.CloudExecutionUnavailable,
            cloud_sessions.CloudSessionError,
        ):
            result = None
        # Shielded: a dropped request must not leave the send recorded as in progress.
        finish = asyncio.ensure_future(in_worker_session(_finish_send, promotion_id, job_id, result))
        settled = await asyncio.shield(finish)
        if include_executor:
            settled["executor"] = "cloud" if use_cloud else "chrome"
        return settled
    except BaseException:
        if finish is not None:
            with contextlib.suppress(BaseException):
                await asyncio.shield(finish)
            if not finish.cancelled() and finish.exception() is None:
                raise
        await asyncio.shield(in_worker_session(_interrupted_send, promotion_id, job_id))
        raise
    finally:
        release_api_job(job_id)


def _follow_allowance(db: Session, marketplace: str, config: dict) -> tuple[dict, dict[str, int]]:
    from anteroom.services.promotion_automations import MAX_DAILY_FOLLOWS, MAX_DAILY_UNFOLLOWS

    used = {"active": 0, "followed": 0, "unfollowed": 0}
    cutoff = _now() - timedelta(hours=24)
    recent = db.query(ListingPromotion.follow_budget).filter(
        ListingPromotion.marketplace == marketplace, ListingPromotion.action == "follow",
        # A completed round may have acted right up to the receipt timestamp.
        or_(ListingPromotion.sent_at > cutoff, ListingPromotion.finished_at > cutoff),
    )
    for (budget,) in recent:
        # Older sends did not track all three counts. Treat them as unknown.
        for key, maximum in (("active", MAX_DAILY_FOLLOWS), ("followed", MAX_DAILY_FOLLOWS),
                             ("unfollowed", MAX_DAILY_UNFOLLOWS)):
            used[key] += budget[key] if budget is not None else maximum
    remaining = max(0, MAX_DAILY_FOLLOWS - used["followed"])
    active = min(remaining, max(0, int(config.get("new_active_sellers_cap") or 0) - used["active"]))
    unfollow = max(0, min(MAX_DAILY_UNFOLLOWS, int(config.get("unfollow_cap") or 0)) - used["unfollowed"])
    reciprocal = bool(config.get("reciprocal_24h"))
    return {
        "reciprocal_24h": reciprocal, "new_active_sellers_cap": active, "unfollow_cap": unfollow,
        "unsubscribe_live_shows": bool(config.get("unsubscribe_live_shows")),
        "remaining_follow_cap": remaining if reciprocal else active,
    }, {"active": active, "followed": remaining if reciprocal else active, "unfollowed": unfollow}


def has_active_account_send(db: Session) -> bool:
    return db.query(ListingPromotion.id).filter(
        ListingPromotion.listing_id == "__account__",
        ListingPromotion.state == "sending",
    ).first() is not None


def _poshmark_bundle_floors(db: Session, row: ListingPromotion) -> dict[str, int]:
    """Verified per-listing floors (in cents) for multi-item Poshmark bundle offers."""
    from anteroom.services.marketplace_delist import listings_for

    shared_min: int | None = None
    if row.automation_id:
        automation = db.get(PromotionAutomation, row.automation_id)
        if automation and automation.minimum_price_cents:
            shared_min = int(automation.minimum_price_cents)

    result: dict[str, int] = {}
    for saved in db.query(MarketplaceOfferFloor).filter_by(marketplace="poshmark").all():
        if saved.listing_id and saved.floor_cents and saved.floor_cents > 0:
            result[str(saved.listing_id)] = max(int(saved.floor_cents), shared_min or 1)

    if shared_min is not None and shared_min > 0:
        convs = db.query(Conversation).filter(Conversation.status != "sold").all()
        known = listings_for(db, convs)
        for conv in convs:
            listing_id, reason = live_entry(known.get(conv.id, {}).get("poshmark") or {}, "poshmark")
            if listing_id and not reason:
                result.setdefault(listing_id, shared_min)

    if row.listing_id and row.floor_cents and row.floor_cents > 0:
        result[row.listing_id] = max(result.get(row.listing_id, 0), int(row.floor_cents))
    return result


async def _abandon_account_send(started: tuple[str, dict, bool]) -> None:
    promotion_id = started[0]
    await in_worker_session(_finish_send, promotion_id, None, {
        "ok": False,
        "safe_to_retry": True,
        "release_budget": True,
        "error": "Stopped before it was sent. Nothing was sent.",
    })


async def perform_account_action(
    marketplace: str,
    action: str,
    *,
    automation_id: str,
    batch_id: str,
    extra_payload: dict,
) -> dict:
    """Execute an account-level promotion action (e.g., Poshmark community shares or automatic follows).

    Commits a durable ``ListingPromotion`` journal row before asking Cloud or Chrome,
    serializes against active jobs/account sends, and settles interrupted runs as ``unknown``.
    """
    from anteroom.services import cloud_marketplace, cloud_sessions

    def _start_account(db: Session) -> tuple[str, dict, bool]:
        from anteroom.services.promotion_automations import MAX_DAILY_SHARES, _shares_today

        if not available(marketplace, action):
            raise PromotionError(409, "Anteroom cannot send this on that marketplace yet.")
        automation = db.get(PromotionAutomation, automation_id)
        if not automation or not automation.enabled or automation.action != action or automation.marketplace != marketplace:
            raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
        use_cloud = cloud_sessions.has_active_session(marketplace, db)
        require_chrome(marketplace, db=db)
        repo = JobRepo(db)
        extra = dict(extra_payload)
        with listing_mode.guard(db, "direct"):
            if repo.get_active() or has_active_account_send(db):
                raise PromotionError(409, "Another automation job is queued or running. Wait for it to finish.", "busy")
            db.refresh(automation)
            if not automation.enabled or automation.action != action or automation.marketplace != marketplace:
                raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
            budget: dict[str, int] | None = None
            if action == "follow":
                config, budget = _follow_allowance(db, marketplace, automation.follow_config or {})
                if not any(budget.values()) and not config["unsubscribe_live_shows"]:
                    raise PromotionError(409, "The rolling daily follow and unfollow budget is used.", "daily_cap")
                extra["follow_config"] = config
            elif action == "share" and extra.get("share_mode") == "community":
                if "community" not in (automation.share_modes or []):
                    raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
                cap = int(automation.daily_cap or MAX_DAILY_SHARES)
                remaining = max(0, cap - _shares_today(db, marketplace, _now()))
                requested = int(extra.get("max_shares") or 50)
                allowed = min(max(0, requested), remaining, 50)
                if allowed <= 0:
                    raise PromotionError(409, f"Reached the daily cap of {cap:,} shares.", "daily_cap")
                extra["max_shares"] = allowed
                budget = {"max_shares": allowed}
            row_id = new_id()
            row = ListingPromotion(
                id=row_id,
                batch_id=batch_id,
                conversation_id=None,
                marketplace=marketplace,
                action=action,
                listing_id="__account__",
                title=automation.name,
                automation_id=automation_id,
                job_id=f"{'cloud' if use_cloud else 'chrome'}-account:{row_id}",
                state="sending",
                follow_budget=budget,
                expires_at=_now() + PREVIEW_TTL,
                sent_at=_now(),
            )
            db.add(row)
            db.flush()
            db.commit()
        payload = {
            "action_id": row.id,
            "marketplace": marketplace,
            "action": action,
            "listing_id": "__account__",
            "confirm": True,
            **extra,
        }
        return row.id, payload, use_cloud

    promotion_id, payload, use_cloud = await claim_in_worker_session(
        _start_account, _abandon_account_send)
    finish: asyncio.Future | None = None
    try:
        try:
            if use_cloud:
                result = await cloud_marketplace.execute(
                    marketplace,
                    {"type": "marketplace.promote", "job_id": promotion_id, "payload": payload},
                    timeout=180,
                )
            else:
                result = await browser_bridge.request(SimpleNamespace(id=promotion_id), "marketplace.promote", payload, timeout=180)
        except (
            browser_bridge.BrowserBridgeError,
            cloud_marketplace.CloudExecutionUnavailable,
            cloud_sessions.CloudSessionError,
        ):
            result = None
        finish = asyncio.ensure_future(in_worker_session(_finish_send, promotion_id, None, result))
        return await asyncio.shield(finish)
    except BaseException:
        if finish is not None:
            with contextlib.suppress(BaseException):
                await asyncio.shield(finish)
            if not finish.cancelled() and finish.exception() is None:
                raise
        await asyncio.shield(in_worker_session(_interrupted_send, promotion_id, None))
        raise


async def _abandon_send(promotion_id: str, started: tuple[str, dict, bool]) -> None:
    job_id = started[0]
    try:
        await in_worker_session(_finish_send, promotion_id, job_id, {
            "ok": False, "safe_to_retry": True, "error": "Stopped before it was sent. Nothing was sent."})
    finally:
        release_api_job(job_id)


def _start_send(db: Session, promotion_id: str, shipping_discount_id: str | None,
                new_like_at: datetime | None) -> tuple[str, dict, bool]:
    """Check the reviewed row, claim the job and mark the row ``sending``. Returns the job, payload and cloud flag."""
    from anteroom.services import cloud_sessions

    row = _get(db, promotion_id)
    if row.state != "preview":
        raise PromotionError(409, "This item was already sent or skipped. Review it again to act on it.")
    if row.expires_at < _now():
        raise PromotionError(409, "This review expired. Review the items again.")
    if not available(row.marketplace, row.action):
        raise PromotionError(409, "Anteroom cannot send this on that marketplace yet.")
    conv = db.get(Conversation, row.conversation_id) if row.conversation_id else None
    listing_id, reason = live_listing(db, conv, row.marketplace) if conv else (None, "This item was deleted.")
    if reason or listing_id != row.listing_id:
        raise PromotionError(409, reason or "This item's listing changed. Review it again.")
    assert conv is not None
    if blocked := blocking(db, row.marketplace, row.listing_id, row.action, new_like_at=new_like_at):
        raise PromotionError(409, blocked)
    if row.action in ("offer", "bundle_offer"):
        if row.offer_price_cents is None or row.floor_cents is None or row.offer_price_cents < row.floor_cents:
            raise PromotionError(409, "This offer is below your floor.")
        minimum = offer_floor(db, row)
        if row.offer_price_cents < minimum:
            raise PromotionError(409, "Your floor for this listing changed. Review the offer again.")
        row.floor_cents = max(row.floor_cents, minimum)
        _choose_shipping(row, shipping_discount_id)
    use_cloud = cloud_sessions.has_active_session(row.marketplace, db)
    require_chrome(row.marketplace, db=db)
    repo = JobRepo(db)
    listing = ListingRepo(db).get_current(conv.id)
    with listing_mode.guard(db, "direct"):
        if repo.get_active() or has_active_account_send(db):
            raise PromotionError(409, "Another automation job is queued or running. Wait for it to finish.", "busy")
        # Again under the seller's lock (shared by every Machine on Postgres):
        # a send another Machine finished since the check above stops this one.
        if blocked := blocking(db, row.marketplace, row.listing_id, row.action, new_like_at=new_like_at):
            raise PromotionError(409, blocked)
        job = repo.create(conv.id, listing.current_revision_id if listing else "",
                          {"promotion": {"id": row.id, "marketplace": row.marketplace, "action": row.action,
                                         "executor": "cloud" if use_cloud else "chrome"}},
                          status="dispatched", current_step=STEP)
        claim_api_job(db, job.id)
    job_id = job.id
    try:
        if row.automation_id:
            automation = db.get(PromotionAutomation, row.automation_id)
            allowed_discounts: set[int | None] = set()
            if automation is not None:
                allowed_discounts.add(automation.discount_percent)
                for step in automation.waterfall_steps or []:
                    if isinstance(step, dict) and isinstance(step.get("discount_percent"), int):
                        allowed_discounts.add(step["discount_percent"])
            # Committed with ``sending`` below, so a pause either lands first and stops this send or waits for it.
            approval = update(PromotionAutomation).where(
                PromotionAutomation.id == row.automation_id, PromotionAutomation.enabled.is_(True),
            )
            if row.action in ("offer", "bundle_offer"):
                if row.discount_percent not in allowed_discounts:
                    db.rollback()
                    repo.update_status(job_id, "failed", STEP, error="The automation discount changed before this was sent.")
                    raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
                approval = approval.where(
                    or_(PromotionAutomation.min_likes.is_(None),
                        PromotionAutomation.min_likes <= (row.like_count or 0)),
                    PromotionAutomation.shipping_label == (row.shipping_discount_label if row.shipping_discount_id else None),
                    or_(PromotionAutomation.minimum_price_cents.is_(None),
                        PromotionAutomation.minimum_price_cents <= row.offer_price_cents),
                )
            still_on = db.execute(approval.values(enabled=True))
            if type_cast(CursorResult, still_on).rowcount != 1:
                db.rollback()
                repo.update_status(job_id, "failed", STEP, error="The automation changed or was turned off before this was sent.")
                raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
        # Durable before Cloud or Chrome is asked: a crash from here on is unknown, never resent.
        row.state, row.job_id, row.sent_at = "sending", job_id, _now()
        payload: dict = {"action_id": row.id, "marketplace": row.marketplace, "action": row.action, "listing_id": row.listing_id,
                         "confirm": True, "price_cents": row.price_cents, "offer_price_cents": row.offer_price_cents,
                         "floor_cents": row.floor_cents, "shipping_discount_id": row.shipping_discount_id}
        if row.action == "bundle_offer":
            if row.discount_percent is not None:
                payload["discount_percent"] = row.discount_percent
            if row.waterfall_step is not None:
                payload["waterfall_step"] = row.waterfall_step
            payload["bundle_item_floors"] = _poshmark_bundle_floors(db, row)
        db.commit()
        label = (
            "Sharing" if payload["action"] == "share"
            else "Sending bundle offer" if payload["action"] == "bundle_offer"
            else "Sending offer"
        )
        repo.add_event(job_id, "native_progress", STEP, {"marketplace": payload["marketplace"], "step": payload["action"], "label": label})
    except PromotionError:
        release_api_job(job_id)
        raise
    except BaseException:
        try:
            _interrupted_send(db, promotion_id, job_id)
        finally:
            release_api_job(job_id)
        raise
    return job_id, payload, use_cloud


def _finish_send(db: Session, promotion_id: str, job_id: str | None, result: dict | None) -> dict:
    row = _get(db, promotion_id)
    _settle(row, result)
    error_code = (result or {}).get("error_code")
    db.commit()
    if job_id is not None:
        JobRepo(db).update_status(job_id, "completed" if row.state == "done" else "failed", STEP,
                                  error=None if row.state == "done" else (row.note or "")[:300])
    return {**out(row), "error_code": error_code, "stop": error_code in STOP_CODES}


def _interrupted_send(db: Session, promotion_id: str, job_id: str | None) -> None:
    db.rollback()
    fresh = db.get(ListingPromotion, promotion_id)
    if fresh and fresh.state == "sending":
        fresh.state, fresh.note = "unknown", UNKNOWN_NOTE
        db.commit()
    if job_id is not None:
        JobRepo(db).update_status(job_id, "failed", STEP, error="The action stopped before it finished.")


def _settle(row: ListingPromotion, result: dict | None) -> None:
    row.finished_at = _now()
    if result is None:
        row.state, row.note = "unknown", UNKNOWN_NOTE
    elif result.get("ok") is True and result.get("state") == "done":
        row.state = "done"
        if row.action == "share":
            mode = str(result.get("share_mode") or "self")
            if mode == "party":
                row.note = "Shared to the active Posh Party."
            elif mode == "community":
                count = result.get("recipient_count")
                if type(count) is int and count >= 0:
                    row.recipient_count = count
                    row.follow_budget = {"max_shares": count}
                    row.note = f"Returned {count} community shares."
                else:
                    row.recipient_count = None
                    row.note = "Shared community listings."
            else:
                row.note = "Shared to your followers."
            return
        if row.action == "follow":
            followed = result.get("followed_count")
            unfollowed = result.get("unfollowed_count")
            actual = {"active": result.get("active_followed_count"), "followed": followed, "unfollowed": unfollowed}
            reserved = row.follow_budget
            if reserved is not None and all(type(value) is int and 0 <= value <= reserved[key]
                                            for key, value in actual.items()):
                row.follow_budget = {key: type_cast(int, value) for key, value in actual.items()}
            row.recipient_count = followed if type(followed) is int else None
            row.note = f"Followed {followed} and unfollowed {unfollowed} Poshmark accounts."
            return
        if row.action == "bundle_offer":
            count = result.get("recipient_count")
            row.recipient_count = count if isinstance(count, int) else None
            bundle_total = result.get("bundle_total_price_cents")
            if type(bundle_total) is int and bundle_total > 0:
                row.price_cents = bundle_total
            bundle_offer = result.get("bundle_offer_price_cents")
            if type(bundle_offer) is int and bundle_offer > 0:
                row.offer_price_cents = bundle_offer
            bundle_floor = result.get("bundle_floor_cents")
            if type(bundle_floor) is int and bundle_floor > 0:
                row.floor_cents = bundle_floor
            row.buyer_handle = str(result.get("buyer_handle") or "")[:120] or None
            row.confirmation = str(result.get("confirmation") or "request_accepted")
            price = dollars(row.offer_price_cents) if row.offer_price_cents is not None else ""
            step_label = f" (waterfall step {row.waterfall_step})" if row.waterfall_step else ""
            row.note = f"Poshmark accepted the ${price} private bundle offer{step_label}."
            return
        assert row.offer_price_cents is not None
        price = dollars(row.offer_price_cents)
        # Bridge <0.2.60 transition ends 2027-02-08: after that, require explicit
        # confirmation for new replies. Keep legacy_unverified for historical
        # rows. Until then, a missing receipt never becomes a delivery claim;
        # retain the done gate so it cannot trigger a resend.
        row.confirmation, row.recipient_count = "legacy_unverified", None
        row.note = f"Chrome reported completion of the ${price} offer. Recipient delivery was not verified."
        if row.marketplace == "poshmark" and result.get("confirmation") == "request_accepted":
            row.confirmation = "request_accepted"
            row.note = f"Poshmark accepted the ${price} offer request. Recipient delivery was not verified."
        elif row.marketplace == "depop" and result.get("confirmation") == "job_completed":
            row.confirmation = "job_completed"
            row.note = f"Depop completed the ${price} bulk-offer job. The recipient count was not provided."
        elif row.marketplace == "mercari" and result.get("confirmation") == "recipients_created":
            count = result.get("recipient_count")
            if type(count) is int and count > 0:
                row.confirmation, row.recipient_count = "recipients_created", count
                row.note = f"Mercari confirmed {count} recipient offers created at ${price}."
            else:
                row.state, row.note = "unknown", UNKNOWN_NOTE
    elif result.get("safe_to_retry") is True:
        row.state = "failed"
        row.note = str(result.get("error") or "The marketplace refused this.")[:300]
        row.marketplace_message = scrub(result.get("marketplace_message"))
        if row.listing_id == "__account__":
            if row.action == "follow":
                row.follow_budget = {"active": 0, "followed": 0, "unfollowed": 0}
            elif row.action == "share":
                row.follow_budget = {"max_shares": 0}
    else:
        row.state, row.note = "unknown", UNKNOWN_NOTE
        row.marketplace_message = scrub(result.get("marketplace_message"))


async def check(promotion_id: str) -> dict:
    """Read the journal for an unknown row. Never sends anything, and holds no connection while Chrome answers."""
    settled, job_key, cloud_answer = await in_worker_session(_check_target, promotion_id)
    if settled is not None:
        return settled
    if cloud_answer is not None:
        return await in_worker_session(_record_check, promotion_id, cloud_answer)
    require_chrome()
    try:
        answer = await browser_bridge.request(SimpleNamespace(id=job_key), "marketplace.promotion_result",
                                              {"action_id": promotion_id}, timeout=20)
    except browser_bridge.BrowserBridgeError as exc:
        raise PromotionError(502, str(exc)) from exc
    return await in_worker_session(_record_check, promotion_id, answer)


def _check_target(db: Session, promotion_id: str) -> tuple[dict | None, str, dict | None]:
    from anteroom.services import cloud_sessions

    row = _get(db, promotion_id)
    if row.state != "unknown":
        return out(row), "", None
    executor = None
    if row.job_id:
        if row.job_id.startswith("cloud-account:"):
            executor = "cloud"
        elif row.job_id.startswith("chrome-account:"):
            executor = "chrome"
        else:
            job = JobRepo(db).get(row.job_id)
            if job and isinstance(job.listing_snapshot, dict):
                promo = job.listing_snapshot.get("promotion")
                if isinstance(promo, dict):
                    executor = promo.get("executor")
    if executor == "cloud" or (not row.job_id and row.listing_id == "__account__"):
        journals = cloud_sessions.load_journals(db, row.marketplace)
        stored = journals.get(f"promotion_{promotion_id}")
        if executor != "cloud" and stored is None:
            # Account actions can also run in Chrome; consult its journal next.
            return None, row.id, None
        answer = {
            "ok": True,
            "known": stored is not None,
            "finished": bool(isinstance(stored, dict) and stored.get("finished")),
            "result": stored.get("result") if isinstance(stored, dict) else None,
        }
        return None, row.job_id or f"promotion-{row.id}", answer
    return None, row.job_id or f"promotion-{row.id}", None


def _record_check(db: Session, promotion_id: str, answer: dict) -> dict:
    row = _get(db, promotion_id)
    if row.state != "unknown":
        return out(row)
    # Check replies can arrive out of order. Stage their changes separately,
    # then fence the write so a verified completion or manual check wins.
    staged = ListingPromotion(action=row.action, marketplace=row.marketplace, listing_id=row.listing_id,
                              price_cents=row.price_cents, offer_price_cents=row.offer_price_cents,
                              floor_cents=row.floor_cents, follow_budget=row.follow_budget,
                              state=row.state, note=row.note, marketplace_message=row.marketplace_message,
                              confirmation=row.confirmation, recipient_count=row.recipient_count, finished_at=row.finished_at)
    if answer.get("finished") and isinstance(answer.get("result"), dict):
        _settle(staged, answer["result"])
    else:
        staged.note = ("Chrome started this but has no result. " if answer.get("known") else "Chrome has no record of this. ") + UNKNOWN_NOTE
    db.execute(update(ListingPromotion).where(ListingPromotion.id == promotion_id, ListingPromotion.state == "unknown").values(
        state=staged.state, note=staged.note, marketplace_message=staged.marketplace_message,
        confirmation=staged.confirmation, recipient_count=staged.recipient_count, finished_at=staged.finished_at,
        price_cents=staged.price_cents, offer_price_cents=staged.offer_price_cents, floor_cents=staged.floor_cents,
        follow_budget=staged.follow_budget))
    db.commit()
    db.refresh(row)
    return out(row)


def mark_checked(db: Session, promotion_id: str) -> dict:
    row = _get(db, promotion_id)
    if row.state != "unknown":
        raise PromotionError(409, "This action does not need checking.")
    row.state, row.note = "checked", "You checked this on the marketplace. It was not sent again."
    db.commit()
    return out(row)


def history(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(ListingPromotion).filter(ListingPromotion.state.notin_(("preview",))).order_by(
        ListingPromotion.created_at.desc()).limit(limit).all()
    rows.sort(key=lambda row: row.state not in ("unknown", "sending"))
    return [out(row) for row in rows]


def mark_interrupted(db: Session, job_id: str | None = None) -> int:
    """A job that died with this process or stalled leaves its sends unknown."""
    query = db.query(ListingPromotion).filter(ListingPromotion.state == "sending")
    if job_id:
        query = query.filter(ListingPromotion.job_id == job_id)
    rows = query.all()
    for row in rows:
        row.state, row.note, row.finished_at = "unknown", UNKNOWN_NOTE, _now()
        automation = db.get(PromotionAutomation, row.automation_id) if row.automation_id else None
        if automation and automation.enabled:
            automation.enabled = False
            automation.paused_reason = "Paused: a send was not confirmed. Check it in the activity log, then turn this back on."
    db.commit()
    return len(rows)
