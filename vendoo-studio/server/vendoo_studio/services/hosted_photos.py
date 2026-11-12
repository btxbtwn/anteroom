"""Private Storage calls made with the verified seller's JWT, never a service key."""
from __future__ import annotations

import io
import os
import threading
import uuid
from collections import OrderedDict

import httpx
from fastapi import HTTPException
from PIL import Image

from vendoo_studio.config import MAX_PHOTO_COUNT, MAX_PHOTO_SIZE_MB
from vendoo_studio.services import image_decode

BUCKET = 'anteroom-photos'
MAX_BYTES = MAX_PHOTO_SIZE_MB * 1024 * 1024
MAX_PHOTOS = MAX_PHOTO_COUNT
MAX_SIDE = 2400
# Thumbnail sizes the apps ask for (covers and photo strips). These are kept in
# Storage beside the photo, so a restart or deploy never has to remake them.
# 384 is the iPhone's photo tiles (about 136 pt at 3x).
KEPT_THUMB_SIZES = (96, 192, 384)
# Thumbnails decode a JPEG at a fraction of its size, so they need little memory
# and never wait behind a full-size upload in ``image_decode``.
_thumb_slot = threading.Semaphore(2)
# One client for every Storage call, so each reuses an open TLS connection
# instead of paying a new handshake to Supabase. httpx clients are thread-safe.
_storage = httpx.Client(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))


def normalize(data: bytes) -> tuple[bytes, int, int]:
    try:
        return image_decode.normalized_jpeg(data, max_side=MAX_SIDE)
    except ValueError:
        raise HTTPException(422, f'Choose a JPEG, PNG, WebP or HEIC photo under {image_decode.MAX_PIXELS // 1_000_000} megapixels') from None


def new_key(user_id: str, draft_id: str) -> str:
    return f'{user_id}/{draft_id}/{uuid.uuid4().hex}.jpg'


def check_key(key: str, user_id: str, draft_id: str) -> None:
    prefix = f'{user_id}/{draft_id}/'
    if not key.startswith(prefix):
        raise HTTPException(404, 'Photo not found')
    suffix = key[len(prefix):]
    if len(suffix) != 36 or not suffix.endswith('.jpg') or any(c not in '0123456789abcdef' for c in suffix[:-4]):
        raise HTTPException(404, 'Photo not found')


def rendition_key(key: str, size: int) -> str:
    """Where a stored photo's thumbnail of ``size`` px is kept, beside the photo."""
    return f'{key[:-4]}.t{size}.jpg'


def with_renditions(key: str) -> list[str]:
    """A photo's key and its kept thumbnails, so a delete removes them together."""
    return [key, *(rendition_key(key, size) for size in KEPT_THUMB_SIZES)]


def storage_call(method: str, key: str, token: str, data: bytes | None = None) -> bytes:
    public_key = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
    if not public_key:
        raise HTTPException(503, 'Photo storage is not configured')
    origin = os.environ.get('SUPABASE_URL', '').rstrip('/')
    if not origin:
        raise HTTPException(503, 'Photo storage is not configured')
    try:
        url = f'{origin}/storage/v1/object/{BUCKET}'
        if method != 'DELETE':
            url += '/' + key
        response = _storage.request(method, url,
                                 headers={'Authorization': f'Bearer {token}', 'apikey': public_key,
                                          'Content-Type': 'application/json' if method == 'DELETE' else 'image/jpeg',
                                          'Cache-Control': 'no-store'},
                                 json={'prefixes': with_renditions(key)} if method == 'DELETE' else None,
                                 content=None if method == 'DELETE' else data, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Photo storage is temporarily unavailable') from None
    if method == 'DELETE' and response.status_code == 404:
        return b''
    if not response.is_success:
        raise HTTPException(503, 'Could not upload or load the photo. Please try again.')
    return response.content


def remove_objects(keys: list[str], token: str) -> None:
    """Delete many objects with the seller's JWT; Storage takes up to 1,000 names per call."""
    public_key = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
    origin = os.environ.get('SUPABASE_URL', '').rstrip('/')
    if not public_key or not origin:
        raise HTTPException(503, 'Photo storage is not configured')
    for start in range(0, len(keys), 1000):
        try:
            response = _storage.request('DELETE', f'{origin}/storage/v1/object/{BUCKET}',
                                     headers={'Authorization': f'Bearer {token}', 'apikey': public_key,
                                              'Cache-Control': 'no-store'},
                                     json={'prefixes': keys[start:start + 1000]}, timeout=60, follow_redirects=False)
        except httpx.HTTPError:
            raise HTTPException(503, 'Photo storage is temporarily unavailable') from None
        if not response.is_success and response.status_code != 404:
            raise HTTPException(503, 'Could not delete your photos. Please try again.')


# Made thumbnails, newest last. Stored photos never change in place (an edit
# writes a new key), so a kept thumbnail stays right. Covers are a few KB, so
# this holds thousands while bounding memory on the shared API.
THUMB_CACHE_BYTES = 64 * 1024 * 1024
_thumbs: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()
_thumbs_bytes = 0
_thumbs_lock = threading.Lock()


def thumbnail(key: str, version: str | None, size: int, token: str) -> bytes:
    """A JPEG at most ``size`` px on a side, made once per stored photo.

    Kept in memory for repeat requests, and for the sizes the apps use, in
    Storage beside the photo so a new server process does not remake it from
    the full photo. The caller has already checked that the seller owns ``key``.
    """
    entry = (key, version or '', size)
    with _thumbs_lock:
        kept = _thumbs.get(entry)
        if kept is not None:
            _thumbs.move_to_end(entry)
            return kept
    stored = size in KEPT_THUMB_SIZES
    made = _stored_thumbnail(rendition_key(key, size), token) if stored else None
    if made is None:
        made = _make_thumbnail(storage_call('GET', key, token), size)
        if stored:
            _store_thumbnail(rendition_key(key, size), made, token)
    _keep(entry, made)
    return made


def _stored_thumbnail(name: str, token: str) -> bytes | None:
    try:
        return storage_call('GET', name, token) or None
    except HTTPException:
        # Not made yet, or Storage is slow to say so: make it from the photo.
        return None


def _store_thumbnail(name: str, data: bytes, token: str) -> None:
    try:
        storage_call('POST', name, token, data)
    except HTTPException:
        # Another request stored it first, or Storage is busy; the next ask makes it again.
        pass


def _make_thumbnail(data: bytes, size: int) -> bytes:
    with _thumb_slot, Image.open(io.BytesIO(data)) as image:
        # Decode the JPEG at a fraction of its size; the cover is 96px.
        image.draft('RGB', (size, size))
        image.thumbnail((size, size))
        output = io.BytesIO()
        image.convert('RGB').save(output, 'JPEG', quality=82)
    return output.getvalue()


def _keep(entry: tuple[str, str, int], made: bytes) -> None:
    global _thumbs_bytes
    with _thumbs_lock:
        if entry not in _thumbs:
            _thumbs[entry] = made
            _thumbs_bytes += len(made)
        while _thumbs_bytes > THUMB_CACHE_BYTES:
            _old, dropped = _thumbs.popitem(last=False)
            _thumbs_bytes -= len(dropped)
