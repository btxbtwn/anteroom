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

from vendoo_studio.main import app
from vendoo_studio.services import ebay_actions, photos, safe_fetch
from vendoo_studio.services.photo_store import LocalPhotoStore


def _png(width: int, height: int, mode: str = "1") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height)).save(buffer, "PNG")
    return buffer.getvalue()


# 56 megapixels: past the 50 MP cap, yet below Pillow's own bomb warning.
HUGE = (8000, 7000)


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
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
    monkeypatch.setattr("vendoo_studio.services.service_context.current", lambda: object())
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

    monkeypatch.setattr("vendoo_studio.routes.scout.process_bytes", process)
    response = TestClient(app).post("/api/scout", files=[("files", ("tag.jpg", b"x", "image/jpeg"))])
    assert response.status_code == 400
    assert seen == ["worker thread"]


def test_an_oversized_scout_photo_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr("vendoo_studio.routes.scout.MAX_PHOTO_SIZE_MB", 1)
    monkeypatch.setattr("vendoo_studio.routes.scout.process_bytes", lambda *a, **k: pytest.fail("processed"))
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
