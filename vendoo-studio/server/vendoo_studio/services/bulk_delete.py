"""Seller-reviewed delete of selected items from Anteroom. Nothing is delisted.

Deleting here removes the item, its photos and its history from Anteroom only.
An item still live on a marketplace is refused: deleting it would leave that
listing up with nothing in Anteroom to delist it from, so the seller delists it
or marks it not listed first. The seller reviews the items and confirms once;
each item must still match its review when it is deleted.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.models.marketplace_listing import UNSETTLED_STATES
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services.api_job_lock import start_gate

MAX_ITEMS = 300


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=MAX_ITEMS)


class ReviewedRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str
    review_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rows: list[ReviewedRow] = Field(min_length=1, max_length=MAX_ITEMS)
    confirm: bool = False


def _name(marketplace: str) -> str:
    from vendoo_studio.services.bulk_marketplace import NAMES

    return NAMES.get(marketplace, marketplace)


def review_row(db: Session, conv_id: str) -> dict:
    """Whether this item can be deleted from Anteroom, with a token for it as it is now."""
    from vendoo_studio.services.marketplace_delist import listings

    row = {"conversation_id": conv_id, "title": None, "skip_reason": None, "warning": None, "review_token": None}
    conv = db.get(Conversation, conv_id, populate_existing=True)
    if conv is None:
        row["skip_reason"] = "This item is no longer in Anteroom."
        return row
    row["title"] = conv.title or "Untitled item"
    if db.query(Job.id).filter(Job.conversation_id == conv_id, Job.status.in_(ACTIVE_JOB_STATUSES)).first():
        row["skip_reason"] = "A job is running on this item. Wait for it to finish."
        return row
    records = MarketplaceListingRepo(db).for_conversation(conv_id)
    unsettled = [name for name, record in records.items() if record.get("state") in UNSETTLED_STATES]
    if unsettled:
        row["skip_reason"] = f"Check the last {', '.join(map(_name, unsettled))} action first; Anteroom has not confirmed it."
        return row
    known = listings(db, conv_id)
    live = [name for name, entry in known.items()
            if entry.get("listing_id") and entry.get("state") != "deleted" and not entry.get("sold_here")]
    if live:
        row["skip_reason"] = (f"Still listed on {', '.join(map(_name, live))}. Delist it or mark it not listed first; "
                              "deleting it here would not take the listing down.")
        return row
    if conv.status == "active":
        row["warning"] = "Vendoo shows this item as listed. Deleting it here does not change Vendoo."
    listing = ListingRepo(db).get_current(conv_id)
    basis = [conv.id, conv.title, conv.notes, conv.status, str(conv.updated_at),
             listing.current_revision_id if listing else None, sorted(records.items()), sorted(known.items())]
    row["review_token"] = hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()
    return row


def preview(db: Session, body: PreviewRequest) -> dict:
    """Review only. Nothing is deleted."""
    return {"rows": [review_row(db, conv_id) for conv_id in dict.fromkeys(body.conversation_ids)]}


def delete(db: Session, body: DeleteRequest) -> dict:
    """Delete each reviewed item that still matches its review. Never contacts a marketplace."""
    from fastapi import HTTPException

    from vendoo_studio.services.listing_delete import ListingBusy, delete_listing

    if body.confirm is not True:
        raise HTTPException(400, "Review and confirm the items to delete first.")
    deleted, skipped, seen = [], [], set()
    for reviewed in body.rows:
        conv_id = reviewed.conversation_id
        if conv_id in seen:
            continue
        seen.add(conv_id)
        try:
            with start_gate():
                db.expire_all()
                current = review_row(db, conv_id)
                if current["skip_reason"]:
                    raise ValueError(current["skip_reason"])
                if current["review_token"] != reviewed.review_token:
                    raise ValueError("This item changed since the review. Review again.")
                delete_listing(db, conv_id)
            deleted.append(conv_id)
        except (ValueError, ListingBusy, LookupError) as exc:
            db.rollback()
            skipped.append({"conversation_id": conv_id, "reason": str(exc)})
    return {"deleted": deleted, "skipped": skipped}
