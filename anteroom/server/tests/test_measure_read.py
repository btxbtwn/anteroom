"""Reading measurements from yardstick photos in the bulk upload."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from weakref import WeakValueDictionary

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


class RulerDetectorTest(unittest.TestCase):
    """CV ruler detection handles single dark rulers, portrait geometry, clipping, and striped fabric."""

    def test_to_edges_clips_near_corner_without_coordinate_explosion(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1000, 1333), (240, 240, 240))
        for raw in (
            m.Stick(20, 4, 980, 12, 40),
            m.Stick(-50, 200, 1050, 600, 40),
            m.Stick(500, -30, 510, 1360, 40),
        ):
            clipped = m.to_edges(image, raw)
            for x in (clipped.x1, clipped.x2):
                self.assertGreaterEqual(x, -1e-6)
                self.assertLessEqual(x, image.width - 1 + 1e-6)
            for y in (clipped.y1, clipped.y2):
                self.assertGreaterEqual(y, -1e-6)
                self.assertLessEqual(y, image.height - 1 + 1e-6)

    def test_detects_dark_folding_ruler_on_cream_sweater_and_accepts_single_stick(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1000, 1000), (235, 228, 215))
        draw = ImageDraw.Draw(image)
        # Single vertical darker wooden folding ruler on cream knit sweater.
        draw.rectangle((482, 60, 518, 940), fill=(148, 120, 78))
        for y in range(70, 930, 10):
            draw.line((482, y, 492, y), fill=(40, 32, 22), width=2)

        sticks = m.find_sticks(image)
        self.assertEqual(len(sticks), 1)
        mid_x = (sticks[0].x1 + sticks[0].x2) / 2
        self.assertAlmostEqual(mid_x, 500, delta=15)

        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "sweater.jpg"
            image.save(photo, "JPEG", quality=92)
            self.assertTrue(measure_read._has_wood(photo))
            found = measure_read.find_ruler_photos([photo], Path(tmp) / "out")
            self.assertEqual(len(found), 1)
            self.assertEqual(len(found[0]["sticks"]), 1)

    def test_rejects_striped_tan_fabric_without_ruler_ticks(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1000, 1000), (32, 32, 38))
        draw = ImageDraw.Draw(image)
        # Uniform tan horizontal knit stripes across the garment (wood-coloured, no edge ticks).
        for y in (200, 420, 640, 820):
            draw.rectangle((60, y, 940, y + 34), fill=(212, 184, 140))
            for x in range(60, 940, 8):
                draw.line((x, y, x, y + 34), fill=(192, 164, 122), width=1)
        self.assertEqual(m.find_sticks(image), [])

    def test_detects_horizontal_stick_in_portrait_photo(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1000, 1333), (38, 42, 50))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 520, 960, 568), fill=(228, 196, 146))
        for x in range(48, 952, 11):
            draw.line((x, 520, x, 532), fill=(52, 46, 36), width=2)
        sticks = m.find_sticks(image)
        self.assertEqual(len(sticks), 1)
        mid_y = (sticks[0].y1 + sticks[0].y2) / 2
        self.assertAlmostEqual(mid_y, 544, delta=15)

    def test_verifies_wide_ruler_with_periodic_ticks_in_either_orientation(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1200, 3000), (40, 40, 45))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 1320, 1199, 1679), fill=(228, 196, 146))
        for x in range(18, 1190, 36):
            draw.line((x, 1320, x, 1400), fill=(35, 30, 25), width=8)
        horizontal = m.Stick(0, 1499.5, 1199, 1499.5, 360)
        vertical = m.Stick(1499.5, 0, 1499.5, 1199, 360)
        self.assertIsNotNone(m._verify_stick(image, horizontal, m.wood_mask))
        self.assertIsNotNone(m._verify_stick(image.transpose(Image.Transpose.TRANSPOSE), vertical, m.wood_mask))

    def test_distinguishes_sparse_edge_ticks_from_centre_pattern_and_isolated_mark(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        stick = m.Stick(0, 449.5, 999, 449.5, 100)
        for y, period, width, detected in ((400, 30, 1, True), (441, 12, 4, False), (400, 1000, 1, False)):
            with self.subTest(y=y, period=period):
                image = Image.new("RGB", (1000, 1000), (40, 40, 45))
                draw = ImageDraw.Draw(image)
                draw.rectangle((0, 400, 999, 499), fill=(228, 196, 146))
                for x in range(20, 980, period):
                    draw.line((x, y, x, y + 16), fill=(35, 30, 25), width=width)
                score = m._verify_stick(image, stick, m.wood_mask)
                self.assertEqual(score is not None, detected)
                if y == 441:
                    # The print channel must not bring back the rejected centre pattern.
                    self.assertEqual(m.find_sticks(image), [])

    def test_rotated_row_distance_distinguishes_opposite_sides_at_angle_wrap(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        # At 177 degrees the same physical band lies on the reflected row.
        self.assertEqual(m._row_distance(80, 319, 177.0, 400), 0)
        self.assertEqual(m._row_distance(80, 80, 177.0, 400), 239)
        self.assertEqual(m._row_distance(80, 82, 3.0, 400), 2)

    def test_fine_search_refines_off_centre_band_across_zero_degrees(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        background = (40, 40, 45)
        image = Image.new("RGB", (1000, 1000), background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 800, 960, 834), fill=(228, 196, 146))
        for x in range(50, 950, 10):
            draw.line((x, 800, x, 810), fill=(35, 30, 25), width=2)
        image = image.rotate(1.0, resample=Image.Resampling.BILINEAR, fillcolor=background)
        # Observe the fine search's line before full-resolution fitting can correct it.
        with patch.object(m, "refine", new=lambda _image, stick, **_kwargs: stick):
            sticks = m.find_sticks(image)
        self.assertEqual(len(sticks), 1)
        left, right = sorted(((sticks[0].x1, sticks[0].y1), (sticks[0].x2, sticks[0].y2)))
        self.assertAlmostEqual(right[1] - left[1], -17.4, delta=3)

    def test_keeps_two_close_parallel_rulers(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        image = Image.new("RGB", (1000, 1000), (40, 40, 45))
        draw = ImageDraw.Draw(image)
        for y in (420, 530):
            draw.rectangle((30, y, 970, y + 34), fill=(228, 196, 146))
            for x in range(40, 960, 9):
                draw.line((x, y, x, y + 10), fill=(35, 30, 25), width=2)
        midpoints = sorted((stick.y1 + stick.y2) / 2 for stick in m.find_sticks(image))
        self.assertEqual(len(midpoints), 2)
        for got, expected in zip(midpoints, (437, 547), strict=True):
            self.assertAlmostEqual(got, expected, delta=10)

    def test_find_ruler_photos_ranks_by_confidence_with_bounded_image_retention(self):
        from anteroom.services import measure_read

        m = measure_read.ruler()
        scores = [10.0, 500.0, 50.0, 800.0, 300.0, 300.0] + [1.0] * 14
        call_idx = 0
        # Key by identity: Pillow image equality is based on pixel contents.
        live_images = WeakValueDictionary()
        peak_images = 0

        def fake_find(image):
            nonlocal call_idx, peak_images
            live_images[id(image)] = image
            peak_images = max(peak_images, len(live_images))
            score = scores[call_idx]
            call_idx += 1
            return [m.Stick(0, 500, 999, 500, 40, score=score)]

        cropped_names: list[str] = []

        def fake_crop(path, _out, _lines, _flips, *, image=None):
            cropped_names.append(path.name)
            return {"photo": str(path), "sticks": [{"stick": "A"}]}

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(len(scores)):
                p = Path(tmp) / f"p{i}.jpg"
                p.write_bytes(_plain_photo())
                paths.append(p)
            with (
                patch.object(measure_read, "_has_wood", return_value=True),
                patch.object(m, "find_sticks", new=fake_find),
                patch.object(m, "crop", side_effect=fake_crop),
            ):
                found = measure_read.find_ruler_photos(paths, Path(tmp) / "out")

        self.assertEqual(cropped_names, ["p1.jpg", "p3.jpg", "p4.jpg"])
        self.assertEqual([item["number"] for item in found], [1, 2, 3])
        self.assertLessEqual(peak_images, measure_read.MAX_RULER_PHOTOS + 1)


if __name__ == "__main__":
    unittest.main()
