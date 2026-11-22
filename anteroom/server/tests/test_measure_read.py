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

from anteroom.services import listing_persistence
from anteroom.services.photo_store import LocalPhotoStore
from anteroom.database import Base
from anteroom.main import app
from anteroom.repositories.queries import ConversationRepo
from anteroom.routes import measurements as route


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

    def test_the_model_is_logged_not_shown_to_the_seller(self):
        """A model name in the seller's notes was copied into listings; diagnostics keep it."""
        provider = FakeProvider([{"pit_to_pit": 20.5}, {"pit_to_pit": 20.5}])
        provider.name, provider.vision_model = "openrouter", "google/gemini-flash-latest"
        with self.assertLogs("anteroom.services.measure_read", "INFO") as logs:
            response = self.post(provider, [_ruler_photo()])
        self.assertNotIn("reader", response.json())
        self.assertNotIn("gemini", response.text.casefold())
        self.assertNotIn("openrouter", response.text.casefold())
        self.assertIn("OpenRouter (google/gemini-flash-latest)", "\n".join(logs.output))

    def test_needs_a_connected_model(self):
        response = self.post(None, [_ruler_photo()])
        self.assertEqual(response.status_code, 503)

    def test_rejects_an_unknown_garment(self):
        self.assertEqual(self.post(FakeProvider([]), [_ruler_photo()], garment="hat").status_code, 400)


class PhotoSearchMemoryTest(unittest.TestCase):
    """A bulk read sends every item at once; the full-size photo work must not all run at once."""

    def test_at_most_two_items_search_their_photos_at_a_time(self):
        import asyncio
        import threading
        import time

        from anteroom.services import measure_read

        running = 0
        most = 0
        lock = threading.Lock()

        def search(paths, out):
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
            time.sleep(0.05)
            with lock:
                running -= 1
            return []

        async def read_many():
            await asyncio.gather(*(
                measure_read.read_measurements(FakeProvider([]), [Path("x.jpg")], "top") for _ in range(6)
            ))

        with patch.object(measure_read, "find_ruler_photos", search):
            asyncio.run(read_many())
        self.assertEqual(most, measure_read.PHOTO_SEARCHES_AT_ONCE)
        self.assertLessEqual(most, 2)


class BundledImportsTest(unittest.TestCase):
    """The Mac app loads the skill script from disk, so PyInstaller only packs what the spec lists."""

    def test_the_app_bundle_carries_every_module_the_script_imports(self):
        import ast
        import sys as _sys

        root = Path(__file__).resolve().parents[3]
        script = root / "skills" / "measure-this" / "scripts" / "ruler.py"
        spec = (root / "anteroom" / "desktop" / "Anteroom.spec").read_text()
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
        Session = sessionmaker(bind=engine)
        self.db = Session()
        sessions = patch("anteroom.database.SessionLocal", Session)
        sessions.start()
        self.addCleanup(sessions.stop)
        photos = tempfile.TemporaryDirectory()
        self.addCleanup(photos.cleanup)
        folder = Path(photos.name)
        directory = patch("anteroom.services.photo_store._store", LocalPhotoStore(str(folder)))
        directory.start()
        self.addCleanup(directory.stop)
        repo = ConversationRepo(self.db)
        self.conv = repo.create(title="Graphic tee")
        for name, body in (("tag.jpg", _plain_photo()), ("ruler.jpg", _ruler_photo())):
            (folder / name).write_bytes(body)
            listing_persistence.add_photo(repo.db, self.conv.id, name, name, "image/jpeg", len(body), checksum=name * 8, width=1, height=1)

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


class LocateParsingTest(unittest.TestCase):
    """Models occasionally quote numbers, prefix stick names, or return a single tile."""

    def test_tolerates_quoted_numbers_prefixed_sticks_and_single_tiles(self):
        import asyncio

        from anteroom.services import measure_read

        class VariedLocateProvider:
            def __init__(self):
                self.read_prompts: list[str] = []

            async def vision_chat(self, messages):
                if "Decide which photo" in messages[0]["content"]:
                    yield json.dumps({
                        "pit_to_pit": {"photo": "01", "stick": "Stick a", "tiles": ["02", 2, "3", 4]},
                        "length": {"photo": " 1 ", "stick": " b ", "tiles": 3},
                        "sleeve": {"photo": True, "stick": "A", "tiles": ["²", "bad", True, 0, 99]},
                    })
                else:
                    texts = [part["text"] for part in messages[1]["content"] if part.get("type") == "text"]
                    self.read_prompts.extend(texts)
                    yield json.dumps({"pit_to_pit": 20.5, "length": 27.0})

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "tile.jpg"
            img.write_bytes(_plain_photo())
            img_path = str(img)
            photos = [{
                "number": 1,
                "overview": img_path,
                "sticks": [
                    {"stick": "A", "strip": img_path, "tiles": [img_path, img_path, img_path, img_path]},
                    {"stick": "B", "strip": img_path, "tiles": [img_path, img_path, img_path, img_path]},
                ],
            }]
            provider = VariedLocateProvider()
            result = asyncio.run(measure_read._read_once(provider, photos, "top", ["pit_to_pit", "length", "sleeve"]))

        self.assertEqual(result, {"pit_to_pit": 20.5, "length": 27.0})
        joined = "\n".join(provider.read_prompts)
        # pit_to_pit picked [2, 3] (deduped and capped at 2) -> tiles with neighbours [1, 2, 3, 4]
        self.assertIn("pit_to_pit: photo 1, stick A (whole stick, then tiles [1, 2, 3, 4]).", joined)
        # length picked [3] from single int -> tiles with neighbours [2, 3, 4]
        self.assertIn("length: photo 1, stick B (whole stick, then tiles [2, 3, 4]).", joined)
        # sleeve had invalid/bool photo and tiles -> skipped cleanly
        self.assertNotIn("sleeve:", joined)

    def test_ignores_all_invalid_locate_entries_without_calling_read_step(self):
        import asyncio

        from anteroom.services import measure_read

        class BadLocateProvider:
            def __init__(self):
                self.calls = 0

            async def vision_chat(self, messages):
                self.calls += 1
                yield json.dumps({
                    "pit_to_pit": {"photo": False, "stick": "A", "tiles": [1]},
                    "length": {"photo": "1", "stick": 2, "tiles": ["1"]},
                    "waist": {"photo": "9" * 5000, "stick": "A", "tiles": ["1"]},
                    "sleeve": {"photo": "1", "stick": "A", "tiles": [True, "²", "abc", 0, "9" * 5000]},
                })

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "tile.jpg"
            img.write_bytes(_plain_photo())
            img_path = str(img)
            photos = [{
                "number": 1,
                "overview": img_path,
                "sticks": [{"stick": "A", "strip": img_path, "tiles": [img_path, img_path]}],
            }]
            provider = BadLocateProvider()
            result = asyncio.run(measure_read._read_once(provider, photos, "top", ["pit_to_pit", "length", "waist", "sleeve"]))

        self.assertEqual(result, {})
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
