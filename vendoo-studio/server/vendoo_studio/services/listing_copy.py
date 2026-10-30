"""Create an independent local draft from saved listing fields and photos."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, Photo, new_id as new_conversation_id
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.listing_copy import ListingCopyReceipt
from vendoo_studio.models.schema import ListingSchema
from vendoo_studio.models.validation import validate_listing
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import photos
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.native_marketplaces import running_marketplace
from vendoo_studio.services.price_markdown import listing_busy
from vendoo_studio.services.vendoo_import import parse_notes

# Seller-entered inputs, never imported state, account bindings or history.
INPUT_KEYS = ("sellerNotes", "knownFlaws", "descriptionMeasurements", "garment", "measurements", "packageDimensions", "categoryOverride")
CATEGORY_KEYS = ("marketplace_categories", "marketplace_category_ids", "marketplace_category_objects", "direct_category_ids")
# Form answers may include custom category aspects, so preserve their keys while
# dropping operational identity that is not a reusable field answer.
OPERATIONAL_KEYS = frozenset(("listingid", "itemid", "draftid", "orderid", "lineid", "conversationid", "jobid", "offerid",
                              "listingurl", "drafturl", "vendooitemid", "vendoourl", "sku", "images", "photos", "media", "imageurls",
                              "status", "state", "video", "videos", "videoid", "videourl", "sellernotes", "privatenotes", "internalnotes",
                              "sellerprivateinfo", "sellerinventoryprivateinfo", "accesstoken", "refreshtoken", "token", "cookie", "authorization"))


class CopyError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class CopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_basis: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=200)
    sku: str = Field(default="", max_length=120)
    quantity: int = Field(default=1, ge=1, le=1000000)
    include_photos: bool = True
    include_cost: bool = False

    @field_validator("title", "sku", mode="before")
    @classmethod
    def trimmed(cls, value):
        return value.strip() if isinstance(value, str) else value


def _clean_answers(value):
    if isinstance(value, dict):
        return {key: _clean_answers(answer) for key, answer in value.items()
                if "".join(char for char in key.casefold() if char.isalnum()) not in OPERATIONAL_KEYS
                and not key.casefold().endswith("sku")}
    if isinstance(value, list):
        return [_clean_answers(answer) for answer in value]
    return copy.deepcopy(value)


def reusable_listing(raw: dict) -> dict:
    """Known contract fields plus saved category selections; no job snapshot metadata."""
    fields = {*ListingSchema.model_fields, *CATEGORY_KEYS, "vinted_specifics", "sellwild_specifics", "material"}
    return {key: _clean_answers(value) if key.endswith("_specifics") or key in CATEGORY_KEYS else copy.deepcopy(value)
            for key, value in raw.items() if key in fields}


def read_source(db: Session, conv_id: str):
    from vendoo_studio.services.service_context import current
    if current() is not None:
        from fastapi import HTTPException
        from vendoo_studio.services.background_tasks import require_idle
        try:
            require_idle(db, conv_id)
        except HTTPException as error:
            raise CopyError(str(error.detail), error.status_code) from None
    db.expire_all()
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise CopyError("Listing not found.", 404)
    if listing_busy(conv_id, db) or running_marketplace(db, conv_id):
        raise CopyError("Wait for this listing’s current action to finish before copying it.")
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    images = ConversationRepo(db).get_photos(conv_id)
    basis = hashlib.sha256(json.dumps([conv.id, conv.title, conv.notes, conv.status, conv.settled_at,
        revision.id if revision else None, [(image.id, image.stored_filename, image.checksum, image.display_order) for image in images]], default=str).encode()).hexdigest()
    return conv, revision, images, basis


def preview(db: Session, conv_id: str) -> dict:
    conv, revision, images, basis = read_source(db, conv_id)
    return {"source_id": conv.id, "source_title": conv.title or "Untitled", "expected_basis": basis,
            "photo_count": len(images), "has_listing": revision is not None,
            "title": f"Copy of {conv.title or 'Untitled'}"[:200]}


class BatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=300)


def batch_preview(db: Session, body: BatchPreviewRequest) -> dict:
    rows = []
    for conv_id in dict.fromkeys(body.conversation_ids):
        try:
            rows.append({**preview(db, conv_id), "skip_reason": None})
        except CopyError as exc:
            conv = db.get(Conversation, conv_id)
            title = (conv.title or "Untitled") if conv else f"Missing item ({conv_id})"
            rows.append({"source_id": conv_id, "source_title": title, "skip_reason": str(exc)})
    return {"rows": rows}


def copy_result(db: Session, source_id: str, request_id: str, *, source_kind: str = "listing") -> str:
    receipt = db.query(ListingCopyReceipt).filter_by(request_id=request_id).populate_existing().one_or_none()
    if receipt is None or receipt.source_id != source_id or receipt.source_kind != source_kind:
        raise CopyError("No completed copy was found for this request.", 404)
    if not receipt.conversation_id or db.get(Conversation, receipt.conversation_id) is None:
        raise CopyError("This copy was already created and then removed. Start a new copy if needed.", 410)
    return receipt.conversation_id


def completed_copy(db: Session, source_kind: str, source_id: str, request_id: str, fingerprint: str) -> str | None:
    receipt = db.query(ListingCopyReceipt).filter_by(request_id=request_id).populate_existing().one_or_none()
    if receipt is None:
        return None
    if receipt.source_kind != source_kind or receipt.source_id != source_id or receipt.fingerprint != fingerprint:
        raise CopyError("This request already created a different draft. Check its result or start a new review.")
    return copy_result(db, source_id, request_id, source_kind=source_kind)


def _copy_file(name: str, mime_type: str, created_files: list[str], target_id: str) -> dict:
    try:
        meta = photos.process_bytes(photo_store().read(name), Path(name).name, mime_type, collection_id=target_id)
        created_files.append(meta['stored_filename'])
    except (OSError, ValueError) as exc:
        raise CopyError("A saved photo is unavailable. Restore it or copy without photos.", 422) from exc
    return meta


def add_draft(db: Session, title: str, notes: dict, listing: dict, images: list[dict], source: str, *, identity: str) -> str:
    """Stage a clean local draft. The caller commits it with its creation receipt."""
    conv = Conversation(id=identity, title=title, notes=json.dumps(notes), status="draft")
    db.add(conv)
    db.flush()
    for index, image in enumerate(images):
        db.add(Photo(conversation_id=conv.id, display_order=index, **image))
    revision = ListingRevision(conversation_id=conv.id, listing_json=listing, source=source)
    db.add(revision)
    db.flush()
    validation = validate_listing(listing, len(images), require_photos=True)
    db.add(Listing(conversation_id=conv.id, current_revision_id=revision.id,
                   validation_status="valid" if validation.valid else "error", validation_errors=validation.errors + validation.warnings))
    return conv.id


def create_copy(db: Session, conv_id: str, body: CopyRequest) -> str:
    created_files = []
    target_id = new_conversation_id()
    fingerprint = hashlib.sha256(json.dumps([conv_id, body.model_dump(exclude={"request_id"})], sort_keys=True).encode()).hexdigest()
    with start_gate():
        existing = completed_copy(db, "listing", conv_id, body.request_id, fingerprint)
        if existing:
            return existing
        source, revision, images, basis = read_source(db, conv_id)
        if basis != body.expected_basis:
            raise CopyError("The source listing or its photos changed. Reload the review before copying.")
        raw = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
        data = reusable_listing(raw)
        data.update(sku=body.sku, quantity=body.quantity)
        if not body.include_cost:
            data.pop("cost", None)
        source_notes = parse_notes(source.notes)
        notes = {key: copy.deepcopy(source_notes[key]) for key in INPUT_KEYS if key in source_notes}
        notes.update(sku=body.sku, titlePinned=True)
        if isinstance(data.get("labels"), list):
            notes["vendooLabels"] = ", ".join(str(label) for label in data["labels"])
        if body.include_cost and data.get("cost") is not None:
            notes["cog"] = str(data["cost"])
        elif body.include_cost and source_notes.get("cog"):
            notes["cog"] = source_notes["cog"]
        if body.include_photos and len(images) > 20:
            raise CopyError("A copied listing can have at most 20 photos.", 422)
        try:
            copied_images = []
            for image in images if body.include_photos else []:
                meta = _copy_file(image.stored_filename, image.mime_type, created_files, target_id)
                if image.source_filename:
                    # The copy keeps the original under its edits, so it can be reset too.
                    meta["source_filename"] = _copy_file(image.source_filename, image.mime_type, created_files, target_id)["stored_filename"]
                    meta["edits"] = copy.deepcopy(image.edits)
                meta["original_filename"] = image.original_filename
                copied_images.append(meta)
            new_id = add_draft(db, body.title, notes, data, copied_images, "copy", identity=target_id)
            db.add(ListingCopyReceipt(request_id=body.request_id, fingerprint=fingerprint, source_kind="listing", source_id=conv_id, conversation_id=new_id))
            db.commit()
            return new_id
        except BaseException:
            db.rollback()
            for key in created_files:
                photo_store().delete(key)
            raise
