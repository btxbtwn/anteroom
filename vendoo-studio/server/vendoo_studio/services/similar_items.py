"""Likely duplicates among the seller's items, for the seller to review.

Anteroom's take on Vendoo's Duplicate Finder. Two items are suggested when they
share a photo file or a SKU, or when the Chrome import's title matcher reads
their titles as the same item and their brand and size do not disagree. Cover
photos that look alike (a 64-bit difference hash a few bits apart) rank a pair
higher but never suggest one alone: sellers shoot every item on the same
backdrop, so different garments hash close together.

Finding changes nothing. Merge and dismiss happen only when the seller asks,
and neither deletes nor delists anything: a merge moves the other item's
marketplace links and sales onto the one kept, adds its labels, and moves it to
Finished with a note of where it went.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendoo_studio.database import json_value
from vendoo_studio.services.chrome_import import MATCH_THRESHOLD, title_similarity, title_tokens
from vendoo_studio.services.photo_store import photo_store

# Bits two cover hashes may differ by and still look like the same shot.
PHOTO_DISTANCE = 6
MAX_PAIRS = 200
# A picture or SKU on more items than this is a stock shot or placeholder, not a duplicate.
_SHARED_LIMIT = 6
# Words on more items than this ("women", "size") say nothing about a match on their own.
_COMMON_WORD_SHARE = 0.15
# Titles Anteroom gives an item before it has one of its own.
_UNTITLED = {"", "new listing", "untitled listing", "imported from vendoo"}


class MergeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class _Item:
    id: str
    title: str
    status: str
    brand: str
    size: str
    price: float | None
    sku: str
    cover_id: str | None = None
    cover_checksum: str | None = None
    checksums: set[str] = field(default_factory=set)


def difference_hash(path: Path) -> str | None:
    """A 64-bit difference hash of an image as 16 hex digits, or None when it cannot be read."""
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as image:
            image.draft("L", (64, 64))
            small = ImageOps.exif_transpose(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = bits << 1 | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return f"{bits:016x}"


def _fingerprints(db: Session, photos: dict[str, str]) -> dict[str, int]:
    """Cover hashes by checksum, computing and saving the ones not seen before."""
    from vendoo_studio.models.similar_item import PhotoFingerprint

    known = {
        row.checksum: row.dhash
        for row in db.query(PhotoFingerprint).filter(PhotoFingerprint.checksum.in_(list(photos))).all()
    } if photos else {}
    for checksum, stored in photos.items():
        if checksum in known:
            continue
        try:
            known[checksum] = difference_hash(photo_store().local_path(stored))
        except (FileNotFoundError, ValueError):
            continue
        db.add(PhotoFingerprint(checksum=checksum, dhash=known[checksum]))
    db.commit()
    return {checksum: int(value, 16) for checksum, value in known.items() if value}


def _load(db: Session) -> dict[str, _Item]:
    from vendoo_studio.models.conversation import Conversation, Photo
    from vendoo_studio.models.listing import Listing, ListingRevision
    from vendoo_studio.services.vendoo_import import parse_notes

    facets = {
        conv_id: (brand, size, price)
        for conv_id, brand, size, price in db.query(
            Listing.conversation_id,
            json_value(ListingRevision.listing_json, "brand"),
            json_value(ListingRevision.listing_json, "size"),
            json_value(ListingRevision.listing_json, "price"),
        ).join(ListingRevision, ListingRevision.id == Listing.current_revision_id).all()
    }
    items: dict[str, _Item] = {}
    for conv_id, title, status, notes_raw in db.query(
        Conversation.id, Conversation.title, Conversation.status, Conversation.notes
    ).all():
        notes = parse_notes(notes_raw)
        if notes.get("mergedInto"):
            continue
        brand, size, price = facets.get(conv_id, (None, None, None))
        try:
            amount = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        items[conv_id] = _Item(
            id=conv_id,
            title=str(title or "").strip(),
            status=str(status or "draft"),
            brand=str(brand or "").strip(),
            size=str(size or "").strip(),
            price=amount,
            sku=str(notes.get("sku") or "").strip(),
        )
    rows = db.query(Photo.id, Photo.conversation_id, Photo.checksum, Photo.stored_filename).order_by(
        Photo.conversation_id, Photo.display_order, Photo.created_at
    )
    for photo_id, conv_id, checksum, _stored in rows:
        item = items.get(conv_id)
        if item is None or not checksum:
            continue
        if item.cover_id is None:
            item.cover_id, item.cover_checksum = photo_id, checksum
        item.checksums.add(checksum)
    return items


def _agree(left: str, right: str) -> bool:
    return not left or not right or left.casefold() == right.casefold()


def _reviewed(db: Session) -> set[tuple[str, str]]:
    from vendoo_studio.models.similar_item import SimilarItemReview

    return {(first, second) for first, second in db.query(SimilarItemReview.first_id, SimilarItemReview.second_id)}


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def find_similar(db: Session) -> dict[str, Any]:
    """Pairs worth a look, most alike first. Reads and fingerprints photos; changes no item."""
    from vendoo_studio.models.conversation import Photo
    from vendoo_studio.services.photos import cover_thumb_url

    items = _load(db)
    skip = _reviewed(db)
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def note(a: str, b: str, reason: str, score: float, **extra: Any) -> None:
        key = _pair(a, b)
        if a == b or key in skip:
            return
        entry = found.setdefault(key, {"reasons": [], "score": 0.0})
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
        entry["score"] = max(entry["score"], score)
        entry.update(extra)

    by_checksum: dict[str, list[str]] = defaultdict(list)
    by_sku: dict[str, list[str]] = defaultdict(list)
    for item in items.values():
        for checksum in item.checksums:
            by_checksum[checksum].append(item.id)
        if item.sku:
            by_sku[item.sku.casefold()].append(item.id)
    for reason, groups in (("same_photo", by_checksum), ("same_sku", by_sku)):
        for owners in groups.values():
            if 1 < len(owners) <= _SHARED_LIMIT:
                for index, first in enumerate(owners):
                    for second in owners[index + 1:]:
                        note(first, second, reason, 1.0)

    titled = {item.id: title_tokens(item.title) for item in items.values() if item.title.casefold() not in _UNTITLED}
    by_word: dict[str, list[str]] = defaultdict(list)
    for conv_id, words in titled.items():
        for word in words:
            by_word[word].append(conv_id)
    common = max(20, int(len(titled) * _COMMON_WORD_SHARE))
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for owners in by_word.values():
        if len(owners) > common:
            continue
        for index, first in enumerate(owners):
            for second in owners[index + 1:]:
                shared[_pair(first, second)] += 1
    for (first, second), count in shared.items():
        left, right = items[first], items[second]
        if count < min(2, len(titled[first]), len(titled[second])):
            continue
        if not (_agree(left.brand, right.brand) and _agree(left.size, right.size)):
            continue
        score = title_similarity(left.title, right.title)
        if score >= MATCH_THRESHOLD:
            note(first, second, "similar_title", round(score, 2), title_score=round(score, 2))

    covers = {items[conv_id].cover_checksum for pair in found for conv_id in pair} - {None}
    stored = dict(
        db.query(Photo.checksum, Photo.stored_filename).filter(Photo.checksum.in_(covers)).all()
    ) if covers else {}
    hashes = _fingerprints(db, stored)
    for (first, second), entry in found.items():
        left, right = hashes.get(items[first].cover_checksum), hashes.get(items[second].cover_checksum)
        if left is None or right is None or "same_photo" in entry["reasons"]:
            continue
        distance = (left ^ right).bit_count()
        if distance <= PHOTO_DISTANCE:
            entry["reasons"].append("similar_photo")
            entry["photo_distance"] = distance

    def describe(item: _Item) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title or "Untitled listing",
            "status": item.status,
            "brand": item.brand or None,
            "size": item.size or None,
            "price": item.price,
            "sku": item.sku or None,
            "cover_url": cover_thumb_url(item.cover_id, item.cover_checksum) if item.cover_id else None,
        }

    ranked = sorted(
        found.items(),
        key=lambda entry: (-entry[1]["score"], -len(entry[1]["reasons"]), entry[0]),
    )
    return {
        "total": len(ranked),
        "pairs": [
            {
                "first": describe(items[first]),
                "second": describe(items[second]),
                "reasons": entry["reasons"],
                "score": entry["score"],
                "photo_distance": entry.get("photo_distance"),
                "title_score": entry.get("title_score"),
            }
            for (first, second), entry in ranked[:MAX_PAIRS]
        ],
    }


def _record(db: Session, a: str, b: str, decision: str, kept_id: str | None = None) -> None:
    from vendoo_studio.models.similar_item import SimilarItemReview

    first, second = _pair(a, b)
    row = db.query(SimilarItemReview).filter_by(first_id=first, second_id=second).one_or_none()
    if row is None:
        db.add(SimilarItemReview(first_id=first, second_id=second, decision=decision, kept_id=kept_id))
    else:
        row.decision, row.kept_id = decision, kept_id


def dismiss(db: Session, first_id: str, second_id: str) -> None:
    """The seller says these are different items: stop suggesting the pair."""
    from vendoo_studio.models.conversation import Conversation

    if first_id == second_id or db.get(Conversation, first_id) is None or db.get(Conversation, second_id) is None:
        raise MergeError(404, "One of these items no longer exists.")
    _record(db, first_id, second_id, "dismissed")
    db.commit()


def merge(db: Session, keep_id: str, merge_id: str) -> dict[str, Any]:
    """Fold ``merge_id`` into ``keep_id`` at the seller's request.

    Moves its marketplace links and sales-ledger lines and adds its labels.
    The merged item stays, moved to Finished with ``mergedInto`` in its notes,
    for the seller to delete if they want. Nothing is delisted.
    """
    from vendoo_studio.models.conversation import Conversation, utcnow
    from vendoo_studio.models.listing import Listing, ListingRevision
    from vendoo_studio.models.marketplace_listing import MarketplaceListing
    from vendoo_studio.models.marketplace_sale import MarketplaceSale
    from vendoo_studio.repositories.queries import BUSY_LISTING_STATUSES, ListingRepo, RevisionConflict, _settle
    from vendoo_studio.services.native_marketplaces import NAMES, running_marketplace
    from vendoo_studio.services.vendoo_import import merge_notes, parse_notes, split_vendoo_labels

    if keep_id == merge_id:
        raise MergeError(400, "Pick two different items to merge.")
    # Use the same row locks as hosted draft submissions. Take both in ID order
    # so simultaneous reversed merges cannot deadlock.
    rows = db.scalars(select(Conversation).where(Conversation.id.in_([keep_id, merge_id]))
                      .order_by(Conversation.id).with_for_update()).all()
    owned = {row.id: row for row in rows}
    keep, other = owned.get(keep_id), owned.get(merge_id)
    if keep is None or other is None:
        raise MergeError(404, "One of these items no longer exists.")
    from vendoo_studio.models.background_task import BackgroundTask
    if db.scalar(select(BackgroundTask.id).where(
            BackgroundTask.conversation_id.in_([keep_id, merge_id]),
            BackgroundTask.status.in_(("queued", "running"))).limit(1)):
        raise MergeError(409, "Wait for the draft task to finish, or stop it before merging.")
    if parse_notes(other.notes).get("mergedInto") or parse_notes(keep.notes).get("mergedInto"):
        raise MergeError(409, "One of these items was already merged.")
    for conv in (keep, other):
        busy = running_marketplace(db, conv.id)
        if conv.status in BUSY_LISTING_STATUSES or busy:
            where = f" on {busy}" if busy else ""
            raise MergeError(409, f"Wait for “{conv.title or 'Untitled listing'}” to finish listing{where} before merging.")

    links = db.query(MarketplaceListing).filter_by(conversation_id=merge_id).all()
    kept_markets = {row.marketplace for row in db.query(MarketplaceListing.marketplace).filter_by(conversation_id=keep_id)}
    clash = sorted(NAMES.get(row.marketplace, row.marketplace) for row in links if row.marketplace in kept_markets)
    if clash:
        raise MergeError(409, f"Both items have a {', '.join(clash)} listing. Clear one of them, then merge.")
    for row in links:
        row.conversation_id = keep_id
    sales = db.query(MarketplaceSale).filter_by(conversation_id=merge_id).all()
    for line in sales:
        line.conversation_id = keep_id

    kept_labels = split_vendoo_labels(parse_notes(keep.notes).get("vendooLabels"))
    added = [
        label for label in split_vendoo_labels(parse_notes(other.notes).get("vendooLabels"))
        if label.casefold() not in {kept.casefold() for kept in kept_labels}
    ]
    label_revision = None
    if added:
        labels = [*kept_labels, *added]
        keep.notes = merge_notes(keep.notes, {"vendooLabels": ", ".join(labels)})
        current = db.get(Listing, keep_id)
        revision = db.get(ListingRevision, current.current_revision_id) if current and current.current_revision_id else None
        if revision is not None and isinstance(revision.listing_json, dict):
            label_revision = (revision.id, {**revision.listing_json, "labels": labels})

    now = utcnow()
    other.notes = merge_notes(other.notes, {"mergedInto": keep_id})
    _settle(other, now, force=True)
    keep.updated_at = now
    _record(db, keep_id, merge_id, "merged", kept_id=keep_id)
    if label_revision:
        revision_id, body = label_revision
        try:
            # Commit the new revision and the links, sales and merge decision
            # together. A concurrent editor save rolls the entire merge back.
            ListingRepo(db).save_revision(keep_id, body, source="merge", parent_revision_id=revision_id,
                                          expected_revision_id=revision_id)
        except RevisionConflict as exc:
            raise MergeError(409, "This listing changed while merging. Review it and try again.") from exc
    else:
        db.commit()
    return {
        "kept_id": keep_id,
        "merged_id": merge_id,
        "marketplaces": sorted(row.marketplace for row in links),
        "sales": len(sales),
        "labels": added,
    }
