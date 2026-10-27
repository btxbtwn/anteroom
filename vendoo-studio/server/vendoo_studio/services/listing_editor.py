"""The listing editor's shared local preparation and revision save."""
from __future__ import annotations

import copy

from fastapi import HTTPException
from sqlalchemy.orm import Session

from vendoo_studio.models.validation import normalize_listing_dropdowns, validate_listing
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo

CORRUPT_SPECIFIC_KEYS = (
    "ebay_specifics",
    "depop_specifics",
    "etsy_specifics",
    "poshmark_specifics",
    "mercari_specifics",
)


def reject_corrupt_listing(listing: dict) -> None:
    if not isinstance(listing, dict):
        raise HTTPException(422, "Listing must be an object")
    for key in CORRUPT_SPECIFIC_KEYS:
        value = listing.get(key)
        if value is not None and not isinstance(value, dict):
            raise HTTPException(422, f"{key} must be an object")


def prepared_listing(previous: dict, listing: dict, *, notes: dict | None = None) -> dict:
    from vendoo_studio.services.chrome_catalog import keep_direct_categories
    from vendoo_studio.services.depop_category import align_depop_category
    from vendoo_studio.services.listing_generate import align_size_fields, sanitize_listing_sizes
    from vendoo_studio.services.seller_overrides import prepare_user_listing, preserving_locks

    updated = copy.deepcopy(listing)
    reject_corrupt_listing(updated)
    keep_direct_categories(previous, updated)
    align_depop_category(updated, previous)
    prepare_user_listing(previous, updated, notes=notes)
    sanitize_listing_sizes(updated)
    previous_size = None if previous.get("size") is None else str(previous.get("size"))

    def _normalize(current: dict) -> None:
        align_size_fields(current, previous_size=previous_size)
        normalize_listing_dropdowns(current)

    preserving_locks(updated, _normalize)
    return updated


def save_listing_edit(conv_id: str, listing_data: dict, db: Session, *, source: str = "user_form", expected_revision_id: str | None = None):
    from vendoo_studio.models.job import ACTIVE_JOB_STATUSES
    from vendoo_studio.repositories.queries import JobRepo
    from vendoo_studio.services.schema_probe import is_schema_probe_job

    conv_repo = ConversationRepo(db)
    if not conv_repo.get(conv_id):
        raise HTTPException(404, "Conversation not found")

    listing_repo = ListingRepo(db)
    current = listing_repo.get_current(conv_id)

    photo_count = len(conv_repo.get_photos(conv_id))
    reject_corrupt_listing(listing_data)
    from vendoo_studio.services.listing_evidence import explicit_changes, record_correction

    previous = listing_repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    previous_json = previous.listing_json if previous and isinstance(previous.listing_json, dict) else {}
    changes = explicit_changes(previous_json, listing_data)
    from vendoo_studio.services.vendoo_import import parse_notes

    listing_data = prepared_listing(previous_json, listing_data, notes=parse_notes(conv_repo.get(conv_id).notes))
    from vendoo_studio.services.agent_categories import map_general_category

    map_general_category(db, previous_json, listing_data)
    from vendoo_studio.services.seller_overrides import capture_locked, restore_locked

    # Validation rewrites dropdown keys. A value the seller owns is put back.
    locked = capture_locked(listing_data)
    validation = validate_listing(listing_data, photo_count, require_photos=True)
    restore_locked(listing_data, locked)
    from vendoo_studio.services.vendoo_marketplace_fields import best_offer_error

    offer_problem = best_offer_error(listing_data)
    if offer_problem:
        raise HTTPException(400, offer_problem)

    # Generation pins the SKU in Item Details over the model's, so a SKU the
    # seller edits here goes there too, or Regenerate would bring the old one back.
    revisions = listing_repo.get_revisions(conv_id)
    previous_sku = str((revisions[0].listing_json if revisions else {}).get("sku") or "").strip()
    sku = str(listing_data.get("sku") or "").strip()

    revision = listing_repo.save_revision(
        conv_id=conv_id,
        listing_json=listing_data,
        source=source,
        parent_revision_id=current.current_revision_id if current else None,
        expected_revision_id=expected_revision_id,
    )

    from vendoo_studio.services.seller_overrides import notes_for_listing_edits

    note_updates = notes_for_listing_edits(previous_json, listing_data)
    if sku != previous_sku:
        note_updates["sku"] = sku
    # Item Details feeds snapshots and regeneration. Keep explicitly changed
    # costs and labels level with the same fields saved by the editor.
    if listing_data.get("cost") != previous_json.get("cost"):
        note_updates["cog"] = "" if listing_data.get("cost") is None else str(listing_data["cost"])
    if listing_data.get("labels") != previous_json.get("labels"):
        note_updates["vendooLabels"] = ", ".join(str(label) for label in listing_data.get("labels") or [])
    if note_updates:
        from vendoo_studio.services.vendoo_import import merge_notes

        conv_repo.write_notes(conv_id, merge_notes(conv_repo.get(conv_id).notes, note_updates))


    record_correction(db, conv_id, revision.id, listing_data, changes, source="template" if source == "template" else "user_form")

    if current:
        current.validation_status = "valid" if validation.valid else "error"
        current.validation_errors = validation.errors + validation.warnings

    # Keep unreviewed Vendoo preparation on the edited form. A direct action
    # owns the revision already sent, including after a lost reply or failure.
    refresh_statuses = set(ACTIVE_JOB_STATUSES) | {"failed"}
    for job in JobRepo(db).list_by_conversation(conv_id):
        if (job.status not in refresh_statuses or is_schema_probe_job(job)
                or str(job.current_step or "").startswith("native_")):
            continue
        if JobRepo(db).latest_event(job.id, "vendoo_review"):
            continue  # A reviewed Send owns its approved snapshot, even after edits.
        platforms = (job.listing_snapshot or {}).get("platforms") if isinstance(job.listing_snapshot, dict) else None
        snapshot = dict(listing_data)
        if isinstance(platforms, list):
            snapshot["platforms"] = platforms
        job.listing_snapshot = snapshot

    db.commit()

    return {
        "ok": True,
        "revision_id": revision.id,
        "validation": validation.model_dump(),
    }

