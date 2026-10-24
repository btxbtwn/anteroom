"""Preview first, then send only seller-approved eBay offers.

Automation runs locally, only for individually enabled rules. A committed
reservation precedes every buyer-facing call. Ambiguous outcomes block all
further sends for that listing until the seller checks Seller Hub.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import case, or_, update
from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.models.conversation import Conversation, new_id
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import ebay_api
from vendoo_studio.services.keychain import get_marketplace_accounts
from vendoo_studio.services.marketplace_accounts import SignInRequired
from vendoo_studio.services.relay_sales import listing_id_from_url
from vendoo_studio.services.vendoo_import import parse_notes

log = logging.getLogger("vendoo_studio.ebay_offers")
CHECK_INTERVAL_S = 30 * 60
PREVIEW_TTL = timedelta(minutes=15)
SEND_COOLDOWN = timedelta(hours=24)
UNKNOWN_NOTE = "Send result unknown. Check offers in Seller Hub, then mark this checked. Automation is paused."
_task: asyncio.Task | None = None
_last_check: str | None = None
_last_error: str | None = None


class OfferConflict(ValueError):
    pass


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def account_id() -> str:
    account = get_marketplace_accounts().get("ebay") or {}
    if not account.get("accountId") or not account.get("accessToken"):
        raise SignInRequired("Connect eBay before previewing offers.")
    return str(account["accountId"])


def _listing_id(conv: Conversation) -> str | None:
    urls = parse_notes(conv.notes).get("vendooListingUrls") or {}
    return listing_id_from_url("ebay", urls.get("ebay")) if isinstance(urls, dict) else None


def _local_check(db: Session, rule: EbayOfferRule) -> None:
    if account_id() != rule.account_id:
        raise OfferConflict("The connected eBay account changed. Preview again.")
    conv = db.get(Conversation, rule.conversation_id)
    if not conv or conv.status == "sold" or _listing_id(conv) != rule.listing_id:
        raise OfferConflict("This item sold or its eBay listing changed. Sync from Vendoo before making offers.")
    if db.query(MarketplaceSale.id).filter(MarketplaceSale.conversation_id == conv.id).first():
        raise OfferConflict("A marketplace reported this item sold. Offers are blocked.")
    if db.query(EbayOfferAttempt.id).filter(
        EbayOfferAttempt.rule_id == rule.id,
        EbayOfferAttempt.state.in_(("sending", "unknown")),
    ).first():
        raise OfferConflict(UNKNOWN_NOTE)
    if rule.last_attempt_at and rule.last_attempt_at > now() - SEND_COOLDOWN:
        raise OfferConflict("An offer was sent in the last 24 hours. Wait before sending another.")


def attempt_out(attempt: EbayOfferAttempt, rule: EbayOfferRule, title: str | None = None) -> dict:
    return {
        "id": attempt.id, "ruleId": rule.id, "listingId": rule.listing_id, "title": title,
        "currentPrice": ebay_api.dollars(attempt.current_price_cents),
        "offerPrice": ebay_api.dollars(attempt.offer_price_cents),
        "minimumPrice": ebay_api.dollars(attempt.minimum_price_cents),
        "discountPercent": attempt.discount_percent, "state": attempt.state,
        "note": attempt.note, "recipientCount": attempt.recipient_count,
        "createdAt": attempt.created_at.isoformat() + "Z",
        "expiresAt": attempt.expires_at.isoformat() + "Z",
    }


def status(db: Session) -> dict:
    account = get_marketplace_accounts().get("ebay") or {}
    seller = str(account.get("accountId") or "")
    rules = {r.listing_id: r for r in db.query(EbayOfferRule).filter_by(account_id=seller).all()}
    listings = []
    seen = set()
    sold = {row[0] for row in db.query(MarketplaceSale.conversation_id).all()}
    for conv in db.query(Conversation).filter(Conversation.status != "sold").order_by(Conversation.title).all():
        listing_id = _listing_id(conv)
        if not listing_id or listing_id in seen or conv.id in sold:
            continue
        seen.add(listing_id)
        rule = rules.get(listing_id)
        listings.append({
            "conversationId": conv.id, "listingId": listing_id, "title": conv.title or listing_id,
            "ruleId": rule.id if rule else None, "enabled": bool(rule and rule.enabled),
            "discountPercent": rule.discount_percent if rule else 10,
            "minimumPrice": ebay_api.dollars(rule.minimum_price_cents) if rule else "",
        })
    history = []
    for attempt, rule, title in db.query(EbayOfferAttempt, EbayOfferRule, Conversation.title).join(
        EbayOfferRule, EbayOfferRule.id == EbayOfferAttempt.rule_id,
    ).outerjoin(Conversation, Conversation.id == EbayOfferRule.conversation_id).filter(
        EbayOfferRule.account_id == seller, EbayOfferAttempt.state != "preview",
    ).order_by(case((EbayOfferAttempt.state.in_(("unknown", "sending")), 0), else_=1),
               EbayOfferAttempt.created_at.desc()).limit(50).all():
        history.append(attempt_out(attempt, rule, title))
    return {"connected": bool(account.get("accessToken")), "listings": listings, "history": history,
            "checkIntervalMinutes": CHECK_INTERVAL_S // 60, "lastCheck": _last_check, "lastError": _last_error}


async def preview(db: Session, selections: list[dict], discount_percent: int) -> dict:
    seller = account_id()
    eligible = await ebay_api.eligible_items()
    rows = []
    seen = set()
    for selection in selections:
        conv = db.get(Conversation, selection["conversation_id"])
        listing_id = _listing_id(conv) if conv else None
        title = conv.title if conv else "Missing listing"
        row = {"conversationId": selection["conversation_id"], "title": title, "listingId": listing_id}
        try:
            minimum = ebay_api.money_cents(selection["minimum_price"])
            if not listing_id or listing_id in seen:
                raise OfferConflict("Select a distinct listing with an eBay link imported from Vendoo.")
            seen.add(listing_id)
            rule = db.query(EbayOfferRule).filter_by(account_id=seller, listing_id=listing_id).first()
            if not rule:
                rule = EbayOfferRule(id=new_id(), account_id=seller, listing_id=listing_id, conversation_id=conv.id,
                                     discount_percent=discount_percent, minimum_price_cents=minimum, enabled=False)
            elif rule.conversation_id != conv.id:
                rule.conversation_id = conv.id
                rule.enabled = False
            _local_check(db, rule)
            price = await ebay_api.listing_price(listing_id)
            # Round up to the cent, so rounding never increases the requested discount.
            offer = (price * (100 - discount_percent) + 99) // 100
            if offer < minimum:
                raise OfferConflict("This discount would go below your minimum price. Lower the discount or skip this item.")
            if offer * 100 > price * 95:
                raise OfferConflict("This price is too low to offer at least 5% off after rounding.")
            # Do not hold SQLite's writer lock across the live eBay request.
            existing = db.query(EbayOfferRule).filter_by(account_id=seller, listing_id=listing_id).first()
            if existing:
                rule = existing
            else:
                db.add(rule)
            _local_check(db, rule)
            db.flush()
            attempt = EbayOfferAttempt(rule_id=rule.id, current_price_cents=price, offer_price_cents=offer,
                                       discount_percent=discount_percent, minimum_price_cents=minimum,
                                       state="preview", expires_at=now() + PREVIEW_TTL)
            db.add(attempt)
            db.commit()
            rows.append({**row, **attempt_out(attempt, rule, title), "eligible": listing_id in eligible})
        except (ValueError, ebay_api.EbayError) as exc:
            db.rollback()
            rows.append({**row, "state": "skipped", "note": str(exc)})
    return {"offers": rows}


def _get_preview(db: Session, attempt_id: str) -> tuple[EbayOfferAttempt, EbayOfferRule]:
    attempt = db.get(EbayOfferAttempt, attempt_id)
    rule = db.get(EbayOfferRule, attempt.rule_id) if attempt else None
    if not attempt or not rule or rule.account_id != account_id():
        raise OfferConflict("Offer preview not found for the connected account.")
    if attempt.state != "preview" or attempt.expires_at < now():
        raise OfferConflict("This preview expired or was already used. Preview again.")
    _local_check(db, rule)
    return attempt, rule


def enable(db: Session, attempt_ids: list[str]) -> dict:
    pairs = [_get_preview(db, attempt_id) for attempt_id in attempt_ids]
    for attempt, rule in pairs:
        rule.discount_percent = attempt.discount_percent
        rule.minimum_price_cents = attempt.minimum_price_cents
        rule.enabled = True
        attempt.state = "enabled"
        attempt.note = "Automatic offers approved. Anteroom checks every 30 minutes while running."
    db.commit()
    return {"ok": True}


def pause(db: Session, rule_id: str | None = None) -> dict:
    query = db.query(EbayOfferRule).filter_by(account_id=account_id())
    if rule_id:
        query = query.filter_by(id=rule_id)
    query.update({"enabled": False}, synchronize_session="fetch")
    db.commit()
    return {"ok": True}


def resolve(db: Session, attempt_id: str) -> dict:
    attempt = db.get(EbayOfferAttempt, attempt_id)
    rule = db.get(EbayOfferRule, attempt.rule_id) if attempt else None
    if not rule or rule.account_id != account_id() or attempt.state != "unknown":
        raise OfferConflict("This offer does not need a Seller Hub check.")
    attempt.state = "checked"
    attempt.note = "Seller checked the result in Seller Hub. Automation remains paused."
    rule.enabled = False
    # Keep the 24-hour cooldown; acknowledging an uncertain send is not permission to repeat it.
    db.commit()
    return {"ok": True}


async def send(db: Session, attempt_id: str, *, automatic: bool = False) -> dict:
    attempt, rule = _get_preview(db, attempt_id)
    rule_id = rule.id
    eligible = await ebay_api.eligible_items()
    if rule.listing_id not in eligible:
        raise OfferConflict("This listing no longer has eligible buyers. Preview again later.")
    live_price = await ebay_api.listing_price(rule.listing_id)
    token = await ebay_api.access_token("ebay")
    # Refresh after network waits so a sale, pause, or account switch takes effect.
    db.expire_all()
    attempt, rule = _get_preview(db, attempt_id)
    if (get_marketplace_accounts().get("ebay") or {}).get("accessToken") != token:
        raise OfferConflict("The eBay connection changed. Preview again before sending.")
    if live_price != attempt.current_price_cents:
        raise OfferConflict("The live eBay price changed. Preview again before sending.")
    if attempt.offer_price_cents < attempt.minimum_price_cents:
        raise OfferConflict("The offer is below the approved minimum price.")
    if automatic and (not rule.enabled or rule.discount_percent != attempt.discount_percent
                      or rule.minimum_price_cents != attempt.minimum_price_cents):
        raise OfferConflict("The automatic offer rule changed or was paused.")
    previous_attempt_at = rule.last_attempt_at
    reservation = update(EbayOfferRule).where(
        EbayOfferRule.id == rule_id,
        or_(EbayOfferRule.last_attempt_at.is_(None), EbayOfferRule.last_attempt_at <= now() - SEND_COOLDOWN),
    )
    if automatic:
        # A control request can commit from another thread between the read and
        # this write. Approval and reservation must be checked atomically.
        reservation = reservation.where(
            EbayOfferRule.enabled.is_(True),
            EbayOfferRule.discount_percent == attempt.discount_percent,
            EbayOfferRule.minimum_price_cents == attempt.minimum_price_cents,
        )
    reserved = db.execute(reservation.values(last_attempt_at=now()))
    if reserved.rowcount != 1:
        db.rollback()
        raise OfferConflict("Another offer was already sent or started for this listing.")
    attempt.state = "sending"
    # Crash or cancellation after this commit leaves a durable unresolved send.
    db.commit()
    try:
        count = await ebay_api.send_offer(rule.listing_id, attempt.offer_price_cents, token=token)
    except (ebay_api.EbayError, SignInRequired) as exc:
        attempt.state = "failed"
        attempt.note = str(exc)
        rule.last_attempt_at = previous_attempt_at
        if automatic:
            rule.enabled = False
        db.commit()
    except asyncio.CancelledError:
        attempt.state = "unknown"
        attempt.note = UNKNOWN_NOTE
        rule.enabled = False
        db.commit()
        raise
    except Exception:
        # Any unexpected exception after reservation is ambiguous. Never retry.
        attempt.state = "unknown"
        attempt.note = UNKNOWN_NOTE
        rule.enabled = False
        db.commit()
    else:
        attempt.state = "sent"
        attempt.recipient_count = count
        attempt.note = f"eBay confirmed offers sent to {count} interested {'buyer' if count == 1 else 'buyers'}."
        db.commit()
    return attempt_out(attempt, rule)


async def send_previews(db: Session, attempt_ids: list[str]) -> dict:
    rows = []
    for attempt_id in attempt_ids:
        try:
            rows.append(await send(db, attempt_id))
        except (OfferConflict, SignInRequired, ebay_api.EbayError, httpx.HTTPError) as exc:
            db.rollback()
            note = "Can't reach eBay. No offer was sent." if isinstance(exc, httpx.HTTPError) else str(exc)
            rows.append({"id": attempt_id, "state": "skipped", "note": note})
    return {"offers": rows}


async def run_automation() -> None:
    global _last_check, _last_error
    _last_check = now().isoformat() + "Z"
    _last_error = None
    try:
        seller = account_id()
        with SessionLocal() as db:
            rules = db.query(EbayOfferRule).filter_by(account_id=seller, enabled=True).all()
            if not rules:
                return
            # One eligibility read for the sweep; recheck immediately before each send.
            eligible = await ebay_api.eligible_items()
            for rule in rules:
                db.refresh(rule)
                if not rule.enabled or rule.listing_id not in eligible:
                    continue
                try:
                    _local_check(db, rule)
                    result = await preview(db, [{"conversation_id": rule.conversation_id,
                                                "minimum_price": ebay_api.dollars(rule.minimum_price_cents)}],
                                           rule.discount_percent)
                    offer = result["offers"][0]
                    if offer["state"] == "preview":
                        await send(db, offer["id"], automatic=True)
                except OfferConflict:
                    db.rollback()
                    continue
    except SignInRequired as exc:
        _last_error = str(exc)
    except Exception:
        _last_error = "Could not complete the eBay offer check. Check your connection and try a preview."
        log.warning("eBay offer check failed; no unconfirmed sends will be retried")


async def _timer() -> None:
    while True:
        # Never send as a side effect of startup or enabling a rule.
        await asyncio.sleep(CHECK_INTERVAL_S)
        await run_automation()


def start() -> None:
    global _task
    if _task and not _task.done():
        return
    with SessionLocal() as db:
        interrupted = db.query(EbayOfferAttempt).filter_by(state="sending").all()
        for attempt in interrupted:
            attempt.state = "unknown"
            attempt.note = UNKNOWN_NOTE
            db.get(EbayOfferRule, attempt.rule_id).enabled = False
        db.commit()
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
