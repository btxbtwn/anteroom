"""Shared local import lifecycle. Marketplace ownership is verified by the caller."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from vendoo_studio.config import MAX_PHOTO_COUNT, PHOTOS_DIR
from vendoo_studio.repositories import inventory_imports
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import photos
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.relay_sales import matching_listings, reconcile_unmatched
from vendoo_studio.services.safe_fetch import DOWNLOAD_TIMEOUT_SEC, fetch_public, is_image_type


class ImportConflict(ValueError):
    pass


def owner(db: Session, marketplace: str, listing_id: str):
    matches = list({conv.id: conv for conv in [*matching_listings(db, marketplace, listing_id),
                                             *inventory_imports.owners_by_id(db, marketplace, listing_id)]}.values())
    if len(matches) > 1:
        raise ImportConflict("This listing matches more than one Anteroom item. Resolve the duplicate inventory first.")
    return matches[0] if matches else None


async def download_photos(urls: list[str]) -> list[dict]:
    limit = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=False, trust_env=False) as client:
        async def download(index: int, url: str):
            async with limit:
                try:
                    content, content_type, _ = await fetch_public(client, url, accept=is_image_type)
                    return await asyncio.to_thread(photos.process_bytes, content, f"import-{index + 1}.jpg", content_type)
                except (httpx.HTTPError, ValueError, OSError):
                    return None
        result = await asyncio.gather(*(download(index, url) for index, url in enumerate(urls[:MAX_PHOTO_COUNT])))
    return [meta for meta in result if meta]


def _result(db: Session, conv_id: str, listing_id: str, *, reused: bool, warnings: list[str]) -> dict:
    return {"conversation_id": conv_id, "listing_id": listing_id, "reused": reused,
            "photo_count": len(ConversationRepo(db).get_photos(conv_id)), "warnings": warnings}


async def save_owned_listing(db: Session, marketplace: str, listing_id: str, snapshot: dict, urls: list[str],
                            verify_connection: Callable[[], None], *, binding: dict | None = None) -> dict:
    """Save an already verified listing; reuse an existing local identity without overwriting it."""
    verify_connection()
    existing = owner(db, marketplace, listing_id)
    if existing:
        return _result(db, existing.id, listing_id, reused=True, warnings=["Already in Anteroom. Existing edits and photos were kept."])
    urls = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))
    downloaded = await download_photos(urls)
    retained = False
    try:
        verify_connection()
        with start_gate():
            existing = owner(db, marketplace, listing_id)
            if existing:
                return _result(db, existing.id, listing_id, reused=True, warnings=["Already in Anteroom. Existing edits and photos were kept."])
            conv = inventory_imports.create_import(db, marketplace, snapshot, listing_id, downloaded, binding)
            retained = True
        warnings = []
        if len(downloaded) < len(urls):
            warnings.append(f"Imported {len(downloaded)} of {len(urls)} photos. Add the missing photos before crosslisting.")
        elif not downloaded:
            warnings.append("The marketplace did not provide photos. Add photos before crosslisting.")
        reconcile_unmatched(db, conversation_id=conv.id)
        return _result(db, conv.id, listing_id, reused=False, warnings=warnings)
    finally:
        if not retained:
            for meta in downloaded:
                (Path(PHOTOS_DIR) / meta["stored_filename"]).unlink(missing_ok=True)
