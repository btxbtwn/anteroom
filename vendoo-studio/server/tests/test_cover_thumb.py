"""Sidebar covers from another host are served as a small cached JPEG."""
from __future__ import annotations

from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from vendoo_studio.routes import photos as photos_route
from vendoo_studio.services import cover_thumb


def _jpeg(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "navy").save(buffer, "JPEG")
    return buffer.getvalue()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(photos_route.router)
    return TestClient(app)


def test_a_private_or_non_https_cover_is_refused():
    client = _client()
    refused = [
        "http://127.0.0.1/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://localhost/a.jpg",
        "file:///etc/passwd",
        "not a url",
    ]
    for url in refused:
        response = client.get("/api/cover-thumb", params={"url": url})
        assert response.status_code == 400, url


def test_a_public_cover_is_cached_as_a_small_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("vendoo_studio.config.PHOTOS_DIR", str(tmp_path))
    source = _jpeg(1200, 800)
    calls = {"n": 0}

    async def fake_fetch(client, url, **kwargs):
        calls["n"] += 1
        assert kwargs["allow_http"] is False
        assert kwargs["max_bytes"] == cover_thumb.MAX_COVER_BYTES
        return source, "image/jpeg", url

    monkeypatch.setattr(cover_thumb, "fetch_public", fake_fetch)
    monkeypatch.setattr(cover_thumb, "resolve_fetch_target", lambda url, allow_http=False: (url, ["1.2.3.4"]))

    client = _client()
    url = "https://images.vendoo.co/abc.jpg"
    first = client.get("/api/cover-thumb", params={"url": url})
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("image/jpeg")
    with Image.open(BytesIO(first.content)) as image:
        assert max(image.size) <= cover_thumb.COVER_THUMB_SIZE
    assert len(first.content) < len(source)

    cached = list((tmp_path / "remote-covers").glob("*.jpg"))
    assert len(cached) == 1
    written_at = cached[0].stat().st_mtime_ns

    second = client.get("/api/cover-thumb", params={"url": url})
    assert second.status_code == 200
    assert second.content == first.content
    assert cached[0].stat().st_mtime_ns == written_at
    assert calls["n"] == 1
