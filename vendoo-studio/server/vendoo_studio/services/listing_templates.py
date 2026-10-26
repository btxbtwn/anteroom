"""Seller-selected saved fields; applying a template always requires local review."""
from __future__ import annotations

import copy
from datetime import UTC
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.listing_copy import ListingCopyReceipt
from vendoo_studio.models.listing_template import ListingTemplate
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.bulk_edit import basis, digest
from vendoo_studio.services.listing_copy import CopyError, add_draft, completed_copy, read_source, reusable_listing
from vendoo_studio.services.listing_editor import prepared_listing, save_listing_edit
from vendoo_studio.repositories.queries import RevisionConflict


class TemplateSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    expected_basis: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: list[str] = Field(min_length=1, max_length=60)


class TemplateSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    selection: TemplateSource | None = None
    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TemplateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    sku: str = Field(default="", max_length=120)
    quantity: int = Field(default=1, ge=1, le=1000000)

    @field_validator("title", "sku", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TemplateApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    expected_template_revision: int = Field(ge=1, strict=True)
    mode: str = Field(pattern=r"^(replace|fill_blank)$")
    # Present only when saving the reviewed result.
    expected_revision_id: str | None = None
    expected_basis: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_result: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class TemplateApproval(TemplateApply):
    @model_validator(mode="after")
    def reviewed(self):
        if not all((self.expected_revision_id, self.expected_basis, self.expected_result)):
            raise ValueError("Review the template changes before saving.")
        return self


def _row(db: Session, template_id: str) -> ListingTemplate:
    row = db.get(ListingTemplate, template_id, populate_existing=True)
    if row is None:
        raise CopyError("Template not found.", 404)
    return row


def view(row: ListingTemplate) -> dict:
    return {"id": row.id, "name": row.name, "revision": row.revision, "fields": row.fields,
            "updated_at": row.updated_at.replace(tzinfo=UTC).isoformat()}


def list_templates(db: Session) -> list[dict]:
    return [view(row) for row in db.query(ListingTemplate).order_by(ListingTemplate.name, ListingTemplate.id)]


def source_fields(db: Session, conv_id: str) -> dict:
    conv, revision, _photos, token = read_source(db, conv_id)
    fields = reusable_listing(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}
    # Templates never prescribe an item's identity or private seller notes.
    for key in ("sku", "internal_notes"):
        fields.pop(key, None)
    return {"source_id": conv.id, "source_title": conv.title or "Untitled", "expected_basis": token, "fields": fields}


def _selected_fields(db: Session, selection: TemplateSource) -> dict:
    current = source_fields(db, selection.source_id)
    if current["expected_basis"] != selection.expected_basis:
        raise CopyError("The source item changed. Reload its template review.")
    names = list(dict.fromkeys(selection.fields))
    if any(name not in current["fields"] for name in names):
        raise CopyError("Choose fields present in this source item.", 422)
    return {name: copy.deepcopy(current["fields"][name]) for name in names}


def save(db: Session, body: TemplateSave, template_id: str | None = None) -> dict:
    with start_gate():
        if template_id is None:
            if body.selection is None or body.expected_revision is not None:
                raise CopyError("Choose source fields for a new template.", 422)
            row = ListingTemplate(name=body.name, fields=_selected_fields(db, body.selection))
            db.add(row)
            db.commit()
            db.refresh(row)
            return view(row)
        row = _row(db, template_id)
        if row.revision != body.expected_revision:
            raise CopyError("The template changed. Reload it before saving.")
        fields = _selected_fields(db, body.selection) if body.selection else copy.deepcopy(row.fields)
        changed = db.execute(update(ListingTemplate).where(ListingTemplate.id == template_id, ListingTemplate.revision == body.expected_revision)
                             .values(name=body.name, fields=fields, revision=ListingTemplate.revision + 1, updated_at=utcnow()))
        if changed.rowcount != 1:
            db.rollback()
            raise CopyError("The template changed. Reload it before saving.")
        db.commit()
        return view(_row(db, template_id))


def remove(db: Session, template_id: str, expected_revision: int) -> dict:
    with start_gate():
        _row(db, template_id)
        result = db.execute(delete(ListingTemplate).where(ListingTemplate.id == template_id, ListingTemplate.revision == expected_revision))
        if result.rowcount != 1:
            db.rollback()
            raise CopyError("The template changed. Reload it before removing it.")
        db.commit()
    return {"ok": True}


def create_draft(db: Session, template_id: str, body: TemplateDraft) -> str:
    fingerprint = hashlib.sha256(json.dumps([template_id, body.model_dump(exclude={"request_id"})], sort_keys=True).encode()).hexdigest()
    with start_gate():
        existing = completed_copy(db, "template", template_id, body.request_id, fingerprint)
        if existing:
            return existing
        row = _row(db, template_id)
        if row.revision != body.expected_revision:
            raise CopyError("The template changed. Reload it before creating a draft.")
        fields = copy.deepcopy(row.fields)
        fields.update(sku=body.sku, quantity=body.quantity)
        notes = {"sku": body.sku, "titlePinned": True}
        if isinstance(fields.get("labels"), list):
            notes["vendooLabels"] = ", ".join(str(label) for label in fields["labels"])
        if fields.get("cost") is not None:
            notes["cog"] = str(fields["cost"])
        try:
            target = add_draft(db, body.title, notes, fields, [], "template")
            db.add(ListingCopyReceipt(request_id=body.request_id, fingerprint=fingerprint, source_kind="template", source_id=template_id, conversation_id=target))
            db.commit()
            return target
        except BaseException:
            db.rollback()
            raise


def apply_fields(listing: dict, fields: dict, mode: str) -> dict:
    result = copy.deepcopy(listing)
    for key, value in fields.items():
        if mode == "fill_blank" and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = apply_fields(result[key], value, mode)
        elif mode == "replace" or result.get(key) is None or result.get(key) == "" or result.get(key) == [] or result.get(key) == {}:
            result[key] = copy.deepcopy(value)
    return result


def _application(db: Session, template_id: str, body: TemplateApply):
    row = _row(db, template_id)
    if row.revision != body.expected_template_revision:
        raise CopyError("The template changed. Reload the review.")
    conv, revision, _photos, _token = read_source(db, body.conversation_id)
    if revision is None or not isinstance(revision.listing_json, dict):
        raise CopyError("Save listing fields before applying a template.", 422)
    raw = apply_fields(revision.listing_json, row.fields, body.mode)
    prepared = prepared_listing(revision.listing_json, raw) if raw != revision.listing_json else raw
    return conv, revision, raw, prepared


def preview_apply(db: Session, template_id: str, body: TemplateApply) -> dict:
    conv, revision, _raw, prepared = _application(db, template_id, body)
    changes = [{"field": key, "old": revision.listing_json.get(key), "new": prepared.get(key)}
               for key in sorted(prepared.keys() | revision.listing_json.keys()) if revision.listing_json.get(key) != prepared.get(key)]
    return {"conversation_id": conv.id, "title": conv.title or "Untitled", "changes": changes,
            "expected_revision_id": revision.id, "expected_basis": basis(conv, revision.id), "expected_result": digest(prepared)}


def apply(db: Session, template_id: str, body: TemplateApproval) -> dict:
    with start_gate():
        conv, revision, raw, prepared = _application(db, template_id, body)
        if revision.id != body.expected_revision_id or basis(conv, revision.id) != body.expected_basis or digest(prepared) != body.expected_result:
            raise CopyError("The item or proposed changes changed. Review them again.")
        if raw == revision.listing_json:
            return {"ok": True, "changed": False}
        try:
            result = save_listing_edit(conv.id, raw, db, source="template", expected_revision_id=revision.id)
        except RevisionConflict as exc:
            raise CopyError(str(exc)) from exc
        from vendoo_studio.models.listing_evidence import ListingCorrection

        # A reusable preset is not a fresh observation of this item's facts.
        db.query(ListingCorrection).filter_by(revision_id=result["revision_id"]).update({"source": "template"})
        db.commit()
        return {"ok": True, "changed": True, "revision_id": result["revision_id"], "validation": result["validation"]}
