"""What an item's live marketplaces are behind on, and going back to what they show.

``unsent_edits`` says which items moved on since they were last listed. This
names, per live marketplace, the fields that differ between the revision that
marketplace was written from and the item's current revision, and can put an
item back to that sent revision. Nothing here contacts a marketplace.
"""
from __future__ import annotations

import copy
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_evidence import ListingCorrection
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo, ListingRepo, RevisionConflict
from anteroom.services import listing_editor
from anteroom.services.hosted_listing_contract import MARKETS, ROOTS, without_operational_keys
from anteroom.services.price_markdown import listing_busy
from anteroom.services.unsent_edits import normalized_listing_hash, unsent_edits

# Keys whose children are named after a marketplace (``ebay``, ``ebay_path``, ``ebay_suggested``).
_PER_MARKET_CHILDREN = ("direct_category_ids", "marketplace_photos")
# The seller's own bookkeeping: never sent, so never behind, and kept when edits are discarded.
_PRIVATE = ("labels", "cost")


class PendingEditsError(ValueError):
    """A discard the seller has to sort out first: nothing live, or the item is busy."""


def _comparable(listing: dict | None) -> dict[str, Any]:
    raw = listing if isinstance(listing, dict) else {}
    # A dict in gives a dict out; the helper is typed for any JSON value.
    return cast(dict[str, Any], without_operational_keys(
        {key: value for key, value in raw.items() if key in ROOTS and key not in _PRIVATE}))


def _changes(live: dict, new: dict, path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in sorted(set(live) | set(new), key=str):
        before, after = live.get(key), new.get(key)
        if isinstance(before, dict) and isinstance(after, dict):
            out.extend(_changes(before, after, (*path, key)))
        elif before != after and not (before in (None, "") and after in (None, "")):
            out.append({"field": ".".join((*path, key)), "live": copy.deepcopy(before), "new": copy.deepcopy(after)})
    return out


def _concerns(marketplace: str, field: str) -> bool:
    """Whether a changed field is part of what ``marketplace`` shows."""
    root, _, rest = field.partition(".")
    if root.endswith("_specifics"):
        return root == f"{marketplace}_specifics"
    if root in _PER_MARKET_CHILDREN and rest:
        child = rest.split(".", 1)[0].removesuffix("_path").removesuffix("_suggested")
        return child not in MARKETS or child == marketplace
    return True


def pending_edits(db: Session, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Items with local edits their live marketplaces do not show yet, newest edit first."""
    query = select(Conversation).order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    if conversation_id:
        query = query.where(Conversation.id == conversation_id)
    convs = list(db.scalars(query).all())
    current = {
        conv_id: rev_id
        for conv_id, rev_id in db.execute(select(Listing.conversation_id, Listing.current_revision_id)).all()
        if rev_id
    }
    unsent = unsent_edits(db, convs, current)
    if not unsent:
        return []
    records = MarketplaceListingRepo(db).live_records(conversation_id)
    wanted = {current[conv_id] for conv_id in unsent} | {
        rec.revision_id for rec in records if rec.conversation_id in unsent and rec.revision_id
    }
    bodies = ListingRepo(db).revision_bodies(wanted)
    hashes = {rev_id: normalized_listing_hash(body) for rev_id, body in bodies.items()}

    items: list[dict[str, Any]] = []
    for conv in convs:
        if conv.id not in unsent:
            continue
        rev_id = current[conv.id]
        now = _comparable(bodies.get(rev_id))
        behind: list[dict[str, Any]] = []
        for rec in sorted((r for r in records if r.conversation_id == conv.id), key=lambda r: r.marketplace):
            sent = rec.revision_id
            if sent and (sent == rev_id or hashes.get(sent) == hashes.get(rev_id)):
                continue
            changes = None
            if sent and sent in bodies:
                changes = [c for c in _changes(_comparable(bodies[sent]), now) if _concerns(rec.marketplace, c["field"])]
                if not changes:
                    continue
            behind.append({
                "marketplace": rec.marketplace,
                "live_revision_id": sent,
                "listing_url": rec.listing_url,
                # None when Anteroom does not know which version the marketplace was sent.
                "changes": changes,
            })
        if behind:
            items.append({
                "conversation_id": conv.id,
                "title": str(now.get("title") or conv.title or "Untitled"),
                "revision_id": rev_id,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "marketplaces": behind,
            })
    return items


def discard_pending_edits(
    db: Session, conv_id: str, *, expected_revision_id: str, marketplace: str | None = None,
) -> dict[str, Any]:
    """Put an item back to the revision last sent to its live marketplaces. Contacts no marketplace.

    When live marketplaces were sent different versions, the newest one wins
    unless ``marketplace`` names the one to match. Saves a new revision (as a
    revision restore does), so a scheduled draft is unscheduled like any edit.
    """
    if ConversationRepo(db).get(conv_id) is None:
        raise PendingEditsError("Listing not found.")
    listing = db.get(Listing, conv_id)
    current_id = listing.current_revision_id if listing else None
    if not current_id or current_id != expected_revision_id:
        raise RevisionConflict("This listing changed since you read it. Read it again before discarding its edits.")
    if listing_busy(conv_id, db):
        raise PendingEditsError("Wait for the active listing job or background task to finish before discarding edits.")

    records = [rec for rec in MarketplaceListingRepo(db).live_records(conv_id) if rec.revision_id]
    if marketplace:
        records = [rec for rec in records if rec.marketplace == marketplace]
    if not records:
        where = f"on {marketplace}" if marketplace else "on any marketplace"
        raise PendingEditsError(f"This item is not live {where} through Anteroom, so there is no sent version to go back to.")
    sent = list(db.scalars(
        select(ListingRevision).where(ListingRevision.id.in_({rec.revision_id for rec in records}))
    ).all())
    if not sent:
        raise PendingEditsError("The version last sent to this item's marketplaces is no longer stored.")
    target = max(sent, key=lambda rev: (rev.created_at is not None, rev.created_at, rev.id))
    current = db.get(ListingRevision, current_id)
    current_json = current.listing_json if current is not None and isinstance(current.listing_json, dict) else {}
    restored = copy.deepcopy(target.listing_json)
    # Labels and cost are the seller's bookkeeping, not what marketplaces show: keep today's.
    for key in _PRIVATE:
        if key in current_json:
            restored[key] = copy.deepcopy(current_json[key])
        else:
            restored.pop(key, None)
    if current is not None and normalized_listing_hash(current_json) == normalized_listing_hash(restored):
        return {"discarded": False, "revision_id": current_id, "restored_from_revision_id": target.id,
                "listing": current.listing_json}

    saved = listing_editor.save_listing_edit(
        conv_id, restored, db, source="restore",
        expected_revision_id=expected_revision_id, commit=False, verbatim=True,
    )
    # Going back is not a correction for generation to learn from.
    db.query(ListingCorrection).filter_by(revision_id=saved["revision_id"]).update({"source": "restore"})
    db.commit()
    return {"discarded": True, "revision_id": saved["revision_id"], "restored_from_revision_id": target.id,
            "listing": saved["listing"], "validation": saved["validation"]}
