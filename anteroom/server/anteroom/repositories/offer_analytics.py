"""Bounded, seller-scoped aggregates; never hydrate the promotion journal."""
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing_promotion import ListingPromotion


def promotion_counts(db: Session, seller: str, start: datetime, end: datetime):
    # Classify each row before aggregation: a receipt on one send cannot
    # verify another send, and invalid counts must not reduce valid counts.
    has_receipt = case((ListingPromotion.recipient_count > 0, True), else_=False)
    recipients = case((has_receipt, ListingPromotion.recipient_count), else_=None)
    return db.execute(select(ListingPromotion.marketplace, ListingPromotion.state,
        ListingPromotion.confirmation, has_receipt, func.count(), func.sum(recipients)).where(
            ListingPromotion.user_id == seller, ListingPromotion.action == 'offer',
            ListingPromotion.sent_at >= start, ListingPromotion.sent_at < end,
        ).group_by(ListingPromotion.marketplace, ListingPromotion.state, ListingPromotion.confirmation, has_receipt)).all()


def ebay_counts(db: Session, seller: str, start: datetime, end: datetime):
    has_receipt = case((EbayOfferAttempt.recipient_count > 0, True), else_=False)
    recipients = case((has_receipt, EbayOfferAttempt.recipient_count), else_=None)
    return db.execute(select(EbayOfferAttempt.state, has_receipt, func.count(), func.sum(recipients)).where(
        EbayOfferAttempt.user_id == seller, EbayOfferAttempt.sent_at >= start, EbayOfferAttempt.sent_at < end,
    ).group_by(EbayOfferAttempt.state, has_receipt)).all()


def _after(sent_at, row_id, before: tuple[datetime, str] | None):
    """Keyset paging: rows strictly older than the cursor, ties broken by ID."""
    if before is None:
        return sent_at.is_not(None)
    at, after_id = before
    return or_(sent_at < at, and_(sent_at == at, row_id < after_id))


def promotion_offer_log(db: Session, seller: str, before: tuple[datetime, str] | None, limit: int) -> list[ListingPromotion]:
    """Offers that reached a marketplace (a send time), newest first."""
    return list(db.scalars(select(ListingPromotion).where(
        ListingPromotion.user_id == seller, ListingPromotion.action == 'offer',
        _after(ListingPromotion.sent_at, ListingPromotion.id, before),
    ).order_by(ListingPromotion.sent_at.desc(), ListingPromotion.id.desc()).limit(limit)))


def ebay_offer_log(db: Session, seller: str, before: tuple[datetime, str] | None, limit: int):
    """eBay offers that reached eBay, newest first, with their listing and item title."""
    return db.execute(select(EbayOfferAttempt, EbayOfferRule.listing_id, Conversation.title)
        .join(EbayOfferRule, and_(EbayOfferRule.user_id == EbayOfferAttempt.user_id, EbayOfferRule.id == EbayOfferAttempt.rule_id))
        .outerjoin(Conversation, and_(Conversation.user_id == EbayOfferRule.user_id, Conversation.id == EbayOfferRule.conversation_id))
        .where(EbayOfferAttempt.user_id == seller, _after(EbayOfferAttempt.sent_at, EbayOfferAttempt.id, before))
        .order_by(EbayOfferAttempt.sent_at.desc(), EbayOfferAttempt.id.desc()).limit(limit)).all()
