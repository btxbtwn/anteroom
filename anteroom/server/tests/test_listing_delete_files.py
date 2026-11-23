"""A listing's files go only once the database has let go of their rows."""
from anteroom.database import SessionLocal
from anteroom.models.conversation import Conversation, Photo, new_id
from anteroom.services.listing_delete import delete_listing
from anteroom.services.photo_store import photo_store


def _draft_with_photo(name: str) -> str:
    photo_store().put(name, b"photo bytes")
    with SessionLocal() as db:
        conv = Conversation(id=new_id(), title="Files draft", status="draft")
        db.add(conv)
        db.flush()
        db.add(Photo(id=new_id(), conversation_id=conv.id, original_filename=name, stored_filename=name,
                     mime_type="image/jpeg", size_bytes=11, display_order=0))
        db.commit()
        return conv.id


def test_a_refused_delete_keeps_the_photo_files():
    conv_id = _draft_with_photo("kept-after-refusal.jpg")
    with SessionLocal() as db:
        def refuse(_session):
            raise TimeoutError

        from sqlalchemy import event

        event.listen(db, "before_commit", refuse)
        try:
            delete_listing(db, conv_id)
        except TimeoutError:
            db.rollback()
        else:
            raise AssertionError("the commit should have been refused")
    assert photo_store().exists("kept-after-refusal.jpg")
    with SessionLocal() as db:
        assert db.get(Conversation, conv_id) is not None
        assert db.query(Photo).filter_by(conversation_id=conv_id).count() == 1


def test_a_committed_delete_removes_the_photo_files():
    conv_id = _draft_with_photo("gone-after-commit.jpg")
    with SessionLocal() as db:
        delete_listing(db, conv_id)
    assert not photo_store().exists("gone-after-commit.jpg")
    with SessionLocal() as db:
        assert db.get(Conversation, conv_id) is None


def test_a_later_commit_after_a_refused_delete_does_not_remove_the_files():
    conv_id = _draft_with_photo("kept-after-later-commit.jpg")
    with SessionLocal() as db:
        refused = {"once": True}

        def refuse_first(_session):
            if refused.pop("once", False):
                raise TimeoutError

        from sqlalchemy import event

        event.listen(db, "before_commit", refuse_first)
        try:
            delete_listing(db, conv_id)
        except TimeoutError:
            db.rollback()
        db.commit()  # the same session commits something else later
    assert photo_store().exists("kept-after-later-commit.jpg")
