"""Listing videos in the seller's private Storage, beside their photos.

A video lives in the same private ``anteroom-photos`` bucket as the photos, under
the seller's own folder (``<seller>/<listing>/<32 hex>.video``), so the bucket's
owner-only policies, the account-deletion fence and account deletion itself
cover it with no second set of rules.

Foreground routes call Storage with the seller's own session token; durable
background work (an eBay or Etsy listing run by the hosted worker) uses the
server key, scoped here to the verified seller's folder. Playback and the
Bridge get short-lived signed links, so no server process streams a video.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException

from anteroom.services.hosted_photos import BUCKET, _storage

# The largest video any marketplace takes from Anteroom (eBay's 150 MB).
MAX_BYTES = 157_286_400
UPLOAD_TIMEOUT_SEC = 300.0
# Playback in the web app, and the Bridge's download for a Poshmark listing.
LINK_SECONDS = 15 * 60
_KEY = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[A-Za-z0-9_-]{1,100}/[0-9a-f]{32}\.video')


def new_key(owner: str, conv_id: str) -> str:
    return check_key(f'{owner}/{conv_id}/{uuid.uuid4().hex}.video', owner, conv_id)


def check_key(key: str, owner: str, conv_id: str | None = None) -> str:
    """``key`` when it names a video in this seller's folder (and listing); 404 otherwise."""
    if not _KEY.fullmatch(key) or not key.startswith(owner + '/') or (
            conv_id is not None and key.split('/')[1] != conv_id):
        raise HTTPException(404, 'Video not found')
    return key


def _credentials() -> tuple[str, dict[str, str]]:
    """Storage's origin and headers: the seller's token when this execution has one, else the server key."""
    from anteroom.services.service_context import current

    context = current()
    if context is None:
        raise RuntimeError('Hosted videos require a verified seller context')
    origin = os.environ.get('SUPABASE_URL', '').rstrip('/')
    token = getattr(context.photos, 'storage_token', lambda: None)()
    if token:
        public_key = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
        if not origin or not public_key:
            raise HTTPException(503, 'Video storage is not configured')
        return origin, {'Authorization': f'Bearer {token}', 'apikey': public_key, 'Cache-Control': 'no-store'}
    secret = os.environ.get('SUPABASE_SERVER_SECRET_KEY', '')
    if not origin or not secret:
        raise HTTPException(503, 'Video storage is not configured')
    return origin, {'apikey': secret, 'Cache-Control': 'no-store'}


def _owned(key: str) -> str:
    from anteroom.services.service_context import current

    context = current()
    if context is None:
        raise RuntimeError('Hosted videos require a verified seller context')
    return check_key(key, context.seller_id)


def upload(key: str, path: Path, mime_type: str) -> None:
    """Send the file at ``path`` to Storage without reading it into memory."""
    _owned(key)
    origin, headers = _credentials()
    try:
        with path.open('rb') as stream:
            response = _storage.post(f'{origin}/storage/v1/object/{BUCKET}/{key}', content=stream,
                                     headers={**headers, 'Content-Type': mime_type, 'x-upsert': 'false'},
                                     timeout=httpx.Timeout(UPLOAD_TIMEOUT_SEC, connect=10), follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Video storage did not answer in time. Try again.') from None
    if response.status_code == 413:
        raise HTTPException(413, 'This video is larger than storage accepts.')
    if not response.is_success:
        raise HTTPException(503, 'Could not save the video. Try again.')


def download_to_path(key: str, dest: Path) -> None:
    """Stream the video from Storage to ``dest`` in chunks instead of buffering it in memory."""
    _owned(key)
    origin, headers = _credentials()
    try:
        with _storage.stream('GET', f'{origin}/storage/v1/object/{BUCKET}/{key}', headers=headers,
                             timeout=httpx.Timeout(UPLOAD_TIMEOUT_SEC, connect=10),
                             follow_redirects=False) as response:
            if response.status_code in (400, 404):
                raise ValueError('The selected listing video is missing. Choose another video or clear it.')
            if not response.is_success:
                raise ValueError('The listing video could not be loaded from storage. Try again.')
            with dest.open('wb') as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    stream.write(chunk)
    except httpx.HTTPError:
        raise ValueError('The listing video could not be loaded from storage in time. Try again.') from None


def read(key: str) -> bytes:
    """The whole video, for an eBay or Etsy upload (each at most ``MAX_BYTES``)."""
    _owned(key)
    origin, headers = _credentials()
    try:
        response = _storage.get(f'{origin}/storage/v1/object/{BUCKET}/{key}', headers=headers,
                                timeout=httpx.Timeout(UPLOAD_TIMEOUT_SEC, connect=10), follow_redirects=False)
    except httpx.HTTPError:
        raise ValueError('The listing video could not be loaded from storage in time. Try again.') from None
    if response.status_code in (400, 404):
        raise ValueError('The selected listing video is missing. Choose another video or clear it.')
    if not response.is_success:
        raise ValueError('The listing video could not be loaded from storage. Try again.')
    return response.content


def delete(key: str) -> None:
    _owned(key)
    origin, headers = _credentials()
    try:
        response = _storage.request('DELETE', f'{origin}/storage/v1/object/{BUCKET}', json={'prefixes': [key]},
                                    headers={**headers, 'Content-Type': 'application/json'}, timeout=30,
                                    follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Video storage is temporarily unavailable') from None
    if not response.is_success and response.status_code != 404:
        raise HTTPException(503, 'Could not delete the video. Try again.')


def signed_url(key: str, seconds: int = LINK_SECONDS) -> str:
    """A link that reads this one video for ``seconds``, with no session."""
    _owned(key)
    origin, headers = _credentials()
    try:
        response = _storage.post(f'{origin}/storage/v1/object/sign/{BUCKET}/{key}', json={'expiresIn': seconds},
                                 headers={**headers, 'Content-Type': 'application/json'}, timeout=20,
                                 follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'Video storage is temporarily unavailable') from None
    if response.status_code in (400, 404):
        raise HTTPException(404, 'Video file not found')
    if not response.is_success:
        raise HTTPException(503, 'Could not open the video. Try again.')
    path = str((response.json() or {}).get('signedURL') or '')
    if not path.startswith(f'/object/sign/{BUCKET}/'):
        raise HTTPException(503, 'Could not open the video. Try again.')
    return f'{origin}/storage/v1{path}'
