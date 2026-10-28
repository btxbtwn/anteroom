"""Recommend a department from the photos and listing text already on the item.

Uses the same photo-analysis pipeline as listing generation. It does not call a
new provider, and it does not publish.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.services.category_lookup import category_department
from vendoo_studio.services.photo_store import photo_store

_CONFIDENCE_RE = re.compile(r"model confidence \(not verified\):\s*([0-9.]+)", re.I)
_EVIDENCE_RE = re.compile(r"Evidence for department:\s*(.+)", re.I)


def _recommendation(text: str) -> dict[str, Any]:
    department = category_department(text or "")
    confidence = None
    match = _CONFIDENCE_RE.search(text or "")
    if match:
        try:
            confidence = float(match.group(1))
        except ValueError:
            confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))
    evidence = _EVIDENCE_RE.search(text or "")
    reasoning = evidence.group(1).strip() if evidence else ""
    if department and not reasoning:
        reasoning = "The photo analysis named this department."
        if confidence is None:
            confidence = 0.6
    return {
        "department": {"men": "Men", "women": "Women"}.get(department or "", department),
        "confidence": confidence,
        "reasoning": reasoning,
        "evidence": text.strip() if text else "",
    }


def apply_department(listing: dict, department: str) -> dict:
    """Set the department and remap marketplace categories onto that side."""
    from vendoo_studio.services.registry import align_listing_gender, map_marketplace_category_path
    from vendoo_studio.services.seller_overrides import locked_paths

    label = {"men": "Men", "women": "Women", "unisex": "Unisex"}.get(department.casefold(), department)
    listing["department"] = label
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
        listing["ebay_specifics"] = ebay
    stored = "Men" if department.casefold() in {"men", "unisex"} else "Women" if department.casefold() == "women" else label
    ebay["department"] = stored
    align_listing_gender(listing)
    paths = listing.get("marketplace_categories")
    if isinstance(paths, dict):
        listing["marketplace_categories"] = {
            marketplace: map_marketplace_category_path(marketplace, str(path or ""), listing) or path
            for marketplace, path in paths.items()
        }
    direct = listing.get("direct_category_ids")
    if isinstance(direct, dict):
        for key in list(direct):
            if key in {"ebay", "etsy", "poshmark", "mercari", "depop"} or str(key).endswith("_path"):
                direct.pop(key, None)
    locks = locked_paths(listing)
    locks.update({"department", "ebay_specifics.department"})
    listing["seller_fields"] = sorted(locks)
    return listing


async def review_department(db: Session, conv_id: str, *, apply: bool = False) -> dict[str, Any]:
    from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
    from vendoo_studio.services.listing_generate import analyze_photos_with_tag_retry, latest_photo_analysis
    from vendoo_studio.services.listing_provider import get_listing_provider

    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        return {"ok": False, "error": "Conversation not found"}
    messages = repo.get_messages(conv_id)
    text = latest_photo_analysis(messages) or ""
    if not text:
        provider = get_listing_provider()
        if provider is None or not callable(getattr(provider, "analyze_photos", None)):
            return {
                "ok": False,
                "error": "Connect a model in Settings to re-read department from the photos.",
            }
        photos = repo.get_photos(conv_id)
        paths = [str(path) for path in photo_store().local_paths([photo.stored_filename for photo in photos])]
        if not paths:
            return {"ok": False, "error": "Add photos before asking for a department."}
        result = await analyze_photos_with_tag_retry(provider, paths)
        if not isinstance(result, dict) or result.get("error"):
            return {"ok": False, "error": str((result or {}).get("error") or "The model did not read the photos.")}
        from vendoo_studio.services.listing_generate import format_photo_analysis, normalize_evidence

        text = format_photo_analysis(normalize_evidence(result.get("evidence") or {}))
    found = _recommendation(text)
    if not found["department"]:
        return {"ok": False, "error": "The photos did not show whether this is men's, women's, or unisex.", **found}
    applied = False
    if apply:
        from vendoo_studio.routes.listings import ListingUpdate, update_listing

        current = ListingRepo(db).get_current(conv_id)
        revision = ListingRepo(db).get_revision(current.current_revision_id) if current and current.current_revision_id else None
        listing = dict(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}
        apply_department(listing, found["department"])
        update_listing(conv_id, ListingUpdate(listing=listing), db)
        applied = True
    return {"ok": True, "applied": applied, **found}
