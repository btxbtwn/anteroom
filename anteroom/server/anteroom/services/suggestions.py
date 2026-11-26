"""Rank inventory rows into a short Suggestions feed.

Rules only: no model call, no publish. Each conversation contributes at most
one card, the highest-priority kind it matches. The seller opens the listing
and uses the existing Generate / editor / price-drop controls.

Stale listings (up 60+ days) are ordered by what is worth relisting first:
items like the ones that sold in the last 60 days (same brand or category),
then items in a season buyers are shopping for now, then the oldest.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from anteroom.models.conversation import Conversation, Photo
from anteroom.services.ebay_fields import (
    DEFAULT_EBAY_SEASONS,
    EBAY_SEASON_CUES,
    ebay_season_parts,
    exact_ebay_season,
)
from anteroom.models.listing import Listing, ListingRevision
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import BUSY_LISTING_STATUSES, ConversationRepo
from anteroom.services.photos import cover_thumb_url
from anteroom.services.sell_through import _parse_moment
from anteroom.services.imported_listing import oldest_live_listed_at
from anteroom.services.vendoo_import import parse_notes

MAX_SUGGESTIONS = 20
# Sales this recent say what buyers want right now.
SELLING_WINDOW_DAYS = 60
# Points per recent sale like this item: a shared brand says more than a shared category.
SAME_CATEGORY_POINTS = 1
SAME_BRAND_POINTS = 2
MAX_SELLING_POINTS = 12
# About two same-brand sales: in season lifts an item, out of season sinks it.
SEASON_POINTS = 4
# Resellers list a season ahead, so each month covers the season it is in and
# the one buyers are starting to shop for. Northern hemisphere.
SEASONS_IN_DEMAND: dict[int, tuple[str, ...]] = {
    1: ("Winter", "Spring"),
    2: ("Winter", "Spring"),
    3: ("Spring", "Summer"),
    4: ("Spring", "Summer"),
    5: ("Summer",),
    6: ("Summer",),
    7: ("Summer", "Fall"),
    8: ("Summer", "Fall"),
    9: ("Fall", "Winter"),
    10: ("Fall", "Winter"),
    11: ("Fall", "Winter"),
    12: ("Winter",),
}
NO_BRAND = frozenset({
    "", "unbranded", "unknown", "n/a", "na", "none", "does not apply", "no brand", "generic",
})

KIND_FAILED = "failed"
KIND_READY_TO_GENERATE = "ready_to_generate"
KIND_FIX_VALIDATION = "fix_validation"
KIND_STALE_ACTIVE = "stale_active"
KIND_READY_TO_REVIEW = "ready_to_review"

KIND_BASE_SCORE = {
    KIND_FAILED: 500,
    KIND_READY_TO_GENERATE: 400,
    KIND_FIX_VALIDATION: 300,
    KIND_STALE_ACTIVE: 200,
    KIND_READY_TO_REVIEW: 100,
}

MARKETPLACE_LABELS = {
    "ebay": "eBay",
    "etsy": "Etsy",
    "poshmark": "Poshmark",
    "mercari": "Mercari",
    "depop": "Depop",
    "shopify": "Shopify",
    "whatnot": "Whatnot",
    "vestiaire": "Vestiaire Collective",
    "vestiaireApi": "Vestiaire Collective",
}


@dataclass(frozen=True)
class SuggestionItem:
    id: str
    title: str
    status: str
    photo_count: int
    listing: dict[str, Any] | None
    cover_url: str | None
    marketplaces: list[str]
    listed_at: datetime | None


@dataclass(frozen=True)
class SuggestionInputs:
    items: list[SuggestionItem]
    sales: list[tuple[datetime, str, str]]
    stale_days: int


def list_suggestions(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Return ranked suggestion cards for the empty workspace and sidebar."""
    return rank_suggestions(read_suggestion_inputs(db), now=now, limit=limit)


def read_suggestion_inputs(db: Session) -> SuggestionInputs:
    from anteroom.services.user_settings import get_stale_listing_days

    # Hosted preferences have their own short session. Read them before the
    # inventory session checks out a connection from the same bounded pool.
    from anteroom.services.hosted_read_snapshots import snapshot_step

    with snapshot_step(db, 'preferences'):
        stale_days = get_stale_listing_days()
    repo = ConversationRepo(db)
    with snapshot_step(db, 'covers'):
        covers = repo.cover_photos()
    with snapshot_step(db, 'photo_counts'):
        photo_counts = _photo_counts(db)
    with snapshot_step(db, 'listing_rows'):
        listing_rows = _listing_rows(db)
    with snapshot_step(db, 'direct'):
        direct = MarketplaceListingRepo(db).summaries()
    with snapshot_step(db, 'conversations'):
        conversations = list(db.scalars(select(Conversation).options(load_only(
            Conversation.id,
            Conversation.title,
            Conversation.status,
            Conversation.settled_at,
            Conversation.notes,
        ))).all())
    from anteroom.models.background_task import BackgroundTask

    with snapshot_step(db, 'busy'):
        busy = set(db.scalars(select(BackgroundTask.conversation_id).where(
            BackgroundTask.status.in_(("queued", "running")))))
    # Ranking parses every item's cues and notes. It needs only loaded data;
    # return the connection before that CPU work so other requests can read.
    db.close()
    with snapshot_step(db, 'ranking'):
        listings = _listing_state(listing_rows)
        items = []
        sales = []
        for conv in conversations:
            notes = parse_notes(conv.notes)
            status = str(conv.status or "draft")
            listing = listings.get(conv.id)
            if listing and (status == "sold" or notes.get("vendooStatus") == "sold"):
                sold_at = _sale_date(notes)
                if sold_at and (listing["brand"] or listing["category"]):
                    sales.append((sold_at, listing["brand"], listing["category"]))
            if conv.settled_at is not None or conv.id in busy or status == "sold" or status in BUSY_LISTING_STATUSES:
                continue
            cover = covers.get(conv.id)
            invalid = listing and listing.get("revision_id") and listing.get("validation_status") == "error"
            items.append(SuggestionItem(
                id=conv.id, title=(conv.title or "").strip() or "Untitled", status=status,
                photo_count=photo_counts.get(conv.id, 0), listing=listing, marketplaces=_marketplaces(notes),
                cover_url=cover_thumb_url(*cover) if cover else (str(notes.get("vendooCoverUrl") or "") or None),
                listed_at=_listed_at(notes, direct.get(conv.id, {})) if status == "active" and not invalid else None,
            ))
        return SuggestionInputs(items, sales, stale_days)


def rank_suggestions(inputs: SuggestionInputs, *, now: datetime | None = None,
                     limit: int = MAX_SUGGESTIONS) -> list[dict[str, Any]]:
    when = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    since = when - timedelta(days=SELLING_WINDOW_DAYS)
    selling = RecentSales(Counter(), Counter(), Counter())
    for sold_at, brand, category in inputs.sales:
        if since <= sold_at <= when:
            if brand:
                selling.brands[brand] += 1
            if category:
                selling.categories[category] += 1
            if brand and category:
                selling.pairs[brand, category] += 1
    cards = []
    for item in inputs.items:
        card = _card_for(item, now=when, selling=selling, in_demand=SEASONS_IN_DEMAND[when.month],
                         stale_days=inputs.stale_days)
        if card:
            cards.append(card)
    cards.sort(key=lambda card: (-int(card["score"]), -int(card["age_days"] or 0), str(card["conversation_id"])))
    return cards[:max(0, int(limit))]


def _photo_counts(db: Session) -> dict[str, int]:
    rows = (
        db.query(Photo.conversation_id, func.count(Photo.id))
        .group_by(Photo.conversation_id)
        .all()
    )
    return {conv_id: int(count) for conv_id, count in rows}


def _listing_rows(db: Session):
    """Read the current revision facets without ranking them in the transaction."""
    rows = (
        db.query(
            Listing.conversation_id,
            Listing.current_revision_id,
            Listing.validation_status,
            ListingRevision.inventory_fields,
        )
        .outerjoin(
            ListingRevision,
            (ListingRevision.id == Listing.current_revision_id)
            & (ListingRevision.conversation_id == Listing.conversation_id)
            & (ListingRevision.user_id == Listing.user_id),
        )
        .all()
    )
    result = []
    for conv_id, revision_id, validation_status, fields in rows:
        doc = fields if isinstance(fields, dict) else {}
        result.append((
            conv_id,
            revision_id,
            validation_status,
            doc.get("brand"),
            doc.get("category_path"),
            doc.get("season"),
            doc.get("title"),
            doc.get("type"),
            doc.get("material"),
        ))
    return result


def _listing_state(rows) -> dict[str, dict[str, Any]]:
    """Current revision state, plus the facets stale ranking compares on."""
    state: dict[str, dict[str, Any]] = {}
    for conv_id, revision_id, validation_status, brand, category, season, title, kind, material in rows:
        cues = " ".join(str(part) for part in (title, category, kind, material) if part)
        state[conv_id] = {
            "revision_id": revision_id,
            "validation_status": validation_status,
            "brand": _brand_key(brand),
            "category": _category_key(category),
            "seasons": _seasons(season, cues),
        }
    return state


def _json_list(value: Any) -> Any:
    """Older rows stored arrays as JSON text; ``json_value`` hands back lists."""
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _brand_key(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return "" if text in NO_BRAND else text


def _category_key(value: Any) -> str:
    """The whole category path, so Women > Tops and Men > Tops stay apart."""
    value = _json_list(value)
    parts = value if isinstance(value, list) else str(value or "").split(">")
    return " > ".join(str(part).strip().casefold() for part in parts if str(part).strip())


def _seasons(field: Any, cues: str) -> frozenset[str]:
    """The seasons an item sells in; empty when it sells all year or nothing says.

    The listing's own Season wins. Without one, the strongest season cue in the
    title, category, type and material decides.
    """
    named = {
        season
        for part in ebay_season_parts(_json_list(field))
        if (season := exact_ebay_season(part))
    }
    if named:
        return frozenset() if len(named) == len(DEFAULT_EBAY_SEASONS) else frozenset(named)
    hits = {
        season: sum(1 for pattern in patterns if re.search(pattern, cues, flags=re.I))
        for season, patterns in EBAY_SEASON_CUES.items()
    }
    best = max(hits.values(), default=0)
    if best <= 0:
        return frozenset()
    return frozenset(season for season, count in hits.items() if count == best)


def _sale_date(notes: dict) -> datetime | None:
    dates = notes.get("vendooDates")
    sale = notes.get("vendooSale")
    sold = dates.get("sold") if isinstance(dates, dict) else None
    sold_at = sale.get("soldAt") if isinstance(sale, dict) else None
    return _parse_moment(str(sold or sold_at or ""))


@dataclass
class RecentSales:
    brands: Counter[str]
    categories: Counter[str]
    pairs: Counter[tuple[str, str]]

    def matching(self, listing: dict[str, Any] | None) -> tuple[int, int]:
        facets = listing or {}
        pair = facets.get("brand", ""), facets.get("category", "")
        brands, categories = self.brands[pair[0]], self.categories[pair[1]]
        count = brands + categories - self.pairs[pair]
        points = SAME_BRAND_POINTS * brands + SAME_CATEGORY_POINTS * categories
        return count, min(points, MAX_SELLING_POINTS)


def _season_fit(facets: dict[str, Any] | None, in_demand: tuple[str, ...]) -> str | None:
    """``in`` or ``off`` season right now; None for all-year or unknown items."""
    seasons = (facets or {}).get("seasons") or frozenset()
    if not seasons:
        return None
    return "in" if seasons & set(in_demand) else "off"


def _card_for(
    item: SuggestionItem,
    *,
    now: datetime,
    selling: RecentSales,
    in_demand: tuple[str, ...],
    stale_days: int,
) -> dict[str, Any] | None:
    status, listing, marketplaces = item.status, item.listing, item.marketplaces
    has_revision = bool(listing and listing.get("revision_id"))
    kind: str | None = None
    reason = ""
    score = 0
    days: int | None = None
    similar_sold = 0
    season_fit: str | None = None

    if status == KIND_FAILED:
        kind = KIND_FAILED
        reason = "The last listing task failed"
        score = KIND_BASE_SCORE[KIND_FAILED]
    elif status == "draft" and item.photo_count > 0 and not has_revision:
        kind = KIND_READY_TO_GENERATE
        reason = "Photos are ready to generate a listing"
        score = KIND_BASE_SCORE[KIND_READY_TO_GENERATE]
    elif has_revision and listing is not None and str(listing.get("validation_status") or "") == "error":
        kind = KIND_FIX_VALIDATION
        reason = "Listing fields need to be fixed before listing"
        score = KIND_BASE_SCORE[KIND_FIX_VALIDATION]
    elif status == "active":
        days = (now - item.listed_at).days if item.listed_at and item.listed_at <= now else None
        if days is not None and days >= stale_days:
            kind = KIND_STALE_ACTIVE
            similar_sold, selling_points = selling.matching(listing)
            season_fit = _season_fit(listing, in_demand)
            season_points = {"in": SEASON_POINTS, "off": -SEASON_POINTS}.get(season_fit or "", 0)
            reason = _stale_reason(days, marketplaces, similar_sold, season_fit)
            # 0..80 on top of the base, so a stale card never outranks the next
            # kind up; age breaks ties in the sort.
            score = KIND_BASE_SCORE[KIND_STALE_ACTIVE] + 4 * (
                selling_points + season_points + SEASON_POINTS
            )
    elif status == "draft" and has_revision and not marketplaces:
        kind = KIND_READY_TO_REVIEW
        reason = "Draft is ready to review"
        score = KIND_BASE_SCORE[KIND_READY_TO_REVIEW]

    if not kind:
        return None

    return {
        "conversation_id": item.id,
        "kind": kind,
        "title": item.title,
        "reason": reason,
        "action": "open",
        "score": score,
        "cover_photo_url": item.cover_url,
        "age_days": days if kind == KIND_STALE_ACTIVE else None,
        "similar_sold": similar_sold,
        "season_fit": season_fit,
    }


def _marketplaces(notes: dict[str, Any]) -> list[str]:
    raw = notes.get("vendooMarketplaces")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _listed_at(notes: dict[str, Any], direct: dict[str, dict]) -> datetime | None:
    text = oldest_live_listed_at(notes, direct).strip()
    if not text:
        return None
    try:
        listed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if listed.tzinfo is None:
        listed = listed.replace(tzinfo=UTC)
    return listed


def _stale_reason(
    days: int,
    marketplaces: list[str],
    similar_sold: int = 0,
    season_fit: str | None = None,
) -> str:
    age = f"Listed {days} day{'s' if days != 1 else ''} ago"
    labels = [_marketplace_label(item) for item in marketplaces]
    if len(labels) == 1:
        age = f"{age} on {labels[0]}"
    elif labels:
        age = f"{age} on {', '.join(labels[:-1])} and {labels[-1]}"
    parts = [age]
    if similar_sold:
        parts.append(f"{similar_sold} like it sold in the last {SELLING_WINDOW_DAYS} days")
    if season_fit == "in":
        parts.append("In season now")
    elif season_fit == "off":
        parts.append("Out of season")
    return ". ".join(parts)


def _marketplace_label(marketplace_id: str) -> str:
    if marketplace_id in MARKETPLACE_LABELS:
        return MARKETPLACE_LABELS[marketplace_id]
    return marketplace_id[:1].upper() + marketplace_id[1:] if marketplace_id else marketplace_id
