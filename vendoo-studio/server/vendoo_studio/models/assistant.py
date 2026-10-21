"""The business assistant's conversations, apart from any listing's chat."""

from sqlalchemy import JSON, Column, DateTime, String, Text

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow

NEW_THREAD_TITLE = "New thread"


class AssistantThread(Base):
    """One conversation with the business assistant.

    The seller can keep several of these. A listing's chat is never one of them.
    """

    __tablename__ = "assistant_threads"

    id = Column(String, primary_key=True, default=new_id)
    title = Column(String, nullable=False, default=NEW_THREAD_TITLE)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class AssistantMessage(Base):
    """One turn of a conversation about the business.

    Only the question and the answer are kept. The figures an answer was built
    from are read fresh from the database on every question, never stored here.
    An answer that proposes changes carries them, and whether the seller applied them."""

    __tablename__ = "assistant_messages"

    id = Column(String, primary_key=True, default=new_id)
    thread_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # user | assistant
    text = Column(Text, nullable=False)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    # {"status": proposed | applied | dismissed, "items": [...]}; see services/assistant_changes.py.
    changes = Column(JSON, nullable=True)
    # Images attached to a question; see services/chat_attachments.py.
    attachments = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
