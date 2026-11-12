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

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from vendoo_studio.database import claim_in_worker_session, in_worker_session
from vendoo_studio.models.conversation import Conversation, new_id
from vendoo_studio.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from vendoo_studio.models.promotion_automation import PromotionAutomation
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.services import browser_bridge, listing_mode
from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job
from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.listing_result import scrub
from vendoo_studio.services.marketplace_diagnostics import failure_context

log = logging.getLogger("vendoo_studio.listing_promotions")

# What each marketplace's own site lets Anteroom do. See docs/direct-form-parity.md.
SUPPORTED: dict[str, frozenset[str]] = {
    "poshmark": frozenset({"share", "offer"}),
    "mercari": frozenset({"offer"}),
    "depop": frozenset({"offer"}),
}
# Built against fakes; the signed-in web client's request has not been captured
# yet (docs/direct-form-parity.md, "Mercari and Depop offer requests"). Nothing is sent while listed here.
AWAITING_CAPTURE: dict[str, frozenset[str]] = {}
# Marketplaces whose offers carry a seller shipping discount the seller picks.
SHIPPING_DISCOUNTS = frozenset({"poshmark"})
MAX_ITEMS = 100
PREVIEW_TTL = timedelta(minutes=15)
OFFER_COOLDOWN = timedelta(hours=24)
STEP = "native_promote"
UNKNOWN_NOTE = "Chrome did not confirm this. Check the listing on the marketplace, then mark it checked. It will not be sent again."
# A refusal that applies to every item that follows: stop the batch.
STOP_CODES = frozenset({"rate_limited", "challenge", "signed_out"})
NAMES = {"poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}


class PromotionError(RuntimeError):
    def __init__(self, status_code: int, detail: str, code: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code


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
        "like_count": row.like_count, "shipping_options": row.shipping_options or [],
        "confirmation": row.confirmation, "recipient_count": row.recipient_count,
        "shipping_discount_id": row.shipping_discount_id, "shipping_discount_label": row.shipping_discount_label,
        "state": row.state, "note": row.note, "marketplace_message": row.marketplace_message,
        "automation_id": row.automation_id, "created_at": _iso(row.created_at), "expires_at": _iso(row.expires_at), "finished_at": _iso(row.finished_at),
    }


def live_listing(db: Session, conv: Conversation, marketplace: str) -> tuple[str | None, str | None]:
    """The marketplace listing ID to act on, or why there is none."""
    from vendoo_studio.services.marketplace_delist import listings

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
    rows = db.query(ListingPromotion).filter(
        ListingPromotion.marketplace == marketplace, ListingPromotion.listing_id == listing_id,
        ListingPromotion.action == action, ListingPromotion.state.in_(("sending", "unknown", "done")),
    ).all()
    if any(row.state in ("sending", "unknown") for row in rows):
        return f"An earlier {action} on this listing is unconfirmed. Check it first."
    if action == "offer" and any(row.sent_at and row.sent_at > _now() - OFFER_COOLDOWN
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


def require_chrome() -> None:
    from vendoo_studio.services.extension_connection import current_extension
    from vendoo_studio.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, relay_for
    from vendoo_studio.services.service_context import current

    context = current()
    if context is not None:
        if refusal := relay_for(context.seller_id).refusal(MARKETPLACE_PROMOTIONS):
            raise PromotionError(409, refusal, "chrome")
        return
    if not current_extension().connected:
        raise PromotionError(409, "Connect Chrome first.", "chrome")


def new_row(conv: Conversation, marketplace: str, action: str, batch_id: str, discount_percent: int | None = None,
            automation_id: str | None = None) -> ListingPromotion:
    return ListingPromotion(batch_id=batch_id, conversation_id=conv.id, marketplace=marketplace, action=action, listing_id="",
                            title=conv.title, discount_percent=discount_percent, automation_id=automation_id,
                            state="preview", expires_at=_now() + PREVIEW_TTL)


def offer_floor(db: Session, row: ListingPromotion) -> int:
    """Automation minimums are optional; a saved individual minimum can only raise them."""
    saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=row.marketplace, listing_id=row.listing_id).first()
    if row.automation_id:
        automation = db.query(PromotionAutomation).filter_by(id=row.automation_id).populate_existing().first()
        if not automation or automation.action != "offer" or automation.marketplace != row.marketplace:
            raise PromotionError(409, "This offer rule changed. Review it again.")
        return max(automation.minimum_price_cents or 1, saved.floor_cents if saved else 1)
    if not saved:
        raise PromotionError(409, "Set the lowest price you accept for this listing before sending offers.")
    return saved.floor_cents


async def review(db: Session, row: ListingPromotion, conv: Conversation, floor_price: str | None = None,
                 *, new_like_at: datetime | None = None) -> None:
    """Read one listing now and fill in what would be sent. Raises PromotionError when it would not be."""
    listing_id = _review_local(db, row, conv, floor_price, new_like_at=new_like_at)
    # Only reads are open here. End them before Chrome answers, which can take
    # minutes: a held connection is one the seller's own pages cannot use.
    db.commit()
    await _review_remote(row, listing_id)


def _review_local(db: Session, row: ListingPromotion, conv: Conversation, floor_price: str | None,
                  *, new_like_at: datetime | None) -> str:
    """The checks ``review`` makes before asking Chrome. Returns the listing ID to read."""
    listing_id, reason = live_listing(db, conv, row.marketplace)
    row.listing_id = listing_id or ""
    if reason:
        raise PromotionError(409, reason)
    assert listing_id is not None
    if row.action == "offer":
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
    """Ask Chrome for the listing's live facts and fill them in. Uses no database session."""
    name = NAMES.get(row.marketplace, row.marketplace)
    payload = {
        "marketplace": row.marketplace, "action": row.action, "listing_id": listing_id,
        **({"discount_percent": row.discount_percent} if row.action == "offer" else {}),
    }
    diagnostic_inputs = {**payload, "floor_cents": row.floor_cents}
    started = time.monotonic()
    try:
        facts = await browser_bridge.request(SimpleNamespace(id=f"promotion-{row.batch_id}"), "marketplace.promotion_check", payload, timeout=90)
    except browser_bridge.BrowserBridgeError as exc:
        log.error("%s %s preview failed for listing %s: %s", row.marketplace, row.action, listing_id, scrub(str(exc)), exc_info=True,
                  extra={"marketplace_context": failure_context("marketplace.promotion_check", diagnostic_inputs, None,
                                                               time.monotonic() - started, 90)})
        raise PromotionError(502, str(exc), "network") from exc
    if facts.get("ok") is not True:
        row.marketplace_message = scrub(facts.get("marketplace_message"))
        # A refusal can still say how many likers could get an offer (Depop's "nobody to offer to").
        if isinstance(facts.get("like_count"), int):
            row.like_count = facts["like_count"]
        log.error("%s %s preview rejected for listing %s, automation %s: %s; %s",
                  row.marketplace, row.action, listing_id, row.automation_id,
                  scrub(str(facts.get("error") or "The marketplace did not answer.")), row.marketplace_message or "-",
                  extra={"marketplace_context": failure_context("marketplace.promotion_check", diagnostic_inputs, facts,
                                                               time.monotonic() - started, 90)})
        raise PromotionError(409, str(facts.get("error") or "The marketplace did not answer."), facts.get("error_code"))
    row.price_cents = int(facts["price_cents"])
    row.like_count = int(facts.get("like_count") or 0)
    if row.action != "offer":
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
    require_chrome()
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
                  new_like_at: datetime | None = None) -> dict:
    """Send one reviewed row, once. The caller has the seller's explicit confirmation or enabled automation.

    The checks, the job claim and the durable ``sending`` mark are committed before Chrome is asked;
    no database connection is held while Chrome sends.
    """
    # A request dropped while claiming sent nothing: the row fails as safe to retry and the job is released.
    job_id, payload = await claim_in_worker_session(
        _start_send, partial(_abandon_send, promotion_id), promotion_id, shipping_discount_id, new_like_at)
    if gap_ms and payload["action"] == "share":
        payload["gap_ms"] = gap_ms
    finish: asyncio.Future | None = None
    try:
        try:
            result = await browser_bridge.request(SimpleNamespace(id=job_id), "marketplace.promote", payload, timeout=150)
        except browser_bridge.BrowserBridgeError:
            result = None
        # Shielded: a dropped request must not leave the send recorded as in progress.
        finish = asyncio.ensure_future(in_worker_session(_finish_send, promotion_id, job_id, result))
        return await asyncio.shield(finish)
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


async def _abandon_send(promotion_id: str, started: tuple[str, dict]) -> None:
    job_id = started[0]
    try:
        await in_worker_session(_finish_send, promotion_id, job_id, {
            "ok": False, "safe_to_retry": True, "error": "Stopped before it was sent. Nothing was sent."})
    finally:
        release_api_job(job_id)


def _start_send(db: Session, promotion_id: str, shipping_discount_id: str | None,
                new_like_at: datetime | None) -> tuple[str, dict]:
    """Check the reviewed row, claim the job and mark the row ``sending``. Returns the job and Chrome's payload."""
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
    if row.action == "offer":
        if row.offer_price_cents is None or row.floor_cents is None or row.offer_price_cents < row.floor_cents:
            raise PromotionError(409, "This offer is below your floor.")
        minimum = offer_floor(db, row)
        if row.offer_price_cents < minimum:
            raise PromotionError(409, "Your floor for this listing changed. Review the offer again.")
        row.floor_cents = max(row.floor_cents, minimum)
        _choose_shipping(row, shipping_discount_id)
    require_chrome()
    repo = JobRepo(db)
    listing = ListingRepo(db).get_current(conv.id)
    with listing_mode.guard(db, "direct"):
        if repo.get_active():
            raise PromotionError(409, "Another automation job is queued or running. Wait for it to finish.", "busy")
        # Again under the seller's lock (shared by every Machine on Postgres):
        # a send another Machine finished since the check above stops this one.
        if blocked := blocking(db, row.marketplace, row.listing_id, row.action, new_like_at=new_like_at):
            raise PromotionError(409, blocked)
        job = repo.create(conv.id, listing.current_revision_id if listing else "",
                          {"promotion": {"id": row.id, "marketplace": row.marketplace, "action": row.action}},
                          status="dispatched", current_step=STEP)
        claim_api_job(db, job.id)
    job_id = job.id
    try:
        if row.automation_id:
            # Committed with ``sending`` below, so a pause either lands first and stops this send or waits for it.
            approval = update(PromotionAutomation).where(
                PromotionAutomation.id == row.automation_id, PromotionAutomation.enabled.is_(True),
            )
            if row.action == "offer":
                approval = approval.where(
                    PromotionAutomation.discount_percent == row.discount_percent,
                    or_(PromotionAutomation.minimum_price_cents.is_(None),
                        PromotionAutomation.minimum_price_cents <= row.offer_price_cents),
                )
            still_on = db.execute(approval.values(enabled=True))
            if type_cast(CursorResult, still_on).rowcount != 1:
                db.rollback()
                repo.update_status(job_id, "failed", STEP, error="The automation changed or was turned off before this was sent.")
                raise PromotionError(409, "This automation changed or was turned off. Nothing was sent.", "paused")
        # Durable before Chrome is asked: a crash from here on is unknown, never resent.
        row.state, row.job_id, row.sent_at = "sending", job_id, _now()
        payload = {"action_id": row.id, "marketplace": row.marketplace, "action": row.action, "listing_id": row.listing_id,
                   "confirm": True, "price_cents": row.price_cents, "offer_price_cents": row.offer_price_cents,
                   "floor_cents": row.floor_cents, "shipping_discount_id": row.shipping_discount_id}
        db.commit()
        repo.add_event(job_id, "native_progress", STEP, {"marketplace": payload["marketplace"], "step": payload["action"],
                                                         "label": "Sharing" if payload["action"] == "share" else "Sending offer"})
    except PromotionError:
        release_api_job(job_id)
        raise
    except BaseException:
        try:
            _interrupted_send(db, promotion_id, job_id)
        finally:
            release_api_job(job_id)
        raise
    return job_id, payload


def _finish_send(db: Session, promotion_id: str, job_id: str, result: dict | None) -> dict:
    row = _get(db, promotion_id)
    _settle(row, result)
    error_code = (result or {}).get("error_code")
    db.commit()
    JobRepo(db).update_status(job_id, "completed" if row.state == "done" else "failed", STEP,
                              error=None if row.state == "done" else (row.note or "")[:300])
    return {**out(row), "error_code": error_code, "stop": error_code in STOP_CODES}


def _interrupted_send(db: Session, promotion_id: str, job_id: str) -> None:
    db.rollback()
    fresh = db.get(ListingPromotion, promotion_id)
    if fresh and fresh.state == "sending":
        fresh.state, fresh.note = "unknown", UNKNOWN_NOTE
        db.commit()
    JobRepo(db).update_status(job_id, "failed", STEP, error="The action stopped before it finished.")


def _settle(row: ListingPromotion, result: dict | None) -> None:
    row.finished_at = _now()
    if result is None:
        row.state, row.note = "unknown", UNKNOWN_NOTE
    elif result.get("ok") is True and result.get("state") == "done":
        row.state = "done"
        if row.action == "share":
            row.note = "Shared to your followers."
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
    else:
        row.state, row.note = "unknown", UNKNOWN_NOTE
        row.marketplace_message = scrub(result.get("marketplace_message"))


async def check(promotion_id: str) -> dict:
    """Read the extension's journal for an unknown row. Never sends anything, and holds no connection while Chrome answers."""
    settled, job_key = await in_worker_session(_check_target, promotion_id)
    if settled is not None:
        return settled
    require_chrome()
    try:
        answer = await browser_bridge.request(SimpleNamespace(id=job_key), "marketplace.promotion_result",
                                              {"action_id": promotion_id}, timeout=20)
    except browser_bridge.BrowserBridgeError as exc:
        raise PromotionError(502, str(exc)) from exc
    return await in_worker_session(_record_check, promotion_id, answer)


def _check_target(db: Session, promotion_id: str) -> tuple[dict | None, str]:
    row = _get(db, promotion_id)
    if row.state != "unknown":
        return out(row), ""
    return None, row.job_id or f"promotion-{row.id}"


def _record_check(db: Session, promotion_id: str, answer: dict) -> dict:
    row = _get(db, promotion_id)
    if row.state != "unknown":
        return out(row)
    # Check replies can arrive out of order. Stage their changes separately,
    # then fence the write so a verified completion or manual check wins.
    staged = ListingPromotion(action=row.action, marketplace=row.marketplace, offer_price_cents=row.offer_price_cents,
                              state=row.state, note=row.note, marketplace_message=row.marketplace_message,
                              confirmation=row.confirmation, recipient_count=row.recipient_count, finished_at=row.finished_at)
    if answer.get("finished") and isinstance(answer.get("result"), dict):
        _settle(staged, answer["result"])
    else:
        staged.note = ("Chrome started this but has no result. " if answer.get("known") else "Chrome has no record of this. ") + UNKNOWN_NOTE
    db.execute(update(ListingPromotion).where(ListingPromotion.id == promotion_id, ListingPromotion.state == "unknown").values(
        state=staged.state, note=staged.note, marketplace_message=staged.marketplace_message,
        confirmation=staged.confirmation, recipient_count=staged.recipient_count, finished_at=staged.finished_at))
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
