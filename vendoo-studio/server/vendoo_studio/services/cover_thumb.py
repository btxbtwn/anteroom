"""Small JPEGs for sidebar covers that live on someone else's image host.

A listing row is about 40px. Pointing it at the original — a phone photo, or
Vendoo's full-size image — makes WebKit decode the whole bitmap and keep it.
An inventory of those is tens of gigabytes, and the machine then spends its
CPU compressing memory. This stores one small JPEG per URL and serves that.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.photos import save_jpeg_thumbnail
from vendoo_studio.services.safe_fetch import (
    DOWNLOAD_TIMEOUT_SEC,
    UnsafeURLError,
    fetch_public,
    is_image_type,
    resolve_fetch_target,
)

COVER_THUMB_SIZE = 192
# A cover is a thumbnail. Refuse a download that cannot be a reasonable photo.
MAX_COVER_BYTES = 8 * 1024 * 1024
# Two decodes at once: each original is briefly full size in Python.
_DECODE_LIMIT = asyncio.Semaphore(2)


def cover_cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return photo_store().cache_dir("remote-covers") / f"{digest}.jpg"


async def remote_cover_thumb(url: str) -> Path:
    """The cached thumbnail for ``url``. Raises ``UnsafeURLError`` when it is not a public https image."""
    raw, _addresses = resolve_fetch_target(url, allow_http=False)
    dest = cover_cache_path(raw)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    async with _DECODE_LIMIT:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SEC) as client:
            contents, _content_type, _final = await fetch_public(
                client,
                raw,
                allow_http=False,
                accept=is_image_type,
                max_bytes=MAX_COVER_BYTES,
            )
        await asyncio.to_thread(save_jpeg_thumbnail, contents, dest, COVER_THUMB_SIZE)
    return dest


__all__ = ["COVER_THUMB_SIZE", "UnsafeURLError", "remote_cover_thumb"]
