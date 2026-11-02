"""Field gaps Ask chat can answer."""

from __future__ import annotations

import json
from copy import deepcopy

from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services.fill_log import (
    field_intentionally_empty,
    field_lookup_key,
    listing_value_for_field,
    prompt_options,
)
from vendoo_studio.services.hidden_fields import hidden_fields, normalize_field_name
from vendoo_studio.services.listing_field_gaps import collect_empty_discovered_fields
from vendoo_studio.services.marketplaces import get_selected_marketplaces
from vendoo_studio.services.registry import SELLER_SETTING_LABELS, RegistryService, is_account_managed_field
from vendoo_studio.services.vendoo_specifics import is_not_applicable


def _field_id(field: dict) -> tuple[str, str]:
    return str(field.get("marketplace") or "general"), field_lookup_key(field.get("field") or "")


def _field_out_of_scope(marketplace: str, label: str) -> bool:
    """Seller/account rows chat never fills: settings, shipping, policies."""
    return field_lookup_key(label) in SELLER_SETTING_LABELS or is_account_managed_field(marketplace, label)


def chat_field_context(db: Session, conv_id: str) -> str:
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return ""
    listing = deepcopy(revisions[0].listing_json)
    RegistryService(db).merge_learned_fields(listing)
    selected = {"general", *get_selected_marketplaces()}
    hidden = hidden_fields(conv_id)
    hidden_keys = {
        (entry["marketplace"], normalize_field_name(entry["field"]))
        for entry in hidden["always"] + hidden["listing"]
    }
    rows: dict[tuple[str, str], dict] = {}

    def add(field: dict, source: str) -> None:
        marketplace = str(field.get("marketplace") or "general").lower()
        label = str(field.get("field") or "").strip()
        if (
            not label or marketplace not in selected
            or (marketplace, normalize_field_name(label)) in hidden_keys
            or _field_out_of_scope(marketplace, label)
        ):
            return
        if not field.get("required") and field_intentionally_empty(listing, marketplace, label):
            return
        if not field.get("required") and is_not_applicable(listing_value_for_field(
            listing, marketplace, label, include_not_applicable=True,
        )):
            return
        value = listing_value_for_field(listing, marketplace, label)
        row = {
            "marketplace": marketplace, "field": label,
            "listing_value": value, "source": source,
            "action": "keep_value" if value else "generate_value",
            "required": bool(field.get("required")),
        }
        key = _field_id(row)
        previous = rows.get(key, {})
        raw_options = field.get("options") or previous.get("options") or []
        labels = [
            str(option.get("label") or option.get("value") or "")
            if isinstance(option, dict) else str(option)
            for option in raw_options
        ]
        options, complete = prompt_options(
            labels,
            f"{listing.get('title', '')} {listing.get('description', '')}",
        )
        if options:
            row["options"] = options
            row["options_complete"] = complete and field.get(
                "options_complete", previous.get("options_complete", True),
            )
        row["required"] = row["required"] or previous.get("required", False)
        rows[key] = row

    for gap in collect_empty_discovered_fields(db, listing):
        add(gap, "listing_schema")

    return (
        "\n\n--- Listing field gaps ---\n"
        "These are the current listing gaps. "
        "When asked to complete or fix fields, generate or repair supportable values using photos, "
        "seller notes, and exact allowed options. Preserve unrelated fields. "
        "Use a fenced JSON object with missing_fields rows (marketplace, field, value) "
        "so Anteroom saves the answers. If an optional field truly does not apply, return value \"\" "
        "and status \"not_applicable\" to clear it and remember the decision. "
        "Do not mark missing evidence or an unsupported dropdown value as not applicable. "
        "For a request targeting specific fields, change only those fields. "
        "A keep_value row already has a listing value: preserve it unless the seller asks to change it. "
        "Saving chat answers updates Anteroom only; never claim a marketplace listing was changed "
        "by chat. Do not invent unsupported facts. Never publish.\n"
        + json.dumps(list(rows.values()), ensure_ascii=False)
    )
