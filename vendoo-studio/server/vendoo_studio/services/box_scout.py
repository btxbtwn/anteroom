"""Keep a buy list of wholesale clothing boxes ready without anyone asking for it.

Every few hours Studio crawls Raghouse and Thrift Vintage Fashion and picks
qualifying boxes within the seller's budget. Once a week it refreshes the trending
words with the listing model's web search, and the comparable sales from the
seller's own sales first, then eBay's and Poshmark's sold listings. The crawl, scoring and buy list live in the box-scout
skill's script, loaded from the skills directory, so Studio and the skill agree.

Nothing here buys anything. The Sourcing page links to each store's cart with the
picks already in it; the seller reviews and pays there.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import math
import re
import statistics
import sys
import threading
import time
import urllib.error
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from vendoo_studio.config import skills_dir, user_data_root
from vendoo_studio.services.inventory_analytics import AnalyticsItem
from vendoo_studio.services.sourcing_seasonality import planning_context, research_context
from vendoo_studio.services import sold_listings
from vendoo_studio.services.sold_comps import MIN_CONFIDENT_COMPS, trim_outliers

log = logging.getLogger("vendoo_studio.box_scout")

REFRESH_INTERVAL = timedelta(hours=6)
TREND_MAX_AGE = timedelta(days=7)
RESALE_MAX_AGE = timedelta(days=7)
RESEARCH_RETRY = timedelta(days=1)  # before searching again for a theme the marketplaces could not price
RESEARCH_THEMES = 60  # themes searched per refresh at most
RESEARCH_PAUSE_SEC = 1  # between Poshmark searches
MODEL_TIMEOUT_SEC = 240
ZONE_CHART_MAX_AGE = timedelta(days=30)
RECENT_ZIPS = 4
MEASURED_MIN_SALES = 30  # recorded sales in MEASURED_WINDOW before the seller's own rates replace the settings
MEASURED_WINDOW = timedelta(days=365)
THEME_MIN_ITEMS = 10  # the seller's own sold and listed pieces of a theme before its sell-through is its own
THEME_PRIOR = 10  # pieces of the seller's overall rate blended into a theme's own
CAPACITY_MIN = 15  # planned sales a theme can take on, however slow the seller's recent pace
HAIRCUT_MIN_THEMES = 3  # themes priced both ways before marketplace prices are scaled to the seller's own
HAIRCUT_RANGE = (0.25, 1.0)
# Bump when the snapshot's shape changes: a snapshot written by another build is
# a stale cache, dropped and rebuilt rather than served to a page that cannot
# read it.
SNAPSHOT_FORMAT = 4

DEFAULT_PREFS = {
    "budget": 300.0, "min_roi": 1.0, "raghouse_vip": False, "zip": "70115", "recent_zips": ["70115"],
    "sell_through": 0.5, "sell_through_auto": True, "fees": 0.2, "fees_auto": True,
    "cost_per_piece": 2.0, "cost_per_piece_auto": True,
    "ready_in_weeks": 4, "selling_window_weeks": 4, "selling_window_auto": True,
}
PLAN_PREFS = ("budget", "min_roi", "raghouse_vip", "zip", "sell_through", "fees", "cost_per_piece",
              "ready_in_weeks", "selling_window_weeks")

TRENDS_PROMPT = (
    "You track what secondhand and vintage clothing sells fastest on eBay, Poshmark, Depop "
    "and Mercari. Plan sourcing for the supplied future selling window. Search the live web "
    "for seasonal resale trends, upcoming holidays, marketplace reports and "
    "trend pages and reseller \"what's selling\" posts from the last 30 days. Return JSON "
    'only: {"terms":["carhartt","y2k"]} with 10 to 20 lowercase terms of one or two words: '
    "brands, eras, themes and garment types, spelled the way wholesale lot titles spell "
    'them (for example "harley", "cartoon", "workwear", "90s"). No commentary.'
)

_state_lock = threading.RLock()
_refresh_lock = threading.Lock()
# The last crawl, so a new ZIP or budget re-prices boxes without asking the stores again.
_catalogs: dict[str, list[dict]] = {}
_timer_stop = threading.Event()
_timer: threading.Thread | None = None


# --- The skill's script -----------------------------------------------------------------


@lru_cache(maxsize=1)
def scout() -> ModuleType:
    path = skills_dir() / "box-scout" / "scripts" / "scout.py"
    spec = importlib.util.spec_from_file_location("box_scout_skill", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise RuntimeError(f"The box-scout skill is missing at {path}.")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- State file ---------------------------------------------------------------------------


def state_path() -> Path:
    return user_data_root() / "sourcing.json"


def read_state() -> dict:
    with _state_lock:
        try:
            state = json.loads(state_path().read_text())
        except (OSError, json.JSONDecodeError):
            state = {}
    state.setdefault("prefs", {})
    state["prefs"] = {**DEFAULT_PREFS, **{k: v for k, v in state["prefs"].items() if k in DEFAULT_PREFS}}
    state.setdefault("trend", {"terms": [], "updated_at": None, "source": None})
    state.setdefault("resale", {})
    state.setdefault("zone_charts", {})
    state.setdefault("snapshot", None)
    # Sourcing snapshots are disposable caches; rebuild when the plan format changes.
    if state["snapshot"] and (state["snapshot"].get("format") != SNAPSHOT_FORMAT
                              or not all(key in state["snapshot"].get("preferences", {}) for key in PLAN_PREFS)):
        state["snapshot"] = None
    return state


def _write_state(state: dict, *, preserve_prefs: bool = False) -> None:
    with _state_lock:
        path = state_path()
        if preserve_prefs:
            try:
                latest = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                latest = {}
            state["prefs"] = {**DEFAULT_PREFS, **latest.get("prefs", state["prefs"])}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        tmp.replace(path)


def _now() -> datetime:
    return datetime.now(UTC)


def _age(iso: str | None, now: datetime) -> timedelta | None:
    if not iso:
        return None
    try:
        return now - datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def set_prefs(
    *,
    budget: float | None = None,
    min_roi: float | None = None,
    raghouse_vip: bool | None = None,
    zip: str | None = None,
    sell_through: float | None = None,
    sell_through_auto: bool | None = None,
    fees: float | None = None,
    fees_auto: bool | None = None,
    cost_per_piece: float | None = None,
    cost_per_piece_auto: bool | None = None,
    ready_in_weeks: int | None = None,
    selling_window_weeks: int | None = None,
    selling_window_auto: bool | None = None,
) -> dict:
    with _state_lock:
        state = read_state()
        prefs = state["prefs"]
        for name, value in (("budget", budget), ("min_roi", min_roi), ("sell_through", sell_through),
                            ("fees", fees), ("cost_per_piece", cost_per_piece)):
            if value is not None and (not math.isfinite(value) or
                                     (value <= 0 if name in {"budget", "sell_through"} else value < 0) or
                                     (name == "sell_through" and value > 1) or (name == "fees" and value >= 1)):
                raise ValueError(f"Invalid {name.replace('_', ' ')}.")
        for name, value, minimum in (("ready_in_weeks", ready_in_weeks, 0),
                                      ("selling_window_weeks", selling_window_weeks, 1)):
            if value is not None:
                if type(value) is not int or not minimum <= value <= 26:
                    raise ValueError(f"Invalid {name.replace('_', ' ')}.")
                prefs[name] = value
        if selling_window_weeks is not None:
            prefs["selling_window_auto"] = False if selling_window_auto is None else bool(selling_window_auto)
        elif selling_window_auto is not None:
            prefs["selling_window_auto"] = bool(selling_window_auto)
        if zip is not None:
            if not scout().valid_zip(zip):
                raise ValueError("A ZIP code is five digits.")
            if scout().OUTSIDE_48.match(zip):
                raise ValueError("Shipping estimates cover the 48 contiguous states only.")
            prefs["zip"] = zip
            prefs["recent_zips"] = [zip, *[z for z in prefs["recent_zips"] if z != zip]][:RECENT_ZIPS]
        if budget is not None:
            prefs["budget"] = float(budget)
        if min_roi is not None:
            prefs["min_roi"] = float(min_roi)
        if raghouse_vip is not None:
            prefs["raghouse_vip"] = bool(raghouse_vip)
        if sell_through is not None:
            prefs["sell_through"] = float(sell_through)
            prefs["sell_through_auto"] = False if sell_through_auto is None else bool(sell_through_auto)
        elif sell_through_auto is not None:
            prefs["sell_through_auto"] = bool(sell_through_auto)
        if fees is not None:
            prefs["fees"] = float(fees)
            # Typing a rate takes over from the measured one, unless the caller says otherwise.
            prefs["fees_auto"] = False if fees_auto is None else bool(fees_auto)
        elif fees_auto is not None:
            prefs["fees_auto"] = bool(fees_auto)
        if cost_per_piece is not None:
            prefs["cost_per_piece"] = float(cost_per_piece)
            prefs["cost_per_piece_auto"] = False if cost_per_piece_auto is None else bool(cost_per_piece_auto)
        elif cost_per_piece_auto is not None:
            prefs["cost_per_piece_auto"] = bool(cost_per_piece_auto)
        _write_state(state)
        return prefs


# --- Web research with the connected models ---------------------------------------------


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


def research_available() -> bool:
    from vendoo_studio.services.comp_research import model_search

    return model_search() is not None


async def _ask(system: str, user: str, key: str) -> tuple[dict | None, str | None]:
    """Ask the listing model, with web search, for JSON holding `key`."""
    from vendoo_studio.services.comp_research import model_search

    search = model_search()
    if search is None:
        return None, None
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        result = await asyncio.wait_for(search.search(messages), timeout=MODEL_TIMEOUT_SEC)
    except Exception as exc:  # a failed lookup is retried on the next refresh
        log.warning("%s web search for sourcing failed: %s", search.label, exc)
        return None, None
    payload = _json_object(str(result.get("answer") or ""))
    if payload and isinstance(payload.get(key), list):
        return payload, search.label
    return None, None


def clean_terms(raw: list) -> list[str]:
    terms: list[str] = []
    for item in raw:
        term = re.sub(r"\s+", " ", str(item)).strip().lower()
        if term and len(term) <= 30 and len(term.split()) <= 3 and term not in terms:
            terms.append(term)
    return terms[:20]


async def _research_trends(context: dict) -> tuple[list[str], str | None]:
    payload, source = await _ask(TRENDS_PROMPT, research_context(context), "terms")
    return (clean_terms(payload["terms"]) if payload else []), source


# --- Comparable sales ---------------------------------------------------------------------


def price_evidence(web: dict, own: list[dict], *, now: datetime | None = None) -> dict | None:
    """One theme's price from sold listings, taking the sources in order until there
    are MIN_CONFIDENT_COMPS distinct sales within 30 days: the seller's own sales,
    then eBay's sold listings, then any other marketplace's. The price is the median
    of those, capped by the asking-price median when enough current listings were
    found. Older sales within 90 days stay as context."""
    today = (now or _now()).date()

    def age(comp: dict) -> int:
        return (today - date.fromisoformat(comp["sold_at"])).days

    found = [c for c in web.get("comps") or [] if isinstance(c, dict) and c.get("url")]
    tiers = (
        [c for c in own if 0 <= age(c) <= 90 and 1 <= c["price"] <= 500],
        [c for c in found if c.get("marketplace") == "eBay"],
        [c for c in found if c.get("marketplace") != "eBay"],
    )
    comps: list[dict] = []
    recent: list[dict] = []
    for tier in tiers:
        if not tier:
            continue
        comps = sorted([*comps, *tier], key=lambda comp: comp["sold_at"], reverse=True)
        recent = [c for c in comps if age(c) <= 30]
        kept_prices = trim_outliers([c["price"] for c in recent])
        recent = [c for c in recent if c["price"] in kept_prices][:16]
        if len(recent) >= MIN_CONFIDENT_COMPS:
            break
    if len(recent) < MIN_CONFIDENT_COMPS:
        return None
    comps = [*recent, *(c for c in comps if age(c) > 30)][:16]
    values = [comp["price"] for comp in recent]
    active = web.get("active") or []
    sold_median = statistics.median(values)
    active_median = statistics.median(trim_outliers([c["price"] for c in active])) if len(active) >= MIN_CONFIDENT_COMPS else None
    # Named in the order they were taken: the seller's sales, eBay, then the rest.
    sources = sorted({OWN_SOURCE if c.get("conversation_id") else c["marketplace"] for c in recent},
                     key=lambda name: (name != OWN_SOURCE, name != "eBay", name))
    return {
        "per_piece": round(min(sold_median, active_median) if active_median is not None else sold_median, 2),
        "sold_median": round(sold_median, 2), "active_median": active_median, "active": active,
        "low": min(values), "high": max(values), "comps": comps, "source": " + ".join(sources),
    }


def _research_prices(themes: list[str], own: dict[str, list[dict]], now: datetime) -> dict[str, dict]:
    """Sold listings for themes the seller's own sales cannot price: eBay's, read
    through Chrome, then Poshmark's for a theme still short of evidence. Poshmark
    also supplies the current asking prices. A listing counts only when its title
    fits the theme (the skill's `sale_mismatch`)."""
    today = now.date()

    def fitting(theme: str, listings: list[dict]) -> list[dict]:
        return [c for c in listings if not scout().sale_mismatch(theme, c["title"], c["snippet"])]

    queries = {theme: query for theme in themes if (query := sold_listings.search_query(theme))}
    ebay = sold_listings.ebay_sold(list(dict.fromkeys(queries.values())), today)
    found = {}
    for theme, query in queries.items():
        entry = {"comps": fitting(theme, ebay.get(query, [])), "active": [], "ebay": query in ebay}
        try:
            if price_evidence(entry, own.get(theme, []), now=now) is None:
                entry["comps"] += fitting(theme, sold_listings.parse_poshmark_sold(sold_listings.poshmark_listings(query, sold=True), today))
                time.sleep(RESEARCH_PAUSE_SEC)
            entry["active"] = fitting(theme, sold_listings.parse_poshmark_active(sold_listings.poshmark_listings(query, sold=False)))[:8]
            time.sleep(RESEARCH_PAUSE_SEC)
        except FETCH_ERRORS as exc:
            log.warning("Poshmark sold listings for %s were not read: %s", theme, exc)
        found[theme] = entry
    return found


# --- Refresh --------------------------------------------------------------------------------


def _fresh_research(state: dict, now: datetime, own: dict[str, list[dict]]) -> dict[str, dict]:
    """Themes with enough current evidence: the seller's own sales, and cached sold
    listings under a week old, rechecked as their sale dates age out and against the
    theme they must fit."""
    fresh = {}
    for name in set(state["resale"]) | set(own):
        entry = _cached_research(state, name, now, RESALE_MAX_AGE)
        if entry:
            entry = _fitting(name, entry)
        if priced := price_evidence(entry or {}, own.get(name, []), now=now):
            fresh[name] = {**(entry or {"updated_at": now.isoformat()}), **priced}
            haircut = (state.get("market_haircut") or {}).get("factor")
            # Prices that came, in whole or part, from the seller's own sales are already theirs.
            if haircut and OWN_SOURCE not in priced["source"]:
                fresh[name]["per_piece"] = round(priced["per_piece"] * haircut, 2)
                fresh[name]["low"] = round(priced["low"] * haircut, 2)
    return fresh


def _wants_research(state: dict, theme: str, now: datetime, fresh: dict[str, dict], ebay_ready: bool) -> bool:
    """Whether to search the marketplaces for `theme` in this refresh."""
    if sold_listings.search_query(theme) is None:
        return False
    # Searched while Chrome was away, so eBay, which goes before Poshmark, was never read.
    if ebay_ready and (state["resale"].get(theme) or {}).get("ebay") is False:
        return True
    return theme not in fresh and _cached_research(state, theme, now, RESEARCH_RETRY) is None


def _cached_research(state: dict, name: str, now: datetime, max_age: timedelta) -> dict | None:
    entry = state["resale"].get(name)
    age = _age(entry and entry.get("updated_at"), now)
    return entry if age is not None and timedelta(0) <= age < max_age else None


FETCH_ERRORS = (urllib.error.URLError, TimeoutError, ValueError, KeyError)


def _crawl(errors: dict[str, str], *, recrawl: bool) -> dict[str, list[dict]]:
    catalogs = {}
    for store in scout().STORES:
        if not recrawl and store in _catalogs:
            catalogs[store] = _catalogs[store]
            continue
        try:
            catalogs[store] = _catalogs[store] = scout().fetch_catalog(store)
        except FETCH_ERRORS as exc:
            errors[store] = f"{scout().STORES[store]['name']} did not return its catalog: {exc}"
            log.warning(errors[store])
    return catalogs


def _zone_chart(state: dict, origin: str, now: datetime) -> dict | None:
    """The USPS chart from a 3-digit origin, kept for a month; None when unreadable."""
    cached = state["zone_charts"].get(origin)
    age = _age(cached and cached.get("fetched_at"), now)
    if age is None or age >= ZONE_CHART_MAX_AGE:
        try:
            chart = scout().fetch_zone_chart(origin)
            cached = state["zone_charts"][origin] = {"chart": chart, "fetched_at": now.isoformat()}
        except FETCH_ERRORS as exc:
            log.warning("Could not read the USPS zone chart for origin %s: %s", origin, exc)
    return cached["chart"] if cached else None


def _zones(state: dict, cfg: dict, catalogs: dict[str, list[dict]], dest_zip: str, now: datetime,
           errors: dict[str, str]) -> dict[str, int | dict[str, int]]:
    """Each store's shipping zone to `dest_zip`: one zone for a warehouse store,
    {state: zone} for a marketplace whose lots ship from each seller's state."""
    s = scout()
    zones: dict[str, int | dict[str, int]] = {}
    for store, info in cfg["stores"].items():
        origins = s.origin_zip3s(store, catalogs.get(store, []), cfg)
        by_origin: dict[str, int] = {}
        unreadable = []
        for label, origin in origins.items():
            chart = _zone_chart(state, origin, now)
            if chart is None:
                unreadable.append(label or info["origin"])
                continue
            zone = s.zone_for(chart, dest_zip)
            if zone is None:
                errors.setdefault(store, f"{dest_zip} is outside the 48 contiguous states the shipping rates cover.")
                break
            by_origin[label] = zone
        else:
            if origins and not by_origin:
                errors.setdefault(store, f"Could not read the USPS zone chart for {', '.join(unreadable)}.")
            elif by_origin:
                zones[store] = by_origin[""] if list(by_origin) == [""] else by_origin
    return zones


OWN_SOURCE = "Your sales"


def _own_sales() -> list[AnalyticsItem]:
    """The seller's recorded sales, read from the same rows Analytics uses."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.inventory_analytics import load_rows

    db = SessionLocal()
    try:
        return [item for item in load_rows(db) if item.status == "sold" and item.sold_price and item.sold_at]
    finally:
        db.close()


def measured_fees(sales: list[AnalyticsItem], now: datetime) -> dict | None:
    """The share of revenue the seller's recent sales paid in marketplace and
    payment fees, once there are enough of them to trust."""
    recent = [item for item in sales if item.fees is not None and item.sold_at is not None
              and (item.sold_at if item.sold_at.tzinfo else item.sold_at.replace(tzinfo=UTC)) >= now - MEASURED_WINDOW]
    if len(recent) < MEASURED_MIN_SALES:
        return None
    revenue = sum(item.sold_price for item in recent)
    if revenue <= 0:
        return None
    return {"rate": round(min(0.5, max(0.0, sum(item.fees for item in recent) / revenue)), 3), "sales": len(recent)}


def measured_sell_through(rows: list[AnalyticsItem], now: datetime) -> dict | None:
    """The sell-through rate the Analytics page shows for the last 12 months:
    sales in the period against sales plus what is still listed."""
    from vendoo_studio.services.inventory_analytics import summarize

    payload = summarize(rows, range_id="12m", now=now)
    sales = payload["sales"]["count"]
    rate = payload["sell_through_rate"]
    if sales < MEASURED_MIN_SALES or rate is None:
        return None
    return {"rate": round(min(1.0, max(0.05, rate / 100)), 3), "sales": sales}


def measured_cost_per_piece(sales: list[AnalyticsItem], now: datetime) -> dict | None:
    """What the seller's recent sales cost to ship out per piece, never below the
    default allowance for supplies and packing."""
    recent = [item for item in sales if item.sold_at is not None
              and (item.sold_at if item.sold_at.tzinfo else item.sold_at.replace(tzinfo=UTC)) >= now - MEASURED_WINDOW]
    if len(recent) < MEASURED_MIN_SALES:
        return None
    shipping = sum(item.shipping_cost for item in recent) / len(recent)
    return {"value": round(max(DEFAULT_PREFS["cost_per_piece"], shipping), 2), "sales": len(recent)}


def measured_selling_window(sales: list[AnalyticsItem], now: datetime) -> dict | None:
    """Weeks the seller's recent sales took to sell, from listing to sale (median)."""
    recent = [item.days_listed for item in sales if item.days_listed is not None and item.sold_at is not None
              and (item.sold_at if item.sold_at.tzinfo else item.sold_at.replace(tzinfo=UTC)) >= now - MEASURED_WINDOW]
    if len(recent) < MEASURED_MIN_SALES:
        return None
    weeks = min(26, max(1, math.ceil(statistics.median(recent) / 7)))
    return {"value": weeks, "sales": len(recent)}


def _follow_measured(name: str, measured: dict | None, key: str = "rate", auto: str | None = None) -> None:
    """While a setting is on automatic, its saved value tracks the seller's own."""
    with _state_lock:
        state = read_state()
        prefs = state["prefs"]
        if measured and prefs[auto or f"{name}_auto"] and prefs[name] != measured[key]:
            prefs[name] = measured[key]
            _write_state(state)


def _own_rows() -> list[AnalyticsItem]:
    """Every listing the seller has recorded, sold or not."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.inventory_analytics import load_rows

    db = SessionLocal()
    try:
        return load_rows(db)
    finally:
        db.close()


def theme_inventory(rows: list[AnalyticsItem], themes: set[str], now: datetime) -> dict[str, dict[str, int]]:
    """Per theme, the seller's pieces of that kind sold in the last 12 months and
    still listed now."""
    s = scout()
    counted = []
    for item in rows:
        if item.status == "active":
            counted.append((item, False))
        elif item.status == "sold" and item.sold_at is not None:
            moment = item.sold_at if item.sold_at.tzinfo else item.sold_at.replace(tzinfo=UTC)
            if moment >= now - MEASURED_WINDOW:
                counted.append((item, True))
    parts = [(sold, *s.theme_parts(item.title)) for item, sold in counted]
    result = {}
    for name in themes:
        matching = [sold for sold, styles, garment in parts if s.theme_matches(name, styles, garment)]
        result[name] = {"sold": sum(matching), "active": len(matching) - sum(matching)}
    return result


def theme_sell_through(inventory: dict[str, dict[str, int]], overall: float) -> dict[str, float]:
    """Each theme's own sell-through, blended toward the seller's overall rate until
    there are enough of its pieces to stand alone."""
    rates = {}
    for name, counts in inventory.items():
        n = counts["sold"] + counts["active"]
        if n >= THEME_MIN_ITEMS:
            rates[name] = round(min(1.0, max(0.05, (counts["sold"] + THEME_PRIOR * overall) / (n + THEME_PRIOR))), 3)
    return rates


def theme_capacity(rows: list[AnalyticsItem], inventory: dict[str, dict[str, int]], now: datetime) -> dict[str, float] | None:
    """Planned sales a theme can take on: the seller's monthly pace over the last
    90 days, less the pieces of that kind they already hold. None without enough
    history to know a pace."""
    recent = [item for item in rows if item.status == "sold" and item.sold_at is not None
              and (item.sold_at if item.sold_at.tzinfo else item.sold_at.replace(tzinfo=UTC)) >= now - timedelta(days=90)]
    if len(recent) < MEASURED_MIN_SALES // 2:
        return None
    pace = max(CAPACITY_MIN, round(len(recent) / 3))
    return {name: max(0, pace - counts["active"]) for name, counts in inventory.items()}


def _fitting(name: str, entry: dict) -> dict:
    """A cached theme entry with the listings that do not fit the theme dropped."""
    return {**entry, **{key: [c for c in entry.get(key) or [] if isinstance(c, dict) and not scout().sale_mismatch(
        name, str(c.get("title") or ""), str(c.get("snippet") or ""))] for key in ("comps", "active")}}


def market_haircut(state: dict, now: datetime, own: dict[str, list[dict]]) -> dict | None:
    """How the seller's own sold prices of the last 12 months compare with what the
    marketplaces' sold listings said for the same themes: the median of own median /
    marketplace median. Marketplace prices are scaled by it, never up."""
    ratios = []
    for name, comps in own.items():
        mine = [c["price"] for c in comps
                if 0 <= (now.date() - date.fromisoformat(c["sold_at"])).days <= MEASURED_WINDOW.days]
        entry = _cached_research(state, name, now, RESALE_MAX_AGE)
        if len(mine) < MIN_CONFIDENT_COMPS or not entry:
            continue
        market = price_evidence(_fitting(name, entry), [], now=now)
        if market and market["per_piece"] > 0:
            ratios.append(statistics.median(mine) / market["per_piece"])
    if len(ratios) < HAIRCUT_MIN_THEMES:
        return None
    low, high = HAIRCUT_RANGE
    return {"factor": round(min(high, max(low, statistics.median(ratios))), 2), "themes": len(ratios)}


def all_sales_price(sales: list[AnalyticsItem], now: datetime) -> dict | None:
    """What a piece of any kind sold for lately: the price of a mixed box, whose
    contents no single-garment search can price."""
    recent = [item for item in sales if item.sold_at is not None
              and 0 <= (now.date() - item.sold_at.date()).days <= 90 and 1 <= item.sold_price <= 500]
    if len(recent) < MEASURED_MIN_SALES:
        return None
    prices = sorted(float(item.sold_price) for item in recent)
    median = statistics.median(prices)
    low = prices[len(prices) // 5]
    newest = sorted(recent, key=lambda item: item.sold_at, reverse=True)[:16]
    return {
        "per_piece": round(median, 2), "sold_median": round(median, 2), "active_median": None, "active": [],
        "low": round(low, 2), "high": prices[-1], "source": f"{OWN_SOURCE} (all items)",
        "updated_at": now.isoformat(),
        "comps": [{
            "url": "", "conversation_id": item.conversation_id, "title": item.title[:200],
            "sold_at": item.sold_at.date().isoformat(), "price": float(item.sold_price),
            "marketplace": item.marketplace, "currency": "USD",
            "snippet": f"Your sale on {item.marketplace}, recorded in Studio.",
        } for item in newest],
    }


def own_evidence(sales: list[AnalyticsItem], themes: set[str]) -> dict[str, list[dict]]:
    """The seller's own sales that sold the kind of piece each theme names, newest
    first, as comps alongside the web's. A sale matches a theme when it is the same
    garment and carries every style the theme names."""
    s = scout()
    parts = [(item, *s.theme_parts(item.title)) for item in sales]
    evidence: dict[str, list[dict]] = {}
    for name in themes:
        matches = [item for item, styles, garment in parts if s.theme_matches(name, styles, garment)]
        if matches:
            evidence[name] = [{
                "url": "", "conversation_id": item.conversation_id, "title": item.title[:200],
                "sold_at": item.sold_at.date().isoformat(), "price": float(item.sold_price),
                "marketplace": item.marketplace, "currency": "USD",
                "snippet": f"Your sale on {item.marketplace}, recorded in Studio.",
            } for item in sorted(matches, key=lambda item: item.sold_at, reverse=True)]
    return evidence


def _lot_themes(catalogs: dict[str, list[dict]]) -> set[str]:
    s = scout()
    return {lot["theme"] for store, products in catalogs.items() for lot in s.ADAPTERS[store](products)}


def evidence(theme: str) -> dict | None:
    """Everything behind one theme's resale estimate, for the seller to inspect."""
    state = read_state()
    own = own_evidence(_own_sales(), {theme})
    entry = _fresh_research(state, _now(), own).get(theme)
    if entry is None and sold_listings.search_query(theme) is None:
        entry = state.get("all_sales_price")
    if entry is None:
        return None
    return {"theme": theme, **{key: entry.get(key) for key in (
        "per_piece", "sold_median", "active_median", "low", "high", "comps", "active", "updated_at", "source")}}


def _calibration() -> dict[str, dict]:
    """How the seller's own sales from each store compare with the resale estimates."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.boxes import resale_calibration

    db = SessionLocal()
    try:
        return resale_calibration(db)
    finally:
        db.close()


def _shipping_calibration() -> dict[str, dict]:
    """What each store charged the seller's own orders against the carrier list rate."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.boxes import shipping_calibration

    db = SessionLocal()
    try:
        return shipping_calibration(db)
    finally:
        db.close()


RAGHOUSE_VIP_MONTHLY = 64.0


def refresh(*, recrawl: bool = True, research: bool = True) -> dict:
    """Crawl, research what is stale, rebuild the buy list and save it."""
    with _refresh_lock:
        return _refresh(recrawl=recrawl, research=research)


def _refresh(*, recrawl: bool, research: bool) -> dict:
    s = scout()
    now = _now()
    sales = _own_sales()
    own_rows = _own_rows()
    fees_measured = measured_fees(sales, now)
    sell_through_measured = measured_sell_through(own_rows, now)
    _follow_measured("fees", fees_measured)
    _follow_measured("sell_through", sell_through_measured)
    cost_measured = measured_cost_per_piece(sales, now)
    window_measured = measured_selling_window(sales, now)
    _follow_measured("cost_per_piece", cost_measured, "value")
    _follow_measured("selling_window_weeks", window_measured, "value", "selling_window_auto")
    state = read_state()
    prefs = state["prefs"]
    errors: dict[str, str] = {}
    cfg = s.load_shipping()
    shipping_calibration = _shipping_calibration()
    for store, entry in shipping_calibration.items():
        if entry["factor"] is not None and store in cfg["stores"]:
            cfg["stores"][store]["ship_factor"] = entry["factor"]
    crawled = _crawl(errors, recrawl=recrawl)
    zones = _zones(state, cfg, crawled, prefs["zip"], now, errors)
    catalogs = {k: v for k, v in crawled.items() if k in zones}
    can_research = research and research_available()

    context = planning_context(prefs, now)
    # Reuse current-price comps across horizons, but never reuse themes for another
    # selling window. Weekly buckets avoid repeating trend research every crawl.
    week = (now.date() - timedelta(days=now.weekday())).isoformat()
    trend_key = hashlib.sha256(json.dumps({
        "week": week, "ready": prefs["ready_in_weeks"], "duration": prefs["selling_window_weeks"],
        "groups": [{key: group[key] for key in ("category_path", "item_type", "count", "historical_median_price")}
                   for group in context["seller_history"]["groups"]],
    }, sort_keys=True).encode()).hexdigest()
    trend_age = _age(state["trend"].get("updated_at"), now)
    if (state["trend"].get("context_key") != trend_key or trend_age is None
            or trend_age < timedelta(0) or trend_age >= TREND_MAX_AGE):
        state["trend"] = {"terms": [], "updated_at": None, "source": None}
        if can_research:
            terms, source = asyncio.run(_research_trends(context))
            if terms:
                state["trend"] = {"terms": terms, "updated_at": now.isoformat(), "source": source,
                                  "context_key": trend_key}

    # Members-only Raghouse boxes are scored and researched too, so a seller who
    # is not a VIP can see what joining would add to the list.
    filters = s.Filters(trend=tuple(state["trend"]["terms"]), include_vip=True,
                        sell_through=prefs["sell_through"], fees=prefs["fees"], cost_per_piece=prefs["cost_per_piece"])
    calibration = _calibration()
    factors = {store: c["factor"] for store, c in calibration.items() if c["factor"] is not None}
    themes = _lot_themes(catalogs)
    own = own_evidence(sales, themes)
    inventory = theme_inventory(own_rows, themes, now)
    own_rates = theme_sell_through(inventory, prefs["sell_through"]) if prefs["sell_through_auto"] else {}
    capacity = theme_capacity(own_rows, inventory, now)
    state["all_sales_price"] = all_sales_price(sales, now)

    def price_boxes():
        """Current prices, then every box scored with them. The haircut is read
        from what is cached, so it is taken again after a research pass."""
        state["market_haircut"] = market_haircut(state, now, own)
        fresh = _fresh_research(state, now, own)
        if state["all_sales_price"]:
            for theme in themes:
                if theme not in fresh and sold_listings.search_query(theme) is None:
                    fresh[theme] = state["all_sales_price"]
        baselines, all_rows = s.score_lots(
            catalogs, cfg, zones, filters, {k: v["per_piece"] for k, v in fresh.items()}, factors,
            resale_low={k: v["low"] for k, v in fresh.items()}, theme_sell_through=own_rates)
        return fresh, baselines, all_rows

    fresh, baselines, all_rows = price_boxes()

    if research:
        ebay_ready = sold_listings.ebay_ready()
        missing = s.research_themes([r for r in all_rows if r["price"] <= prefs["budget"]
                                     and _wants_research(state, r["theme"], now, fresh, ebay_ready)],
                                    limit=RESEARCH_THEMES)
        for name, entry in _research_prices(missing, own, now).items():
            # A theme nothing was found for is kept too, so it waits RESEARCH_RETRY before the next search.
            state["resale"][name] = {**entry, "updated_at": now.isoformat()}
        fresh, baselines, all_rows = price_boxes()
    rows = [r for r in all_rows if prefs["raghouse_vip"] or not r["vip"]]

    planning = {"budget": prefs["budget"], "min_roi": prefs["min_roi"],
                "sell_through": prefs["sell_through"], "fees": prefs["fees"],
                "cost_per_piece": prefs["cost_per_piece"],
                "theme_capacity": capacity}
    plan = s.buy_list(rows, cfg, **planning)
    store_plans = {
        store: s.buy_list([r for r in rows if r["store"] == store], cfg, **planning)
        for store in s.STORES
    }
    vip_upside = None
    if not prefs["raghouse_vip"]:
        # What the members-only Raghouse boxes would add to this same list.
        vip_plan = s.buy_list(all_rows, cfg, **planning)
        vip_upside = {
            "boxes": sum(1 for c in vip_plan["carts"] for p in c["lots"] if p["vip"]),
            "extra_profit": round(vip_plan["expected_profit"] - plan["expected_profit"], 2),
            "monthly_fee": RAGHOUSE_VIP_MONTHLY,
        }
    for lot in [*rows, *(p for choice in [plan, *store_plans.values()]
                          for c in choice["carts"] for p in c["lots"])]:
        # The examples themselves are served by `evidence` when the seller opens them.
        entry = fresh.get(lot["theme"], {})
        lot["comps_count"] = len(entry.get("comps", []))
        lot["active_median"] = entry.get("active_median")
        lot["research_at"] = entry.get("updated_at")
        lot["research_source"] = entry.get("source")
    _announce_new_picks(state["snapshot"], plan, prefs)
    state["snapshot"] = {
        "format": SNAPSHOT_FORMAT,
        "updated_at": now.isoformat(),
        "destination_zip": prefs["zip"],
        "preferences": {key: prefs[key] for key in PLAN_PREFS},
        "stores": {
            store: {"name": s.STORES[store]["name"], "error": errors.get(store),
                    "sellout": baselines.get(store),
                    "zone": zones[store] if isinstance(zones.get(store), int) else None,
                    "origins": len(zones[store]) if isinstance(zones.get(store), dict) else 1}
            for store in s.STORES
        },
        "research": can_research,
        "priced_themes": len(fresh),
        "shipping": {"residential_surcharge": cfg["residential_surcharge"],
                     "fuel_surcharge_pct": cfg["fuel_surcharge_pct"],
                     "fuel_surcharge_as_of": cfg["fuel_surcharge_as_of"],
                     "factors": {store: cfg["stores"][store]["ship_factor"] for store in s.STORES},
                     "calibration": shipping_calibration},
        "vip_upside": vip_upside,
        "assumptions": {"sell_through": prefs["sell_through"], "fees": prefs["fees"],
                        "fees_measured": fees_measured, "sell_through_measured": sell_through_measured,
                        "cost_per_piece_measured": cost_measured, "selling_window_measured": window_measured,
                        "market_haircut": state["market_haircut"],
                        "grade_yield": s.GRADE_YIELD, "cost_per_piece": prefs["cost_per_piece"]},
        "buy_list": plan,
        "store_buy_lists": store_plans,
        "lots": rows,
        "calibration": calibration,
        "seasonality": context,
    }
    _write_state(state, preserve_prefs=True)
    return state["snapshot"]


def _announce_new_picks(previous: dict | None, plan: dict, prefs: dict) -> None:
    """Tell the seller when a box joins the buy list between two checks of the
    same plan, so a Raghouse drop is not missed while the page is closed."""
    if not previous or any(previous["preferences"].get(key) != prefs[key] for key in PLAN_PREFS):
        return
    known = {f"{p['store']}:{p['variant_id']}" for c in previous["buy_list"]["carts"] for p in c["lots"]}
    new = [p for c in plan["carts"] for p in c["lots"] if f"{p['store']}:{p['variant_id']}" not in known]
    if not new:
        return
    from vendoo_studio.desktop import notify

    first = new[0]
    more = f" and {len(new) - 1} more" if len(new) > 1 else ""
    notify(f"New on your buy list: {first['title']} ({scout().STORES[first['store']]['name']}, "
           f"${first['landed']:,.0f} landed){more}.")


def refreshing() -> bool:
    return _refresh_lock.locked()


def refresh_in_background(*, recrawl: bool = True) -> bool:
    """Start a refresh unless one is running. Returns whether one was started."""
    if not _refresh_lock.acquire(blocking=False):
        return False

    def _run() -> None:
        try:
            _refresh(recrawl=recrawl, research=True)
        except Exception:
            log.exception("Sourcing refresh failed")
        finally:
            _refresh_lock.release()

    try:
        threading.Thread(target=_run, name="sourcing-refresh", daemon=True).start()
    except Exception:
        _refresh_lock.release()
        raise
    return True


def refresh_is_due(*, now: datetime | None = None) -> bool:
    state = read_state()
    snapshot = state["snapshot"]
    if snapshot and any(snapshot["preferences"][key] != state["prefs"][key] for key in PLAN_PREFS):
        return True
    age = _age(snapshot and snapshot.get("updated_at"), now or _now())
    return age is None or age < timedelta(0) or age >= REFRESH_INTERVAL


def start_sourcing_timer() -> threading.Thread | None:
    """Refresh on startup when one is due, then every REFRESH_INTERVAL."""
    global _timer
    if _timer is not None and _timer.is_alive():
        return None

    def _run() -> None:
        while True:
            if refresh_is_due():
                try:
                    refresh()
                except Exception:
                    log.exception("Sourcing refresh failed")
            if _timer_stop.wait(timedelta(minutes=15).total_seconds()):
                return

    _timer_stop.clear()
    _timer = threading.Thread(target=_run, name="sourcing-timer", daemon=True)
    _timer.start()
    return _timer


def stop_sourcing_timer(timeout: float = 5.0) -> None:
    _timer_stop.set()
    if _timer is not None and _timer.is_alive():
        _timer.join(timeout=timeout)
