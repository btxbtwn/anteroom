"""Where photo files live, addressed by key.

A photo's key is its ``stored_filename``: a bare file name such as
``3f2a….jpg``, which is what the database already records. Everything that
reads, writes or deletes a photo goes through the store, so the hosted API can
later keep photos in Supabase Storage (``docs/cloud-architecture.md``, step 1)
without touching the code that uses them.

``LocalPhotoStore`` keeps them in one folder, the Mac app's
``<data>/photos``. Code that needs a real file on disk — Pillow, the vision
encoders, marketplace uploads, ``FileResponse`` — asks for ``local_path``. The
local store hands back the stored file itself; a remote store would download it
to a local cache first. Treat that path as read-only: changes go through
``put``, so they reach every copy.

Derived files, such as thumbnails, are not photos and never leave the machine
that made them. They go in ``cache_dir``.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from vendoo_studio.config import PHOTOS_DIR


def _check_key(key: str) -> str:
    if not key or key in {".", ".."} or "/" in key or "\\" in key or "\x00" in key:
        raise ValueError(f"Not a photo key: {key!r}")
    return key


class LocalPhotoStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _file(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put(self, key: str, data: bytes) -> None:
        """Save ``data`` under ``key``. Readers never see a half-written file."""
        target = self._file(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
        partial.write_bytes(data)
        os.replace(partial, target)

    def read(self, key: str) -> bytes:
        return self._file(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._file(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        """Remove ``key``; a photo that is already gone is not an error."""
        self._file(key).unlink(missing_ok=True)

    def copy(self, source: str, target: str) -> None:
        shutil.copyfile(self._file(source), self._file(target))

    def local_path(self, key: str) -> Path:
        """A file on this machine holding the photo; raises FileNotFoundError when there is none."""
        path = self._file(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def local_paths(self, keys: list[str]) -> list[Path]:
        """Files for the photos that exist, in order, skipping missing ones."""
        paths: list[Path] = []
        for key in keys:
            try:
                paths.append(self.local_path(key))
            except (FileNotFoundError, ValueError):
                continue
        return paths

    def cache_dir(self, name: str) -> Path:
        """A folder on this machine for files derived from photos, such as thumbnails."""
        path = self.root / _check_key(name)
        path.mkdir(parents=True, exist_ok=True)
        return path


_store = LocalPhotoStore(PHOTOS_DIR)


def photo_store() -> LocalPhotoStore:
    return _store
