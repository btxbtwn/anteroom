"""When a listing actually went live.

Most items were listed through Vendoo or imported, so they have no
``marketplace_listings`` row. The rows that do exist were backfilled together,
and that ``created_at`` is not a listed date when an earlier one is on record.

The date is the earliest real one: the first successful publish, the date the
Vendoo job carried, then the item's own listed timestamp.
``marketplace_listings.created_at`` is used only when nothing earlier exists.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job, JobEvent
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.services.automation_prompt import MARKETPLACES
from vendoo_studio.services.automation_schedule import as_utc
from vendoo_studio.services.vendoo_import import VENDOO_MARKETPLACE_ALIASES, parse_notes

PUBLISH_ACTIONS = frozenset({"publish", "list"})
LIVE_STATES = frozenset({"published", "pending_review"})
_NOT_A_LISTING = frozenset({"fields_applied", "filling_fields", "queued", "automation", "imported"})


class ListedIndex:
    """Publish successes and Vendoo jobs, loaded once per preview."""

    def __init__(self) -> None:
        self.publishes: dict[str, list[tuple[datetime, str]]] = {}
        self.vendoo_jobs: dict[str, list[datetime]] = {}


def load_listed_index(db: Session) -> ListedIndex:
    index = ListedIndex()
    jobs = db.query(Job).all()
    by_id = {job.id: job for job in jobs}
    for event in db.query(JobEvent).filter(JobEvent.event_type == "native_result").all():
        job = by_id.get(event.job_id)
        found = _publish_event(job, event)
        if found is not None and job is not None:
            when, market = found
            index.publishes.setdefault(job.conversation_id, []).append((when, market))
    for job in jobs:
        found = _publish_step(job)
        if found is not None:
            when, market = found
            index.publishes.setdefault(job.conversation_id, []).append((when, market))
        if _is_vendoo_job(job) and job.created_at is not None:
            index.vendoo_jobs.setdefault(job.conversation_id, []).append(as_utc(job.created_at))
    return index


def listed_markets(notes: dict, rows: list[MarketplaceListing]) -> set[str]:
    """Marketplaces with a live direct row or a Vendoo listing that is not sold."""
    direct = {row.marketplace for row in rows if row.state in LIVE_STATES}
    return direct | set(_vendoo_live(notes))


def resolve_listed_at(
    index: ListedIndex,
    conv: Conversation,
    rows: list[MarketplaceListing],
    wanted: set[str],
) -> tuple[datetime | None, str | None]:
    """``(when, source)`` for this item. ``source`` is what the preview shows."""
    notes = parse_notes(conv.notes)
    candidates: list[tuple[datetime, str]] = []
    for when, market in index.publishes.get(conv.id, []):
        if not wanted or market in wanted:
            candidates.append((when, "publish_job"))
    note_times = _note_times(notes, wanted)
    vendoo_jobs = index.vendoo_jobs.get(conv.id, [])
    # An import job's own timestamp is the day it was copied in. Use it only
    # when Vendoo recorded no listed date at all. A date for some other
    # marketplace is not a listed date for the ones this rule names.
    if note_times and vendoo_jobs:
        candidates.extend((when, "vendoo_job") for when in note_times)
    elif note_times:
        candidates.extend((when, "item_listed") for when in note_times)
    elif vendoo_jobs and not _note_times(notes, set()):
        candidates.append((min(vendoo_jobs), "vendoo_job"))
    row_when = _row_created(rows, wanted)
    if row_when is not None and not any(when < row_when for when, _source in candidates):
        candidates.append((row_when, "marketplace_listing"))
    if not candidates:
        return None, None
    when, source = min(candidates, key=lambda item: item[0])
    return when, source


def _publish_event(job: Job | None, event: JobEvent) -> tuple[datetime, str] | None:
    if job is None or job.status != "completed" or event.created_at is None:
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    action = str(payload.get("action") or "")
    state = str(payload.get("state") or "")
    market = _market(str(payload.get("marketplace") or ""))
    if payload.get("ok") is not True or action not in PUBLISH_ACTIONS or state not in LIVE_STATES or not market:
        return None
    return as_utc(event.created_at), market


def _publish_step(job: Job) -> tuple[datetime, str] | None:
    if job.status != "completed" or job.created_at is None:
        return None
    market = _native_market(job.current_step or "")
    if market is None:
        return None
    return as_utc(job.updated_at or job.created_at), market


def _native_market(step: str) -> str | None:
    if not step.startswith("native_"):
        return None
    rest = step[len("native_"):]
    for market in sorted(MARKETPLACES, key=len, reverse=True):
        prefix = f"{market}_"
        if rest.startswith(prefix) and rest[len(prefix):] in PUBLISH_ACTIONS:
            return market
    return None


def _is_vendoo_job(job: Job) -> bool:
    """An import or a finished Vendoo send. Filling fields only saves a draft."""
    step = str(job.current_step or "")
    if step == "automation":
        return False
    if job.status == "imported" or step == "imported":
        return True
    if job.status != "completed":
        return False
    if step.startswith("vendoo"):
        return True
    return bool(job.vendoo_item_id) and step not in _NOT_A_LISTING


def _vendoo_live(notes: dict) -> list[str]:
    dates = notes.get("vendooDates")
    if not isinstance(dates, dict):
        dates = {}
    sold = dates.get("soldByMarketplace")
    if not isinstance(sold, dict):
        sold = {}
    sold_names = {_market(str(name)) for name in sold if sold.get(name)}
    raw = notes.get("vendooMarketplaces")
    if not isinstance(raw, list):
        raw = []
    live = []
    for name in raw:
        market = _market(str(name))
        if market and market not in sold_names and market not in live:
            live.append(market)
    return live


def _note_times(notes: dict, wanted: set[str]) -> list[datetime]:
    dates = notes.get("vendooDates")
    if not isinstance(dates, dict):
        dates = {}
    listed_by = dates.get("listedByMarketplace")
    if not isinstance(listed_by, dict):
        listed_by = {}
    sold = dates.get("soldByMarketplace")
    if not isinstance(sold, dict):
        sold = {}
    sold_names = {_market(str(name)) for name in sold if sold.get(name)}
    live = set(_vendoo_live(notes))
    stamps = []
    for name, raw in listed_by.items():
        market = _market(str(name))
        if not market or market in sold_names:
            continue
        if live and market not in live:
            continue
        if wanted and market not in wanted:
            continue
        parsed = _parse(raw)
        if parsed is not None:
            stamps.append(parsed)
    if stamps:
        return stamps
    if wanted and live and not (wanted & live):
        return []
    parsed = _parse(dates.get("listed"))
    return [parsed] if parsed is not None else []


def _row_created(rows: list[MarketplaceListing], wanted: set[str]) -> datetime | None:
    stamps = [
        as_utc(row.created_at)
        for row in rows
        if row.state in LIVE_STATES and row.created_at is not None and (not wanted or row.marketplace in wanted)
    ]
    return min(stamps) if stamps else None


def _market(name: str) -> str:
    cleaned = name.strip()
    return str(VENDOO_MARKETPLACE_ALIASES.get(cleaned, cleaned)).lower()


def _parse(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_utc(parsed)
