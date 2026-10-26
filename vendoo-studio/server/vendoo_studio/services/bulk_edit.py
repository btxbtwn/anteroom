"""Review and save selected local listing fields. No marketplace actions."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.listing_editor import prepared_listing, save_listing_edit
from vendoo_studio.services.price_drop import apply_price_to_listing
from vendoo_studio.services.price_markdown import BUSY, CHANGED, listing_busy

MAX_ITEMS = 300


class Values(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(default="", min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    brand: str = Field(default="", max_length=120)
    size: str = Field(default="", min_length=1, max_length=40)
    sku: str = Field(default="", max_length=120)
    price: int = Field(default=1, ge=1, le=1000000)
    cost: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    quantity: int = Field(default=1, ge=1, le=1000000)

    @field_validator("title", "description", "brand", "size", "sku", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Edits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: Values = Field(default_factory=Values)
    labels_mode: Literal["keep", "add", "remove", "replace"] = "keep"
    labels: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, values):
        labels = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(label) > 120 for label in labels):
            raise ValueError("Labels may have at most 120 characters.")
        return labels

    @model_validator(mode="after")
    def nonempty(self):
        if not self.values.model_fields_set and self.labels_mode == "keep":
            raise ValueError("Choose at least one field or label action.")
        if self.labels_mode in ("add", "remove") and not self.labels:
            raise ValueError("Enter labels to add or remove.")
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[str] = Field(min_length=1, max_length=MAX_ITEMS)
    edits: Edits


class ReviewedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    expected_revision_id: str
    expected_basis: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_result: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: Edits
    rows: list[ReviewedRow] = Field(min_length=1, max_length=MAX_ITEMS)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def basis(conv: Conversation, revision_id: str) -> str:
    return digest([revision_id, conv.notes, conv.status, conv.settled_at, conv.box_id])


def edit_values(listing: dict, edits: Edits) -> dict:
    updated = copy.deepcopy(listing)
    for field, value in edits.values.model_dump(exclude_unset=True).items():
        if field == "price":
            updated = apply_price_to_listing(updated, value)
        else:
            updated[field] = value
    if edits.labels_mode != "keep":
        current = listing.get("labels") if isinstance(listing.get("labels"), list) else []
        current = [str(value) for value in current]
        if edits.labels_mode == "add":
            labels = list(dict.fromkeys([*current, *edits.labels]))
        elif edits.labels_mode == "remove":
            labels = [label for label in current if label not in edits.labels]
        else:
            labels = edits.labels
        if len(labels) > 30:
            raise ValueError("The resulting listing has more than 30 labels.")
        updated["labels"] = labels
    return updated


def edited(listing: dict, edits: Edits) -> dict:
    updated = edit_values(listing, edits)
    return prepared_listing(listing, updated) if updated != listing else updated


def _read(db: Session, conv_id: str):
    conv = db.get(Conversation, conv_id, populate_existing=True)
    if conv is None:
        raise ValueError("It is no longer in Anteroom")
    from vendoo_studio.services.native_marketplaces import running_marketplace

    if listing_busy(conv_id, db) or running_marketplace(db, conv_id):
        raise ValueError(BUSY)
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    if revision is None or not isinstance(revision.listing_json, dict):
        raise ValueError("It has no saved listing fields yet")
    return conv, revision


def _changes(before: dict, after: dict) -> list[dict]:
    return [{"field": field, "old": before.get(field), "new": after.get(field)}
            for field in sorted(before.keys() | after.keys()) if before.get(field) != after.get(field)]


def preview(db: Session, body: PreviewRequest) -> dict:
    rows = []
    for conv_id in dict.fromkeys(body.conversation_ids):
        row = {"conversation_id": conv_id, "title": "Unavailable listing", "changes": [], "skip_reason": None}
        try:
            conv, revision = _read(db, conv_id)
            row["title"] = conv.title or "Untitled"
            updated = edited(revision.listing_json, body.edits)
            row.update(expected_revision_id=revision.id, expected_basis=basis(conv, revision.id), expected_result=digest(updated),
                       changes=_changes(revision.listing_json, updated))
            if not row["changes"]:
                row["skip_reason"] = "Already has these values"
        except (ValueError, HTTPException) as exc:
            row["skip_reason"] = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        rows.append(row)
    return {"rows": rows}


def apply(db: Session, body: ApplyRequest) -> dict:
    applied, skipped, seen = [], [], set()
    for reviewed in body.rows:
        conv_id = reviewed.conversation_id
        if conv_id in seen:
            continue
        seen.add(conv_id)
        try:
            with start_gate():
                db.expire_all()
                conv, revision = _read(db, conv_id)
                if revision.id != reviewed.expected_revision_id or basis(conv, revision.id) != reviewed.expected_basis:
                    raise ValueError(CHANGED)
                updated = edited(revision.listing_json, body.edits)
                if digest(updated) != reviewed.expected_result:
                    raise ValueError("The proposed changes differ from the review. Review them again")
                if updated == revision.listing_json:
                    raise ValueError("Already has these values")
                result = save_listing_edit(conv_id, edit_values(revision.listing_json, body.edits), db, source="bulk_edit", expected_revision_id=revision.id)
            applied.append({"conversation_id": conv_id, "revision_id": result["revision_id"], "valid": result["validation"]["valid"]})
        except (ValueError, HTTPException) as exc:
            db.rollback()
            skipped.append({"conversation_id": conv_id, "reason": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)})
    return {"applied": applied, "skipped": skipped}
