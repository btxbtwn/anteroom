"""Removing an unsent draft from a seller's account. Never contacts a marketplace.

Only an unsent draft can be removed here: an item with a Send or a marketplace
record keeps its identities and history until the web app can delist it.
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


def remove_unsent_draft(db, identity) -> set[str]:
    """Delete the locked, idle draft's rows and return its Storage keys. The caller commits."""
    from vendoo_studio.services.background_tasks import delete_history
    from vendoo_studio.services.hosted_photos import check_key

    reason = blocker(db, identity)
    if reason:
        raise ValueError(reason)
    keys = set()
    for photo in db.scalars(select(Photo).where(Photo.conversation_id == identity)):
        for key in (photo.stored_filename, photo.source_filename):
            if key:
                check_key(key, db.user_id, identity)
                keys.add(key)
    attachments = [item for batch in db.scalars(select(Message.attachments).where(Message.conversation_id == identity))
                   for item in (batch or [])]
    for payload in db.scalars(select(BackgroundTask.payload).where(BackgroundTask.conversation_id == identity)):
        attachments.extend(payload.get('attachments', []))
    for item in attachments:
        check_key(item['id'], db.user_id, identity)
        keys.add(item['id'])
    delete_history(db, identity)
    for model in (Listing, ListingRevision, Photo, Message):
        db.execute(delete(model).where(model.conversation_id == identity))
    db.execute(delete(Conversation).where(Conversation.id == identity))
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
