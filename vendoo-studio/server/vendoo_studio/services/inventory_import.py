"""Shared local import lifecycle. Marketplace ownership is verified by the caller."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.orm import Session

from vendoo_studio.config import MAX_PHOTO_COUNT
from vendoo_studio.database import in_worker_session
from vendoo_studio.repositories import inventory_imports
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import photos
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.relay_sales import matching_listings, reconcile_unmatched
from vendoo_studio.services.safe_fetch import DOWNLOAD_TIMEOUT_SEC, fetch_public, is_image_type


class ImportConflict(ValueError):
    pass


class LinkError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def owner(db: Session, marketplace: str, listing_id: str):
    matches = list({conv.id: conv for conv in [*matching_listings(db, marketplace, listing_id),
                                             *inventory_imports.owners_by_id(db, marketplace, listing_id)]}.values())
    if len(matches) > 1:
        raise ImportConflict("This listing matches more than one Anteroom item. Resolve the duplicate inventory first.")
    return matches[0] if matches else None


async def download_photos(urls: list[str], collection_id: str) -> list[dict]:
    limit = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=False, trust_env=False) as client:
        async def download(index: int, url: str):
            async with limit:
                try:
                    content, content_type, _ = await fetch_public(client, url, accept=is_image_type)
                    return await asyncio.to_thread(photos.process_bytes, content, f"import-{index + 1}.jpg", content_type, collection_id=collection_id)
                except (httpx.HTTPError, ValueError, OSError):
                    return None
        result = await asyncio.gather(*(download(index, url) for index, url in enumerate(urls[:MAX_PHOTO_COUNT])))
    return [meta for meta in result if meta]


def _result(db: Session, conv_id: str, listing_id: str, *, reused: bool, warnings: list[str]) -> dict:
    return {"conversation_id": conv_id, "listing_id": listing_id, "reused": reused,
            "photo_count": len(ConversationRepo(db).get_photos(conv_id)), "warnings": warnings}


_REUSED = "Already in Anteroom. Existing edits and photos were kept."


def _reused(db: Session, marketplace: str, listing_id: str) -> dict | None:
    existing = owner(db, marketplace, listing_id)
    return _result(db, existing.id, listing_id, reused=True, warnings=[_REUSED]) if existing else None


def _create(db: Session, marketplace: str, listing_id: str, snapshot: dict, urls: list[str], downloaded: list[dict],
            binding: dict | None, target_id: str, kept: dict) -> dict:
    with start_gate():
        if reused := _reused(db, marketplace, listing_id):
            return reused
        conv = inventory_imports.create_import(db, marketplace, snapshot, listing_id, downloaded, binding, identity=target_id)
        kept["photos"] = True
    warnings = []
    if len(downloaded) < len(urls):
        warnings.append(f"Imported {len(downloaded)} of {len(urls)} photos. Add the missing photos before crosslisting.")
    elif not downloaded:
        warnings.append("The marketplace did not provide photos. Add photos before crosslisting.")
    reconcile_unmatched(db, conversation_id=conv.id)
    return _result(db, conv.id, listing_id, reused=False, warnings=warnings)


async def save_owned_listing(marketplace: str, listing_id: str, snapshot: dict, urls: list[str],
                             verify_connection: Callable[[], None], *, binding: dict | None = None) -> dict:
    """Save an already verified listing; reuse an existing local identity without overwriting it.

    Database steps run on worker threads with their own sessions, none open while photos download.
    """
    verify_connection()
    if reused := await in_worker_session(_reused, marketplace, listing_id):
        return reused
    urls = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))
    from vendoo_studio.models.conversation import new_id
    target_id = new_id()
    downloaded = await download_photos(urls, target_id)
    kept = {"photos": False}
    try:
        verify_connection()
        # Finish the save even if the request goes away, so its photos are either kept or deleted.
        return await asyncio.shield(in_worker_session(
            _create, marketplace, listing_id, snapshot, urls, downloaded, binding, target_id, kept))
    finally:
        if not kept["photos"]:
            for meta in downloaded:
                photo_store().delete(meta["stored_filename"])


_NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}


async def link_owned_listing(marketplace: str, listing_id: str, conversation_id: str,
                             verify: Callable[[], Awaitable[None]]) -> dict:
    """Record that an existing item is live on this marketplace listing.

    Only the link is written: the item's edits and photos stay as they are, and
    the marketplace is only read. ``verify`` checks the listing is still the
    seller's and still for sale, and raises when it is not. No session is open
    while it runs.
    """
    existing = await in_worker_session(_link_target, marketplace, listing_id, conversation_id)
    if not existing:
        await verify()
    return await in_worker_session(_link, marketplace, listing_id, conversation_id, existing)


def _link_target(db: Session, marketplace: str, listing_id: str, conversation_id: str) -> bool:
    """Whether the item already holds this listing. Raises when it cannot be linked."""
    from vendoo_studio.services.marketplace_delist import listings

    name = _NAMES.get(marketplace, marketplace)
    if not ConversationRepo(db).get(conversation_id):
        raise LinkError(404, "That Anteroom item no longer exists.")
    try:
        existing = owner(db, marketplace, listing_id)
    except ImportConflict as exc:
        raise LinkError(409, str(exc)) from exc
    if existing and existing.id != conversation_id:
        raise LinkError(409, "This listing is already linked to another Anteroom item.")
    if not existing:
        current = listings(db, conversation_id).get(marketplace) or {}
        if current.get("listing_id") and current["listing_id"] != listing_id:
            raise LinkError(409, f"This item is already linked to another {name} listing.")
    return existing is not None


def _link(db: Session, marketplace: str, listing_id: str, conversation_id: str, existing: bool) -> dict:
    from vendoo_studio.services.marketplace_delist import DelistError, mark
    from vendoo_studio.services.marketplace_identity import public_listing_url

    if not existing:
        try:
            mark(db, conversation_id, marketplace, "listed", public_listing_url(marketplace, listing_id))
        except DelistError as exc:
            raise LinkError(exc.status_code, str(exc)) from exc
    return {"conversation_id": conversation_id, "listing_id": listing_id, "reused": True, "linked": True,
            "photo_count": len(ConversationRepo(db).get_photos(conversation_id)),
            "warnings": [] if existing else ["Linked to the existing item. Its edits and photos were kept."]}
