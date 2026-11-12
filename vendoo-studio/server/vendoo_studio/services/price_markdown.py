"""Save price drops onto Studio listings, one at a time or as a batch markdown.

A drop is a ``price_drop`` revision and nothing else: it never calls Vendoo,
queues a send, lists or delists. The listing then carries unsent edits until
the seller presses Update Vendoo on it.

The batch preview uses only the seller's own sales and prior drops. Sold comps
are a web search that takes minutes per listing, so they stay on the
single-listing dialog.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendoo_studio.services import listing_persistence
from vendoo_studio.services.validation import normalize_listing_dropdowns, validate_listing
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo, RevisionConflict
from vendoo_studio.services.price_drop import (
    PRICE_DROP_SOURCE,
    apply_price_to_listing,
    build_preview,
    listing_price,
    whole_dollars,
)

NO_PRICE = "Listing has no price to drop"
NOT_LOWER = "New price must be lower than the current price"
BUSY = "Anteroom is generating or sending this listing"
CHANGED = "This listing changed since you reviewed it"
NOT_FOUND = "Conversation not found"


class PriceDropError(ValueError):
    """A listing that cannot take this drop; ``status`` is the HTTP status a route should use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def listing_busy(conv_id: str, db: Session) -> bool:
    """Whether Studio is generating, answering about, or sending this listing right now."""
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES
    from vendoo_studio.services import activity
    from vendoo_studio.services.streaming import active_generation
    from vendoo_studio.models.background_task import BackgroundTask

    return bool(db.scalar(select(BackgroundTask.id).where(
        BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(("queued", "running"))).limit(1))
        or active_generation(conv_id) or activity.running(conv_id) or any(
        job.status in ACTIVE_JOB_STATUSES for job in JobRepo(db).list_by_conversation(conv_id)
    ))


def current_listing(conv_id: str, db: Session) -> tuple[dict, list]:
    """The newest revision's listing and every revision, newest first."""
    if not ConversationRepo(db).get(conv_id):
        raise PriceDropError(NOT_FOUND, 404)
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        raise PriceDropError(NO_PRICE)
    listing = copy.deepcopy(revisions[0].listing_json)
    if listing_price(listing) is None:
        raise PriceDropError(NO_PRICE)
    return listing, revisions


def apply_price_drop(
    conv_id: str,
    price: float,
    db: Session,
    *,
    expected_revision_id: str,
) -> dict[str, Any]:
    """Save a confirmed whole-dollar price as a ``price_drop`` revision and commit."""
    from vendoo_studio.services.listing_generate import (
        propagate_general_size,
        sanitize_listing_sizes,
    )
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.background_task import BackgroundTask

    # Serialize with draft submission before reading the reviewed revision.
    if db.scalar(select(Conversation.id).where(Conversation.id == conv_id).with_for_update()) is None:
        raise PriceDropError(NOT_FOUND, 404)
    if db.scalar(select(BackgroundTask.id).where(
            BackgroundTask.conversation_id == conv_id,
            BackgroundTask.status.in_(("queued", "running"))).limit(1)):
        raise PriceDropError(BUSY, 409)

    listing, revisions = current_listing(conv_id, db)
    if not expected_revision_id or revisions[0].id != expected_revision_id:
        raise PriceDropError(CHANGED, 409)
    current = listing_price(listing)
    target = whole_dollars(price)
    if current is not None and target >= current:
        raise PriceDropError(NOT_LOWER)

    listing_repo = ListingRepo(db)
    current_row = listing_repo.get_current(conv_id)
    updated = apply_price_to_listing(listing, target)
    sanitize_listing_sizes(updated)
    propagate_general_size(updated)
    normalize_listing_dropdowns(updated)

    photo_count = len(ConversationRepo(db).get_photos(conv_id))
    validation = validate_listing(updated, photo_count, require_photos=True)
    try:
        revision = listing_persistence.save_revision(listing_repo.db,
            conv_id=conv_id,
            listing_json=updated,
            source=PRICE_DROP_SOURCE,
            parent_revision_id=expected_revision_id,
            expected_revision_id=expected_revision_id,
        )
    except RevisionConflict as exc:
        db.rollback()
        raise PriceDropError(CHANGED, 409) from exc
    if current_row:
        current_row.validation_status = "valid" if validation.valid else "error"
        current_row.validation_errors = validation.errors + validation.warnings

    db.commit()
    return {
        "revision_id": revision.id,
        "price": target,
        "previous_price": current,
        "validation": validation,
    }


def _cover_url(conv, covers: dict[str, tuple[str, str | None]]) -> str | None:
    from vendoo_studio.services.photos import cover_thumb_url
    from vendoo_studio.services.vendoo_import import parse_notes

    cover = covers.get(conv.id)
    if cover:
        return cover_thumb_url(*cover)
    return str(parse_notes(conv.notes).get("vendooCoverUrl") or "") or None


def _batch_reads(db: Session, conv_ids: list[str]) -> tuple[dict, dict, set[str]]:
    """Everything the review reads for a batch, in a fixed number of queries.

    Each query is a network round trip on the hosted database, so the batch
    never reads per listing. Revisions carry only their small stored fields
    (``inventory_fields``): price, brand and category are all a markdown needs,
    and the full marketplace forms can be hundreds of KB each.
    """
    from types import SimpleNamespace

    from vendoo_studio.models.background_task import BackgroundTask
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
    from vendoo_studio.models.listing import ListingRevision

    convs: dict[str, Any] = {}
    revisions: dict[str, list[Any]] = {conv_id: [] for conv_id in conv_ids}
    busy: set[str] = set()
    for start in range(0, len(conv_ids), _ID_BATCH):
        batch = conv_ids[start : start + _ID_BATCH]
        for row in db.execute(select(Conversation.id, Conversation.title, Conversation.notes)
                              .where(Conversation.id.in_(batch))):
            convs[row.id] = row
        for row in db.execute(select(ListingRevision.id, ListingRevision.conversation_id, ListingRevision.source,
                                     ListingRevision.parent_revision_id, ListingRevision.created_at,
                                     ListingRevision.inventory_fields)
                              .where(ListingRevision.conversation_id.in_(batch))
                              .order_by(ListingRevision.created_at.desc())):
            revisions[row.conversation_id].append(SimpleNamespace(
                id=row.id, source=row.source, parent_revision_id=row.parent_revision_id,
                created_at=row.created_at, listing_json=row.inventory_fields))
        busy.update(conv_id for conv_id in db.scalars(select(BackgroundTask.conversation_id).where(
            BackgroundTask.conversation_id.in_(batch), BackgroundTask.status.in_(("queued", "running")))) if conv_id)
        busy.update(conv_id for conv_id in db.scalars(select(Job.conversation_id).where(
            Job.conversation_id.in_(batch), Job.status.in_(ACTIVE_JOB_STATUSES))) if conv_id)
    return convs, revisions, busy


# SQLite binds each id in an IN clause as its own variable; older builds stop at 999.
_ID_BATCH = 500


def batch_preview(conv_ids: list[str], db: Session) -> list[dict[str, Any]]:
    """One review row per listing, in the order asked, with its suggested markdown.

    A listing that cannot be marked down comes back with ``skip_reason`` set
    rather than failing the batch.
    """
    from vendoo_studio.services import activity
    from vendoo_studio.services.sell_through import collect_outcomes, listing_age_days
    from vendoo_studio.services.streaming import active_generation

    ids = list(dict.fromkeys(conv_ids))
    covers = ConversationRepo(db).cover_photos(ids)
    outcomes = collect_outcomes(db)
    convs, revisions_by_id, busy = _batch_reads(db, ids)
    direct = MarketplaceListingRepo(db).summaries()
    rows: list[dict[str, Any]] = []
    for conv_id in ids:
        conv = convs.get(conv_id)
        row: dict[str, Any] = {
            "conversation_id": conv_id,
            "title": ((conv.title or "").strip() or "Untitled") if conv else "Untitled",
            "cover_photo_url": _cover_url(conv, covers) if conv else None,
            "revision_id": None,
            "current_price": None,
            "suggested_price": None,
            "suggested_effective_percent": None,
            "suggested_mode": None,
            "suggested_reason": "",
            "age_days": None,
            "skip_reason": None,
        }
        rows.append(row)
        if conv is None:
            row["skip_reason"] = "It is no longer in Anteroom"
            continue
        revisions = revisions_by_id.get(conv_id) or []
        if not revisions or listing_price(revisions[0].listing_json) is None:
            row["skip_reason"] = NO_PRICE
            continue
        age_days = listing_age_days(conv.notes, direct.get(conv_id, {}))
        preview = build_preview(revisions[0].listing_json, revisions, sold_outcomes=outcomes, age_days=age_days)
        row.update(
            revision_id=revisions[0].id,
            current_price=preview["current_price"],
            suggested_price=preview["suggested_price"],
            suggested_effective_percent=preview["suggested_effective_percent"],
            suggested_mode=preview["suggested_mode"],
            suggested_reason=preview["suggested_reason"],
            age_days=age_days,
        )
        if conv_id in busy or active_generation(conv_id) or activity.running(conv_id):
            row["skip_reason"] = BUSY
        elif preview["suggested_price"] >= preview["current_price"]:
            row["skip_reason"] = "The price is already as low as it goes"
    return rows


def batch_apply(drops: list[dict[str, Any]], db: Session) -> dict[str, list[dict[str, Any]]]:
    """Save each confirmed drop; report the ones that could not be saved instead of stopping.

    Each drop is ``{"conversation_id", "price", "expected_revision_id"}``.
    """
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for drop in drops:
        conv_id = drop["conversation_id"]
        if conv_id in seen:
            continue
        seen.add(conv_id)
        if listing_busy(conv_id, db):
            skipped.append({"conversation_id": conv_id, "reason": BUSY})
            continue
        try:
            result = apply_price_drop(
                conv_id,
                drop["price"],
                db,
                expected_revision_id=drop["expected_revision_id"],
            )
        except PriceDropError as exc:
            db.rollback()
            skipped.append({"conversation_id": conv_id, "reason": str(exc)})
            continue
        applied.append({
            "conversation_id": conv_id,
            "revision_id": result["revision_id"],
            "price": result["price"],
            "previous_price": result["previous_price"],
            "valid": result["validation"].valid,
        })
    return {"applied": applied, "skipped": skipped}
