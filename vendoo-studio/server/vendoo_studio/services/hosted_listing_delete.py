"""Removing or clearing an unsent draft in a seller's account. Never contacts a marketplace.

Only an unsent draft can be removed or cleared here: an item with a Send or a
marketplace record keeps its identities and history until the web app can act
on those marketplaces.
"""
from sqlalchemy import delete, select

from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.services.background_tasks import ACTIVE

NOT_REMOVABLE = 'Only an unsent draft can be removed from this workspace'


def blocker(db, identity) -> str | None:
    """Why this owned item cannot be removed right now, or None."""
    row = db.get(Conversation, identity)
    if row is None:
        return 'This item is no longer in Anteroom.'
    if db.scalar(select(BackgroundTask.id).where(
            BackgroundTask.conversation_id == identity, BackgroundTask.status.in_(ACTIVE)).limit(1)):
        return 'Wait for the draft task to finish, or stop it first.'
    if row.status != 'draft' or db.scalar(select(Job.id).where(Job.conversation_id == identity).limit(1)) or db.scalar(
            select(MarketplaceListing.id).where(MarketplaceListing.conversation_id == identity).limit(1)):
        return NOT_REMOVABLE
    return None


def _photo_keys(db, identity) -> set[str]:
    from vendoo_studio.services.hosted_photos import check_key

    keys = set()
    for photo in db.scalars(select(Photo).where(Photo.conversation_id == identity)):
        for key in (photo.stored_filename, photo.source_filename):
            if key:
                check_key(key, db.user_id, identity)
                keys.add(key)
    return keys


def _attachment_keys(db, identity) -> set[str]:
    from vendoo_studio.services.hosted_photos import check_key

    keys = set()
    attachments = [item for batch in db.scalars(select(Message.attachments).where(Message.conversation_id == identity))
                   for item in (batch or [])]
    for payload in db.scalars(select(BackgroundTask.payload).where(BackgroundTask.conversation_id == identity)):
        attachments.extend(payload.get('attachments', []))
    for item in attachments:
        check_key(item['id'], db.user_id, identity)
        keys.add(item['id'])
    return keys


def remove_unsent_draft(db, identity) -> set[str]:
    """Delete the locked, idle draft's rows and return its Storage keys. The caller commits."""
    from vendoo_studio.services.background_tasks import delete_history

    reason = blocker(db, identity)
    if reason:
        raise ValueError(reason)
    keys = _photo_keys(db, identity) | _attachment_keys(db, identity)
    delete_history(db, identity)
    for model in (Listing, ListingRevision, Photo, Message):
        db.execute(delete(model).where(model.conversation_id == identity))
    db.execute(delete(Conversation).where(Conversation.id == identity))
    return keys


def reset_unsent_draft(db, identity, *, keep_inputs: bool) -> set[str]:
    """Clear the locked, idle draft's generated listing and chat, as desktop Clear does.

    ``keep_inputs`` is Regenerate: photos stay, and the facts that only lived on
    the listing move into Item Details first. Returns Storage keys to release
    after the caller commits; the caller then clears the listing's hidden fields.
    """
    from vendoo_studio.models.conversation import utcnow
    from vendoo_studio.repositories.queries import ListingRepo
    from vendoo_studio.models.listing_evidence import ListingCorrection
    from vendoo_studio.services.background_tasks import delete_history
    from vendoo_studio.services.listing_carryover import carryover_updates
    from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

    reason = blocker(db, identity)
    if reason:
        raise ValueError(reason)
    row = db.get(Conversation, identity)
    notes = row.notes
    if keep_inputs:
        latest = ListingRepo(db).latest_revision(identity)
        carried = carryover_updates(dict(latest.listing_json) if latest else None, parse_notes(notes))
        if carried:
            notes = merge_notes(notes, carried)
    keys = _attachment_keys(db, identity) | (set() if keep_inputs else _photo_keys(db, identity))
    delete_history(db, identity)
    for model in (Listing, ListingRevision, Message) + (() if keep_inputs else (Photo, ListingCorrection)):
        db.execute(delete(model).where(model.conversation_id == identity))
    # A name the seller gave the listing outlives Regenerate; Clear drops it.
    if not (keep_inputs and parse_notes(notes).get('titlePinned')):
        row.title = 'New Listing'
    row.notes = notes if keep_inputs else None
    row.status, row.settled_at, row.unsettled_at, row.updated_at = 'draft', None, None, utcnow()
    return keys


def release(keys, token) -> None:
    """Delete committed removals from Storage.

    The durable deletion wins. A temporary Storage outage may leave a private
    orphan for cleanup; it cannot resurrect the item or delete another owner.
    """
    from fastapi import HTTPException

    from vendoo_studio.services.hosted_photos import storage_call

    for key in keys:
        try:
            storage_call('DELETE', key, token)
        except HTTPException:
            continue
