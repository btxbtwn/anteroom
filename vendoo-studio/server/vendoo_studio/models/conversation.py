from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import JSON, Column, String, Text, DateTime, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship

from vendoo_studio.database import Base


def new_id():
    return uuid.uuid4().hex[:12]


def utcnow():
    return datetime.now(UTC)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=new_id)
    title = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String, default="draft")
    # The wholesale box this item came out of, when the seller said so.
    box_id = Column(String, ForeignKey("source_boxes.id", ondelete="SET NULL"), nullable=True, index=True)
    settled_at = Column(DateTime, nullable=True)
    unsettled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="conversation", cascade="all, delete-orphan")
    videos = relationship("ListingVideo", back_populates="conversation", cascade="all, delete-orphan")
    listing = relationship("Listing", back_populates="conversation", uselist=False, cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    # Chat history and the activity poll read one listing's messages by time.
    __table_args__ = (Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    # Images the seller attached: [{id, name, mime_type, size_bytes, width, height}];
    # the files live under the data folder, see services/chat_attachments.py.
    attachments = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    display_order = Column(Integer, nullable=False, default=0)
    checksum = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    # An edited photo keeps its upload here and serves the rendered edit from
    # stored_filename, so every send uses the edit and Reset restores the upload.
    source_filename = Column(String, nullable=True)
    edits = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="photos")
