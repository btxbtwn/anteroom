from hashlib import sha256
from types import SimpleNamespace

import pytest

from vendoo_studio.services import approved_photos
from vendoo_studio.services.photo_store import LocalPhotoStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = LocalPhotoStore(tmp_path)
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", store)
    return store


def photo(name, contents):
    return SimpleNamespace(id=name, stored_filename=name, mime_type="image/jpeg", checksum=sha256(contents).hexdigest())


def test_copies_keep_their_order_and_bytes_after_the_sources_change(store):
    store.put("front.jpg", b"Front")
    store.put("back.jpg", b"Back")
    rows = approved_photos.capture([photo("back.jpg", b"Back"), photo("front.jpg", b"Front")])
    store.put("front.jpg", b"Edited")
    store.delete("back.jpg")
    snapshot = {approved_photos.KEY: rows}
    loaded = approved_photos.load(snapshot)
    assert [item.id for item in loaded] == ["back.jpg", "front.jpg"]
    assert [store.read(item.stored_filename) for item in loaded] == [b"Back", b"Front"]
    approved_photos.release(snapshot)
    assert all(not store.exists(item.stored_filename) for item in loaded)
    assert store.read("front.jpg") == b"Edited"


def test_a_partial_copy_failure_cleans_up_without_dispatchable_media(store):
    store.put("front.jpg", b"Front")
    with pytest.raises(FileNotFoundError):
        approved_photos.capture([photo("front.jpg", b"Front"), photo("missing.jpg", b"Missing")])
    assert [path.name for path in store.root.iterdir()] == ["front.jpg"]


def test_a_source_that_does_not_match_its_approved_checksum_is_refused(store):
    store.put("front.jpg", b"Changed")
    with pytest.raises(ValueError, match="changed"):
        approved_photos.capture([photo("front.jpg", b"Front")])
    assert [path.name for path in store.root.iterdir()] == ["front.jpg"]
