"""The listings an import left for the seller to compare with an item they already have.

Import all holds a listing that looks like an existing item instead of
importing a duplicate (``inventory_import.save_owned_listing``). The hold is
kept here so the import screen on any device and the seller's assistant read
one queue. Linking the listing or importing it as new (``inventory_import``)
clears it; ``reject`` records that the two are different products, and that
item is then never suggested for that listing again. Only Anteroom changes:
no marketplace is read or written here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation, Photo
from anteroom.models.import_review import NOT_MATCH, PENDING, ImportReview

MAX_REVIEWS = 200
MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop")
_NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}


class ReviewNotFound(LookupError):
    """No pending review with that ID for this seller."""


def _price(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number.quantize(Decimal("0.01")) if number.is_finite() and 0 < number < 1_000_000 else None


def hold(db: Session, marketplace: str, listing_id: str, title: str, price, match: dict) -> None:
    """Keep a listing that looks like ``match`` (``chrome_import.suggested_match``) for the seller to review.

    One pending suggestion per listing: a newer one replaces it. Commits.
    """
    from anteroom.services.marketplace_identity import public_listing_url

    conversation_id = match["conversation_id"]
    score = match.get("score")
    fields = {"title": (str(title or "").strip() or listing_id)[:255], "price": _price(price),
              "url": public_listing_url(marketplace, listing_id),
              "score": float(score) if isinstance(score, (int, float)) and math.isfinite(score) else 0.0}
    rows = db.scalars(select(ImportReview).where(
        ImportReview.marketplace == marketplace, ImportReview.listing_id == listing_id)).all()
    review = next((row for row in rows if row.conversation_id == conversation_id), None)
    if review is not None and review.decision == NOT_MATCH:
        return
    for row in rows:
        if row.decision == PENDING and row is not review:
            db.delete(row)
    if review is None:
        try:
            # Import all on another device may hold the same listing at the same moment.
            with db.begin_nested():
                db.add(ImportReview(marketplace=marketplace, listing_id=listing_id,
                                    conversation_id=conversation_id, **fields))
        except IntegrityError:
            review = db.scalar(select(ImportReview).where(
                ImportReview.marketplace == marketplace, ImportReview.listing_id == listing_id,
                ImportReview.conversation_id == conversation_id))
            if review is None or review.decision == NOT_MATCH:
                db.commit()
                return
    if review is not None:
        for name, value in fields.items():
            setattr(review, name, value)
    db.commit()


def rejected_items(db: Session, marketplace: str, listing_id: str) -> set[str]:
    """The items the seller said are not this listing."""
    return set(db.scalars(select(ImportReview.conversation_id).where(
        ImportReview.marketplace == marketplace, ImportReview.listing_id == listing_id,
        ImportReview.decision == NOT_MATCH)))


def resolve(db: Session, marketplace: str, listing_id: str) -> None:
    """The listing now belongs to an item (linked or imported): nothing about it is left to review. The caller commits."""
    db.execute(delete(ImportReview).where(
        ImportReview.marketplace == marketplace, ImportReview.listing_id == listing_id))


def _still_open(db: Session, review: ImportReview) -> bool:
    """Whether the listing still has no item and the suggested item is still not on that marketplace."""
    from anteroom.services import inventory_import
    from anteroom.services.marketplace_delist import DelistError, listings

    try:
        if inventory_import.owner(db, review.marketplace, review.listing_id):
            return False
        return review.marketplace not in listings(db, review.conversation_id)
    except (inventory_import.ImportConflict, DelistError):
        return False


def _view(db: Session, review: ImportReview, item: Conversation) -> dict:
    from anteroom.services.chrome_import import local_price

    listing_price = float(review.price) if review.price is not None else None
    item_price = local_price(db, item.id)
    differs = bool(listing_price and item_price and abs(item_price - listing_price) > max(1.0, listing_price * 0.1))
    name = _NAMES.get(review.marketplace, review.marketplace)
    reason = f"The titles read {round(review.score * 100)}% alike"
    if differs:
        reason += f"; priced ${item_price:.2f} in Anteroom and ${listing_price:.2f} on {name}"
    photos = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == item.id)) or 0
    return {
        "id": review.id,
        "marketplace": review.marketplace,
        "listing_id": review.listing_id,
        "title": review.title,
        "price": f"{review.price:.2f}" if review.price is not None else None,
        "url": review.url,
        "held_at": review.created_at.isoformat() if review.created_at else None,
        "match": {"conversation_id": item.id, "title": item.title or "", "score": round(review.score, 2),
                  "price": item_price, "price_differs": differs, "photo_count": int(photos)},
        "reason": reason + ".",
    }


def pending(db: Session, marketplace: str | None = None, limit: int = MAX_REVIEWS) -> list[dict]:
    """Listings waiting for the seller, oldest first. Ones settled some other way since are left out."""
    statement = (select(ImportReview, Conversation)
                 .join(Conversation, Conversation.id == ImportReview.conversation_id)
                 .where(ImportReview.decision == PENDING)
                 .order_by(ImportReview.created_at, ImportReview.id))
    if marketplace:
        statement = statement.where(ImportReview.marketplace == marketplace)
    out = []
    for review, item in db.execute(statement).all():
        if len(out) >= limit:
            break
        if _still_open(db, review):
            out.append(_view(db, review, item))
    return out


def get_pending(db: Session, review_id: str) -> ImportReview:
    review = db.scalar(select(ImportReview).where(ImportReview.id == review_id, ImportReview.decision == PENDING))
    if review is None or not _still_open(db, review):
        raise ReviewNotFound("That listing is no longer waiting for review. List the waiting ones again.")
    return review


def reject(db: Session, review_id: str) -> dict:
    """The listing and the item are different products: stop suggesting that item for it. Commits."""
    review = get_pending(db, review_id)
    review.decision = NOT_MATCH
    db.commit()
    name = _NAMES.get(review.marketplace, review.marketplace)
    return {"id": review.id, "marketplace": review.marketplace, "listing_id": review.listing_id,
            "conversation_id": review.conversation_id, "decision": NOT_MATCH,
            "message": (f"Anteroom will not suggest that item for this {name} listing again. "
                        f"Import all brings the listing in as its own item, or import it from Import from {name}.")}
