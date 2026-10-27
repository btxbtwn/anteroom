"""Seller-confirmed shares and offers to likers on live marketplace listings.

The seller selects items, names the marketplace and the action, and reviews
what each item would do. Every reviewed row is then sent at most once, one item
at a time, holding the single automation slot: ``sending`` is committed before
Chrome is asked, so a reply that never arrives leaves ``unknown`` and nothing is
sent again. Checking an unknown row only reads what the extension journaled.

Offers never go below the seller's floor for that listing, and a floor is
required before an offer can be reviewed. Nothing here runs on a timer, and
nothing here lists, publishes or delists.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, new_id
from vendoo_studio.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.services import browser_bridge
from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job, start_gate
from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.listing_result import scrub

# What each marketplace's own site lets Anteroom do, read from its web client.
# See docs/direct-form-parity.md for what other marketplaces offer and why it is absent here.
SUPPORTED: dict[str, frozenset[str]] = {"poshmark": frozenset({"share", "offer"})}
MAX_ITEMS = 100
PREVIEW_TTL = timedelta(minutes=15)
OFFER_COOLDOWN = timedelta(hours=24)
STEP = "native_promote"
UNKNOWN_NOTE = "Chrome did not confirm this. Check the listing on the marketplace, then mark it checked. It will not be sent again."
# A refusal that applies to every item that follows: stop the batch.
STOP_CODES = frozenset({"rate_limited", "challenge", "signed_out"})


class PromotionError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


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
        "shipping_discount_id": row.shipping_discount_id, "shipping_discount_label": row.shipping_discount_label,
        "state": row.state, "note": row.note, "marketplace_message": row.marketplace_message,
        "created_at": _iso(row.created_at), "expires_at": _iso(row.expires_at), "finished_at": _iso(row.finished_at),
    }


def _live_listing(db: Session, conv: Conversation, marketplace: str) -> tuple[str | None, str | None]:
    """The marketplace listing ID to act on, or why there is none."""
    from vendoo_studio.services.marketplace_delist import listings

    if conv.status == "sold":
        return None, "This item sold. Nothing is shared or offered."
    entry = listings(db, conv.id).get(marketplace) or {}
    name = marketplace.title()
    if not entry.get("listing_id"):
        return None, f"Anteroom has no live {name} listing link for this item."
    if entry.get("sold_here"):
        return None, f"This item sold on {name}."
    if entry.get("state") not in (None, "live", "failed"):
        return None, f"This {name} listing was delisted."
    return str(entry["listing_id"]), None


def _blocking_offer(db: Session, marketplace: str, listing_id: str) -> str | None:
    rows = db.query(ListingPromotion).filter(
        ListingPromotion.marketplace == marketplace, ListingPromotion.listing_id == listing_id,
        ListingPromotion.action == "offer", ListingPromotion.state.in_(("sending", "unknown", "done")),
    ).all()
    if any(row.state in ("sending", "unknown") for row in rows):
        return "An earlier offer on this listing is unconfirmed. Check it first."
    if any(row.sent_at and row.sent_at > _now() - OFFER_COOLDOWN for row in rows):
        return "An offer was sent on this listing in the last 24 hours."
    return None


def floors(db: Session, conversation_ids: list[str], marketplace: str) -> dict[str, dict]:
    """Each selected item's live listing and its saved offer floor."""
    result = {}
    for conv_id in conversation_ids[:MAX_ITEMS]:
        conv = db.get(Conversation, conv_id)
        if not conv:
            continue
        listing_id, reason = _live_listing(db, conv, marketplace)
        saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).first() if listing_id else None
        result[conv_id] = {"title": conv.title, "listing_id": listing_id, "reason": reason,
                           "floor": _money(saved.floor_cents) if saved else None}
    return result


def _save_floor(db: Session, marketplace: str, listing_id: str, conv_id: str, cents: int) -> None:
    row = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).first()
    if row:
        row.floor_cents, row.conversation_id = cents, conv_id
    else:
        db.add(MarketplaceOfferFloor(marketplace=marketplace, listing_id=listing_id, conversation_id=conv_id, floor_cents=cents))
    db.commit()


def _require_chrome() -> None:
    from vendoo_studio.routes.extension import extension_manager

    if not extension_manager.connected:
        raise PromotionError(409, "Connect Chrome first.")


async def preview(db: Session, conversation_ids: list[str], marketplace: str, action: str,
                  discount_percent: int | None = None, floor_prices: dict[str, str] | None = None) -> dict:
    """Read each listing now and record what would be sent. Sends nothing."""
    if action not in SUPPORTED.get(marketplace, frozenset()):
        raise PromotionError(400, "Anteroom cannot do this on that marketplace.")
    ids = list(dict.fromkeys(conversation_ids))
    if not ids or len(ids) > MAX_ITEMS:
        raise PromotionError(400, f"Select between 1 and {MAX_ITEMS} items.")
    if action == "offer" and not (isinstance(discount_percent, int) and 1 <= discount_percent <= 99):
        raise PromotionError(400, "Enter the discount for the offer.")
    _require_chrome()
    batch = new_id()
    reader = SimpleNamespace(id=f"promotion-{batch}")
    rows = []
    for conv_id in ids:
        conv = db.get(Conversation, conv_id)
        if not conv:
            continue
        listing_id, reason = _live_listing(db, conv, marketplace)
        row = ListingPromotion(batch_id=batch, conversation_id=conv.id, marketplace=marketplace, action=action,
                               listing_id=listing_id or "", title=conv.title, discount_percent=discount_percent,
                               state="preview", expires_at=_now() + PREVIEW_TTL)
        try:
            if reason:
                raise PromotionError(409, reason)
            if action == "offer":
                given = (floor_prices or {}).get(conv_id)
                if given not in (None, ""):
                    try:
                        _save_floor(db, marketplace, listing_id, conv.id, money_cents(given))
                    except ValueError as exc:
                        raise PromotionError(400, "Enter the floor as a price, such as 25 or 25.00.") from exc
                saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=marketplace, listing_id=listing_id).first()
                if not saved:
                    raise PromotionError(409, "Set the lowest price you accept for this listing before sending offers.")
                row.floor_cents = saved.floor_cents
                if blocked := _blocking_offer(db, marketplace, listing_id):
                    raise PromotionError(409, blocked)
            try:
                facts = await browser_bridge.request(reader, "marketplace.promotion_check", {
                    "marketplace": marketplace, "action": action, "listing_id": listing_id,
                    **({"discount_percent": discount_percent} if action == "offer" else {}),
                }, timeout=90)
            except browser_bridge.BrowserBridgeError as exc:
                raise PromotionError(502, str(exc)) from exc
            if facts.get("ok") is not True:
                row.marketplace_message = scrub(facts.get("marketplace_message"))
                raise PromotionError(409, str(facts.get("error") or "The marketplace did not answer."))
            row.price_cents = int(facts["price_cents"])
            row.like_count = int(facts.get("like_count") or 0)
            if action == "offer":
                low, high = facts.get("minimum_discount_percent"), facts.get("maximum_discount_percent")
                if facts.get("offer_price_cents") is None:
                    raise PromotionError(409, f"{marketplace.title()} needs offers to likers between {low}% and {high}% off.")
                row.offer_price_cents = int(facts["offer_price_cents"])
                row.shipping_options = [
                    {"id": str(option["id"]), "label": str(option.get("label") or "")[:80],
                     "min_offer_cents": option.get("min_offer_cents")}
                    for option in facts.get("shipping_discounts") or [] if option.get("id")
                ]
                if row.like_count <= 0:
                    raise PromotionError(409, "Nobody has liked this listing yet.")
                if row.offer_price_cents < row.floor_cents:
                    raise PromotionError(409, f"{discount_percent}% off is ${dollars(row.offer_price_cents)}, below your floor "
                                              f"of ${dollars(row.floor_cents)}. Lower the discount or skip this item.")
        except PromotionError as exc:
            db.rollback()
            row.state, row.note = "skipped", str(exc)[:300]
        db.add(row)
        db.commit()
        rows.append(out(row))
    return {"batch_id": batch, "marketplace": marketplace, "action": action, "rows": rows}


def _get(db: Session, promotion_id: str) -> ListingPromotion:
    row = db.get(ListingPromotion, promotion_id)
    if not row:
        raise PromotionError(404, "This review was not found.")
    return row


async def perform(db: Session, promotion_id: str, shipping_discount_id: str | None = None) -> dict:
    """Send one reviewed row, once. The caller has the seller's explicit confirmation."""
    from vendoo_studio.routes.extension import schedule_advance_job_queue

    row = _get(db, promotion_id)
    if row.state != "preview":
        raise PromotionError(409, "This item was already sent or skipped. Review it again to act on it.")
    if row.expires_at < _now():
        raise PromotionError(409, "This review expired. Review the items again.")
    conv = db.get(Conversation, row.conversation_id) if row.conversation_id else None
    listing_id, reason = _live_listing(db, conv, row.marketplace) if conv else (None, "This item was deleted.")
    if reason or listing_id != row.listing_id:
        raise PromotionError(409, reason or "This item's listing changed. Review it again.")
    if row.action == "offer":
        if row.offer_price_cents is None or row.floor_cents is None or row.offer_price_cents < row.floor_cents:
            raise PromotionError(409, "This offer is below your floor.")
        saved = db.query(MarketplaceOfferFloor).filter_by(marketplace=row.marketplace, listing_id=row.listing_id).first()
        if not saved or row.offer_price_cents < saved.floor_cents:
            raise PromotionError(409, "Your floor for this listing changed. Review the offer again.")
        if blocked := _blocking_offer(db, row.marketplace, row.listing_id):
            raise PromotionError(409, blocked)
        options = {option["id"]: option for option in row.shipping_options or []}
        if shipping_discount_id is None:
            raise PromotionError(400, "Choose a shipping discount, or no shipping discount, for this offer.")
        if shipping_discount_id != "none":
            option = options.get(shipping_discount_id)
            if not option:
                raise PromotionError(400, "Choose one of the shipping discounts shown in the review.")
            if option.get("min_offer_cents") and row.offer_price_cents < option["min_offer_cents"]:
                raise PromotionError(409, f"That shipping discount needs an offer of at least ${dollars(option['min_offer_cents'])}.")
            row.shipping_discount_id, row.shipping_discount_label = shipping_discount_id, option.get("label")
        else:
            row.shipping_discount_id, row.shipping_discount_label = None, "No shipping discount"
    _require_chrome()
    repo = JobRepo(db)
    listing = ListingRepo(db).get_current(conv.id)
    with start_gate():
        if repo.get_active():
            raise PromotionError(409, "Another automation job is queued or running. Wait for it to finish.")
        job = repo.create(conv.id, listing.current_revision_id if listing else "",
                          {"promotion": {"id": row.id, "marketplace": row.marketplace, "action": row.action}},
                          status="dispatched", current_step=STEP)
        claim_api_job(job.id)
    error_code = None
    try:
        # Durable before Chrome is asked: a crash from here on is unknown, never resent.
        row.state, row.job_id, row.sent_at = "sending", job.id, _now()
        db.commit()
        repo.add_event(job.id, "native_progress", STEP, {"marketplace": row.marketplace, "step": row.action,
                                                         "label": "Sharing" if row.action == "share" else "Sending offer"})
        try:
            result = await browser_bridge.request(job, "marketplace.promote", {
                "action_id": row.id, "marketplace": row.marketplace, "action": row.action, "listing_id": row.listing_id,
                "confirm": True, "price_cents": row.price_cents, "offer_price_cents": row.offer_price_cents,
                "floor_cents": row.floor_cents, "shipping_discount_id": row.shipping_discount_id,
            }, timeout=150)
        except browser_bridge.BrowserBridgeError:
            result = None
        _settle(row, result)
        error_code = (result or {}).get("error_code")
        db.commit()
        repo.update_status(job.id, "completed" if row.state == "done" else "failed", STEP,
                           error=None if row.state == "done" else (row.note or "")[:300])
    except BaseException:
        db.rollback()
        fresh = db.get(ListingPromotion, row.id)
        if fresh and fresh.state == "sending":
            fresh.state, fresh.note = "unknown", UNKNOWN_NOTE
            db.commit()
        repo.update_status(job.id, "failed", STEP, error="The action stopped before it finished.")
        raise
    finally:
        release_api_job(job.id)
        schedule_advance_job_queue()
    return {**out(row), "error_code": error_code, "stop": error_code in STOP_CODES}


def _settle(row: ListingPromotion, result: dict | None) -> None:
    row.finished_at = _now()
    if result is None:
        row.state, row.note = "unknown", UNKNOWN_NOTE
    elif result.get("ok") is True and result.get("state") == "done":
        row.state = "done"
        row.note = "Shared to your followers." if row.action == "share" else (
            f"Offer of ${dollars(row.offer_price_cents)} sent to likers.")
    elif result.get("safe_to_retry") is True:
        row.state = "failed"
        row.note = str(result.get("error") or "The marketplace refused this.")[:300]
        row.marketplace_message = scrub(result.get("marketplace_message"))
    else:
        row.state, row.note = "unknown", UNKNOWN_NOTE
        row.marketplace_message = scrub(result.get("marketplace_message"))


async def check(db: Session, promotion_id: str) -> dict:
    """Read the extension's journal for an unknown row. Never sends anything."""
    row = _get(db, promotion_id)
    if row.state != "unknown":
        return out(row)
    _require_chrome()
    try:
        answer = await browser_bridge.request(SimpleNamespace(id=row.job_id or f"promotion-{row.id}"),
                                              "marketplace.promotion_result", {"action_id": row.id}, timeout=20)
    except browser_bridge.BrowserBridgeError as exc:
        raise PromotionError(502, str(exc)) from exc
    if answer.get("finished") and isinstance(answer.get("result"), dict):
        _settle(row, answer["result"])
    else:
        row.note = ("Chrome started this but has no result. " if answer.get("known") else "Chrome has no record of this. ") + UNKNOWN_NOTE
    db.commit()
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
    db.commit()
    return len(rows)
