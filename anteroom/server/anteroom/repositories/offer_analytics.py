"""Bounded, seller-scoped aggregates; never hydrate the promotion journal."""
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from anteroom.models.ebay_offer import EbayOfferAttempt
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
