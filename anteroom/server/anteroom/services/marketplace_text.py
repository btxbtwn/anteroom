"""Marketplace titles and descriptions for a bulk crosslist: keep, shorten with AI, or write with AI.

Each mode works on one item's local listing and stops at a saved edit:

- ``keep``: nothing changes; each marketplace keeps following General.
- ``shorten``: only a title or description longer than that marketplace allows
  is shortened, by the AI, and cut at a word if it still runs long.
- ``rewrite``: the AI writes each marketplace its own title and description.

The text is saved on each marketplace's own form, as the seller's edit, so
later generation leaves it alone. Nothing is sent to a marketplace: an item
already live there shows unsent edits until the seller chooses Update.
"""
from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from typing import Any, Literal
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("anteroom.marketplace_text")

Mode = Literal["keep", "shorten", "rewrite"]
Market = Literal["ebay", "poshmark", "mercari", "depop", "etsy"]

# Each marketplace's own title and description limits, in characters. Depop has
# no title. Where a marketplace has no practical description limit, a rewrite
# keeps it under this many characters.
LIMITS: dict[str, dict[str, int]] = {
    "ebay": {"title": 80, "description": 4000},
    "poshmark": {"title": 80, "description": 1500},
    "mercari": {"title": 80, "description": 1000},
    "depop": {"description": 1000},
    "etsy": {"title": 140, "description": 4000},
}
# The limits the marketplace itself enforces; the others are only a rewrite's target length.
ENFORCED = {("ebay", "title"), ("poshmark", "title"), ("poshmark", "description"), ("mercari", "title"),
            ("mercari", "description"), ("depop", "description"), ("etsy", "title")}


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marketplaces: list[Market] = Field(min_length=1, max_length=5)
    mode: Mode
    expected_revision_id: str | None = Field(default=None, max_length=100)


def targets(listing: dict, marketplaces: list[str], mode: str) -> list[tuple[str, str, str, int]]:
    """``(marketplace, field, current text, limit)`` for each field the mode changes."""
    from anteroom.services.seller_overrides import marketplace_text

    found = []
    for market in dict.fromkeys(marketplaces):
        for field, limit in LIMITS.get(market, {}).items():
            text = marketplace_text(listing, market, field).strip()
            if not text:
                continue
            if mode == "rewrite" or (mode == "shorten" and (market, field) in ENFORCED and len(text) > limit):
                found.append((market, field, text, limit))
    return found


def prompt(listing: dict, wanted: list[tuple[str, str, str, int]], mode: str) -> list[dict]:
    asks = "\n".join(f"- {market}.{field}: at most {limit} characters. Now ({len(text)} characters): {text}"
                     for market, field, text, limit in wanted)
    if mode == "shorten":
        task = ("Shorten each field to fit its limit. Keep the brand, item type, size, color and every "
                "fact; drop filler words first. Do not add anything that is not already there.")
    else:
        task = ("Write each marketplace its own version of the field, in the style buyers on that "
                "marketplace search and read, within its limit. Use only facts in the listing; never "
                "invent measurements, flaws, materials or prices.")
    facts = {key: listing.get(key) for key in ("title", "description", "brand", "size", "condition", "primaryColor")
             if listing.get(key)}
    return [
        {"role": "system", "content": (
            "You prepare marketplace copy for a resale listing. " + task + "\n"
            'Reply with only a JSON object like {"ebay": {"title": "..."}, "mercari": {"description": "..."}}, '
            "one key per marketplace asked for, holding only the fields asked for.")},
        {"role": "user", "content": f"Listing facts: {json.dumps(facts, ensure_ascii=False)}\n\nFields:\n{asks}"},
    ]


def parse_reply(text: str, wanted: list[tuple[str, str, str, int]]) -> dict[tuple[str, str], str]:
    """The model's text for each wanted field. A flat ``{title, description}`` applies to every marketplace."""
    from anteroom.services.listing_generate import extract_listing_json

    data: Any = extract_listing_json(text)
    if not isinstance(data, dict):
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = None
    if not isinstance(data, dict):
        return {}
    found = {}
    for market, field, _text, _limit in wanted:
        block = data.get(market)
        value = block.get(field) if isinstance(block, dict) else data.get(field)
        if isinstance(value, str) and value.strip():
            found[(market, field)] = " ".join(value.split()) if field == "title" else value.strip()
    return found


def fit(text: str, field: str, limit: int) -> str:
    from anteroom.services.listing_generate import fit_description

    if len(text) <= limit:
        return text
    if field == "title":
        cut = text[: limit + 1]
        return (cut.rsplit(" ", 1)[0] if " " in cut else text[:limit]).rstrip(" -,|/:&+")
    return fit_description(text, limit)


def _text_provider():
    from anteroom.services.listing_provider import get_listing_provider, managed_task_provider

    return managed_task_provider("field_edit") or get_listing_provider()


def _read(db, conv_id: str, expected_revision_id: str | None) -> tuple[str, dict]:
    from anteroom.models.conversation import Conversation
    from anteroom.repositories.queries import ListingRepo
    from anteroom.services.native_marketplaces import running_marketplace
    from anteroom.services.price_markdown import BUSY, CHANGED, listing_busy

    if db.get(Conversation, conv_id) is None:
        raise HTTPException(404, "Listing not found")
    if listing_busy(conv_id, db) or running_marketplace(db, conv_id):
        raise HTTPException(409, BUSY)
    current = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(current.current_revision_id) if current and current.current_revision_id else None
    if revision is None or not isinstance(revision.listing_json, dict):
        raise HTTPException(400, "This item has no saved listing fields yet.")
    if expected_revision_id and revision.id != expected_revision_id:
        raise HTTPException(409, CHANGED)
    return revision.id, revision.listing_json


def _save(db, conv_id: str, revision_id: str, updates: list[dict]) -> dict:
    import copy

    from anteroom.services.api_job_lock import start_gate
    from anteroom.services.background_tasks import require_idle
    from anteroom.services.listing_editor import save_listing_edit
    from anteroom.services.seller_overrides import apply_field_updates
    from anteroom.services.service_context import current

    hosted = current() is not None
    with nullcontext() if hosted else start_gate():
        db.expire_all()
        if hosted:
            require_idle(db, conv_id)
        latest_id, listing = _read(db, conv_id, revision_id)
        updated = copy.deepcopy(listing)
        apply_field_updates(updated, updates)
        result = save_listing_edit(conv_id, updated, db, source="marketplace_text", expected_revision_id=latest_id)
    return {"revision_id": result["revision_id"], "valid": result["validation"]["valid"]}


async def prepare(run_db: Callable[..., Awaitable[Any]], conv_id: str, body: TextRequest) -> dict:
    """Prepare one item's marketplace text and save it locally. Never sends anything."""
    revision_id, listing = await run_db(_read, conv_id, body.expected_revision_id)
    wanted = targets(listing, list(body.marketplaces), body.mode)
    if body.mode == "keep" or not wanted:
        return {"conversation_id": conv_id, "revision_id": revision_id, "changes": [], "skipped": []}
    provider = _text_provider()
    if provider is None:
        raise HTTPException(400, "Set up an AI provider in Settings → Providers to shorten or write marketplace text.")
    from anteroom.services.listing_generate import collect_provider_text

    try:
        reply = await collect_provider_text(provider, prompt(listing, wanted, body.mode), quick=True)
    except Exception as exc:  # noqa: BLE001 - shown to the seller; nothing was saved
        from anteroom.services.provider_deadlines import provider_label

        log.info("marketplace text failed for %s: %s", conv_id, type(exc).__name__)
        raise HTTPException(502, f"{provider_label(getattr(provider, 'name', None))} could not write the text: "
                                 f"{str(exc)[:200] or type(exc).__name__}. Nothing was changed.") from exc
    written = parse_reply(reply, wanted)
    changes, skipped, updates = [], [], []
    for market, field, text, limit in wanted:
        value = written.get((market, field))
        if value is None and body.mode == "shorten":
            value = text  # The AI gave nothing back; cut the original at a word instead.
        if value is None:
            skipped.append({"marketplace": market, "field": field, "reason": "The AI did not write this field."})
            continue
        value = fit(value, field, limit)
        if value == text:
            continue
        updates.append({"path": f"{market}_specifics.{field}", "value": value})
        changes.append({"marketplace": market, "field": field, "old": text, "new": value})
    if not updates:
        return {"conversation_id": conv_id, "revision_id": revision_id, "changes": [], "skipped": skipped}
    saved = await run_db(_save, conv_id, revision_id, updates)
    return {"conversation_id": conv_id, "revision_id": saved["revision_id"], "changes": changes, "skipped": skipped}
