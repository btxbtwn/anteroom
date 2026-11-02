"""Reading measurements from yardstick photos in the bulk upload."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.services.photo_store import LocalPhotoStore
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.routes import measurements as route


def _ruler_photo() -> bytes:
    """A dark garment on a grey backdrop with two crossed wooden yardsticks."""
    image = Image.new("RGB", (1200, 1200), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 150, 1000, 1100), fill=(40, 40, 45))
    for box in ((60, 420, 1140, 480), (580, 60, 640, 1140)):
        draw.rectangle(box, fill=(232, 200, 150))
    for x in range(60, 1140, 12):
        draw.line((x, 420, x, 432), fill=(60, 70, 110), width=2)
    for y in range(60, 1140, 12):
        draw.line((580, y, 592, y), fill=(60, 70, 110), width=2)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=92)
    return out.getvalue()


def _plain_photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 800), (40, 40, 45)).save(out, "JPEG")
    return out.getvalue()


class FakeProvider:
    """Answers the locate step, then the read step, with the values it is given."""

    def __init__(self, reads: list[dict]):
        self.reads = list(reads)
        self.calls: list[list[dict]] = []

    async def vision_chat(self, messages):
        self.calls.append(messages)
        if "Decide which photo" in messages[0]["content"]:
            yield json.dumps({
                "pit_to_pit": {"photo": 1, "stick": "A", "tiles": [2]},
                "length": {"photo": 1, "stick": "B", "tiles": [3]},
                "sleeve": "cannot read",
            })
        else:
            yield "```json\n" + json.dumps(self.reads.pop(0)) + "\n```"


class ReadMeasurementsRouteTest(unittest.TestCase):
    def post(self, provider, files, garment="top"):
        with patch.object(route, "get_photo_provider", return_value=provider):
            return TestClient(app).post(
                "/api/measurements/read",
                data={"garment": garment},
                files=[("files", (f"{i}.jpg", body, "image/jpeg")) for i, body in enumerate(files)],
            )

    def test_accepts_reads_that_agree_and_flags_the_rest(self):
        provider = FakeProvider([
            {"pit_to_pit": 20.5, "length": 27.0},
            {"pit_to_pit": 20.625, "length": 28.0},
        ])
        response = self.post(provider, [_plain_photo(), _ruler_photo()])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["photos_read"], 1)
        self.assertEqual(body["measurements"]["pitToPit"]["status"], "accepted")
        self.assertEqual(body["measurements"]["pitToPit"]["value"], 20.5)
        self.assertEqual(body["measurements"]["length"]["status"], "disagree")
        self.assertEqual(body["measurements"]["sleeve"]["status"], "cannot_read")
        # Two independent reads, each a locate call and a read call.
        self.assertEqual(len(provider.calls), 4)

    def test_a_third_read_settles_a_split(self):
        provider = FakeProvider([
            {"pit_to_pit": 20.5, "length": 27.0},
            {"pit_to_pit": "cannot read", "length": 27.125},
            {"pit_to_pit": 20.625},
        ])
        body = self.post(provider, [_ruler_photo()]).json()
        self.assertEqual(body["measurements"]["pitToPit"]["status"], "accepted")
        self.assertEqual(body["measurements"]["pitToPit"]["reads"], [20.5, 20.625])
        self.assertEqual(body["measurements"]["length"]["value"], 27.0)
        self.assertEqual(len(provider.calls), 6)

    def test_accepted_values_round_to_the_nearest_half_inch(self):
        provider = FakeProvider([
            {"pit_to_pit": 20.25, "length": 27.75, "sleeve": 8.125},
            {"pit_to_pit": 20.25, "length": 27.75, "sleeve": 8.375},
        ])
        body = self.post(provider, [_ruler_photo()]).json()
        self.assertEqual(body["measurements"]["pitToPit"]["value"], 20.5)
        self.assertEqual(body["measurements"]["length"]["value"], 28.0)
        self.assertEqual(body["measurements"]["sleeve"]["value"], 8.5)

    def test_no_yardstick_photo_reads_nothing(self):
        provider = FakeProvider([])
        body = self.post(provider, [_plain_photo()]).json()
        self.assertEqual(body, {"photos_read": 0, "measurements": {}})
        self.assertEqual(provider.calls, [])

    def test_needs_a_connected_model(self):
        response = self.post(None, [_ruler_photo()])
        self.assertEqual(response.status_code, 503)

    def test_rejects_an_unknown_garment(self):
        self.assertEqual(self.post(FakeProvider([]), [_ruler_photo()], garment="hat").status_code, 400)


class BundledImportsTest(unittest.TestCase):
    """The Mac app loads the skill script from disk, so PyInstaller only packs what the spec lists."""

    def test_the_app_bundle_carries_every_module_the_script_imports(self):
        import ast
        import sys as _sys

        root = Path(__file__).resolve().parents[3]
        script = root / "skills" / "measure-this" / "scripts" / "ruler.py"
        spec = (root / "vendoo-studio" / "desktop" / "Anteroom.spec").read_text()
        needed = set()
        for node in ast.walk(ast.parse(script.read_text())):
            if isinstance(node, ast.Import):
                needed |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module == "PIL":
                    needed |= {f"PIL.{alias.name}" for alias in node.names}
                else:
                    needed.add(node.module)
        third_party = {name for name in needed if name.split(".")[0] not in _sys.stdlib_module_names}
        missing = sorted(name for name in third_party if f'"{name}"' not in spec)
        self.assertEqual(missing, [], "add these to hiddenimports in desktop/Anteroom.spec")


class ReadSavedMeasurementsRouteTest(unittest.TestCase):
    """Regenerate's review window reads a saved listing's photos the same way."""

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        photos = tempfile.TemporaryDirectory()
        self.addCleanup(photos.cleanup)
        folder = Path(photos.name)
        directory = patch("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(folder)))
        directory.start()
        self.addCleanup(directory.stop)
        repo = ConversationRepo(self.db)
        self.conv = repo.create(title="Graphic tee")
        for name, body in (("tag.jpg", _plain_photo()), ("ruler.jpg", _ruler_photo())):
            (folder / name).write_bytes(body)
            listing_persistence.add_photo(repo.db, self.conv.id, name, name, "image/jpeg", len(body), checksum=name * 8, width=1, height=1)

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(app.dependency_overrides.clear)

    def post(self, provider, conv_id, garment="top"):
        with patch.object(route, "get_photo_provider", return_value=provider):
            return TestClient(app).post(f"/api/conversations/{conv_id}/measurements/read", json={"garment": garment})

    def test_reads_the_saved_photos(self):
        provider = FakeProvider([{"pit_to_pit": 19.5, "length": 26}, {"pit_to_pit": 19.5, "length": 26.125}])
        body = self.post(provider, self.conv.id).json()
        self.assertEqual(body["photos_read"], 1)
        self.assertEqual(body["measurements"]["pitToPit"]["value"], 19.5)
        self.assertEqual(body["measurements"]["length"]["value"], 26.0)

    def test_unknown_listing(self):
        self.assertEqual(self.post(FakeProvider([]), "missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
