"""Private draft inventory and General edits using the canonical revision history."""
from datetime import UTC, datetime

from sqlalchemy import select, update

from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services.hosted_fields import DraftFields

from vendoo_studio.models.conversation import Conversation


def recent(db, offset=0, search=""):
    statement = select(Conversation).where(Conversation.status == "draft")
    if search:
        escaped = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        statement = statement.where(Conversation.title.ilike(f"%{escaped}%", escape="!"))
    return db.scalars(statement.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).offset(offset).limit(100)).all()


def get(db, draft_id):
    return db.scalar(select(Conversation).where(Conversation.id == draft_id, Conversation.status == 'draft'))


def create(db, title, notes):
    # SellerDatabaseSession assigns the verified owner. No caller-selected ID,
    # owner, parent, status, file path or marketplace action is accepted.
    draft = Conversation(title=title, notes=notes, status='draft')
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def save(db, draft_id, title, notes, expected_updated_at, fields=None):
    if expected_updated_at.tzinfo is not None:
        expected_updated_at = expected_updated_at.astimezone(UTC).replace(tzinfo=None)
    result = db.execute(update(Conversation).where(
        Conversation.id == draft_id, Conversation.status == 'draft',
        Conversation.updated_at == expected_updated_at,
    ).values(title=title, notes=notes, updated_at=datetime.now(UTC).replace(tzinfo=None)))
    if result.rowcount != 1:
        db.rollback()
        return None
    if fields is not None:
        listing = db.get(Listing, draft_id)
        previous = current_fields_revision(db, draft_id)
        # Carry existing marketplace values forward internally. Never return
        # arbitrary JSON or accept a caller-selected revision/history pointer.
        payload = dict(previous.listing_json or {}) if previous else {}
        payload.update(fields.model_dump())
        payload['title'] = title
        revision = ListingRevision(conversation_id=draft_id, listing_json=payload, source='manual',
                                   parent_revision_id=previous.id if previous else None)
        db.add(revision)
        db.flush()
        if listing is None:
            listing = Listing(conversation_id=draft_id)
            db.add(listing)
        listing.current_revision_id = revision.id
        listing.validation_status = None
        listing.validation_errors = None
    db.commit()
    return get(db, draft_id)


def current_fields_revision(db, draft_id):
    # Both edges are checked; a legacy soft pointer must not expose another item.
    return db.scalar(select(ListingRevision).join(Listing,
        (Listing.current_revision_id == ListingRevision.id)
        & (Listing.conversation_id == ListingRevision.conversation_id)
        & (Listing.user_id == ListingRevision.user_id)).where(Listing.conversation_id == draft_id))


def editable_fields(db, draft_id):
    revision = current_fields_revision(db, draft_id)
    raw = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    # Existing imported values can have incompatible types. Expose only values
    # that satisfy the same contract accepted by a save, without losing source JSON.
    values = {}
    for name in DraftFields.model_fields:
        if name in raw:
            try:
                values[name] = getattr(DraftFields(**{name: raw[name]}), name)
            except ValueError:
                continue
    return DraftFields(**values)
