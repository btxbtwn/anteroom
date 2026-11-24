"""Listing and photo writes with validation and marketplace side effects.

Repositories retain row insertion, scalar projections and atomic revision
selection; this service owns the behavior surrounding those operations.
"""
from __future__ import annotations

import copy
from sqlalchemy import event
from sqlalchemy.orm import Session
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.listing import Listing, ListingRevision
from anteroom.repositories.queries import ConversationRepo, ListingRepo

@event.listens_for(Session, "after_commit")
def _publish_category_fills(db):
    if db.in_nested_transaction():
        return
    from anteroom.services.native_field_fill import category_saved
    for _transaction, args in db.info.pop("category_fills", []):
        category_saved(*args)


@event.listens_for(Session, "after_soft_rollback")
def _rollback_category_fills(db, previous_transaction):
    def rolled_back(transaction):
        while transaction is not None:
            if transaction is previous_transaction:
                return True
            transaction = transaction.parent
        return False
    db.info["category_fills"] = [(transaction, args) for transaction, args in db.info.get("category_fills", [])
                                 if not rolled_back(transaction)]


@event.listens_for(Session, "after_transaction_end")
def _discard_category_fills(db, transaction):
    if transaction.parent is None:
        db.info.pop("category_fills", None)


def _recheck_error_flag(db: Session, conv_id: str) -> None:
    """Re-check a listing a form save flagged, now that its inputs changed.

    Chat, repair, regeneration and Vendoo sync write revisions without
    touching the flag, and photos change its photo rule, so a listing fixed any
    other way would otherwise stay flagged forever.
    """
    from anteroom.services import native_contract
    from anteroom.services.validation import validate_listing

    listing = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
    if not listing or listing.validation_status != "error":
        return
    revision = db.get(ListingRevision, listing.current_revision_id) if listing.current_revision_id else None
    data = native_contract.copy_listing(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}
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
    from anteroom.models.marketplace_listing import (
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


def save_revision(db: Session, conv_id: str, listing_json: dict, source: str, parent_revision_id: str | None = None, *, expected_revision_id: str | None = None, commit: bool = True, use_saved_defaults: bool = False, depop_notes: dict | None = None, verbatim: bool = False) -> ListingRevision:
    """Save atomically; an empty expected id means no listing may exist yet.

    ``verbatim`` keeps ``listing_json`` as given (an earlier revision coming
    back), skipping the native-form and quantity rewrites against the current one.
    """
    repo = ListingRepo(db)
    from anteroom.services.native_field_fill import (
        FILL_ON_SOURCES,
        category_saved,
    )

    before: dict = {}
    current = repo.get_current(conv_id)
    previous = repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    before = previous.listing_json if previous is not None and isinstance(previous.listing_json, dict) else {}
    from anteroom.services import activity

    scope = activity.edit_fields.get()
    if scope is not None and source in FILL_ON_SOURCES:
        listing_json = activity.scoped_listing(before, listing_json, scope)
        if "direct_category_ids.depop" in scope:
            from anteroom.services.depop_category import align_depop_category

            # Derive Depop's other category keys from the accepted picker edit.
            align_depop_category(listing_json, before)
    from anteroom.services import choice_lists, native_contract

    # Auto-relist saves the previous revision with one marketplace price changed
    # and locked. Normalizing it as generated content would restore the old
    # locks and drop that price, so it is kept as written, like a schema refresh.
    if not verbatim and source not in {"native_schema", "auto_relist"} and (native_contract.KEY in before or use_saved_defaults):
        listing_json = native_contract.copy_listing(listing_json)
        if source in FILL_ON_SOURCES and native_contract.KEY in before:
            listing_json[native_contract.KEY] = choice_lists.compact_contracts(before[native_contract.KEY])
            listing_json[native_contract.EVIDENCE_KEY] = copy.deepcopy(before.get(native_contract.EVIDENCE_KEY) or {})
            native_contract.invalidate_dependents(before, listing_json)
            if source != "user_form":
                native_contract.constrain_generated(listing_json, before, freeze_categories=False,
                                                    use_saved_defaults=use_saved_defaults)
        else:
            # An agent's edit over MCP sets categories on purpose, like the seller's own (#2076).
            native_contract.constrain_generated(listing_json, before, freeze_categories=source not in {
                "native_fields", "native_categories", "vendoo_prepare", "category_analysis", "category_schema_cache", "mcp"},
                keep_evidence=source == "native_fields", use_saved_defaults=use_saved_defaults)
    elif native_contract.KEY in listing_json:
        compacted = choice_lists.compact_contracts(listing_json[native_contract.KEY])
        if compacted is not listing_json[native_contract.KEY]:
            listing_json = native_contract.copy_listing(listing_json)
            listing_json[native_contract.KEY] = compacted
    if depop_notes is not None:
        from anteroom.services.depop_measurements import sync_depop_measurements
        from anteroom.services.general_measurements import is_garment_measurement_path

        path = "depop_specifics.measurements"
        if scope is None or (
            any(is_garment_measurement_path(field) for field in scope)
            and not any(field == path or field.startswith(f"{path}.") for field in scope)
        ):
            # Reconcile accepted General changes after native answers have been restored.
            sync_depop_measurements(listing_json, depop_notes, clear_empty=True)
    from anteroom.services.poshmark_form import follow_general_quantity

    # After native normalization, which restores locked rows from `before`.
    if not verbatim:
        listing_json = follow_general_quantity(before, listing_json)
    from anteroom.services.pricing_rules import SNAPSHOT_RULES
    from anteroom.services.unsent_edits import normalized_listing_hash

    if SNAPSHOT_RULES in listing_json:
        listing_json = {key: value for key, value in listing_json.items() if key != SNAPSHOT_RULES}
    revision = repo.insert_revision(
        conv_id, listing_json, source, parent_revision_id,
        normalized_hash=normalized_listing_hash(listing_json),
    )

    if source == "model_refinement":
        from anteroom.services.chat_listing import recover_saved_chat_clears

        revision.listing_json = recover_saved_chat_clears(db, conv_id, revision)
        listing_json = revision.listing_json
        revision.normalized_hash = normalized_listing_hash(listing_json)

    repo.set_current_revision(conv_id, revision.id, expected_revision_id=expected_revision_id)
    if source not in {"native_schema", "auto_relist", "scheduled_publish", "sale_quantity_sync"}:
        from anteroom.services.draft_scheduling import unschedule_on_edit

        unschedule_on_edit(db, conv_id)
    elif source == "native_schema" and previous is not None:
        listing_row = db.query(Listing).filter(Listing.conversation_id == conv_id).first()
        if (
            listing_row is not None
            and listing_row.scheduled_status == "scheduled"
            and listing_row.scheduled_revision_id == previous.id
        ):
            meta_keys = {native_contract.KEY, native_contract.EVIDENCE_KEY}
            before_public = {k: v for k, v in before.items() if k not in meta_keys}
            after_public = {k: v for k, v in listing_json.items() if k not in meta_keys}
            if normalized_listing_hash(before_public) == normalized_listing_hash(after_public):
                listing_row.scheduled_revision_id = revision.id
            else:
                from anteroom.services.draft_scheduling import unschedule_on_edit

                unschedule_on_edit(db, conv_id)
    _recheck_error_flag(db, conv_id)
    _drop_fixed_marketplace_errors(db, conv_id, before, listing_json)

    title = (listing_json.get("title") or "").strip()
    if title:
        from anteroom.services.vendoo_import import parse_notes

        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if conv and not parse_notes(conv.notes).get("titlePinned"):
            conv.title = title

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(revision)
    if source in FILL_ON_SOURCES:
        # A newly chosen marketplace category has new fields for the listing agent to fill.
        if commit:
            category_saved(conv_id, before, listing_json, source)
        else:
            db.info.setdefault("category_fills", []).append(
                (db.get_nested_transaction() or db.get_transaction(),
                 (conv_id, copy.deepcopy(before), copy.deepcopy(listing_json), source)))
    return revision


def add_photo(db: Session, conv_id: str, original_filename: str, stored_filename: str, mime_type: str, size_bytes: int, checksum: str | None = None, width: int | None = None, height: int | None = None, marketplace: str | None = None) -> Photo:
    from anteroom.services.draft_scheduling import unschedule_on_edit

    photo = ConversationRepo(db).insert_photo(
        conv_id, original_filename, stored_filename, mime_type, size_bytes,
        checksum=checksum, width=width, height=height, marketplace=marketplace,
    )
    unschedule_on_edit(db, conv_id, reason="Photos changed after scheduling; review and reschedule before publishing.")
    _recheck_error_flag(db, conv_id)
    db.commit()
    db.refresh(photo)
    return photo


def delete_photo(db: Session, conv_id: str, photo_id: str) -> bool:
    from anteroom.services.draft_scheduling import unschedule_on_edit

    if not ConversationRepo(db).remove_photo(conv_id, photo_id):
        return False
    unschedule_on_edit(db, conv_id, reason="Photos changed after scheduling; review and reschedule before publishing.")
    _recheck_error_flag(db, conv_id)
    db.commit()
    return True


def reorder_photos(db: Session, conv_id: str, ordered_ids: list[str]) -> None:
    from anteroom.services.draft_scheduling import unschedule_on_edit

    ConversationRepo(db).update_photo_order(conv_id, ordered_ids)
    unschedule_on_edit(db, conv_id, reason="Photos changed after scheduling; review and reschedule before publishing.")
    db.commit()
