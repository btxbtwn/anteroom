"""Seller-reviewed List, Delist, Relist, Update and Mark as not listed for selected items.

The seller picks the items, the action and the marketplaces, reviews what each
item would do, and confirms once. The browser then sends one request per item,
in order. Each request carries the reviewed marketplaces, revision and review
token, and must still match the item as it is now. Nothing is stored to run
later: closing the review stops the batch, and a failed item is never retried
on its own. List never takes a live listing down; that needs Relist.

Update writes the saved listing onto listings already live, on the named
marketplaces, through the same path as the item's own Update. It never lists
or delists anything. Mark as not listed only changes what Anteroom records: the
marketplace is not contacted and a listing still up there stays up.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_listing import LIVE_STATES, UNSETTLED_STATES
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.services.api_job_lock import start_gate

MAX_ITEMS = 100
Action = Literal["list", "delist", "relist", "update", "not_listed"]
Name = Literal["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"]
NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop",
         "sellwild": "Sellwild", "vinted": "Vinted"}
_running = False


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=MAX_ITEMS)
    action: Action
    marketplaces: list[Name] = Field(min_length=1, max_length=len(NAMES))


class PerformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str
    action: Action
    marketplaces: list[Name] = Field(min_length=1, max_length=len(NAMES))
    revision_id: str | None
    review_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool = False


def _name(marketplace: str) -> str:
    return NAMES.get(marketplace, marketplace)


def _target_reason(action: str, marketplace: str, entry: dict, record: dict) -> str | None:
    """Why this marketplace is left out for this item, or None when the action applies."""
    name = _name(marketplace)
    if entry.get("sold_here"):
        return f"Sold on {name}. That listing is left alone."
    if record.get("state") in UNSETTLED_STATES or entry.get("state") == "deleting":
        return f"Check the last {name} action first; Anteroom has not confirmed it."
    live = bool(entry.get("listing_id")) and entry.get("state") != "deleted"
    if action == "update":
        if not live or entry.get("state") != "live":
            return f"Not listed on {name}."
        return None
    if action == "not_listed":
        if not entry:
            return f"Anteroom has no {name} listing recorded for this item."
        return None
    if action == "list":
        if live or record.get("state") in LIVE_STATES:
            return f"Already listed on {name}. Choose Relist to take it down and list it again."
        return None
    if not live:
        return f"Not listed on {name}."
    return None


def _token(action: str, marketplaces: list[str], basis: str) -> str:
    return hashlib.sha256(json.dumps([action, marketplaces, basis]).encode()).hexdigest()


def review_row(db: Session, conv_id: str, action: str, marketplaces: list[str]) -> dict:
    """What ``action`` would do for one item on the named marketplaces, with a token for exactly that."""
    from vendoo_studio.services.automations import approval_snapshot
    from vendoo_studio.services.marketplace_delist import listings

    conv = db.get(Conversation, conv_id, populate_existing=True)
    row = {"conversation_id": conv_id, "title": None, "action": action, "revision_id": None,
           "marketplaces": [], "skipped": [], "skip_reason": None, "review_token": None}
    if conv is None:
        row["skip_reason"] = "This item is no longer in Anteroom."
        return row
    row["title"] = conv.title or "Untitled item"
    listing = ListingRepo(db).get_current(conv_id)
    row["revision_id"] = listing.current_revision_id if listing else None
    if action not in ("delist", "not_listed"):
        if conv.status == "sold":
            row["skip_reason"] = "This item has sold. It cannot be listed again."
            return row
        if not row["revision_id"]:
            row["skip_reason"] = "Save a listing for this item first."
            return row
    known = listings(db, conv_id)
    records = MarketplaceListingRepo(db).for_conversation(conv_id)
    for marketplace in dict.fromkeys(marketplaces):
        record = records.get(marketplace) or {}
        reason = _target_reason(action, marketplace, known.get(marketplace) or {}, record)
        if (not reason and action == "update" and record.get("state") == "published"
                and record.get("revision_id") and record.get("revision_id") == row["revision_id"]):
            reason = f"Already up to date on {_name(marketplace)}."
        if reason:
            row["skipped"].append({"marketplace": marketplace, "reason": reason})
        else:
            row["marketplaces"].append(marketplace)
    if not row["marketplaces"]:
        row["skip_reason"] = "Nothing to do on the chosen marketplaces."
        return row
    basis, _targets = approval_snapshot(db, conv_id, row["marketplaces"])
    row["review_token"] = _token(action, row["marketplaces"], basis)
    return row


def preview(db: Session, body: PreviewRequest) -> dict:
    """Review only. Nothing is sent to a marketplace."""
    marketplaces = list(dict.fromkeys(body.marketplaces))
    return {"action": body.action, "marketplaces": marketplaces,
            "rows": [review_row(db, conv_id, body.action, marketplaces) for conv_id in dict.fromkeys(body.conversation_ids)]}


async def perform(db: Session, body: PerformRequest) -> dict:
    """One reviewed item. Every marketplace named is tried once; nothing retries."""
    global _running
    from vendoo_studio.services.automations import AutomationBusy

    if body.confirm is not True:
        raise HTTPException(400, "Review and confirm this marketplace action first.")
    if _running:
        raise HTTPException(409, "Another marketplace action is running. Wait for it to finish.")
    _running = True
    try:
        with start_gate():
            db.expire_all()
            if JobRepo(db).get_active():
                raise HTTPException(409, "A marketplace job is running. Wait for it to finish.")
            current = review_row(db, body.conversation_id, body.action, list(body.marketplaces))
            if current["skip_reason"] or current["skipped"]:
                reasons = [current["skip_reason"]] if current["skip_reason"] else [entry["reason"] for entry in current["skipped"]]
                raise HTTPException(409, " ".join(reasons) + " Review again.")
            if (current["marketplaces"] != list(body.marketplaces) or current["revision_id"] != body.revision_id
                    or current["review_token"] != body.review_token):
                raise HTTPException(409, "The item, its photos or marketplace links changed since the review. Review again.")
        try:
            result = await _dispatch(db, body)
        except AutomationBusy as exc:
            raise HTTPException(409, str(exc) or "A marketplace job is running. Wait for it to finish.") from exc
    finally:
        _running = False
    return {"conversation_id": body.conversation_id, "action": body.action, "marketplaces": list(body.marketplaces),
            "ok": bool(result.get("ok")), "state": result.get("state"), "error": result.get("error"),
            "errors": result.get("errors") or {}, "down": list(result.get("down") or [])}


async def _dispatch(db: Session, body: PerformRequest) -> dict:
    from vendoo_studio.services import automation_actions

    marketplaces = list(body.marketplaces)
    if body.action == "list":
        return await automation_actions.publish_revision(db, body.conversation_id, marketplaces, body.revision_id)
    if body.action == "update":
        return await automation_actions.update_revision(db, body.conversation_id, marketplaces, body.revision_id)
    if body.action == "not_listed":
        return _mark_not_listed(db, body.conversation_id, marketplaces)
    item = {"conversation_id": body.conversation_id}
    return await automation_actions.perform(db, item, {"type": body.action}, marketplaces, body.revision_id, confirm=True)


def _mark_not_listed(db: Session, conv_id: str, marketplaces: list[str]) -> dict:
    """Record each named marketplace as not listed. Nothing is sent to the marketplace."""
    from vendoo_studio.services.marketplace_delist import DelistError, mark

    errors: dict[str, str] = {}
    for name in marketplaces:
        try:
            mark(db, conv_id, name, "not_listed")
        except DelistError as exc:
            db.rollback()
            errors[name] = str(exc)
    if errors:
        return {"ok": False, "error": "; ".join(f"{_name(name)}: {error}" for name, error in errors.items()), "errors": errors}
    return {"ok": True, "state": "not_listed"}
