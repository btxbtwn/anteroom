"""Read-only send previews, bound to the listing the seller reviewed."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.job_snapshot import prepare_listing_snapshot

# A preview is short-lived UI state. Approval copies its preparation into the
# durable job; restarting Studio before approval simply requires another review.
_reviews: OrderedDict[str, tuple[float, dict]] = OrderedDict()
REVIEW_TTL_SECONDS = 15 * 60
MAX_REVIEWS = 128


def review_inputs(db, conv_id: str) -> tuple[dict, str]:
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions:
        raise HTTPException(400, "Generate or edit a listing first.")
    snapshot = prepare_listing_snapshot(db, conv, revisions[0].listing_json)
    from vendoo_studio.services.marketplaces import get_selected_marketplaces
    from vendoo_studio.services.vendoo_import import vendoo_binding

    photos = repo.get_photos(conv_id)
    inputs = {
        "conversation_id": conv_id,
        "revision_id": revisions[0].id,
        "snapshot": snapshot,
        "photo_ids": [p.id for p in photos],
        "photo_versions": photo_versions(repo.get_all_photos(conv_id), revisions[0].listing_json),
        "binding": vendoo_binding(conv.notes),
        "marketplaces": get_selected_marketplaces(),
    }
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return inputs, fingerprint


def photo_versions(photos, listing: dict | None) -> dict:
    """What an approval's photos depend on: each file's content, since an edit keeps
    the photo's ID, and each marketplace form's own photos and order."""
    picks = listing.get("marketplace_photos") if isinstance(listing, dict) else None
    return {"files": [[photo.id, photo.checksum, photo.marketplace] for photo in photos],
            "marketplaces": picks if isinstance(picks, dict) else {}}


def remember_review(inputs: dict, fingerprint: str, preparation: dict) -> str:
    now = monotonic()
    for key, (created, _) in list(_reviews.items()):
        if now - created >= REVIEW_TTL_SECONDS:
            del _reviews[key]
    while len(_reviews) >= MAX_REVIEWS:
        _reviews.popitem(last=False)
    review_id = uuid4().hex
    _reviews[review_id] = (now, deepcopy({**inputs, "fingerprint": fingerprint, "preparation": preparation}))
    return review_id


def reviewed_send(db, conv_id: str, review_id: str) -> dict:
    entry = _reviews.get(review_id)
    if not entry or monotonic() - entry[0] >= REVIEW_TTL_SECONDS:
        raise HTTPException(409, "This preview expired. Review the changes again before sending.")
    review = entry[1]
    _, fingerprint = review_inputs(db, conv_id)
    if review["conversation_id"] != conv_id or review["fingerprint"] != fingerprint:
        raise HTTPException(409, "This listing changed after the preview. Review the changes again before sending.")
    return deepcopy(review)


def _value(item: dict, path: str):
    value = item
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


async def preview_send(db, conv_id: str) -> dict:
    from vendoo_studio.services.vendoo_item_build import build_vendoo_item, marketplaces_left_empty
    from vendoo_studio.services.vendoo_item_update import apply_update_all, changed_fields
    from vendoo_studio.services.vendoo_create import (
        ITEM_READ_TIMEOUT_SEC, label_display_map, prepare_listing_fields_for_vendoo, run_ops,
    )
    from vendoo_studio.services.vendoo_specifics import specs_to_rows

    inputs, fingerprint = review_inputs(db, conv_id)
    job = SimpleNamespace(id=None)
    item_id = inputs["binding"].get("vendooItemId")

    async def read_current() -> dict | None:
        if not item_id:
            return {}
        reply = await run_ops(job, [{"op": "get_item", "item_id": item_id, "with_version": True}], timeout=ITEM_READ_TIMEOUT_SEC)
        return next((r.get("item") for r in reply.get("results", []) if r.get("op") == "get_item"), None)

    async def read_labels() -> dict[str, str] | None:
        if not inputs["snapshot"].get("labels"):
            return None
        return await label_display_map(job, timeout=ITEM_READ_TIMEOUT_SEC)

    # Independent reads: the extension answers them side by side.
    current, (snapshot, specifics, schema, unresolved, unfilled), names = await asyncio.gather(
        read_current(), prepare_listing_fields_for_vendoo(job, inputs["snapshot"]), read_labels(),
    )
    if not isinstance(current, dict) or (item_id and not current.get("_studio_update_time")):
        raise HTTPException(502, "Could not read the current Vendoo draft version. Reload the extension and try again.")
    images = (current.get("generalDetails") or {}).get("images") or []
    desired, build_unresolved = build_vendoo_item(snapshot, schema, images=images, specifics=specifics)
    # Compare label names, without creating labels before approval.
    if snapshot.get("labels") or current.get("labels"):
        if names is None:
            names = await label_display_map(job, timeout=ITEM_READ_TIMEOUT_SEC)
        current = deepcopy(current)
        current["labels"] = [names.get(str(label), str(label)) for label in current.get("labels", [])]
        desired["labels"] = [names.get(str(label), str(label)) for label in snapshot.get("labels", [])]
    if item_id:
        apply_update_all(
            current, desired, schema=schema, leave=marketplaces_left_empty(snapshot), listing=snapshot,
        )
    updates = changed_fields(current, desired)
    changes = [
        {"field": path, "before": _value(current, path), "after": value}
        for path, value in sorted(updates.items())
        if not path.endswith((".dateCreated", ".dateLastModified"))
    ]
    preparation = {
        "snapshot": snapshot,
        "schema": schema,
        "specifics": {mp: specs_to_rows(fields) for mp, fields in specifics.items()},
        "unresolved": unresolved,
        "unfilled": unfilled,
        "expected_version": current.get("_studio_update_time"),
    }
    review_id = remember_review(inputs, fingerprint, preparation)
    warnings = [f"{row.get('field', 'Field')}: {row.get('value') or row.get('reason') or 'check this value'}" for row in [*unresolved, *build_unresolved]]
    if unfilled:
        warnings.append(f"{len(unfilled)} category fields remain empty. Sending keeps these fields empty.")
    return {
        "review_id": review_id,
        "revision_id": inputs["revision_id"],
        "mode": "update" if item_id else "create",
        "changes": changes,
        "photo_count": len(images) if item_id else len(inputs["photo_ids"]),
        "photo_action": "keep" if item_id else "upload",
        "warnings": list(dict.fromkeys(warnings)),
    }
