"""Etsy's listing form, as the editor's Etsy tab shows it.

The rows, their order and their sections come from the captured page in
``skills/list-this/references/etsy-native-form.json``. The category's
attributes are Etsy's own properties for that leaf. Every value is the one
Etsy's List sends (``services/etsy_listings.py``), so the tab never shows a
value List would not use.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from functools import lru_cache

from vendoo_studio.config import skills_dir
from vendoo_studio.services.seller_overrides import marketplace_text, price_for

log = logging.getLogger(__name__)

CATEGORY_PATH = "direct_category_ids.etsy"
PROPERTIES_PATH = "etsy_specifics.category_specifics"
# Each Etsy read the form waits on stops here; the rest of the form still opens.
ETSY_READ_TIMEOUT_S = 15.0
_SETTINGS_KEYS = {"shipping": "shipping_profile_id", "processing": "readiness_state_id", "returns": "return_policy_id"}


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((skills_dir() / "list-this/references/etsy-native-form.json").read_text())


@lru_cache(maxsize=1)
def category_choices() -> dict:
    """Etsy's category picker, the same for every listing."""
    rows = catalog()["categories"]
    return {"options": [row["path"] for row in rows], "option_values": {row["path"]: row["id"] for row in rows}}


def _row(captured: dict, value) -> dict:
    from vendoo_studio.services.native_editor import field

    extra = {key: copy.deepcopy(item) for key, item in captured.items()
             if key not in {"path", "label", "value", "settings_choice"}}
    if extra.get("options"):
        extra.setdefault("options_source", "marketplace")
    return field(captured["path"], captured.get("label", ""), value, **extra)


def _package(snapshot: dict) -> tuple[str, str]:
    """Item weight and packed size as List sends them, from Item Details."""
    from vendoo_studio.services.native_marketplaces import listing_package

    package = listing_package(snapshot)
    if not package:
        return "", ""
    sides = " × ".join(f"{side:g}" for side in (package["item_length"], package["item_width"], package["item_height"]))
    return f"{package['item_weight']:g} oz", f"{sides} in"


def _property_key(details: dict, name: str) -> str:
    """The key this listing already keeps the answer under ("sleeveLength" for Sleeve length)."""
    from vendoo_studio.services.etsy_listings import _key

    nested = details.get("category_specifics")
    if isinstance(nested, dict):
        found = next((key for key in nested if _key(key) == _key(name)), None)
        if found:
            return found
    return name


def property_rows(snapshot: dict, properties: list[dict], captured: dict) -> list[dict]:
    """One row per Etsy property of this category, showing the value List would assign."""
    from vendoo_studio.services.etsy_listings import _answers, _key, _match
    from vendoo_studio.services.native_editor import field

    details = snapshot.get("etsy_specifics")
    if not isinstance(details, dict):
        details = {}
    answers = _answers(snapshot)
    rows = []
    for prop in properties:
        name = prop["name"]
        if not name or "." in name:
            continue
        values = answers.get(_key(name)) or answers.get(_key(prop.get("alias") or "")) or []
        choices = prop.get("values") or []
        shown: list[str] = []
        for value in values:
            hit = _match(value, choices) if choices else {"label": value}
            label = hit["label"] if hit else value
            if label not in shown:
                shown.append(label)
        multi = bool(prop.get("multi"))
        rows.append(field(f"{PROPERTIES_PATH}.{_property_key(details, name)}", name, shown if multi else (shown[0] if shown else ""),
                          required=bool(prop.get("required")), multi=multi, max_values=prop.get("max_values"),
                          selection_only=bool(choices), options=list(dict.fromkeys(choice["label"] for choice in choices)),
                          options_source="marketplace", category_specific=True, section=captured["section"],
                          group=captured.get("group"), group_help=captured.get("group_help")))
    return rows


async def _properties(category: str) -> tuple[list[dict], str]:
    from vendoo_studio.services import marketplace_schemas

    if not category:
        return [], ""
    try:
        return await asyncio.wait_for(marketplace_schemas.etsy_properties(category), ETSY_READ_TIMEOUT_S), ""
    except Exception as exc:  # noqa: BLE001 - the rest of the form still opens; List reads them again
        log.info("Etsy properties for %s not loaded: %s", category, exc)
        return [], ("Etsy's attributes for this category could not be loaded. Connect Etsy in Settings → Connections, "
                    "then reopen the listing.")


async def _settings_choices() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Settings' shop profiles for new drafts, with their names when Etsy can be read."""
    from vendoo_studio.services import etsy_listings, saved_choices
    from vendoo_studio.services.marketplace_accounts import account_id
    from vendoo_studio.services.user_settings import get_etsy_listing_defaults

    try:
        defaults = await asyncio.to_thread(get_etsy_listing_defaults)
    except Exception:  # noqa: BLE001 - shown as not chosen; List checks Settings again
        defaults = {}
    names: dict[str, dict[str, str]] = {}
    if any(defaults.values()):
        try:
            account = await asyncio.wait_for(account_id("etsy"), ETSY_READ_TIMEOUT_S)
            profiles = await asyncio.wait_for(saved_choices.kept("etsy-profiles", account, etsy_listings.listing_profiles),
                                              ETSY_READ_TIMEOUT_S) if account else {}
            names = {kind: {str(item["id"]): str(item["title"]) for item in profiles.get(kind) or []} for kind in _SETTINGS_KEYS}
        except Exception as exc:  # noqa: BLE001 - the ids still go with List; only their names are missing
            log.info("Etsy shop profiles not loaded: %s", exc)
    return defaults, names


async def editor_form(snapshot: dict) -> dict:
    """Etsy's form rows for this listing, in the page's order."""
    from vendoo_studio.services.direct_categories import direct_category_id
    from vendoo_studio.services.etsy_fields import (etsy_what_raw, etsy_when_options, etsy_when_raw, etsy_when_to_store,
                                                    etsy_who_raw)
    from vendoo_studio.services.etsy_listings import etsy_quantity, list_fields

    category = direct_category_id(snapshot, "etsy")
    known = category in category_choices()["option_values"].values()
    (properties, property_note), (defaults, names) = await asyncio.gather(_properties(category), _settings_choices())
    details = snapshot.get("etsy_specifics")
    if not isinstance(details, dict):
        details = {}
    lists = list_fields(snapshot)
    weight, size = _package(snapshot)
    when = etsy_when_raw(details, snapshot)
    from vendoo_studio.services.user_settings import get_listing_field_defaults

    saved = get_listing_field_defaults()
    who = etsy_who_raw(details) or (saved.get("etsy_specifics.whoMade") or {}).get("label", "")
    what = etsy_what_raw(details) or (saved.get("etsy_specifics.whatIsIt") or {}).get("label", "")
    values = {
        CATEGORY_PATH: category,
        "etsy_specifics.whenMade": etsy_when_to_store(when, snapshot, etsy_when_options()) if when else "",
        "etsy_specifics.title": marketplace_text(snapshot, "etsy", "title"),
        "etsy_specifics.description": marketplace_text(snapshot, "etsy", "description"),
        "etsy_specifics.materials": lists.get("materials", []),
        "etsy_specifics.tags": lists.get("tags", []),
        "price": price_for(snapshot, "etsy"),
        "quantity": etsy_quantity(snapshot) or snapshot.get("quantity") or "",
        "etsy_specifics.itemWeight": weight,
        "etsy_specifics.itemSize": size,
        "etsy_specifics.whoMade": who,
        "etsy_specifics.whatIsIt": what,
    }
    rows: list[dict] = []
    for captured in catalog()["fields"]:
        path = captured["path"]
        if captured.get("input_type") == "category_properties":
            rows.extend(property_rows(snapshot, properties, captured))
            continue
        if path == CATEGORY_PATH:
            rows.append(_row({**captured, **category_choices()}, values[path]))
            continue
        kind = captured.get("settings_choice")
        if kind:
            chosen = str(defaults.get(_SETTINGS_KEYS[kind]) or "")
            shown = names.get(kind, {}).get(chosen, "Chosen in Settings" if chosen else "Not chosen yet")
            rows.append(_row(captured, shown))
            continue
        rows.append(_row(captured, values.get(path, captured.get("value", ""))))
    notes = [property_note] if property_note else []
    if category and not known:
        notes.append("This Etsy category is not in Anteroom's list. Choose the most specific category again.")
    return {"marketplace": "etsy", "native": True, "known": not category or known, "page_labels": True,
            "category_id": category if known else "", "fields": rows, "notes": notes}
