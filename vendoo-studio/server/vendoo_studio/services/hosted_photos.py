"""Private Storage calls made with the verified seller's JWT, never a service key."""
from __future__ import annotations

import io
import os
import threading
import uuid
import warnings
from collections import OrderedDict

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from vendoo_studio.config import MAX_PHOTO_COUNT, MAX_PHOTO_SIZE_MB

BUCKET = 'anteroom-photos'
MAX_BYTES = MAX_PHOTO_SIZE_MB * 1024 * 1024
MAX_PHOTOS = MAX_PHOTO_COUNT
# Camera photos reach about 48 megapixels (6000×8000); larger is refused.
MAX_PIXELS = 50_000_000
MAX_SIDE = 2400
register_heif_opener()
_decode_slot = threading.Semaphore(1)


def normalize(data: bytes) -> tuple[bytes, int, int]:
    # Bound decoder memory on the shared API while uploads arrive concurrently.
    with _decode_slot:
        return _normalize(data)


def _normalize(data: bytes) -> tuple[bytes, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC'} or image.width * image.height > MAX_PIXELS:
                    raise ValueError
                # A JPEG decodes at a fraction of its size when that still
                # covers the stored size, which keeps big photos cheap.
                image.draft('RGB', (MAX_SIDE, MAX_SIDE))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((MAX_SIDE, MAX_SIDE))
                # Flatten transparent images onto white and strip EXIF/location data.
                rgba = image.convert('RGBA')
                result = Image.new('RGB', rgba.size, 'white')
                result.paste(rgba, mask=rgba.getchannel('A'))
                output = io.BytesIO()
                result.save(output, format='JPEG', quality=90)
                return output.getvalue(), result.width, result.height
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, f'Choose a JPEG, PNG, WebP or HEIC photo under {MAX_PIXELS // 1_000_000} megapixels') from None


def new_key(user_id: str, draft_id: str) -> str:
    return f'{user_id}/{draft_id}/{uuid.uuid4().hex}.jpg'


def check_key(key: str, user_id: str, draft_id: str) -> None:
    prefix = f'{user_id}/{draft_id}/'
    if not key.startswith(prefix):
        raise HTTPException(404, 'Photo not found')
    suffix = key[len(prefix):]
    if len(suffix) != 36 or not suffix.endswith('.jpg') or any(c not in '0123456789abcdef' for c in suffix[:-4]):
        raise HTTPException(404, 'Photo not found')


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
        response = httpx.request(method, url,
                                 headers={'Authorization': f'Bearer {token}', 'apikey': public_key,
                                          'Content-Type': 'application/json' if method == 'DELETE' else 'image/jpeg',
                                          'Cache-Control': 'no-store'},
                                 json={'prefixes': [key]} if method == 'DELETE' else None,
                                 content=None if method == 'DELETE' else data, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Photo storage is temporarily unavailable') from None
    if method == 'DELETE' and response.status_code == 404:
        return b''
    if not response.is_success:
        raise HTTPException(503, 'Could not upload or load the photo. Please try again.')
    return response.content


# Made thumbnails, newest last. Stored photos never change in place (an edit
# writes a new key), so a kept thumbnail stays right. Covers are a few KB, so
# this holds thousands while bounding memory on the shared API.
THUMB_CACHE_BYTES = 64 * 1024 * 1024
_thumbs: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()
_thumbs_bytes = 0
_thumbs_lock = threading.Lock()


def thumbnail(key: str, version: str | None, size: int, token: str) -> bytes:
    """A JPEG at most ``size`` px on a side, made once per stored photo.

    The caller has already checked that the seller owns ``key``.
    """
    global _thumbs_bytes
    entry = (key, version or '', size)
    with _thumbs_lock:
        kept = _thumbs.get(entry)
        if kept is not None:
            _thumbs.move_to_end(entry)
            return kept
    data = storage_call('GET', key, token)
    with _decode_slot, Image.open(io.BytesIO(data)) as image:
        # Decode the JPEG at a fraction of its size; the cover is 96px.
        image.draft('RGB', (size, size))
        image.thumbnail((size, size))
        output = io.BytesIO()
        image.convert('RGB').save(output, 'JPEG', quality=82)
    made = output.getvalue()
    with _thumbs_lock:
        if entry not in _thumbs:
            _thumbs[entry] = made
            _thumbs_bytes += len(made)
        while _thumbs_bytes > THUMB_CACHE_BYTES:
            _old, dropped = _thumbs.popitem(last=False)
            _thumbs_bytes -= len(dropped)
    return made
