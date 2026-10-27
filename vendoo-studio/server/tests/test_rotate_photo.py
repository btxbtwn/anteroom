from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo


def _jpeg_with_red_left_half(width: int = 400, height: int = 200) -> bytes:
    img = Image.new("RGB", (width, height), "blue")
    img.paste("red", (0, 0, width // 2, height))
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


class RotatePhotoTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.photos_tmp = tempfile.TemporaryDirectory()
        self.photos_dir = Path(self.photos_tmp.name)
        self.patches = [
            patch("vendoo_studio.services.photos.PHOTOS_DIR", str(self.photos_dir)),
            patch("vendoo_studio.routes.photos.PHOTOS_DIR", str(self.photos_dir)),
        ]
        for p in self.patches:
            p.start()

        repo = ConversationRepo(self.db)
        self.conv = repo.create(title="Nike tee")
        contents = _jpeg_with_red_left_half()
        (self.photos_dir / "front.jpg").write_bytes(contents)
        self.photo = repo.add_photo(
            self.conv.id, "front.jpg", "front.jpg", "image/jpeg", len(contents),
            checksum="a" * 64, width=400, height=200,
        )

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.db.close()
        self.photos_tmp.cleanup()

    def _rotate(self, degrees: int, photo_id: str | None = None):
        return self.client.post(
            f"/api/conversations/{self.conv.id}/photos/{photo_id or self.photo.id}/rotate",
            json={"degrees": degrees},
        )

    def test_rotating_clockwise_rewrites_the_pixels(self):
        self.client.get(f"/api/photos/{self.photo.id}/thumb?size=96")
        self.assertTrue(list((self.photos_dir / "thumbs").glob("*.jpg")))

        response = self._rotate(90)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["width"], body["height"]), (200, 400))

        self.db.refresh(self.photo)
        self.assertNotEqual(self.photo.stored_filename, "front.jpg")
        # The upload is kept so Reset can restore it.
        self.assertEqual(self.photo.source_filename, "front.jpg")
        self.assertTrue((self.photos_dir / "front.jpg").exists())
        self.assertEqual(body["edits"]["rotate"], 90)
        # Thumbnails are per file, so the edit gets its own.
        self.assertEqual(list((self.photos_dir / "thumbs").glob(f"{Path(self.photo.stored_filename).stem}_*")), [])
        self.assertIn(f"?v={self.photo.checksum[:12]}", body["url"])

        with Image.open(self.photos_dir / self.photo.stored_filename) as img:
            self.assertEqual(img.size, (200, 400))
            # The red left half ends up on top after a clockwise turn.
            top = img.getpixel((100, 50))
            bottom = img.getpixel((100, 350))
        self.assertGreater(top[0], 200)
        self.assertGreater(bottom[2], 200)

    def test_four_quarter_turns_restore_the_original(self):
        for _ in range(4):
            self.assertEqual(self._rotate(90).status_code, 200)
        self.db.refresh(self.photo)
        self.assertEqual(self.photo.stored_filename, "front.jpg")
        self.assertIsNone(self.photo.source_filename)
        self.assertEqual([p.name for p in self.photos_dir.glob("*.jpg")], ["front.jpg"])

    def test_rotating_left_puts_the_left_half_at_the_bottom(self):
        response = self._rotate(-90)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(self.photo)
        with Image.open(self.photos_dir / self.photo.stored_filename) as img:
            self.assertGreater(img.getpixel((100, 350))[0], 200)

    def test_cover_url_changes_after_rotation(self):
        before = self.client.get(f"/api/conversations/{self.conv.id}").json()["cover_photo_url"]
        self._rotate(180)
        after = self.client.get(f"/api/conversations/{self.conv.id}").json()["cover_photo_url"]
        self.assertNotEqual(before, after)
        self.assertTrue(after.startswith(f"/api/photos/{self.photo.id}/thumb?size=96&v="))

    def test_rejects_other_angles(self):
        self.assertEqual(self._rotate(45).status_code, 400)
        self.assertTrue((self.photos_dir / "front.jpg").exists())

    def test_unknown_photo_is_404(self):
        self.assertEqual(self._rotate(90, photo_id="missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
