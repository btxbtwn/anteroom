"""Untrusted photos decode with bounded memory and never on the event loop."""
from __future__ import annotations

import asyncio
import io
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anteroom.main import app
from anteroom.services import ebay_actions, photos, safe_fetch
from anteroom.services.photo_store import LocalPhotoStore


def _png(width: int, height: int, mode: str = "1") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height)).save(buffer, "PNG")
    return buffer.getvalue()


# 56 megapixels: past the 50 MP cap, yet below Pillow's own bomb warning.
HUGE = (8000, 7000)


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    return tmp_path


def test_an_upload_past_the_pixel_cap_is_refused(local_store):
    with pytest.raises(ValueError):
        photos.process_bytes(_png(*HUGE), "huge.png", "image/png", collection_id="c1")
    assert not list(local_store.rglob("*.png"))


def test_a_thumbnail_past_the_pixel_cap_is_refused(tmp_path):
    with pytest.raises(ValueError):
        photos.save_jpeg_thumbnail(_png(*HUGE), tmp_path / "t.jpg", 96)
    assert not (tmp_path / "t.jpg").exists()


def test_a_hosted_upload_is_stored_as_a_bounded_jpeg(monkeypatch):
    kept = {}

    class Store:
        def new_key(self, collection_id, ext):
            return f"{collection_id}/photo{ext}"

        def put(self, key, data):
            kept[key] = data

    monkeypatch.setattr(photos, "photo_store", lambda: Store())
    monkeypatch.setattr("anteroom.services.service_context.current", lambda: object())
    meta = photos.process_bytes(_png(4000, 3000, "RGBA"), "big.png", "image/png", collection_id="c1")
    assert meta["mime_type"] == "image/jpeg"
    assert max(meta["width"], meta["height"]) == 2400
    with Image.open(io.BytesIO(kept[meta["stored_filename"]])) as image:
        assert image.format == "JPEG"
        assert max(image.size) == 2400


def test_scout_photos_are_processed_off_the_event_loop(monkeypatch):
    seen = []

    def process(contents, filename, content_type, *, collection_id):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("worker thread")
        raise ValueError("stop here")

    monkeypatch.setattr("anteroom.routes.scout.process_bytes", process)
    response = TestClient(app).post("/api/scout", files=[("files", ("tag.jpg", b"x", "image/jpeg"))])
    assert response.status_code == 400
    assert seen == ["worker thread"]


def test_an_oversized_scout_photo_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr("anteroom.routes.scout.MAX_PHOTO_SIZE_MB", 1)
    monkeypatch.setattr("anteroom.routes.scout.process_bytes", lambda *a, **k: pytest.fail("processed"))
    response = TestClient(app).post(
        "/api/scout", files=[("files", ("tag.jpg", b"\xff\xd8" + b"0" * (2 * 1024 * 1024), "image/jpeg"))],
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_an_ebay_decompression_bomb_is_a_422(monkeypatch):
    import base64

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    data = base64.b64encode(_png(100, 100)).decode()
    with pytest.raises(ebay_actions.EbayFailure) as raised:
        ebay_actions.image_bytes({"data": data, "mime": "image/png"})
    assert raised.value.status == 422


def test_dns_lookups_leave_the_event_loop_free(monkeypatch):
    def slow_lookup(host, *args, **kwargs):
        time.sleep(0.3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", slow_lookup)

    async def run():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        await asyncio.sleep(0)
        await safe_fetch.resolve_fetch_target("https://example.com/a.jpg")
        ticker.cancel()
        return ticks

    assert asyncio.run(run()) > 5


def test_a_stalled_dns_lookup_ends_at_its_deadline(monkeypatch):
    release = threading.Event()

    def stalled(host, *args, **kwargs):
        release.wait(5)
        return []

    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", stalled)
    monkeypatch.setattr(safe_fetch, "DNS_TIMEOUT_SEC", 0.1)

    async def run():
        started = time.monotonic()
        try:
            with pytest.raises(safe_fetch.UnsafeURLError, match="did not resolve in time"):
                await safe_fetch.resolve_fetch_target("https://example.com/a.jpg")
            return time.monotonic() - started
        finally:
            release.set()

    assert asyncio.run(run()) < 2


def test_cached_thumbnail_refuses_image_past_pixel_cap(local_store):
    from fastapi import HTTPException
    from anteroom.routes import photos as photos_route
    from anteroom.services.image_decode import ImageTooLargeError

    (local_store / "huge.png").write_bytes(_png(*HUGE))
    with pytest.raises(ImageTooLargeError):
        photos.get_or_create_thumbnail("huge.png", 96)
    assert not photos.thumbnail_path("huge.png", 96).exists()

    class _FakePhoto:
        id = "p-huge"
        stored_filename = "huge.png"
        mime_type = "image/png"

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return _FakePhoto()

    class _FakeDB:
        def query(self, *_args, **_kwargs):
            return _FakeQuery()

    with pytest.raises(HTTPException) as exc:
        photos_route.serve_photo_thumbnail("p-huge", size=96, db=_FakeDB())  # type: ignore[arg-type]
    assert exc.value.status_code == 422


def test_photo_edit_preview_enforces_pixel_cap_and_passes_max_side(local_store, monkeypatch):
    from anteroom.services import image_decode, photo_edits

    (local_store / "huge.png").write_bytes(_png(*HUGE))
    with pytest.raises(ValueError):
        photo_edits.preview_jpeg("huge.png", photo_edits.PhotoEdits(), crop=True)

    (local_store / "normal.png").write_bytes(_png(2400, 1800, "RGB"))
    seen_max_sides: list[int | None] = []
    real_decode = image_decode.decode

    def spy_decode(data: bytes, *, max_side: int | None = None):
        seen_max_sides.append(max_side)
        return real_decode(data, max_side=max_side)

    monkeypatch.setattr(image_decode, "decode", spy_decode)
    preview = photo_edits.preview_jpeg("normal.png", photo_edits.PhotoEdits(exposure=0.2), crop=True)
    assert preview
    assert seen_max_sides == [photo_edits.PREVIEW_MAX_SIDE]


def test_ruler_load_and_measure_read_enforce_pixel_cap_and_max_side(tmp_path):
    from anteroom.services import measure_read

    module = measure_read.ruler()
    huge_path = tmp_path / "huge.png"
    huge_path.write_bytes(_png(*HUGE))
    with pytest.raises(ValueError):
        module.load(huge_path)
    assert measure_read.find_ruler_photos([huge_path], tmp_path / "out") == []

    tall_path = tmp_path / "tall.png"
    tall_path.write_bytes(_png(6000, 4500, "RGB"))
    loaded = module.load(tall_path)
    assert max(loaded.size) == module.MAX_SIDE

    # Manual --line coordinates are in original photo pixels; y=4000 in a 6000x4500
    # photo must scale into the resized 4096x3072 frame rather than falling outside it.
    report = module.crop(tall_path, tmp_path / "manual", ["500,4000,5500,4000,200"], set())
    assert report["size"] == [4096, 3072]
    stick_line = report["sticks"][0]["line"]
    assert 0 < stick_line[1] < 3072
    assert abs(stick_line[1] - round(4000 * (3072 / 4500))) <= 1


