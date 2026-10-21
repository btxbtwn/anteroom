"""Sold comps research: the listing model's own web search, plus Brave.

The model that writes listings (Settings → Listing AI: the primary when it is
connected, else the fallback) searches with its native web search tool. It
runs as two searches side by side — one for sold comps, one for listings still
for sale — so each spends its whole search budget on one question and the pair
finishes in the time of the slower one rather than the sum. Brave, when it has
a key and quota left this month, runs beside them instead of after them, so it
never adds wall time. Every source answers into one merged, de-duplicated
report. ``stream_sold_comps`` reports each source and the report as they land,
so the chat and the Regenerate dialog can show progress.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace

from vendoo_studio.config import skills_dir
from vendoo_studio.services import live_trace, model_slots
from vendoo_studio.services.brave_search import (
    brave_active_queries,
    brave_sold_queries,
    comp_identities,
    item_fields,
    quota_exhausted,
    research_brave_report,
    sold_comps_query,
)
from vendoo_studio.services.keychain import get_brave_api_key
from vendoo_studio.services.listing_provider import get_listing_provider
from vendoo_studio.services.sold_comps import (
    SoldCompsReport,
    comps_from_model_answer,
    format_sold_comps,
    merge_reports,
    research_note,
)

log = logging.getLogger("vendoo_studio.comp_research")

# How long each model search gets. Cursor's agent reads listing pages one by
# one and can run for minutes.
MODEL_SEARCH_TIMEOUT_SEC = 90
# The whole lookup. Mobile generate SSE drops when comps stall for many minutes
# on the prior status.
SOLD_COMPS_TIMEOUT_SEC = 120

COMPS_SETUP_NOTE = (
    "Sold comps lookup needs ChatGPT, Cursor or MiMo connected, or a Brave Search API key "
    "in Settings. Listing prices will use an estimated baseline until then."
)
COMPS_THIN_IDENTITY_NOTE = (
    "Not enough brand or item detail from the photos to search sold comps. "
    "Use an estimated baseline."
)
COMPS_TIMEOUT_NOTE = (
    "Sold comps search timed out. Use an estimated baseline."
)
COMPS_FAILED_NOTE = (
    "Sold comps search failed. Use an estimated baseline."
)

# Brave runs on every listing beside the model search, so it gets a small fixed
# budget: the broad sold query and the first per-site ones (the builders put the
# highest-value queries first), plus one active query.
BRAVE_SOLD_QUERIES = 3
BRAVE_ACTIVE_QUERIES = 1

SOLD = "sold"
ACTIVE = "active"
_FOCUS = {
    SOLD: (
        "Find recently sold comps for: {query}\n"
        "Another search is covering listings still for sale, so spend every search on sold "
        'items and return "live": [].'
    ),
    ACTIVE: (
        "Find active marketplace listings still for sale for: {query}\n"
        "Another search is covering sold comps, so spend every search on current listings "
        'and return "comps": [].'
    ),
}
_AGENTS = {SOLD: ("comps-sold", "Sold comps"), ACTIVE: ("comps-active", "Active listings")}


def comps_search_messages(query: str, side: str | None = None) -> list[dict]:
    """The comp-research prompt; ``side`` narrows it to sold or active listings."""
    prompt = (skills_dir() / "list-this" / "references" / "comp-research.md").read_text(encoding="utf-8")
    if side is None:
        ask = f"Find recently sold comps and active marketplace listings for: {query}"
    else:
        ask = _FOCUS[side].format(query=query)
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": ask},
    ]


Searcher = Callable[[list[dict]], Awaitable[dict]]


@dataclass(frozen=True)
class ModelSearch:
    label: str
    search: Searcher


_PROVIDER_LABELS = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "cursor": "Cursor",
    "xiaomi-mimo": "MiMo",
}


def model_search() -> ModelSearch | None:
    """The listing model's native web search, or None when no model is connected."""
    provider = get_listing_provider()
    if provider is None:
        return None
    return ModelSearch(_PROVIDER_LABELS.get(provider.name, provider.name), provider.web_search)


def comps_search_available() -> bool:
    return model_search() is not None or bool(get_brave_api_key())


def comps_setup_note() -> str:
    """Shown when generate runs with nothing configured to search comps."""
    return format_sold_comps(
        SoldCompsReport(query="", source="not configured", note=COMPS_SETUP_NOTE)
    )


def _comps_note(query: str, source: str, note: str) -> str:
    return format_sold_comps(SoldCompsReport(query=query, source=source, note=note))


def model_report(
    label: str,
    query: str,
    answer: str,
    sources: list[dict],
    *,
    expected_names: tuple[str, ...] = (),
) -> SoldCompsReport:
    market, comps, live = comps_from_model_answer(answer, sources, expected_names=expected_names)
    return SoldCompsReport(
        query=query,
        source=label,
        comps=comps,
        market=market,
        note="" if comps else research_note(answer),
        live=live,
    )


def _active_query(query: str) -> str:
    return query.removesuffix(" sold comps").strip() or query


async def _run_model_search(
    search: ModelSearch, query: str, expected_names: tuple[str, ...], side: str
) -> SoldCompsReport:
    asked = query if side == SOLD else _active_query(query)
    async with model_slots.slot():
        result = await asyncio.wait_for(
            search.search(comps_search_messages(asked, side)),
            timeout=MODEL_SEARCH_TIMEOUT_SEC,
        )
    answer = str(result.get("answer") or "")
    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    report = model_report(
        search.label,
        query,
        answer,
        [item for item in sources if isinstance(item, dict)],
        expected_names=expected_names,
    )
    if side == ACTIVE:
        # Only a sold search may vouch for a sale.
        report = replace(report, comps=[], market="", note="")
    return report


@dataclass(frozen=True)
class CompsEvent:
    """``source``: one source changed state. ``report``: the merged comps so far.
    ``done``: the final comps text."""

    kind: str
    source: str = ""
    state: str = ""
    sold: int = 0
    live: int = 0
    detail: str = ""
    text: str = ""


def _found(report: SoldCompsReport) -> str:
    return f"{len(report.comps)} sold, {len(report.live)} live"


async def _model_side(
    search: ModelSearch, query: str, expected_names: tuple[str, ...], side: str
) -> SoldCompsReport:
    key, label = _AGENTS[side]
    async with live_trace.agent(key, label) as agent:
        agent.update(f"{search.label} is searching the web")
        try:
            report = await _run_model_search(search, query, expected_names, side)
        except TimeoutError:
            agent.result(f"{search.label} timed out")
            raise
        if side == SOLD:
            agent.result(f"Found {len(report.comps)} sold comps")
        else:
            agent.result(f"Found {len(report.live)} active listings")
        return report


async def _brave_side(queries: list[str], expected_names: tuple[str, ...]) -> SoldCompsReport:
    async with live_trace.agent("comps-brave", "Brave search") as agent:
        report = await research_brave_report(queries, expected_names=expected_names)
        agent.result(f"Found {_found(report)}")
        return report


def _in_order(sources: dict[asyncio.Task, str], answered: dict[asyncio.Task, SoldCompsReport]) -> list[SoldCompsReport]:
    """Reports in the order the sources started, not the order they answered,
    so the merged report reads the same however the race went."""
    return [answered[task] for task in sources if task in answered]


async def _stream_comps(fields: dict[str, str]) -> AsyncIterator[CompsEvent]:
    query = sold_comps_query(fields)
    if not query:
        yield CompsEvent("done", text=_comps_note("", "photo analysis", COMPS_THIN_IDENTITY_NOTE))
        return
    expected_names = comp_identities(fields)
    search = model_search()
    brave_key = get_brave_api_key()
    if search is None and not brave_key:
        yield CompsEvent("done", text=comps_setup_note())
        return

    answered: dict[asyncio.Task, SoldCompsReport] = {}
    errors: list[str] = []
    sources: dict[asyncio.Task, str] = {}
    if search is not None:
        for side in (SOLD, ACTIVE):
            source = f"{search.label} ({side})"
            sources[asyncio.create_task(_model_side(search, query, expected_names, side))] = source
            yield CompsEvent("source", source=source, state="searching")
    if brave_key:
        spent = quota_exhausted()
        if spent:
            # Asking again would cost a request and fail the same way.
            errors.append(f"Brave: {spent}")
            if search is None:
                live_trace.emit("step", "Brave quota is spent until next month")
        else:
            queries = [
                *brave_sold_queries(fields)[:BRAVE_SOLD_QUERIES],
                *brave_active_queries(fields)[:BRAVE_ACTIVE_QUERIES],
            ]
            sources[asyncio.create_task(_brave_side(queries, expected_names))] = "Brave"
            yield CompsEvent("source", source="Brave", state="searching")

    pending = set(sources)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                source = sources[task]
                exc = task.exception()
                if isinstance(exc, TimeoutError):
                    errors.append(f"{source}: timed out")
                    yield CompsEvent("source", source=source, state="timeout")
                elif exc is not None:
                    log.warning("%s comps search failed: %s", source, exc)
                    errors.append(f"{source}: {exc}")
                    yield CompsEvent("source", source=source, state="failed", detail=str(exc))
                else:
                    report = task.result()
                    answered[task] = report
                    yield CompsEvent(
                        "source", source=source, state="done",
                        sold=len(report.comps), live=len(report.live),
                    )
                    yield CompsEvent("report", text=format_sold_comps(merge_reports(query, _in_order(sources, answered))))
    finally:
        for task in pending:
            task.cancel()

    if not answered:
        note = COMPS_FAILED_NOTE
        if errors:
            note = f"{note} ({'; '.join(errors)})"
        yield CompsEvent("done", text=_comps_note(query, "web search", note))
        return
    yield CompsEvent("done", text=format_sold_comps(merge_reports(query, _in_order(sources, answered))))


async def stream_comps_for_fields(fields: dict[str, str]) -> AsyncIterator[CompsEvent]:
    """Progress events for one comps lookup on item fields, ending with ``done``."""
    stream = _stream_comps(fields)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SOLD_COMPS_TIMEOUT_SEC
    last_report = ""
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    anext(stream), timeout=max(0.0, deadline - loop.time())
                )
            except StopAsyncIteration:
                return
            except TimeoutError:
                log.warning("sold comps research timed out after %ss", SOLD_COMPS_TIMEOUT_SEC)
                yield CompsEvent(
                    "done",
                    text=last_report or _comps_note(sold_comps_query(fields), "timed out", COMPS_TIMEOUT_NOTE),
                )
                return
            if event.kind == "report":
                last_report = event.text
            yield event
            if event.kind == "done":
                return
    finally:
        await stream.aclose()


async def stream_sold_comps(
    analysis_text: str | None, evidence: dict | None = None
) -> AsyncIterator[CompsEvent]:
    """Progress events for one comps lookup, ending with a ``done`` event."""
    async for event in stream_comps_for_fields(item_fields(analysis_text, evidence)):
        yield event


async def research_comps_for_fields(fields: dict[str, str]) -> str:
    text = ""
    async for event in stream_comps_for_fields(fields):
        if event.kind == "done":
            text = event.text
    return text


async def research_sold_comps(analysis_text: str | None, evidence: dict | None = None) -> str:
    return await research_comps_for_fields(item_fields(analysis_text, evidence))
