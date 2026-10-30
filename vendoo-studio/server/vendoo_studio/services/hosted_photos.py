"""Private Storage calls made with the verified seller's JWT, never a service key."""
from __future__ import annotations

import io
import os
import uuid
import warnings

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

BUCKET = 'anteroom-photos'
MAX_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 30
MAX_PIXELS = 25_000_000


def normalize(data: bytes) -> tuple[bytes, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > MAX_PIXELS:
                    raise ValueError
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2400, 2400))
                # Flatten transparent images onto white and strip EXIF/location data.
                rgba = image.convert('RGBA')
                result = Image.new('RGB', rgba.size, 'white')
                result.paste(rgba, mask=rgba.getchannel('A'))
                output = io.BytesIO()
                result.save(output, format='JPEG', quality=90)
                return output.getvalue(), result.width, result.height
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, 'Choose a JPEG, PNG or WebP photo under 25 megapixels') from None


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
        options = {'json': {'prefixes': [key]}} if method == 'DELETE' else {'content': data}
        if method != 'DELETE':
            url += '/' + key
        response = httpx.request(method, url,
                                 headers={'Authorization': f'Bearer {token}', 'apikey': public_key,
                                          'Content-Type': 'application/json' if method == 'DELETE' else 'image/jpeg',
                                          'Cache-Control': 'no-store'},
                                 **options, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Photo storage is temporarily unavailable') from None
    if method == 'DELETE' and response.status_code == 404:
        return b''
    if not response.is_success:
        raise HTTPException(503, 'Could not upload or load the photo. Please try again.')
    return response.content
