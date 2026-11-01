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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendoo_studio.database import json_value
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.ebay_fields import (
    DEFAULT_EBAY_SEASONS,
    EBAY_SEASON_CUES,
    ebay_season_parts,
    exact_ebay_season,
)
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.repositories.queries import BUSY_LISTING_STATUSES, ConversationRepo
from vendoo_studio.services.photos import cover_thumb_url
from vendoo_studio.services.sell_through import _parse_moment
from vendoo_studio.services.vendoo_import import oldest_live_listed_at, parse_notes

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
    "facebook": "Facebook",
    "shopify": "Shopify",
    "vinted": "Vinted",
    "whatnot": "Whatnot",
    "grailed": "Grailed",
    "vestiaire": "Vestiaire Collective",
    "vestiaireApi": "Vestiaire Collective",
    "sellwild": "Sellwild",
}


def list_suggestions(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Return ranked suggestion cards for the empty workspace and sidebar."""
    when = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

    from vendoo_studio.services.user_settings import get_stale_listing_days

    # Hosted preferences have their own short session. Read them before the
    # inventory session checks out a connection from the same bounded pool.
    stale_days = get_stale_listing_days()
    repo = ConversationRepo(db)
    covers = repo.cover_photos()
    photo_counts = _photo_counts(db)
    listings = _listing_state(db)
    conversations = repo.list_all()
    selling = _recent_sales(conversations, listings, when)
    in_demand = SEASONS_IN_DEMAND[when.month]
    from vendoo_studio.models.background_task import BackgroundTask

    busy = set(db.scalars(select(BackgroundTask.conversation_id).where(
        BackgroundTask.status.in_(("queued", "running")))))

    cards: list[dict[str, Any]] = []
    for conv in conversations:
        if conv.settled_at is not None or conv.id in busy:
            continue
        status = str(conv.status or "draft")
        if status == "sold" or status in BUSY_LISTING_STATUSES:
            continue
        card = _card_for(
            conv,
            status=status,
            photo_count=photo_counts.get(conv.id, 0),
            listing=listings.get(conv.id),
            cover_photo=covers.get(conv.id),
            now=when,
            selling=selling,
            in_demand=in_demand,
            stale_days=stale_days,
        )
        if card:
            cards.append(card)

    cards.sort(key=lambda card: (
        -int(card["score"]),
        -int(card["age_days"] or 0),
        str(card["conversation_id"]),
    ))
    return cards[: max(0, int(limit))]


def _photo_counts(db: Session) -> dict[str, int]:
    rows = (
        db.query(Photo.conversation_id, func.count(Photo.id))
        .group_by(Photo.conversation_id)
        .all()
    )
    return {conv_id: int(count) for conv_id, count in rows}


def _listing_state(db: Session) -> dict[str, dict[str, Any]]:
    """Current revision state, plus the facets stale ranking compares on."""
    doc = ListingRevision.listing_json
    rows = (
        db.query(
            Listing.conversation_id,
            Listing.current_revision_id,
            Listing.validation_status,
            json_value(doc, "brand"),
            json_value(doc, "category_path"),
            json_value(doc, "ebay_specifics.season"),
            json_value(doc, "title"),
            json_value(doc, "type"),
            json_value(doc, "ebay_specifics.material"),
        )
        .outerjoin(ListingRevision, ListingRevision.id == Listing.current_revision_id)
        .all()
    )
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


def _recent_sales(
    conversations, listings: dict[str, dict[str, Any]], now: datetime,
) -> list[dict[str, str]]:
    """Brand and category of each item sold in the last ``SELLING_WINDOW_DAYS``."""
    since = now - timedelta(days=SELLING_WINDOW_DAYS)
    sales: list[dict[str, str]] = []
    for conv in conversations:
        notes = parse_notes(conv.notes)
        if str(conv.status or "") != "sold" and str(notes.get("vendooStatus") or "") != "sold":
            continue
        dates = notes.get("vendooDates")
        if not isinstance(dates, dict):
            dates = {}
        sale = notes.get("vendooSale")
        if not isinstance(sale, dict):
            sale = {}
        sold_at = _parse_moment(str(dates.get("sold") or sale.get("soldAt") or ""))
        if sold_at is None or not since <= sold_at <= now:
            continue
        facets = listings.get(conv.id) or {}
        if facets.get("brand") or facets.get("category"):
            sales.append({"brand": facets.get("brand") or "", "category": facets.get("category") or ""})
    return sales


def _selling_like(facets: dict[str, Any] | None, sales: list[dict[str, str]]) -> tuple[int, int]:
    """How many recent sales share this item's brand or category, and their points."""
    brand = (facets or {}).get("brand") or ""
    category = (facets or {}).get("category") or ""
    count = points = 0
    for sale in sales:
        earned = (SAME_BRAND_POINTS if brand and sale["brand"] == brand else 0) + (
            SAME_CATEGORY_POINTS if category and sale["category"] == category else 0
        )
        if earned:
            count += 1
            points += earned
    return count, min(points, MAX_SELLING_POINTS)


def _season_fit(facets: dict[str, Any] | None, in_demand: tuple[str, ...]) -> str | None:
    """``in`` or ``off`` season right now; None for all-year or unknown items."""
    seasons = (facets or {}).get("seasons") or frozenset()
    if not seasons:
        return None
    return "in" if seasons & set(in_demand) else "off"


def _card_for(
    conv,
    *,
    status: str,
    photo_count: int,
    listing: dict[str, Any] | None,
    cover_photo: tuple[str, str | None] | None,
    now: datetime,
    selling: list[dict[str, str]] | None = None,
    in_demand: tuple[str, ...] = (),
    stale_days: int = 60,
) -> dict[str, Any] | None:
    notes = parse_notes(conv.notes)
    marketplaces = _marketplaces(notes)
    has_revision = bool(listing and listing.get("revision_id"))
    kind: str | None = None
    reason = ""
    score = 0
    days: int | None = None
    similar_sold = 0
    season_fit: str | None = None

    if status == KIND_FAILED:
        kind = KIND_FAILED
        reason = "The last send to Vendoo failed"
        score = KIND_BASE_SCORE[KIND_FAILED]
    elif status == "draft" and photo_count > 0 and not has_revision:
        kind = KIND_READY_TO_GENERATE
        reason = "Photos are ready to generate a listing"
        score = KIND_BASE_SCORE[KIND_READY_TO_GENERATE]
    elif has_revision and listing is not None and str(listing.get("validation_status") or "") == "error":
        kind = KIND_FIX_VALIDATION
        reason = "Listing fields need to be fixed before sending"
        score = KIND_BASE_SCORE[KIND_FIX_VALIDATION]
    elif status == "active":
        days = _listed_days_ago(notes, now)
        if days is not None and days >= stale_days:
            kind = KIND_STALE_ACTIVE
            similar_sold, selling_points = _selling_like(listing, selling or [])
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

    cover = (
        cover_thumb_url(*cover_photo)
        if cover_photo
        else (str(notes.get("vendooCoverUrl") or "") or None)
    )
    return {
        "conversation_id": conv.id,
        "kind": kind,
        "title": (conv.title or "").strip() or "Untitled",
        "reason": reason,
        "action": "open",
        "score": score,
        "cover_photo_url": cover,
        "age_days": days if kind == KIND_STALE_ACTIVE else None,
        "similar_sold": similar_sold,
        "season_fit": season_fit,
    }


def _marketplaces(notes: dict[str, Any]) -> list[str]:
    raw = notes.get("vendooMarketplaces")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _listed_days_ago(notes: dict[str, Any], now: datetime) -> int | None:
    text = oldest_live_listed_at(notes).strip()
    if not text:
        return None
    try:
        listed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if listed.tzinfo is None:
        listed = listed.replace(tzinfo=UTC)
    days = int((now - listed).total_seconds() // timedelta(days=1).total_seconds())
    return days if days >= 0 else None


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
