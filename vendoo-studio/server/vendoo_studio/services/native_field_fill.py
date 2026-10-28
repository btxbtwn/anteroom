"""Fill each direct marketplace's category fields from the form its tab shows.

After generation has chosen every marketplace's category, the listing agent
gets those exact forms (the rows, labels, choices and limits the seller sees)
and picks values for the empty item fields it can tell from the evidence.
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
from vendoo_studio.services.seller_overrides import field_locked, fold_key, is_locked, set_path

log = logging.getLogger("vendoo_studio.native_field_fill")

MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
NAMES = {"ebay": "eBay", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "sellwild": "Sellwild"}
# Rows about the sale rather than the item, or ones generation already decides.
# Charity is the seller's choice to donate, not a fact about the item.
_NOT_ITEM = re.compile(
    r"price|ship|parcel|package|quantity|sku|categor|worldwide|offer|format|duration|store|inventor"
    r"|variation|zip|location|condition|title|description|brand|hashtag|tags|charity|donation"
)
# A row with more choices than this (eBay brands, for one) is not something to pick from a list.
MAX_CHOICES = 300

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


def fillable_rows(form: dict, market: str, listing: dict) -> list[dict]:
    """Empty item fields on this form that the agent may choose values for."""
    prefix = f"{market}_specifics."
    rows = []
    for row in form.get("fields") or []:
        path = _path(row)
        options = row.get("options") or []
        if not path.startswith(prefix) or row.get("locked") or not options or len(options) > MAX_CHOICES:
            continue
        if _NOT_ITEM.search(path[len(prefix):].casefold()) or not _empty(row.get("value")):
            continue
        if is_locked(listing, path) or field_locked(listing, market, path.rsplit(".", 1)[-1]):
            continue
        rows.append(row)
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
            lines.append(f"- {_path(row)} | {label} | {pick} | choices: " + "; ".join(row.get("options") or []))
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


def validated_values(reply: dict, rows: dict[str, dict]) -> dict[str, tuple[Any, list[str]]]:
    """Each answered path's stored value and chosen labels, keeping only listed choices."""
    answers = reply.get("fields") if isinstance(reply.get("fields"), dict) else {}
    accepted: dict[str, tuple[Any, list[str]]] = {}
    for path, raw in answers.items():
        row = rows.get(str(path))
        if row is None:
            continue
        by_fold = {fold_key(option): option for option in row.get("options") or []}
        picked = raw if isinstance(raw, list) else [raw]
        labels = list(dict.fromkeys(by_fold[fold_key(item)] for item in picked
                                    if isinstance(item, (str, int, float)) and fold_key(str(item)) in by_fold))
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
    from vendoo_studio.routes.extension import extension_manager
    from vendoo_studio.services.native_editor import editor_form

    forms = {}
    for market in (market for market in MARKETS if market in set(markets)):
        if market not in ("ebay", "sellwild") and not extension_manager.connected:
            continue
        try:
            form = await editor_form(conv_id, market, None, db)
        except Exception as exc:  # noqa: BLE001 - one marketplace's form never stops the others
            log.info("No %s form to fill for %s: %s", market, conv_id, exc)
            continue
        if isinstance(form, dict) and form.get("known") is not False:
            forms[market] = form
    return forms


async def fill_native_fields(db: Session, conv_id: str, provider, *, evidence: str,
                             markets: Iterable[str] = MARKETS) -> dict | None:
    """Save the agent's picks for empty category fields. None when nothing was filled."""
    if provider is None:
        return None
    listing_repo = ListingRepo(db)
    saved = listing_repo.get_current(conv_id)
    revision = listing_repo.get_revision(saved.current_revision_id) if saved else None
    if revision is None or not isinstance(revision.listing_json, dict):
        return None
    listing = revision.listing_json
    forms = await load_forms(db, conv_id, markets)
    rows_by_market = {market: rows for market, form in forms.items() if (rows := fillable_rows(form, market, listing))}
    if not rows_by_market:
        return None
    rows = {_path(row): row for market_rows in rows_by_market.values() for row in market_rows}
    summary = {key: listing.get(key) for key in (
        "title", "description", "brand", "category_path", "department", "size", "primaryColor",
        "secondaryColor", "material", "condition") if not _empty(listing.get(key))}
    messages = [
        {"role": "system", "content": FILL_RULES},
        {"role": "user", "content": (
            f"--- Fields to fill ---\n{forms_prompt(rows_by_market, forms)}\n\n"
            f"--- Evidence ---\n{evidence}\n\n"
            f"--- Listing ---\n{json.dumps(summary, ensure_ascii=False)}"
        )},
    ]
    from vendoo_studio.services.listing_generate import collect_provider_text

    try:
        text = await collect_provider_text(provider, messages)
    except Exception:
        log.exception("marketplace field fill request failed for %s", conv_id)
        return None
    accepted = validated_values(_json_object(text) or {}, rows)
    if not accepted:
        return None
    db.expire_all()
    latest = listing_repo.get_current(conv_id)
    if not latest or latest.current_revision_id != revision.id:
        # The seller edited, cleared or regenerated while the forms were read.
        return None
    updated = copy.deepcopy(listing)
    for path, (value, _labels) in accepted.items():
        write_value(updated, path, value)
    listing_repo.save_revision(conv_id, updated, source="native_fields", parent_revision_id=revision.id)
    ConversationRepo(db).add_message(conv_id, "system", filled_message(accepted, rows), provider="system", model="")
    return updated


def filled_message(accepted: dict[str, tuple[Any, list[str]]], rows: dict[str, dict]) -> str:
    by_market: dict[str, list[str]] = {}
    for path, (_value, labels) in accepted.items():
        market = path.split("_specifics.", 1)[0]
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
    return {market for market in MARKETS
            if _category(after, market) and _category(after, market) != _category(before, market)}


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
    from vendoo_studio.services.listing_generate import latest_photo_analysis, seller_item_details
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
            latest_photo_analysis(repo.get_messages(conv_id)) or "", seller_item_details(conv.notes)) if part)
        await fill_native_fields(db, conv_id, provider, evidence=evidence, markets=markets)
    except Exception:
        log.exception("category field fill failed for %s", conv_id)
    finally:
        db.close()
