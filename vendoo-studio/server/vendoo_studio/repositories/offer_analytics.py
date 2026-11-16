"""Bounded, seller-scoped aggregates; never hydrate the promotion journal."""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendoo_studio.models.ebay_offer import EbayOfferAttempt
from vendoo_studio.models.listing_promotion import ListingPromotion


def promotion_counts(db: Session, seller: str, start: datetime, end: datetime):
    return db.execute(select(ListingPromotion.marketplace, ListingPromotion.state,
        ListingPromotion.confirmation, func.count(), func.sum(ListingPromotion.recipient_count)).where(
            ListingPromotion.user_id == seller, ListingPromotion.action == 'offer',
            ListingPromotion.sent_at >= start, ListingPromotion.sent_at < end,
        ).group_by(ListingPromotion.marketplace, ListingPromotion.state, ListingPromotion.confirmation)).all()


def ebay_counts(db: Session, seller: str, start: datetime, end: datetime):
    return db.execute(select(EbayOfferAttempt.state, func.count(), func.sum(EbayOfferAttempt.recipient_count)).where(
        EbayOfferAttempt.user_id == seller, EbayOfferAttempt.sent_at >= start, EbayOfferAttempt.sent_at < end,
    ).group_by(EbayOfferAttempt.state)).all()
