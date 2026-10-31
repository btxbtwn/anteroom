from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vendoo_studio.models.listing import Listing
    from vendoo_studio.models.listing_video import ListingVideo

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


def new_id():
    return uuid.uuid4().hex[:12]


def utcnow():
    return datetime.now(UTC)


class Conversation(SellerOwned, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_conversations_seller_id"),
        ForeignKeyConstraint(["user_id", "box_id"], ["source_boxes.user_id", "source_boxes.id"],
            name="fk_conversations_seller_box_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String, default="draft", nullable=True)
    # The wholesale box this item came out of, when the seller said so.
    box_id: Mapped[str | None] = mapped_column(String, ForeignKey("source_boxes.id", ondelete="SET NULL"), nullable=True, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unsettled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    messages: Mapped[list[Message]] = relationship("Message", primaryjoin="and_(Conversation.id == foreign(Message.conversation_id), Conversation.user_id == Message.user_id)", back_populates="conversation", cascade="all, delete-orphan")
    photos: Mapped[list[Photo]] = relationship("Photo", primaryjoin="and_(Conversation.id == foreign(Photo.conversation_id), Conversation.user_id == Photo.user_id)", back_populates="conversation", cascade="all, delete-orphan")
    videos: Mapped[list[ListingVideo]] = relationship("ListingVideo", primaryjoin="and_(Conversation.id == foreign(ListingVideo.conversation_id), Conversation.user_id == ListingVideo.user_id)", back_populates="conversation", cascade="all, delete-orphan")
    listing: Mapped[Listing | None] = relationship("Listing", primaryjoin="and_(Conversation.id == foreign(Listing.conversation_id), Conversation.user_id == Listing.user_id)", back_populates="conversation", uselist=False, cascade="all, delete-orphan")


class Message(SellerOwned, Base):
    __tablename__ = "messages"
    # Chat history and the activity poll read one listing's messages by time.
    __table_args__ = (
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_messages_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    # Images the seller attached: [{id, name, mime_type, size_bytes, width, height}];
    # the files live under the data folder, see services/chat_attachments.py.
    attachments: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # Identifies the exact ordered photo files used for a vision reading.
    photo_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages", primaryjoin="and_(Conversation.id == foreign(Message.conversation_id), Conversation.user_id == Message.user_id)")


class Photo(SellerOwned, Base):
    __tablename__ = "photos"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_photos_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    stored_filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # An edited photo keeps its upload here and serves the rendered edit from
    # stored_filename, so every send uses the edit and Reset restores the upload.
    source_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    edits: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # Set for a photo added on one marketplace's form: only that marketplace can send it.
    marketplace: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="photos", primaryjoin="and_(Conversation.id == foreign(Photo.conversation_id), Conversation.user_id == Photo.user_id)")
