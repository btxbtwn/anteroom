"""The editor form for each marketplace leaf a listing resolved.

``GET /api/conversations/{id}/vendoo-api/fields`` keeps its historical path: it
builds the eBay, Etsy and other marketplace field forms the editor shows.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo

router = APIRouter(tags=["listing-fields"])


def _fold_field_name(text: str) -> str:
    """"attribute_body-fit", "Body Fit" and "bodyFit" all name one control."""
    folded = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    return folded[9:] if folded.startswith("attribute") else folded


def _humanize_field_key(key: str) -> str:
    """"parcelSize" -> "Parcel Size". Keys already written as labels pass through."""
    text = re.sub(r"[_-]+", " ", str(key or "")).strip()
    if " " in text:
        return text[:1].upper() + text[1:]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return spaced[:1].upper() + spaced[1:]


def _scraped_only_fields(
    listing: dict,
    marketplace: str,
    static: dict[str, list[str]],
    fields: list[dict],
) -> list[dict]:
    """Form controls the category schema never mentions.

    Vendoo's ``category/specifics`` answers with a leaf's attributes alone, so a
    marketplace's own item-level controls — Depop material, style, age — are
    missing from a category that does not happen to list them, and the seller
    cannot see or clear a value validation is rejecting. The skill's form scrape
    has those controls with their options, so they are shown too.
    """
    from vendoo_studio.services.fill_log import listing_value_for_field

    seen = {_fold_field_name(row["key"]) for row in fields}
    seen |= {_fold_field_name(row["label"]) for row in fields}
    extra: list[dict] = []
    for key, options in (static or {}).items():
        folded = _fold_field_name(key)
        if not folded or folded in seen or not options:
            continue
        seen.add(folded)
        label = _humanize_field_key(key)
        extra.append({
            "key": key,
            "label": label,
            "value": (
                listing_value_for_field(listing, marketplace, key)
                or listing_value_for_field(listing, marketplace, label)
            ),
            "required": False,
            "multi": False,
            "selection_only": False,
            "options": list(options),
            "options_source": "reference",
            "on_form": False,
        })
    return extra


# Keys Studio keeps in <marketplace>_specifics that no form ever renders.
_NON_FORM_SPECIFIC_KEYS = frozenset({
    "categorypath", "categoryid", "categoryspecifics", "category",
})


def _listing_only_fields(
    listing: dict,
    marketplace: str,
    fields: list[dict],
) -> list[dict]:
    """Values this listing holds that neither the schema nor the scrape names.

    Without these a stray key — one an older run wrote, or one Vendoo renamed —
    stays in the listing JSON, keeps failing validation, and has no row the
    seller can clear it from.
    """
    raw = listing.get(f"{marketplace}_specifics")
    if not isinstance(raw, dict):
        return []
    seen = {_fold_field_name(row["key"]) for row in fields}
    seen |= {_fold_field_name(row["label"]) for row in fields}
    extra: list[dict] = []
    for key, value in raw.items():
        folded = _fold_field_name(key)
        if not folded or folded in seen or folded in _NON_FORM_SPECIFIC_KEYS:
            continue
        text = ", ".join(str(v).strip() for v in value if str(v).strip()) if isinstance(value, list) else str(value or "").strip()
        if not text:
            continue
        seen.add(folded)
        extra.append({
            "key": str(key),
            "label": _humanize_field_key(str(key)),
            "value": text,
            "required": False,
            "multi": isinstance(value, list),
            "selection_only": False,
            "options": [],
            "options_source": "saved",
            "on_form": False,
        })
    return extra


async def _with_live_schema(listing: dict, marketplace: str, fields: list[dict]) -> list[dict]:
    """eBay and Etsy editor rows gain the live schema. Other marketplaces are unchanged."""
    if marketplace not in ("ebay", "etsy"):
        return fields
    from vendoo_studio.services.direct_categories import overlay_live_fields

    return await overlay_live_fields(listing, marketplace, fields)


async def leaf_form(listing: dict, marketplace: str, category_id: str, specs: dict, static: dict) -> dict:
    """One marketplace's editor form for this listing's leaf, from its schema (empty when unknown)."""
    from vendoo_studio.services.fill_log import listing_value_for_field

    static_by_fold = {key.casefold(): values for key, values in static.items()}
    if not specs:
        # No schema for this leaf, live or cached. Show the marketplace's
        # own controls and whatever the listing holds, so the form is never
        # blank and a rejected value is still reachable.
        fallback = _scraped_only_fields(listing, marketplace, static, [])
        fallback.extend(_listing_only_fields(listing, marketplace, fallback))
        fallback = await _with_live_schema(listing, marketplace, fallback)
        fallback.sort(key=lambda row: row["label"])
        return {"marketplace": marketplace, "category_id": category_id, "known": False, "fields": fallback}
    from vendoo_studio.services.form_schema import field_applies, labels_for_spec

    fields = []
    for spec in specs.values():
        label = spec.display or spec.key
        options = labels_for_spec(spec, listing)
        options_source = "vendoo" if options and (spec.options or spec.scales) else "reference"
        if not options:
            # Category schema sometimes omits the closed list; fall back to
            # the skill scrape so the Forms editor still offers choices.
            options = list(
                static.get(spec.key)
                or static.get(label)
                or static_by_fold.get(str(spec.key).casefold())
                or static_by_fold.get(str(label).casefold())
                or []
            )
        fields.append({
            "key": spec.key,
            "label": label,
            "value": listing_value_for_field(listing, marketplace, label),
            "required": spec.required,
            "multi": spec.multi,
            "selection_only": spec.selection_only or bool(spec.scales),
            "options": options,
            "options_source": options_source,
            "on_form": field_applies(label, listing),
        })
    fields.extend(_scraped_only_fields(listing, marketplace, static, fields))
    fields.extend(_listing_only_fields(listing, marketplace, fields))
    fields = await _with_live_schema(listing, marketplace, fields)
    fields.sort(key=lambda row: (not row["required"], row["label"]))
    return {"marketplace": marketplace, "category_id": category_id, "known": True, "fields": fields}


@router.get("/api/conversations/{conv_id}/vendoo-api/fields")
async def listing_fields(conv_id: str, db: Session = Depends(get_db)):
    """Every field each marketplace can hold for this listing's categories.

    ``on_form`` is true for the fields that marketplace's listing form uses for
    this category, department, and condition — the same set the editor shows
    and chat is given. A row with ``on_form`` false stays here, and on the
    listing, so a saved value is never dropped. Choices are that marketplace's
    own options when it publishes a list.
    """
    from vendoo_studio.services.category_fields import listing_category_ids
    from vendoo_studio.services.leaf_schemas import leaf_specifics

    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # The newest version only: older ones can each be hundreds of KB.
    latest = ListingRepo(db).latest_revision(conv_id)
    listing = latest.listing_json if latest else {}
    # End the read transaction before the Mercari sizes fetch below: a session
    # that has queried keeps its pool connection until it commits.
    db.commit()

    from vendoo_studio.models.listing_values import marketplace_dropdown_forms

    static_forms = marketplace_dropdown_forms()
    out: list[dict] = []
    leaves = sorted(listing_category_ids(listing).items())
    fetched = await asyncio.gather(*(
        leaf_specifics(marketplace, category_id) for marketplace, category_id in leaves
    ))
    for (marketplace, category_id), specs in zip(leaves, fetched, strict=True):
        out.append(await leaf_form(listing, marketplace, category_id, specs, static_forms.get(marketplace) or {}))
    return {"ok": True, "forms": out}
