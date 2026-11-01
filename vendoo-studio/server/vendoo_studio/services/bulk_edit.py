"""Review and save selected local listing fields. No marketplace actions.

Category changes name a leaf the seller picked: the shared category and Depop's
from Anteroom's synced category trees, eBay's from eBay's own category search
(the same pickers the listing editor uses). A category Anteroom cannot verify
is refused before anything is reviewed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from contextlib import nullcontext
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from vendoo_studio.models.catalog import CategoryTreeNode
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing_values import marketplace_dropdown_forms
from vendoo_studio.models.schema import VALID_CONDITIONS
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.listing_editor import prepared_listing, save_listing_edit
from vendoo_studio.services.price_drop import apply_price_to_listing
from vendoo_studio.services.price_markdown import BUSY, CHANGED, listing_busy

MAX_ITEMS = 300
MAX_TAGS = 30
ListMode = Literal["keep", "add", "remove", "replace"]


def _colors() -> list[str]:
    return list(marketplace_dropdown_forms().get("vendoo", {}).get("primaryColor") or [])


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
    condition: str = Field(default="", min_length=1, max_length=60)
    primaryColor: str = Field(default="", min_length=1, max_length=40)  # noqa: N815 - listing key
    secondaryColor: str = Field(default="", max_length=40)  # noqa: N815 - listing key; blank clears it

    @field_validator("title", "description", "brand", "size", "sku", "condition", "primaryColor", "secondaryColor", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("condition")
    @classmethod
    def known_condition(cls, value):
        if value not in VALID_CONDITIONS:
            raise ValueError("Choose a condition from the list.")
        return value

    @field_validator("primaryColor", "secondaryColor")
    @classmethod
    def known_color(cls, value, info):
        if value and value not in _colors():
            raise ValueError("Choose a color from the list.")
        return value


class EbayCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[1-9]\d{0,11}$")
    path: str = Field(min_length=1, max_length=400)


class Categories(BaseModel):
    """Leaves the seller picked. Each one set replaces that category on every item."""
    model_config = ConfigDict(extra="forbid", strict=True)
    general: str | None = Field(default=None, min_length=1, max_length=200)
    ebay: EbayCategory | None = None
    depop: str | None = Field(default=None, pattern=r"^(kidswear|menswear|womenswear|everything-else)_.+$", max_length=200)


class Edits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: Values = Field(default_factory=Values)
    labels_mode: ListMode = "keep"
    labels: list[str] = Field(default_factory=list, max_length=30)
    tags_mode: ListMode = "keep"
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    category: Categories | None = None

    @field_validator("labels", "tags")
    @classmethod
    def clean_list(cls, values, info):
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 120 for value in cleaned):
            raise ValueError(f"{info.field_name.capitalize()} may have at most 120 characters.")
        return cleaned

    @model_validator(mode="after")
    def nonempty(self):
        categories = self.category.model_dump(exclude_none=True) if self.category else {}
        if not self.values.model_fields_set and self.labels_mode == "keep" and self.tags_mode == "keep" and not categories:
            raise ValueError("Choose at least one field, label, tag or category action.")
        if self.labels_mode in ("add", "remove") and not self.labels:
            raise ValueError("Enter labels to add or remove.")
        if self.tags_mode in ("add", "remove") and not self.tags:
            raise ValueError("Enter tags to add or remove.")
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


def verified_leaf(db: Session, marketplace: str, category_id: str, name: str) -> dict:
    node = db.get(CategoryTreeNode, (marketplace, category_id))
    if node is None or not node.is_leaf or node.has_children:
        raise HTTPException(400, f"Choose a {name} category from the list. Anteroom could not find that one as a final category.")
    return {"id": node.category_id, "path": node.path}


def resolve_categories(db: Session, edits: Edits) -> dict:
    """The verified leaves to write. Refuses a category the seller could not have picked."""
    chosen = edits.category
    if chosen is None:
        return {}
    resolved: dict = {}
    if chosen.general:
        resolved["general"] = verified_leaf(db, "general", chosen.general, "shared")
    if chosen.depop:
        resolved["depop"] = verified_leaf(db, "depop", chosen.depop, "Depop")
    if chosen.ebay:
        resolved["ebay"] = {"id": chosen.ebay.id, "path": chosen.ebay.path.strip()}
    return resolved


def _bucket(listing: dict, name: str) -> dict:
    return dict(listing[name]) if isinstance(listing.get(name), dict) else {}


def _apply_categories(updated: dict, categories: dict) -> None:
    general = categories.get("general")
    if general:
        updated["category_path"] = general["path"]
        updated["category_id"] = general["id"]
        # A resolved object or mapping for the old leaf would win over the new path.
        objects = _bucket(updated, "marketplace_category_objects")
        if objects.pop("general", None) is not None:
            updated["marketplace_category_objects"] = objects
        for name, value in (("marketplace_categories", general["path"]), ("marketplace_category_ids", general["id"])):
            bucket = _bucket(updated, name)
            if "general" in bucket:
                bucket["general"] = value
                updated[name] = bucket
    ebay = categories.get("ebay")
    if ebay:
        updated["direct_category_ids"] = {**_bucket(updated, "direct_category_ids"), "ebay": ebay["id"], "ebay_path": ebay["path"]}
    depop = categories.get("depop")
    if depop:
        # The editor's save moves every Depop key to this leaf and drops a size from the old one.
        updated["direct_category_ids"] = {**_bucket(updated, "direct_category_ids"), "depop": depop["id"]}


def _merged(listing: dict, key: str, mode: str, values: list[str], limit: int) -> list[str]:
    saved = listing.get(key)
    current = [str(value) for value in saved] if isinstance(saved, list) else []
    if mode == "add":
        merged = list(dict.fromkeys([*current, *values]))
    elif mode == "remove":
        merged = [value for value in current if value not in values]
    else:
        merged = values
    if len(merged) > limit:
        raise ValueError(f"The resulting listing has more than {limit} {key}.")
    return merged


def edit_values(listing: dict, edits: Edits, categories: dict | None = None) -> dict:
    updated = copy.deepcopy(listing)
    for field, value in edits.values.model_dump(exclude_unset=True).items():
        if field == "price":
            updated = apply_price_to_listing(updated, value)
        else:
            updated[field] = value
    if edits.labels_mode != "keep":
        updated["labels"] = _merged(listing, "labels", edits.labels_mode, edits.labels, 30)
    if edits.tags_mode != "keep":
        updated["tags"] = _merged(listing, "tags", edits.tags_mode, edits.tags, MAX_TAGS)
    _apply_categories(updated, categories or {})
    return updated


def edited(listing: dict, edits: Edits, categories: dict | None = None) -> dict:
    updated = edit_values(listing, edits, categories)
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
    categories = resolve_categories(db, body.edits)
    rows = []
    for conv_id in dict.fromkeys(body.conversation_ids):
        row = {"conversation_id": conv_id, "title": "Unavailable listing", "changes": [], "skip_reason": None}
        try:
            conv, revision = _read(db, conv_id)
            row["title"] = conv.title or "Untitled"
            updated = edited(revision.listing_json, body.edits, categories)
            row.update(expected_revision_id=revision.id, expected_basis=basis(conv, revision.id), expected_result=digest(updated),
                       changes=_changes(revision.listing_json, updated))
            if not row["changes"]:
                row["skip_reason"] = "Already has these values"
        except (ValueError, HTTPException) as exc:
            row["skip_reason"] = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        rows.append(row)
    return {"rows": rows}


def apply(db: Session, body: ApplyRequest) -> dict:
    from vendoo_studio.services.service_context import current
    from vendoo_studio.services.background_tasks import require_idle

    hosted = current() is not None
    categories = resolve_categories(db, body.edits)
    applied, skipped, seen = [], [], set()
    for reviewed in body.rows:
        conv_id = reviewed.conversation_id
        if conv_id in seen:
            continue
        seen.add(conv_id)
        try:
            # Hosted edits serialize on the owned row, rather than holding the
            # desktop browser's process-wide send gate across other accounts.
            with (nullcontext() if hosted else start_gate()):
                db.expire_all()
                if hosted:
                    require_idle(db, conv_id)
                conv, revision = _read(db, conv_id)
                if revision.id != reviewed.expected_revision_id or basis(conv, revision.id) != reviewed.expected_basis:
                    raise ValueError(CHANGED)
                updated = edited(revision.listing_json, body.edits, categories)
                if digest(updated) != reviewed.expected_result:
                    raise ValueError("The proposed changes differ from the review. Review them again")
                if updated == revision.listing_json:
                    raise ValueError("Already has these values")
                result = save_listing_edit(conv_id, edit_values(revision.listing_json, body.edits, categories), db, source="bulk_edit", expected_revision_id=revision.id)
            applied.append({"conversation_id": conv_id, "revision_id": result["revision_id"], "valid": result["validation"]["valid"]})
        except (ValueError, HTTPException) as exc:
            db.rollback()
            skipped.append({"conversation_id": conv_id, "reason": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)})
    return {"applied": applied, "skipped": skipped}
