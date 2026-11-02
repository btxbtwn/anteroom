"""Listing and photo writes with validation and marketplace side effects.

Repositories retain row insertion, scalar projections and atomic revision
selection; this service owns the behavior surrounding those operations.
"""
from __future__ import annotations

import copy
from sqlalchemy.orm import Session
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo

def _recheck_error_flag(db: Session, conv_id: str) -> None:
    """Re-check a listing a form save flagged, now that its inputs changed.

    Chat, repair, regeneration and Vendoo sync write revisions without
    touching the flag, and photos change its photo rule, so a listing fixed any
    other way would otherwise stay flagged forever.
    """
    import copy

    from vendoo_studio.services.validation import validate_listing

    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if not listing or listing.validation_status != "error":
        return
    revision = db.get(ListingRevision, listing.current_revision_id) if listing.current_revision_id else None
    data = copy.deepcopy(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}
    photo_count = db.query(Photo).filter(Photo.conversation_id == conv_id, Photo.marketplace.is_(None)).count()
    validation = validate_listing(data, photo_count, require_photos=True)
    listing.validation_status = "valid" if validation.valid else "error"
    listing.validation_errors = validation.errors + validation.warnings


def _field_value(listing: dict, marketplace: str, field: str):
    """What a marketplace reads for ``field``: a dotted path, or its own copy of a top-level field."""
    if "." not in field:
        block = listing.get(f"{marketplace}_specifics")
        return block[field] if isinstance(block, dict) and field in block else listing.get(field)
    value = listing
    for part in field.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _field_unchanged(item, marketplace: str, before: dict, after: dict) -> bool:
    field = str(item.get("field") or "") if isinstance(item, dict) else ""
    # Settings live outside the listing; only a new List can tell they are fixed.
    if not field or field.startswith("settings."):
        return True
    return _field_value(before, marketplace, field) == _field_value(after, marketplace, field)


def _drop_fixed_marketplace_errors(db: Session, conv_id: str, before: dict, after: dict) -> None:
    """Clear a marketplace's "needs these fields" error once a save changes those fields.

    A List that stopped for missing fields keeps its error until the next List,
    so a category fixed in the editor or by chat would still show as missing.
    Each field this save changed leaves the error; the next List checks it again.
    """
    from vendoo_studio.models.marketplace_listing import (
        UNSETTLED_STATES,
        MarketplaceListing,
    )

    rows = db.query(MarketplaceListing).filter(
        MarketplaceListing.conversation_id == conv_id, MarketplaceListing.error_code == "missing_fields",
    ).all()
    for row in rows:
        if row.state in UNSETTLED_STATES or not isinstance(row.error_fields, list):
            continue
        left = [item for item in row.error_fields if _field_unchanged(item, row.marketplace, before, after)]
        if len(left) == len(row.error_fields):
            continue
        if left:
            row.error_fields = left
            row.error = " ".join(f"{str(item.get('message') or '').rstrip('.')}." for item in left if isinstance(item, dict))
        else:
            row.error = row.error_code = row.error_fields = row.marketplace_message = None


def save_revision(db: Session, conv_id: str, listing_json: dict, source: str, parent_revision_id: str | None = None, *, expected_revision_id: str | None = None) -> ListingRevision:
    """Save atomically; an empty expected id means no listing may exist yet."""
    repo = ListingRepo(db)
    from vendoo_studio.services.native_field_fill import (
        FILL_ON_SOURCES,
        category_saved,
    )

    before: dict = {}
    current = repo.get_current(conv_id)
    previous = repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    before = previous.listing_json if previous is not None and isinstance(previous.listing_json, dict) else {}
    from vendoo_studio.services import activity

    scope = activity.edit_fields.get()
    if scope is not None and source in FILL_ON_SOURCES:
        listing_json = activity.scoped_listing(before, listing_json, scope)
    from vendoo_studio.services import native_contract

    if source != "native_schema" and native_contract.KEY in before:
        listing_json = copy.deepcopy(listing_json)
        if source in FILL_ON_SOURCES:
            listing_json[native_contract.KEY] = copy.deepcopy(before[native_contract.KEY])
            listing_json[native_contract.EVIDENCE_KEY] = copy.deepcopy(before.get(native_contract.EVIDENCE_KEY) or {})
            native_contract.invalidate_dependents(before, listing_json)
            if source != "user_form":
                native_contract.constrain_generated(listing_json, before, freeze_categories=False)
        else:
            native_contract.constrain_generated(listing_json, before, freeze_categories=source not in {
                "native_fields", "native_categories", "vendoo_prepare", "category_analysis", "category_schema_cache"},
                keep_evidence=source == "native_fields")
    revision = repo.insert_revision(conv_id, listing_json, source, parent_revision_id)

    if source == "model_refinement":
        from vendoo_studio.services.chat_listing import recover_saved_chat_clears

        revision.listing_json = recover_saved_chat_clears(db, conv_id, revision)
        listing_json = revision.listing_json

    repo.set_current_revision(conv_id, revision.id, expected_revision_id=expected_revision_id)
    _recheck_error_flag(db, conv_id)
    _drop_fixed_marketplace_errors(db, conv_id, before, listing_json)

    title = (listing_json.get("title") or "").strip()
    if title:
        from vendoo_studio.services.vendoo_import import parse_notes

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if conv and not parse_notes(conv.notes).get("titlePinned"):
            conv.title = title

    db.commit()
    db.refresh(revision)
    if source in FILL_ON_SOURCES:
        # A newly chosen marketplace category has new fields for the listing agent to fill.
        category_saved(conv_id, before, listing_json, source)
    return revision


def add_photo(db: Session, conv_id: str, original_filename: str, stored_filename: str, mime_type: str, size_bytes: int, checksum: str | None = None, width: int | None = None, height: int | None = None, marketplace: str | None = None) -> Photo:
    photo = ConversationRepo(db).insert_photo(
        conv_id, original_filename, stored_filename, mime_type, size_bytes,
        checksum=checksum, width=width, height=height, marketplace=marketplace,
    )
    _recheck_error_flag(db, conv_id)
    db.commit()
    db.refresh(photo)
    return photo


def delete_photo(db: Session, conv_id: str, photo_id: str) -> bool:
    if not ConversationRepo(db).remove_photo(conv_id, photo_id):
        return False
    _recheck_error_flag(db, conv_id)
    db.commit()
    return True
