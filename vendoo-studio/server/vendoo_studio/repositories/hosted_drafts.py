"""The first cloud increment: draft shells, without files or history pointers."""
from datetime import UTC, datetime

from sqlalchemy import select, update

from vendoo_studio.models.conversation import Conversation


def recent(db):
    return db.scalars(select(Conversation).where(Conversation.status == 'draft')
                      .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(100)).all()


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


def save(db, draft_id, title, notes, expected_updated_at):
    if expected_updated_at.tzinfo is not None:
        expected_updated_at = expected_updated_at.astimezone(UTC).replace(tzinfo=None)
    result = db.execute(update(Conversation).where(
        Conversation.id == draft_id, Conversation.status == 'draft',
        Conversation.updated_at == expected_updated_at,
    ).values(title=title, notes=notes, updated_at=datetime.now(UTC).replace(tzinfo=None)))
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return get(db, draft_id)
