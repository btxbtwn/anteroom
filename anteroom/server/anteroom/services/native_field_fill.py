"""Fill each direct marketplace's category fields from the form its tab shows.

The listing agent first completes missing or nonterminal categories from the
native forms, then reloads those forms (the rows, labels, choices and limits
the seller sees) and fills the empty item fields supported by the evidence.
Every value is checked against the row's choices before it is written, the
seller's own and locked values are left alone, and nothing is sent to a
marketplace: this only saves a listing revision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from anteroom.services import listing_persistence
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services.seller_overrides import field_locked, fold_key, get_path, is_locked, set_path
from anteroom.services import native_contract

log = logging.getLogger("anteroom.native_field_fill")

MARKETS = native_contract.MARKETS
NAMES = {"ebay": "eBay", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "etsy": "Etsy"}
CATEGORY_PATHS = {f"direct_category_ids.{market}" for market in MARKETS}

FILL_RULES = (
    "You fill a resale listing's marketplace form fields. For selection-only fields, choose values only "
    "from that field's listed choices, spelled exactly as listed. For free-text fields, the listed "
    "choices are suggestions: custom text is allowed within the field's limits. Answer only when "
    "the photos analysis, listing or notes show it. Complete required fields first. For a required free-text field, "
    "use a short supported description when the suggestions do not fit. Leave out any field you cannot tell. Never guess a measurement, material "
    "or fit that the evidence does not show. "
    "Use a list only where a field allows more than one choice, and never more than its limit. "
    "Depop's Source, Age and Style describe what kind of item it is, its era and its look: always answer "
    "them with the closest listed choices, quoting the words they rest on (a decade, vintage, preloved, "
    "graphic tee, the cut or print)."
)

# Depop leaves these optional, but a listing without them is harder to find,
# so the agent always answers them, and falls back to the listing's own words.
DEPOP_TAGS = ("depop_specifics.source", "depop_specifics.age", "depop_specifics.style")


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _path(row: dict) -> str:
    return str(row.get("path") or row.get("key") or "")


def fillable_rows(form: dict, market: str, listing: dict, *, evidence: str = "") -> list[dict]:
    """Empty or unverified item fields the agent may choose values for."""
    prefix = f"{market}_specifics."
    strict = native_contract.KEY in listing
    etsy_attributes_locked = market == "etsy" and is_locked(listing, ETSY_ATTRIBUTES.rstrip("."))
    rows = []
    for row in form.get("fields") or []:
        row = native_contract.applicable_row(row, listing, form.get("fields") or [])
        path = _path(row)
        options = row.get("options") or []
        if not path.startswith(prefix) or row.get("locked") or (not options and not strict):
            continue
        if not native_contract.item_field(row, market) or (not strict and not _empty(row.get("value"))):
            continue
        # Legacy Etsy attributes go through fill_etsy_attributes for quote validation.
        if market == "etsy" and path.startswith(ETSY_ATTRIBUTES) and (not strict or etsy_attributes_locked):
            continue
        if market == "etsy" and fold_key(str(row.get("label") or "")) in _FROM_GENERAL:
            continue
        if row.get("input_type") in {"note", "badge", "photos", "inventory", "variations"}:
            continue
        if is_locked(listing, path) or field_locked(listing, market, path.rsplit(".", 1)[-1]):
            continue
        if strict:
            value = get_path(listing, path)
            proof = (listing.get(native_contract.EVIDENCE_KEY) or {}).get(path) or {}
            quote = proof.get("quote")
            # Refilling dependent fields must not re-roll answers already
            # verified against the same evidence and still valid on this form.
            if (not _empty(value) and proof.get("value") == value
                    and isinstance(quote, str) and len(quote.strip()) >= 3 and quote in evidence
                    and not native_contract.value_error(row, value)):
                continue
        rows.append(row)
    return rows


def category_rows(form: dict, listing: dict) -> list[dict]:
    """Unselected categories, using only terminal choices from the native form."""
    rows = []
    for row in form.get("fields") or []:
        path = _path(row)
        if path not in CATEGORY_PATHS or row.get("locked") or is_locked(listing, path):
            continue
        options = row.get("options") or []
        parents = set()
        for label in options:
            parts = label.split(" > ")
            parents.update(" > ".join(parts[:end]) for end in range(1, len(parts)))
        leaves = [label for label in options if label not in parents]
        values = row.get("option_values") or {}
        current = str(row.get("value") or "").strip()
        if not leaves or current and any(current in (label, str(values.get(label, label))) for label in leaves):
            continue
        rows.append({**row, "options": leaves, "required": True})
    return rows


def _limit(row: dict) -> int:
    if not row.get("multi"):
        return 1
    try:
        return max(1, int(row.get("max_values") or 0)) if row.get("max_values") else len(row.get("options") or [])
    except (TypeError, ValueError):
        return 1


def forms_prompt(rows_by_market: dict[str, list[dict]], forms: dict[str, dict]) -> str:
    blocks = []
    for market, rows in rows_by_market.items():
        category = str(forms[market].get("category_id") or "")
        lines = []
        for row in rows:
            limit = _limit(row)
            pick = f"pick up to {limit}" if row.get("multi") else "pick one"
            mode = "selection-only" if row.get("selection_only") else "free-text; custom text allowed"
            if row.get("max_length") is not None:
                mode += f"; max length {row['max_length']}"
            label = str(row.get("label") or "").removesuffix(" *")
            requirement = "required" if row.get("required") or _path(row) in DEPOP_TAGS else "optional"
            from anteroom.services.option_lookup import project_row

            projected = project_row(row)
            choices = (json.dumps({"options_lookup": projected["options_lookup"]}) if projected.get("options_lookup")
                       else "; ".join(row.get("options") or []) or f"{row.get('input_type') or 'text'}; max length {row.get('max_length') or 'unspecified'}")
            lines.append(f"- {_path(row)} | {label} | {requirement} | {mode} | {pick} | choices: " + choices)
        blocks.append(f"### {NAMES[market]} (category {category})\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _json_object(text: str) -> dict | None:
    candidates = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text or "", re.I)
    if match := re.search(r"\{[\s\S]*\}", text or ""):
        candidates.append(match.group())
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def validated_values(reply: dict, rows: dict[str, dict], *, evidence: str | None = None) -> dict[str, tuple[Any, list[str]]]:
    """Validate choices, or evidence-backed custom text when the native field allows it."""
    answers = reply.get("fields")
    if not isinstance(answers, dict):
        answers = {}
    accepted: dict[str, tuple[Any, list[str]]] = {}
    for path, raw in answers.items():
        row = rows.get(str(path))
        if row is None:
            continue
        if evidence is not None:
            if not isinstance(raw, dict):
                continue
            quote = raw.get("evidence")
            if not isinstance(quote, str) or len(quote.strip()) < 3 or quote not in evidence:
                continue
            raw = raw.get("value")
            if not row.get("selection_only"):
                if raw not in (None, "", [], {}) and not native_contract.value_error(row, raw):
                    accepted[str(path)] = (raw, [str(raw)])
                continue
        by_fold = {fold_key(option): option for option in row.get("options") or []}
        for label, stored in (row.get("option_values") or {}).items():
            if label in (row.get("options") or []):
                by_fold[fold_key(str(stored))] = label
        picked = raw if isinstance(raw, list) else [raw]
        labels = list(dict.fromkeys(by_fold[fold_key(item)] for item in picked
                                    if isinstance(item, (str, int, float)) and fold_key(str(item)) in by_fold))
        if evidence is not None and (len(labels) > _limit(row) or len(labels) != len(picked)):
            continue
        labels = labels[:_limit(row)]
        if not labels:
            continue
        ids = [str((row.get("option_values") or {}).get(label, label)) for label in labels]
        accepted[str(path)] = (ids if row.get("multi") else ids[0], labels)
    return accepted


def _clear_etsy_property_aliases(bucket: dict, want: str, *, keep: str | None = None) -> None:
    """Drop differently-spelled Etsy property keys from category_specifics and top-level etsy_specifics."""
    from anteroom.services.etsy_listings import _NOT_PROPERTIES, _key

    nested = bucket.get("category_specifics")
    if isinstance(nested, dict):
        nested = dict(nested)
        for alias in [name for name in nested if name != keep and fold_key(name) == want]:
            del nested[alias]
        bucket["category_specifics"] = nested
    for alias in [name for name, val in bucket.items()
                  if not isinstance(val, dict) and _key(name) not in _NOT_PROPERTIES and fold_key(name) == want]:
        del bucket[alias]


def write_value(listing: dict, path: str, value: Any) -> None:
    """Store a form value the way the editor does, replacing a differently spelled copy of the key."""
    parts = path.split(".")
    if len(parts) == 2 and parts[0].endswith("_specifics"):
        bucket = listing.get(parts[0])
        bucket = dict(bucket) if isinstance(bucket, dict) else {}
        want = fold_key(parts[1])
        for alias in [name for name in bucket if name != parts[1] and fold_key(name) == want]:
            del bucket[alias]
        bucket[parts[1]] = value
        listing[parts[0]] = bucket
        return
    if len(parts) == 3 and parts[0] == "etsy_specifics" and parts[1] == "category_specifics":
        bucket = listing.get("etsy_specifics")
        bucket = dict(bucket) if isinstance(bucket, dict) else {}
        _clear_etsy_property_aliases(bucket, fold_key(parts[2]), keep=parts[2])
        nested = bucket.get("category_specifics")
        nested = dict(nested) if isinstance(nested, dict) else {}
        nested[parts[2]] = value
        bucket["category_specifics"] = nested
        listing["etsy_specifics"] = bucket
        return
    set_path(listing, path, value)


def clear_value(listing: dict, path: str) -> None:
    """Drop a form answer and any differently spelled copy, so the row is unanswered.

    A stored blank reads as the seller clearing the row, which stops General
    (size, brand) from answering it. Only the seller's own edit stores one.
    """
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "etsy_specifics" and parts[1] == "category_specifics":
        bucket = listing.get("etsy_specifics")
        if not isinstance(bucket, dict):
            return
        _clear_etsy_property_aliases(bucket, fold_key(parts[2]))
        return
    parent = get_path(listing, ".".join(parts[:-1]))
    if not isinstance(parent, dict):
        return
    want = fold_key(parts[-1]) if len(parts) == 2 and parts[0].endswith("_specifics") else None
    for name in [name for name in parent if name == parts[-1] or want is not None and fold_key(name) == want]:
        del parent[name]


def _keep_depop_tags(listing: dict, rows: dict[str, dict]) -> None:
    """Depop's Source, Age and Style always have an answer. The evidence check
    leaves blank what the model did not quote (photos never show "Modern"), so
    a blank one gets the default generation uses, spelled as the form stores it."""
    from anteroom.models.schema import VALID_DEPOP_STYLE
    from anteroom.services.depop_fields import depop_default_age, depop_default_source, infer_depop_styles

    for path in DEPOP_TAGS:
        row = rows.get(path)
        if row is None or not _empty(get_path(listing, path)):
            continue
        if path.endswith(".source"):
            wanted = [depop_default_source(listing)]
        elif path.endswith(".age"):
            wanted = [depop_default_age(listing)]
        else:
            # Rank every style, keep those this form offers, then apply its limit.
            wanted = infer_depop_styles(listing, limit=len(VALID_DEPOP_STYLE))
        values = row.get("option_values") or {}
        by_fold = {}
        for label in row.get("options") or []:
            by_fold.setdefault(fold_key(label), label)
            by_fold.setdefault(fold_key(str(values.get(label, label))), label)
        labels = list(dict.fromkeys(by_fold[fold_key(item)] for item in wanted if fold_key(item) in by_fold))
        stored = [str(values.get(label, label)) for label in labels[:_limit(row)]]
        if stored:
            write_value(listing, path, stored if row.get("multi") else stored[0])


async def load_forms(conv_id: str, markets: Iterable[str] = MARKETS) -> dict[str, dict]:
    """Each named direct marketplace's current form. One that cannot be read is skipped."""
    from anteroom.services.native_editor import editor_form

    forms = {}
    for market in (market for market in MARKETS if market in set(markets)):
        try:
            form = await editor_form(conv_id, market, None, refresh=True)
        except Exception as exc:  # noqa: BLE001 - one marketplace's form never stops the others
            log.info("No %s form to fill for %s: %s", market, conv_id, exc)
            continue
        if isinstance(form, dict) and form.get("known") is not False and not form.get("properties_unavailable"):
            forms[market] = form
    return forms


def _current_revision(db: Session, conv_id: str):
    repo = ListingRepo(db)
    saved = repo.get_current(conv_id)
    return repo.get_revision(saved.current_revision_id) if saved else None


def _revision_data(db: Session, conv_id: str) -> tuple[str, dict] | None:
    revision = _current_revision(db, conv_id)
    return (revision.id, revision.listing_json) if revision is not None else None


def _current_listing(db: Session, conv_id: str) -> dict | None:
    revision = _current_revision(db, conv_id)
    return revision.listing_json if revision is not None else None


def _contract_fresh(listing: dict, market: str) -> bool:
    contract = (listing.get(native_contract.KEY) or {}).get(market)
    if not isinstance(contract, dict) or contract.get("unavailable") or "page_labels" not in contract:
        return False
    fields = contract.get("fields") or []
    return bool(fields) and native_contract.inputs_match(listing, market, fields, contract.get("inputs"))


def _stale_markets(listing: dict | None, markets: set[str], *, include_unavailable: bool = True) -> set[str]:
    """Markets whose saved contract is missing, unavailable, or no longer matches current inputs."""
    if listing is None:
        return set(markets)
    contracts = listing.get(native_contract.KEY) or {}
    stale = set()
    for market in markets:
        contract = contracts.get(market)
        if not include_unavailable and isinstance(contract, dict) and contract.get("unavailable"):
            continue
        if not _contract_fresh(listing, market):
            stale.add(market)
    return stale


async def fill_native_fields(conv_id: str, provider, *, evidence: str,
                             markets: Iterable[str] = MARKETS, expected_photo_fingerprint: str | None = None) -> dict | None:
    """Choose categories, then fill item fields using evidence tied to the supplied photos, if any."""
    from anteroom.database import in_worker_session

    markets = set(markets)
    listing = await in_worker_session(_current_listing, conv_id)
    strict = bool(listing is not None and native_contract.KEY in listing)
    if strict and listing is not None:
        markets &= set(listing[native_contract.KEY])
    categories = await _fill_native_fields(conv_id, provider, evidence=evidence, markets=markets, categories=True,
                                          expected_photo_fingerprint=expected_photo_fingerprint)
    if strict:
        current = await in_worker_session(_current_listing, conv_id)
        stale = _stale_markets(current, markets, include_unavailable=True)
        if stale:
            await record_forms(conv_id, stale)
    fields = None
    required_only = False
    for _round in range(3 if strict else 1):
        before = await in_worker_session(_current_listing, conv_id) or {}
        fields = await _fill_native_fields(conv_id, provider, evidence=evidence, markets=markets,
                                           categories=False, required_only=required_only,
                                           expected_photo_fingerprint=expected_photo_fingerprint)
        dependencies_changed = fields is not None and any(
            native_contract.inputs(before, market, contract.get("fields") or [])
            != native_contract.inputs(fields, market, contract.get("fields") or [])
            for market, contract in (before.get(native_contract.KEY) or {}).items())
        if not strict or fields is None:
            break
        required_paths = {_path(row) for market, contract in (fields.get(native_contract.KEY) or {}).items()
                          if market in markets for row in contract.get("fields") or []
                          if row.get("required") and native_contract.item_field(row, market)}
        missing_required = any(gap["field"] in required_paths for market in markets
                               for gap in native_contract.gaps(fields, market))
        if not dependencies_changed and not missing_required:
            break
        # Another repair would resend the same fields, evidence and choices.
        # Ask again only when a changed answer brought new choices.
        if required_only and not dependencies_changed:
            break
        required_only = not dependencies_changed
        stale = _stale_markets(fields, markets, include_unavailable=False)
        if stale:
            await record_forms(conv_id, stale)
    if strict:
        current = await in_worker_session(_current_listing, conv_id)
        stale = _stale_markets(current, markets, include_unavailable=True)
        if stale:
            await record_forms(conv_id, stale)
            current = await in_worker_session(_current_listing, conv_id)
        return current
    return fields if fields is not None else categories


async def prepare_generation_forms(conv_id: str, provider, *, evidence: str) -> dict:
    """Select categories and capture the exact native forms before listing prose is generated."""
    from anteroom.services.marketplaces import get_selected_marketplaces

    markets = set(MARKETS) & set(get_selected_marketplaces())
    await _fill_native_fields(conv_id, provider, evidence=evidence, markets=markets, categories=True)
    return await record_forms(conv_id, markets, reset_evidence=True)


async def record_forms(conv_id: str, markets, *, reset_evidence: bool = False) -> dict:
    """Save current form definitions and their effective values onto the same revision snapshot."""
    from anteroom.database import in_worker_session

    current = await in_worker_session(_revision_data, conv_id)
    if current is None:
        return {}
    revision_id, saved = current
    forms = await load_forms(conv_id, markets)
    return await in_worker_session(_record_forms, conv_id, revision_id, saved, forms, markets, reset_evidence)


def _record_forms(db: Session, conv_id: str, revision_id: str, saved: dict, forms: dict[str, dict], markets,
                  reset_evidence: bool) -> dict:
    latest = ListingRepo(db).get_current(conv_id)
    if not latest or latest.current_revision_id != revision_id:
        return {}
    from anteroom.services import choice_lists

    listing = native_contract.copy_listing(saved)
    if reset_evidence:
        listing[native_contract.EVIDENCE_KEY] = {}
    for market, form in forms.items():
        for row in form.get("fields") or []:
            path = _path(row)
            value = row.get("value")
            if path in CATEGORY_PATHS and form.get("category_id") and not get_path(listing, path) and not _empty(value):
                write_value(listing, path, str(form["category_id"]))
            sale_field = row.get("locked") or native_contract.is_sale_field(path)
            if (path.startswith(f"{market}_specifics.") and ".package." not in path and sale_field
                    and not is_locked(listing, path)
                    and not (market == "etsy" and row.get("locked"))
                    and path not in CATEGORY_PATHS and not _empty(value) and not native_contract.value_error(row, value)):
                write_value(listing, path, value)
    listing[native_contract.KEY] = {**choice_lists.compact_contracts(listing.get(native_contract.KEY) or {}),
                                    **native_contract.capture(listing, forms, markets)}
    # Keep a separate seed so normalization cannot erase answers it must restore.
    native_contract.constrain_generated(listing, native_contract.copy_listing(listing), freeze_categories=False)
    if listing != saved:
        listing_persistence.save_revision(db, conv_id, listing, source="native_schema", parent_revision_id=revision_id, expected_revision_id=revision_id)
    return listing[native_contract.KEY]


def _fill_inputs(db: Session, conv_id: str):
    """The photos' fingerprint and the current revision, or None when there is nothing to fill."""
    from anteroom.services.listing_generate import photo_fingerprint

    revision = _current_revision(db, conv_id)
    if revision is None or not isinstance(revision.listing_json, dict):
        return None
    return photo_fingerprint(ConversationRepo(db).get_photos(conv_id)), revision.id, revision.listing_json


def _note(db: Session, conv_id: str, text: str) -> None:
    ConversationRepo(db).add_message(conv_id, "system", text, provider="system", model="")


async def _fill_native_fields(conv_id: str, provider, *, evidence: str,
                              markets: Iterable[str], categories: bool, required_only: bool = False,
                              expected_photo_fingerprint: str | None = None) -> dict | None:
    """Save picks; a valid strict check returns its snapshot even when unchanged."""
    from anteroom.database import in_worker_session

    if provider is None:
        return None
    inputs = await in_worker_session(_fill_inputs, conv_id)
    if inputs is None:
        return None
    fingerprint, revision_id, listing = inputs
    # The save already checks this fingerprint again. Do not adopt new photos
    # between passes when the evidence was read from a different set.
    if expected_photo_fingerprint is not None and fingerprint != expected_photo_fingerprint:
        return None
    has_contract = native_contract.KEY in listing
    strict = has_contract and not categories
    # Finishing just captured these definitions. Reuse them while their
    # controlling inputs match; parent/category edits still need fresh forms.
    requested = set(markets)
    forms = {market: form for market in MARKETS
             if has_contract and market in requested and (form := native_contract.editor_form(listing, market)) is not None}
    missing = requested - forms.keys()
    if missing:
        loaded = await load_forms(conv_id, missing)
        forms.update(loaded)
        if strict and loaded:
            # Record freshly loaded contracts before filling so save_revision's
            # constrain_generated validates against them instead of stale/unavailable ones.
            recorded = await in_worker_session(
                _record_forms, conv_id, revision_id, listing, loaded, loaded.keys(), False)
            if not recorded:
                return None
            refreshed = await in_worker_session(_fill_inputs, conv_id)
            if refreshed is None or refreshed[0] != fingerprint:
                return None
            fingerprint, revision_id, listing = refreshed
            # A seller may edit controlling inputs after recording. Use only
            # definitions that belong to the revision we will fill and fence.
            refreshed_forms = {market: form for market in forms
                               if (form := native_contract.editor_form(listing, market)) is not None}
            if refreshed_forms.keys() != forms.keys():
                return None
            forms = refreshed_forms
    rows_by_market = {market: rows for market, form in forms.items()
                      if (rows := category_rows(form, listing) if categories else fillable_rows(form, market, listing, evidence=evidence))}
    if required_only:
        rows_by_market = {market: required_rows for market, rows in rows_by_market.items()
                          if (required_rows := [row for row in rows if row.get("required")])}
    if not rows_by_market:
        return None
    rows = {_path(row): row for market_rows in rows_by_market.values() for row in market_rows}
    reply = await _ask(conv_id, provider, rows_by_market, forms, listing, evidence, strict=strict, categories=categories)
    if reply is None:
        return None
    accepted = validated_values(reply, rows, evidence=evidence if strict else None)
    if not accepted and not strict:
        return None
    return await in_worker_session(_save_fill, conv_id, revision_id, fingerprint, listing, forms, rows, reply,
                                   accepted, strict, categories)


async def _ask(conv_id: str, provider, rows_by_market: dict[str, list[dict]], forms: dict[str, dict], listing: dict,
               evidence: str, *, strict: bool, categories: bool) -> dict | None:
    """The model's answers for these form rows, or None (with a chat note) when it fails or replies unreadably."""
    from anteroom.database import in_worker_session
    from anteroom.services import activity
    from anteroom.services.catalog_index import relevant_skill_rules
    from anteroom.services.listing_generate import collect_provider_text

    rows = [_path(row) for market_rows in rows_by_market.values() for row in market_rows]
    summary = {key: listing.get(key) for key in (
        "title", "description", "brand", "category_path", "department", "size", "primaryColor",
        "secondaryColor", "material", "condition") if not _empty(listing.get(key))}
    query = " ".join([*rows, str(summary.get("category_path") or "")])
    rules = await asyncio.to_thread(relevant_skill_rules, None, query, max_chars=40000)
    shape = ('{"fields":{"<field path>":{"value":"<choice>","evidence":"<exact quote>"}}}'
             if strict else '{"fields":{"<field path>":"<choice>"}}')
    messages = [
        {"role": "system", "content": f"--- Canonical listing rules ---\n{rules}\n\n"
            "The native fields and choices below override generic marketplace templates. " + FILL_RULES + (
            " Category is required on every form. Choose the most specific supported category for each item "
            "from the listed choices; do not skip it or ask the seller to choose. If none fits, leave it "
            "unresolved so listing remains blocked." if categories else "") + (
            ' Copy each exact quote of at least three characters from the Evidence block. '
            ' For fields that accept multiple choices, use a list in "value". '
            "Recheck every listed field, including existing values. Omit unsupported answers; they will be cleared. "
            "Use only the Evidence block as factual support. The existing listing is context, not evidence. "
            "Sold comps support price only, never this item's product facts." if strict else "")
            + f" Reply with one JSON object in this format: {shape}."},
        {"role": "user", "content": (
            f"--- Fields to fill ---\n{forms_prompt(rows_by_market, forms)}\n\n"
            f"--- Evidence ---\n{evidence}\n\n"
            f"--- Listing ---\n{json.dumps(summary, ensure_ascii=False)}"
        )},
    ]
    work = activity.begin(conv_id, "Filling marketplace fields…")
    work.fields = tuple(rows)
    reply = None
    text = ""
    try:
        from anteroom.services.option_lookup import OptionCatalog

        catalog = OptionCatalog({market: {"fields": market_rows} for market, market_rows in rows_by_market.items()})
        for attempt in range(2):
            text = await collect_provider_text(provider, messages, option_catalog=catalog)
            reply = _json_object(text)
            if isinstance(reply, dict) and isinstance(reply.get("fields"), dict):
                break
            if not attempt:
                messages = [*messages, {"role": "assistant", "content": text}, {"role": "user", "content": (
                    f"Return only valid JSON in this format: {shape}. "
                    'Use {"fields":{}} if no answer is supported. Do not invent facts or choices.'
                )}]
    except Exception:
        log.exception("marketplace field fill request failed for %s", conv_id)
        await in_worker_session(
            _note, conv_id,
            "Could not fill marketplace category fields. Your saved listing is unchanged. "
            "Ask chat to fill the remaining fields, or review them in each marketplace tab.",
        )
        return None
    finally:
        activity.end(work)
    if reply is None or not isinstance(reply.get("fields"), dict):
        # A failed/truncated response is not an evidence check that rejected
        # every existing answer. Leave the revision untouched.
        log.warning("unreadable marketplace field response for %s (%s characters)", conv_id, len(text))
        await in_worker_session(
            _note, conv_id, "The agent returned an unreadable marketplace field response. "
            "Your saved listing is unchanged. Ask chat to fill the remaining fields.",
        )
        return None
    return reply


def _save_fill(db: Session, conv_id: str, revision_id: str, fingerprint: str, listing: dict, forms: dict[str, dict],
               rows: dict[str, dict], reply: dict, accepted: dict, strict: bool, categories: bool) -> dict | None:
    from anteroom.services.listing_generate import photo_fingerprint

    latest = ListingRepo(db).get_current(conv_id)
    if photo_fingerprint(ConversationRepo(db).get_photos(conv_id)) != fingerprint:
        return None
    if not latest or latest.current_revision_id != revision_id:
        # The seller edited, cleared or regenerated while the forms were read.
        return None
    updated = native_contract.copy_listing(listing)
    no_brand_path = "mercari_specifics.noBrand"
    brand_path = "mercari_specifics.brand"
    no_brand, _ = accepted.get(no_brand_path, (None, []))
    # An explicit checked box wins over a brand in the same response.
    if no_brand is True:
        accepted = {path: answer for path, answer in accepted.items() if path != brand_path}
    changed_parents = {path for path, (value, _labels) in accepted.items() if get_path(listing, path) != value}
    changed_labels = {rows[path].get("label") for path in changed_parents}
    dependent_paths = {dependent for path in changed_parents if path != no_brand_path
                       for dependent in rows[path].get("clears") or []}
    if no_brand is True:
        dependent_paths.add(brand_path)
    elif no_brand is False:
        # A brand and an unchecked box are compatible and can both be saved.
        dependent_paths.discard(no_brand_path)
    form_rows = {_path(row): row for form in forms.values() for row in form.get("fields") or []}
    dependent_paths.update(path for path, row in form_rows.items() for rules in (row.get("value_constraints") or {}).values()
                           for rule in rules if rule.get("parentName") in changed_labels)
    accepted = {path: answer for path, answer in accepted.items() if path not in dependent_paths}
    for dependent in dependent_paths:
        if not is_locked(updated, dependent):
            dep_row = form_rows.get(dependent) or {}
            empty = (
                {} if dependent.endswith(".attributes")
                else [] if dependent.endswith(".inventoryRows")
                else False if dep_row.get("input_type") == "boolean"
                else ""
            )
            write_value(updated, dependent, empty)
            (updated.get(native_contract.EVIDENCE_KEY) or {}).pop(dependent, None)
    if strict:
        # An omitted or unsupported earlier guess must not survive the evidence check.
        for path in rows:
            clear_value(updated, path)
            (updated.get(native_contract.EVIDENCE_KEY) or {}).pop(path, None)
    for path, (value, _labels) in accepted.items():
        write_value(updated, path, value)
        if strict:
            updated.setdefault(native_contract.EVIDENCE_KEY, {})[path] = {
                "value": copy.deepcopy(value), "quote": reply["fields"][path]["evidence"]}
    _keep_depop_tags(updated, rows)
    if updated == listing:
        return updated if strict else None
    revision = listing_persistence.save_revision(db, conv_id, updated, source="native_categories" if categories else "native_fields", parent_revision_id=revision_id, expected_revision_id=revision_id)
    _note(db, conv_id, filled_message(accepted, rows))
    return revision.listing_json if isinstance(revision.listing_json, dict) else updated


def filled_message(accepted: dict[str, tuple[Any, list[str]]], rows: dict[str, dict]) -> str:
    by_market: dict[str, list[str]] = {}
    for path, (_value, labels) in accepted.items():
        market = path.rsplit(".", 1)[-1] if path.startswith("direct_category_ids.") else path.split("_specifics.", 1)[0]
        label = str(rows[path].get("label") or path).removesuffix(" *")
        by_market.setdefault(NAMES.get(market, market), []).append(f"{label}: {', '.join(labels)}")
    count = len(accepted)
    lines = [f"Filled {count} marketplace category field{'s' if count != 1 else ''}:"]
    lines += [f"- {market}: " + "; ".join(items) for market, items in by_market.items()]
    return "\n".join(lines)


# --- Etsy's category attributes --------------------------------------------
#
# Etsy's tab is built locally (services/etsy_form.py) and has no captured
# native contract: its other keys (styles, profiles, renewal) are read by List
# as they are. Only its category attributes (Sleeve length, Neckline, ...) are
# item facts, so they get the same evidence-checked fill as the other forms.

ETSY_ATTRIBUTES = "etsy_specifics.category_specifics."


# Etsy reads these from General's colors (etsy_listings._answers). A copy of
# its own would stop the row following a later General edit.
_FROM_GENERAL = frozenset(("primarycolor", "secondarycolor"))


def _unanswered(row: dict) -> bool:
    """Empty, or only an earlier automatic answer this category's choices do not offer (List drops it)."""
    value = row.get("value")
    if _empty(value):
        return True
    options = row.get("options") or []
    if not row.get("selection_only") or not options:
        return False
    return not any(item in options for item in (value if isinstance(value, list) else [value]))


def etsy_attribute_rows(form: dict, listing: dict) -> list[dict]:
    """Etsy's unanswered category attributes the seller has not set.

    Editing any attribute in the Etsy tab locks the whole attribute group
    (``lock_seller_edits``), and generation then keeps it as the seller left
    it, blanks included. The fill does the same.
    """
    if is_locked(listing, ETSY_ATTRIBUTES.rstrip(".")):
        return []
    return [row for row in form.get("fields") or []
            if _path(row).startswith(ETSY_ATTRIBUTES) and row.get("category_specific") and _unanswered(row)
            and fold_key(str(row.get("label") or "")) not in _FROM_GENERAL
            and not row.get("locked") and not is_locked(listing, _path(row))
            and not field_locked(listing, "etsy", _path(row).rsplit(".", 1)[-1])]


async def fill_etsy_attributes(conv_id: str, provider, *, evidence: str, photos: str | None) -> dict | None:
    """Fill Etsy's empty category attributes from the evidence. Returns the saved listing, or None when nothing changed.

    ``photos`` is the fingerprint of the photos ``evidence`` describes: answers
    are saved only while those are still the draft's photos.
    """
    from anteroom.database import in_worker_session
    from anteroom.services.native_editor import editor_form

    if provider is None:
        return None
    inputs = await in_worker_session(_fill_inputs, conv_id)
    if inputs is None:
        return None
    fingerprint, revision_id, listing = inputs
    if fingerprint != photos or "etsy" in (listing.get(native_contract.KEY) or {}):
        return None
    try:
        form = await editor_form(conv_id, "etsy", revision_id)
    except Exception as exc:  # noqa: BLE001 - the other marketplaces' fields are already saved
        log.info("No Etsy form to fill for %s: %s", conv_id, exc)
        return None
    if not isinstance(form, dict) or form.get("known") is False:
        return None
    candidates = etsy_attribute_rows(form, listing)
    if not candidates:
        return None
    rows = {_path(row): row for row in candidates}
    forms = {"etsy": form}
    reply = await _ask(conv_id, provider, {"etsy": candidates}, forms, listing, evidence, strict=True, categories=False)
    if reply is None:
        return None
    accepted = validated_values(reply, rows, evidence=evidence)
    if not accepted:
        return None
    return await in_worker_session(_save_fill, conv_id, revision_id, fingerprint, listing, forms, rows, reply,
                                   accepted, False, False)


# --- A category the seller changes later -------------------------------------
#
# Generation fills every marketplace once. When the seller picks another
# category afterwards, in the editor or through chat, that marketplace's form
# has new fields, so they are filled the same way. Saves within a few seconds
# of each other are one fill: chat saves twice for one request (its changes,
# then the category it chose), and a seller may click through categories.

# Saves made by the seller's form and by chat. Generation fills on its own,
# and this fill's own revision ("native_fields") must not start another.
FILL_ON_SOURCES = frozenset({"user_form", "model_refinement"})
SETTLE_SECONDS = 3.0

_loop: asyncio.AbstractEventLoop | None = None
_pending: dict[str, set[str]] = {}
_timers: dict[str, asyncio.TimerHandle] = {}
_running: dict[str, asyncio.Task] = {}


def start_category_fills() -> None:
    """Remember the app's event loop. Call from it at startup."""
    global _loop
    _loop = asyncio.get_running_loop()


def _category(listing: dict, market: str) -> str:
    if not isinstance(listing, dict):
        return ""
    ids = listing.get("direct_category_ids")
    return str((ids if isinstance(ids, dict) else {}).get(market) or "").strip()


def changed_categories(before: dict, after: dict) -> set[str]:
    """Marketplaces whose chosen category is new in this save."""
    changed = {market for market in MARKETS
               if _category(after, market) and _category(after, market) != _category(before, market)}
    for market, contract in (before.get(native_contract.KEY) or {}).items():
        fields = contract.get("fields") or []
        # A stale form is not a new edit. Compare the two revisions so an
        # unrelated save cannot queue the same refill again.
        if native_contract.changed_inputs(after, market, fields, native_contract.inputs(before, market, fields)):
            changed.add(market)
    return changed


def category_saved(conv_id: str, before: dict, after: dict, source: str) -> None:
    """Queue a fill for each marketplace whose category this save changed. Safe from any thread."""
    if source not in FILL_ON_SOURCES or _loop is None or _loop.is_closed():
        return
    markets = changed_categories(before, after)
    if source == "model_refinement":
        proofs = after.get(native_contract.EVIDENCE_KEY) or {}
        for market, contract in (after.get(native_contract.KEY) or {}).items():
            for row in contract.get("fields") or []:
                path = _path(row)
                value = get_path(after, path)
                proof = proofs.get(path) or {}
                if (row.get("required") and native_contract.item_field(row, market)
                        and not _empty(value) and not is_locked(after, path)
                        and (proof.get("value") != value or not proof.get("quote"))):
                    markets.add(market)
                    break
    if markets:
        _loop.call_soon_threadsafe(_queue, conv_id, markets)


def _queue(conv_id: str, markets: set[str]) -> None:
    _pending.setdefault(conv_id, set()).update(markets)
    if timer := _timers.pop(conv_id, None):
        timer.cancel()
    _timers[conv_id] = asyncio.get_running_loop().call_later(SETTLE_SECONDS, _start, conv_id)


def _start(conv_id: str) -> None:
    _timers.pop(conv_id, None)
    if conv_id in _running:
        return  # Picked up again when the running fill ends.
    markets = _pending.pop(conv_id, set())
    if not markets:
        return
    from anteroom.services import activity

    task = asyncio.get_running_loop().create_task(_fill_after_category(conv_id, markets))
    _running[conv_id] = task
    activity.track_task(conv_id, "Filling the new category's fields…", task)
    task.add_done_callback(lambda _task: _finished(conv_id))


def _finished(conv_id: str) -> None:
    _running.pop(conv_id, None)
    if _pending.get(conv_id):
        _queue(conv_id, set())


def _category_fill_evidence(db: Session, conv_id: str) -> tuple[str, str] | None:
    """The evidence for a category refill, with the fingerprint of the photos it describes."""
    from anteroom.services.listing_generate import latest_photo_analysis, photo_fingerprint, seller_item_details

    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if conv is None:
        return None
    photos = photo_fingerprint(repo.get_photos(conv_id))
    messages = repo.get_messages(conv_id)
    return photos, "\n\n".join(part for part in (
        latest_photo_analysis(messages, fingerprint=photos) or "", seller_item_details(conv.notes),
        "Seller messages:\n" + "\n".join(message.text for message in messages
                                            if message.role == "user" and message.text)) if part)


async def _fill_after_category(conv_id: str, markets: set[str]) -> None:
    from anteroom.database import in_worker_session
    from anteroom.services.listing_provider import get_listing_provider, managed_task_provider

    provider = managed_task_provider("field_edit") or get_listing_provider()
    if provider is None:
        return
    try:
        read = await in_worker_session(_category_fill_evidence, conv_id)
        if read is None:
            return
        photos, evidence = read
        native = markets & set(MARKETS)
        if native:
            await fill_native_fields(conv_id, provider, evidence=evidence, markets=native,
                                     expected_photo_fingerprint=photos)
        if "etsy" in markets:
            await fill_etsy_attributes(conv_id, provider, evidence=evidence, photos=photos)
    except Exception:
        log.exception("category field fill failed for %s", conv_id)
