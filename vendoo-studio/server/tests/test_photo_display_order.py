"""A new photo goes after every photo the item already has, even after deletes."""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.routes import hosted_photos
from vendoo_studio.services import similar_items


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    conv = Conversation(title="Tee", status="draft")
    db.add(conv)
    db.commit()
    return db, conv.id


def _names(db, conv_id):
    rows = db.scalars(select(Photo).where(Photo.conversation_id == conv_id, Photo.marketplace.is_(None))
                      .order_by(Photo.display_order, Photo.id)).all()
    return [row.stored_filename for row in rows]


def _delete(db, conv_id, names):
    for row in db.scalars(select(Photo).where(Photo.conversation_id == conv_id,
                                              Photo.stored_filename.in_(names))).all():
        db.delete(row)
    db.commit()


def test_local_photo_added_after_deletes_goes_last():
    db, conv_id = _session()
    repo = ConversationRepo(db)
    for name in ("A.jpg", "B.jpg", "C.jpg"):
        repo.insert_photo(conv_id, name, name, "image/jpeg", 10)
        db.commit()
    _delete(db, conv_id, ["A.jpg", "B.jpg"])
    repo.insert_photo(conv_id, "D.jpg", "D.jpg", "image/jpeg", 10)
    db.commit()
    assert _names(db, conv_id) == ["C.jpg", "D.jpg"]


def test_hosted_photo_added_after_deletes_goes_last():
    db, conv_id = _session()
    for name in ("A.jpg", "B.jpg", "C.jpg"):
        hosted_photos.save_photo(db, conv_id, name, b"x", 10, 10)
    _delete(db, conv_id, ["A.jpg", "B.jpg"])
    hosted_photos.save_photo(db, conv_id, "D.jpg", b"x", 10, 10)
    assert _names(db, conv_id) == ["C.jpg", "D.jpg"]


def test_hosted_photo_added_after_one_delete_does_not_tie():
    db, conv_id = _session()
    for name in ("A.jpg", "B.jpg", "C.jpg"):
        hosted_photos.save_photo(db, conv_id, name, b"x", 10, 10)
    _delete(db, conv_id, ["A.jpg"])
    hosted_photos.save_photo(db, conv_id, "D.jpg", b"x", 10, 10)
    assert _names(db, conv_id) == ["B.jpg", "C.jpg", "D.jpg"]


def test_cover_stays_an_item_photo_when_a_marketplace_photo_is_added():
    # A marketplace's first photo is numbered 0 within its own group, so it
    # sorted ahead of an item photo left at 1 after the cover was deleted.
    db, conv_id = _session()
    repo = ConversationRepo(db)
    for name in ("A.jpg", "B.jpg"):
        repo.insert_photo(conv_id, name, name, "image/jpeg", 10, checksum=name)
        db.commit()
    _delete(db, conv_id, ["A.jpg"])
    repo.insert_photo(conv_id, "M.jpg", "M.jpg", "image/jpeg", 10, checksum="M.jpg", marketplace="ebay")
    db.commit()
    cover = db.scalar(select(Photo.id).where(Photo.stored_filename == "B.jpg"))
    assert repo.cover_photo(conv_id) == (cover, "B.jpg")
    assert repo.cover_photos([conv_id])[conv_id] == (cover, "B.jpg")
    assert similar_items._load(db)[conv_id].cover_id == cover
