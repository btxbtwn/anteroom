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

from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.seller_overrides import field_locked, fold_key, get_path, is_locked, set_path
from vendoo_studio.services import native_contract

log = logging.getLogger("vendoo_studio.native_field_fill")

MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
NAMES = {"ebay": "eBay", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "sellwild": "Sellwild"}
CATEGORY_PATHS = {f"direct_category_ids.{market}" for market in MARKETS} | {"sellwild_specifics.nativeCategoryId"}

FILL_RULES = (
    "You fill a resale listing's marketplace form fields. For each field below, choose values only "
    "from that field's listed choices, spelled exactly as listed, and only when the photos analysis, "
    "listing or notes show it. Leave out any field you cannot tell. Never guess a measurement, material "
    "or fit that the evidence does not show. Reply with one JSON object and nothing else: "
    '{"fields": {"<field path>": "<choice>" or ["<choice>", ...]}}. '
    "Use a list only where a field allows more than one choice, and never more than its limit."
)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _path(row: dict) -> str:
    return str(row.get("path") or row.get("key") or "")


def fillable_rows(form: dict, market: str, listing: dict, *, evidence: str = "") -> list[dict]:
    """Empty or unverified item fields the agent may choose values for."""
    prefix = f"{market}_specifics."
    strict = native_contract.KEY in listing
    rows = []
    for row in form.get("fields") or []:
        row = native_contract.applicable_row(row, listing, form.get("fields") or [])
        path = _path(row)
        options = row.get("options") or []
        if not path.startswith(prefix) or row.get("locked") or (not options and not strict):
            continue
        if not native_contract.item_field(row, market) or (not strict and not _empty(row.get("value"))):
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
        leaves = [label for label in options if not any(other.startswith(label + " > ") for other in options)]
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
            label = str(row.get("label") or "").removesuffix(" *")
            from vendoo_studio.services.option_lookup import project_row

            projected = project_row(row)
            choices = (json.dumps({"options_lookup": projected["options_lookup"]}) if projected.get("options_lookup")
                       else "; ".join(row.get("options") or []) or f"{row.get('input_type') or 'text'}; max length {row.get('max_length') or 'unspecified'}")
            lines.append(f"- {_path(row)} | {label} | {pick} | choices: " + choices)
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
    """Each answered path's stored value and chosen labels, keeping only listed choices."""
    answers = reply.get("fields") if isinstance(reply.get("fields"), dict) else {}
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
    set_path(listing, path, value)


async def load_forms(db: Session, conv_id: str, markets: Iterable[str] = MARKETS) -> dict[str, dict]:
    """Each named direct marketplace's current form. One that cannot be read is skipped."""
    from vendoo_studio.services.native_editor import editor_form

    forms = {}
    for market in (market for market in MARKETS if market in set(markets)):
        try:
            form = await editor_form(conv_id, market, None, db, refresh=True)
        except Exception as exc:  # noqa: BLE001 - one marketplace's form never stops the others
            log.info("No %s form to fill for %s: %s", market, conv_id, exc)
            continue
        if isinstance(form, dict) and form.get("known") is not False:
            forms[market] = form
    return forms


async def fill_native_fields(db: Session, conv_id: str, provider, *, evidence: str,
                             markets: Iterable[str] = MARKETS) -> dict | None:
    """Choose required categories first, then read their forms again to fill item fields."""
    markets = set(markets)
    repo = ListingRepo(db)
    saved = repo.get_current(conv_id)
    revision = repo.get_revision(saved.current_revision_id) if saved else None
    strict = bool(revision and native_contract.KEY in revision.listing_json)
    if strict:
        markets &= set(revision.listing_json[native_contract.KEY])
    categories = await _fill_native_fields(db, conv_id, provider, evidence=evidence, markets=markets, categories=True)
    if strict:
        await record_forms(db, conv_id, markets)
    fields = None
    for _round in range(3 if strict else 1):
        saved = repo.get_current(conv_id)
        before = repo.get_revision(saved.current_revision_id).listing_json if saved else {}
        fields = await _fill_native_fields(db, conv_id, provider, evidence=evidence, markets=markets, categories=False)
        dependencies_changed = fields is not None and any(
            native_contract.inputs(before, market, contract.get("fields") or [])
            != native_contract.inputs(fields, market, contract.get("fields") or [])
            for market, contract in (before.get(native_contract.KEY) or {}).items())
        if not strict or fields is None or not dependencies_changed:
            break
        await record_forms(db, conv_id, markets)
    if strict:
        await record_forms(db, conv_id, markets)
        saved = repo.get_current(conv_id)
        return repo.get_revision(saved.current_revision_id).listing_json if saved else None
    return fields if fields is not None else categories


async def prepare_generation_forms(db: Session, conv_id: str, provider, *, evidence: str) -> dict:
    """Select categories and capture the exact native forms before listing prose is generated."""
    from vendoo_studio.services.marketplaces import get_selected_marketplaces

    markets = set(MARKETS) & set(get_selected_marketplaces())
    await _fill_native_fields(db, conv_id, provider, evidence=evidence, markets=markets, categories=True)
    return await record_forms(db, conv_id, markets, reset_evidence=True)


async def record_forms(db: Session, conv_id: str, markets, *, reset_evidence: bool = False) -> dict:
    """Save current form definitions and their effective values onto the same revision snapshot."""
    repo = ListingRepo(db)
    current = repo.get_current(conv_id)
    revision = repo.get_revision(current.current_revision_id) if current else None
    if not revision:
        return {}
    forms = await load_forms(db, conv_id, markets)
    db.expire_all()
    latest = repo.get_current(conv_id)
    if not latest or latest.current_revision_id != revision.id:
        return {}
    listing = copy.deepcopy(revision.listing_json)
    if reset_evidence:
        listing[native_contract.EVIDENCE_KEY] = {}
    for market, form in forms.items():
        for row in form.get("fields") or []:
            path = _path(row)
            value = row.get("value")
            if path in CATEGORY_PATHS and form.get("category_id") and not get_path(listing, path) and not _empty(value):
                write_value(listing, path, str(form["category_id"]))
            sale_field = row.get("locked") or native_contract.SALE_FIELDS.search(path.split("_specifics.", 1)[-1].casefold())
            if (path.startswith(f"{market}_specifics.") and ".package." not in path and sale_field
                    and path not in CATEGORY_PATHS and not _empty(value) and not native_contract.value_error(row, value)):
                write_value(listing, path, value)
    listing[native_contract.KEY] = {**(listing.get(native_contract.KEY) or {}),
                                    **native_contract.capture(listing, forms, markets)}
    native_contract.constrain_generated(listing, listing, freeze_categories=False)
    if listing != revision.listing_json:
        repo.save_revision(conv_id, listing, source="native_schema", parent_revision_id=revision.id, expected_revision_id=revision.id)
    return listing[native_contract.KEY]


async def _fill_native_fields(db: Session, conv_id: str, provider, *, evidence: str,
                              markets: Iterable[str], categories: bool) -> dict | None:
    """Save the agent's picks for empty category fields. None when nothing was filled."""
    from vendoo_studio.services.listing_generate import collect_provider_text, photo_fingerprint

    if provider is None:
        return None
    fingerprint = photo_fingerprint(ConversationRepo(db).get_photos(conv_id))
    listing_repo = ListingRepo(db)
    saved = listing_repo.get_current(conv_id)
    revision = listing_repo.get_revision(saved.current_revision_id) if saved else None
    if revision is None or not isinstance(revision.listing_json, dict):
        return None
    listing = revision.listing_json
    strict = native_contract.KEY in listing and not categories
    forms = await load_forms(db, conv_id, markets)
    rows_by_market = {market: rows for market, form in forms.items()
                      if (rows := category_rows(form, listing) if categories else fillable_rows(form, market, listing, evidence=evidence))}
    if not rows_by_market:
        return None
    rows = {_path(row): row for market_rows in rows_by_market.values() for row in market_rows}
    summary = {key: listing.get(key) for key in (
        "title", "description", "brand", "category_path", "department", "size", "primaryColor",
        "secondaryColor", "material", "condition") if not _empty(listing.get(key))}
    messages = [
        {"role": "system", "content": FILL_RULES + (
            " Category is required on every form. Choose the most specific supported category for each item "
            "from the listed choices; do not skip it or ask the seller to choose. If none fits, leave it "
            "unresolved so listing remains blocked." if categories else "") + (
            ' For every answered field, return {"value": <value or list>, "evidence": "<exact quote from Evidence>"}. '
            "Recheck every listed field, including existing values. Omit unsupported answers; they will be cleared. "
            "Use only the Evidence block as factual support. The existing listing is context, not evidence. "
            "Sold comps support price only, never this item's product facts." if strict else "")},
        {"role": "user", "content": (
            f"--- Fields to fill ---\n{forms_prompt(rows_by_market, forms)}\n\n"
            f"--- Evidence ---\n{evidence}\n\n"
            f"--- Listing ---\n{json.dumps(summary, ensure_ascii=False)}"
        )},
    ]
    from vendoo_studio.services import activity

    work = activity.begin(conv_id, "Filling marketplace fields…")
    work.fields = tuple(rows)
    try:
        from vendoo_studio.services.option_lookup import OptionCatalog

        catalog = OptionCatalog({market: {"fields": rows} for market, rows in rows_by_market.items()})
        text = await collect_provider_text(provider, messages, option_catalog=catalog)
    except Exception:
        log.exception("marketplace field fill request failed for %s", conv_id)
        return None
    finally:
        activity.end(work)
    reply = _json_object(text)
    if reply is None or not isinstance(reply.get("fields"), dict):
        # A failed/truncated response is not an evidence check that rejected
        # every existing answer. Leave the revision untouched.
        return None
    accepted = validated_values(reply, rows, evidence=evidence if strict else None)
    if not accepted and not strict:
        return None
    db.expire_all()
    latest = listing_repo.get_current(conv_id)
    if photo_fingerprint(ConversationRepo(db).get_photos(conv_id)) != fingerprint:
        return None
    if not latest or latest.current_revision_id != revision.id:
        # The seller edited, cleared or regenerated while the forms were read.
        return None
    updated = copy.deepcopy(listing)
    changed_parents = {path for path, (value, _labels) in accepted.items() if get_path(listing, path) != value}
    changed_labels = {rows[path].get("label") for path in changed_parents}
    dependent_paths = {dependent for path in changed_parents for dependent in rows[path].get("clears") or []}
    form_rows = {_path(row): row for form in forms.values() for row in form.get("fields") or []}
    dependent_paths.update(path for path, row in form_rows.items() for rules in (row.get("value_constraints") or {}).values()
                           for rule in rules if rule.get("parentName") in changed_labels)
    accepted = {path: answer for path, answer in accepted.items() if path not in dependent_paths}
    for dependent in dependent_paths:
        if not is_locked(updated, dependent):
            empty = {} if dependent.endswith(".attributes") else [] if dependent.endswith(".inventoryRows") else ""
            write_value(updated, dependent, empty)
            (updated.get(native_contract.EVIDENCE_KEY) or {}).pop(dependent, None)
    if strict:
        # An omitted or unsupported earlier guess must not survive the evidence check.
        for path in rows:
            write_value(updated, path, "")
            (updated.get(native_contract.EVIDENCE_KEY) or {}).pop(path, None)
    for path, (value, _labels) in accepted.items():
        write_value(updated, path, value)
        if strict:
            updated.setdefault(native_contract.EVIDENCE_KEY, {})[path] = {
                "value": copy.deepcopy(value), "quote": reply["fields"][path]["evidence"]}
    if updated == listing:
        return None
    listing_repo.save_revision(conv_id, updated, source="native_categories" if categories else "native_fields", parent_revision_id=revision.id, expected_revision_id=revision.id)
    ConversationRepo(db).add_message(conv_id, "system", filled_message(accepted, rows), provider="system", model="")
    return updated


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
    if market == "sellwild":
        specifics = listing.get("sellwild_specifics")
        return str((specifics if isinstance(specifics, dict) else {}).get("nativeCategoryId") or "").strip()
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
    from vendoo_studio.services import activity

    task = asyncio.get_running_loop().create_task(_fill_after_category(conv_id, markets))
    _running[conv_id] = task
    activity.track_task(conv_id, "Filling the new category's fields…", task)
    task.add_done_callback(lambda _task: _finished(conv_id))


def _finished(conv_id: str) -> None:
    _running.pop(conv_id, None)
    if _pending.get(conv_id):
        _queue(conv_id, set())


async def _fill_after_category(conv_id: str, markets: set[str]) -> None:
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.listing_generate import current_photo_analysis, seller_item_details
    from vendoo_studio.services.listing_provider import get_listing_provider

    provider = get_listing_provider()
    if provider is None:
        return
    db = SessionLocal()
    try:
        repo = ConversationRepo(db)
        conv = repo.get(conv_id)
        if conv is None:
            return
        evidence = "\n\n".join(part for part in (
            current_photo_analysis(repo, conv_id) or "", seller_item_details(conv.notes),
            "Seller messages:\n" + "\n".join(message.text for message in repo.get_messages(conv_id)
                                                if message.role == "user" and message.text)) if part)
        await fill_native_fields(db, conv_id, provider, evidence=evidence, markets=markets)
    except Exception:
        log.exception("category field fill failed for %s", conv_id)
    finally:
        db.close()
