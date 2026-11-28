"""The category step of generation: choose the shared category and each marketplace's leaf."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from sqlalchemy.orm import Session

from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import listing_persistence
from anteroom.services.vendoo_import import parse_notes

DEFAULT_CONDITION = "Good"
# The title a draft starts with until generation writes one.
PLACEHOLDER_TITLE = "Draft listing"
# What the saved category was chosen from: a retry with the same photo reading
# and Item Details (so the same department and category override) keeps it.
CATEGORY_INPUTS_KEY = "_category_inputs"


def category_inputs(analysis: str, notes: str) -> str:
    return hashlib.sha256(json.dumps([analysis, notes]).encode()).hexdigest()


def seed_general_fields(snapshot: dict, notes: str | None = None) -> dict:
    """Give the first generated revision a condition, title, ZIP and quantity to start from."""
    if not isinstance(snapshot, dict):
        return {}
    out = snapshot
    parsed = parse_notes(notes)
    if not str(out.get("condition") or "").strip():
        condition = str(parsed.get("condition") or "").strip() or DEFAULT_CONDITION
        out["condition"] = condition
    if not str(out.get("title") or "").strip():
        out["title"] = PLACEHOLDER_TITLE
    if not str(out.get("zipCode") or out.get("zip_code") or "").strip():
        from anteroom.services.user_settings import get_ebay_inventory_location

        location = get_ebay_inventory_location()
        if location:
            out["zipCode"] = location
    if out.get("quantity") in (None, ""):
        out["quantity"] = 1
    return out


def _schema_inputs(db: Session, conv_id: str, notes: str) -> tuple[dict, str, str | None]:
    revisions = ListingRepo(db).get_revisions(conv_id)
    seed = deepcopy(revisions[0].listing_json) if revisions else {}
    revision_id = revisions[0].id if revisions else ""
    conv = ConversationRepo(db).get(conv_id)
    return seed, revision_id, conv.notes if conv else notes


async def prepare_generation_schema(
    conv_id: str,
    provider,
    analysis: str,
    notes: str,
    *,
    on_status=None,
) -> dict:
    """Choose the shared category; native form builders own marketplace fields."""
    from anteroom.database import in_worker_session
    from anteroom.services.category_selection import select_categories
    from anteroom.services.direct_categories import attach_direct_categories

    if on_status:
        on_status("Choosing a category…")
    override = str(parse_notes(notes).get("categoryOverride") or "").strip()
    seed, revision_id, saved_notes = await in_worker_session(_schema_inputs, conv_id, notes)
    inputs = category_inputs(analysis, notes)
    if not (seed.get(CATEGORY_INPUTS_KEY) == inputs and str(seed.get("category_path") or "").strip()):
        paths = await select_categories(provider, analysis, notes, [], override)
        seed["category_path"] = paths["general"]
        seed[CATEGORY_INPUTS_KEY] = inputs
    seed_general_fields(seed, saved_notes)
    if on_status:
        on_status("Loading marketplace categories and fields…")
    await attach_direct_categories(seed, f"{analysis}\n{notes}")
    await in_worker_session(lambda db: listing_persistence.save_revision(
        db, conv_id, seed, source="category_analysis",
        parent_revision_id=revision_id or None,
        expected_revision_id=revision_id,
    ))
    seed["_schema_source"] = "native"
    return seed
