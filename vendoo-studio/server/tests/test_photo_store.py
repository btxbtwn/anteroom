from __future__ import annotations

from pathlib import Path

import pytest

from vendoo_studio.services.photo_store import LocalPhotoStore, photo_store


def test_put_read_copy_delete(tmp_path):
    store = LocalPhotoStore(tmp_path / "photos")
    store.put("a.jpg", b"one")
    assert store.read("a.jpg") == b"one"
    assert store.exists("a.jpg")
    store.copy("a.jpg", "b.jpg")
    assert store.local_path("b.jpg").read_bytes() == b"one"
    store.delete("a.jpg")
    store.delete("a.jpg")  # already gone is fine
    assert not store.exists("a.jpg")
    assert sorted(path.name for path in (tmp_path / "photos").iterdir()) == ["b.jpg"]


def test_put_replaces_without_leaving_partial_files(tmp_path):
    store = LocalPhotoStore(tmp_path)
    store.put("a.jpg", b"first")
    store.put("a.jpg", b"second")
    assert store.read("a.jpg") == b"second"
    assert [path.name for path in tmp_path.iterdir()] == ["a.jpg"]


def test_missing_photos(tmp_path):
    store = LocalPhotoStore(tmp_path)
    store.put("here.jpg", b"x")
    with pytest.raises(FileNotFoundError):
        store.local_path("gone.jpg")
    assert store.local_paths(["gone.jpg", "here.jpg", "../escape.jpg"]) == [tmp_path / "here.jpg"]


@pytest.mark.parametrize("key", ["", ".", "..", "../x.jpg", "a/b.jpg", "a\\b.jpg", "x\x00.jpg"])
def test_keys_cannot_leave_the_store(tmp_path, key):
    store = LocalPhotoStore(tmp_path)
    assert not store.exists(key)
    with pytest.raises(ValueError):
        store.put(key, b"x")


def test_cache_dir_is_inside_the_store(tmp_path):
    store = LocalPhotoStore(tmp_path)
    thumbs = store.cache_dir("thumbs")
    assert thumbs == tmp_path / "thumbs" and thumbs.is_dir()


def test_only_the_store_knows_where_photos_live():
    root = Path(__file__).resolve().parents[1] / "vendoo_studio"
    allowed = {root / "config.py", root / "services" / "photo_store.py"}
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path not in allowed and "migrations" not in path.parts and "PHOTOS_DIR" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"use photo_store() instead of PHOTOS_DIR in: {offenders}"


def test_default_store_is_the_data_folder():
    from vendoo_studio.config import PHOTOS_DIR

    assert photo_store().root == Path(PHOTOS_DIR)
