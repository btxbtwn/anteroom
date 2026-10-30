"""Server-side Storage access for verified, durable seller work.

The server credential bypasses Storage RLS, so this boundary independently
requires the current seller and validates the complete object namespace.
Foreground HTTP photo routes continue using their seller's session JWT.
"""
import os
import re

import httpx
from fastapi import HTTPException

from vendoo_studio.services.hosted_photos import BUCKET, check_key
from vendoo_studio.services.service_context import current


def checked_photo_key(key: str, owner: str) -> str:
    parts = key.split('/')
    if len(parts) != 3 or parts[0] != owner or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', parts[1]):
        raise HTTPException(404, 'Photo not found')
    check_key(key, owner, parts[1])
    return key


def photo_call(method: str, key: str, data: bytes | None = None) -> bytes:
    context = current()
    if context is None:
        raise RuntimeError('Background Storage requires a verified seller context')
    checked_photo_key(key, context.seller_id)
    if method not in {'GET', 'POST', 'DELETE'}:
        raise ValueError('Unsupported photo operation')
    secret = os.environ.get('SUPABASE_SERVER_SECRET_KEY', '')
    origin = os.environ.get('SUPABASE_URL', '').rstrip('/')
    if not secret or not origin:
        raise HTTPException(503, 'Background photo storage is not configured')
    url = f'{origin}/storage/v1/object/{BUCKET}'
    options = {'json': {'prefixes': [key]}} if method == 'DELETE' else {'content': data}
    if method != 'DELETE':
        url += '/' + key
    try:
        response = httpx.request(method, url, headers={
            'apikey': secret, 'Cache-Control': 'no-store',
            'Content-Type': 'application/json' if method == 'DELETE' else 'image/jpeg',
            **({'x-upsert': 'true'} if method == 'POST' else {}),
        }, **options, timeout=30, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Background photo storage is temporarily unavailable') from None
    if response.status_code == 404:
        if method == 'DELETE':
            return b''
        raise HTTPException(404, 'Photo not found')
    if not response.is_success:
        raise HTTPException(503, 'Could not access the saved photo')
    return response.content
