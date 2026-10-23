"""The business assistant: questions about the whole shop, answered from Studio's data.

A listing's chat knows one item. This one is handed a summary of the business
on every question — the same sales and inventory totals Analytics shows, by
range and by month, the leading brands and categories, the listings that have
sat longest and the latest sales, the boxes bought and what they returned, the
current buy list with its best boxes, ad spend, sale events, and sourcing
checks — and the exact tables it can look the rest up in. The brief is read
fresh each time, so an answer is never older than the last Vendoo sync.

Every row behind the summary can be looked up. Each listing with its
description and full field values, every Sourcing-tab box, and the sales
breakdowns are copied into a throwaway in-memory SQLite database, and the model
may ask for a ``SELECT`` over it before it answers. The copy is the only thing a
lookup can touch. The summary stays small, so a question does not spend the
seller's model usage on rows it never needed.

It reads, and it proposes. A reply may end with changes to listings, which are
kept with the reply for the seller to apply or dismiss
(``services/assistant_changes.py``). Nothing here writes a listing, calls
Vendoo, or reaches a marketplace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import statistics
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.assistant import NEW_THREAD_TITLE, AssistantMessage, AssistantThread
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item
from vendoo_studio.services import (
    answer_links,
    assistant_changes,
    assistant_memory,
    box_media,
    business_context,
    sourcing_boxes,
)
from vendoo_studio.services.inventory_analytics import ANALYTICS_RANGES, AnalyticsItem

log = logging.getLogger("vendoo_studio.business_assistant")

# Earlier turns sent back with a question, so a follow-up can lean on them.
HISTORY_MESSAGES = 20
# How many rows of each kind the summary shows; a lookup reads every one.
TOP_BOXES = 15
LONGEST_LISTED = 15
LATEST_SALES = 10
SIZING_ROWS = 10
PRICE_CHANGE_ROWS = 10
TOP_GROUPS = 12
# Lookups one question may run, and what one may cost and return.
MAX_LOOKUPS = 6
LOOKUP_SECONDS = 5.0
LOOKUP_ROWS = 200
LOOKUP_CHARS = 24_000

INSTRUCTIONS = (
    "You are the business assistant inside Vendoo Studio, talking to a reseller about their own shop. "
    "They sell clothing and similar goods on marketplaces such as eBay, Poshmark, Mercari, Depop and Etsy, "
    "cross-listed through Vendoo.\n\n"
    "Below is a summary of the business, taken now: the totals Analytics shows and the few rows most often "
    "wanted. Every row behind it is in the lookup tables described below.\n"
    "- Answer from the summary only when it holds everything the question needs. When the question needs rows "
    "it leaves out, look them up first and then answer: ranking or comparing the boxes, a specific listing's "
    "details, every stale listing, sales of one brand, category, size, marketplace or month, a breakdown the "
    "summary does not show. The summary's short tables are the top rows only; never present one as the whole "
    "list or rank from it alone.\n"
    "- Work figures out from the rows when no total is given, and check a total against the rows when both exist.\n"
    "- Say what a figure rests on when the data is partial: how many sales had a cost recorded, "
    "how many listings had a list date, and so on. Unknown is not zero.\n"
    "- When neither the summary nor a lookup can answer the question, say what is missing and where in Studio "
    "it gets recorded. Never invent a sale, a price, a cost or a date.\n"
    "- Profit is the sold price plus shipping the buyer paid, less fees, shipping the seller paid, and the item's cost.\n"
    "- Give advice when asked — what to source, reprice, discount or drop — and tie each point to the numbers behind it. "
    "Fit it to what the seller says about their business and to what you remember about them.\n"
    "- You can propose changes to listings, described below, and the seller applies them. You cannot change a setting, "
    "and you cannot list, delist, publish or send anything to Vendoo or a marketplace. "
    "When the seller wants one of those, tell them where in Studio to do it.\n"
    "- When you name a specific listing, link it so the seller can open it: [its title](#listing-ID), "
    "with the id from its row. Use only ids that appear in the summary or in a lookup result.\n"
    "- Other ids only tie rows together. Name a box by its store and title, never by its id.\n"
    "- Web links: never build, guess or complete a URL, not even from a box's title or store. Give only a `url` "
    "Studio returned, exactly as it came, from the summary or a lookup this turn. A box missing from the summary "
    "has its `url` in sourcing_boxes: select it with the box. When none came back for a box, say so and tell "
    "the seller to open it from the Sourcing tab. Studio removes any other link from the answer.\n"
    "- Box photos: when the seller asks about specific boxes or whether a box is worth buying, look at their "
    "photos first with a view_box_media lookup (up to 5 boxes per answer, 8 images each). Then judge whether the "
    "contents are worth buying and give each box a clear verdict, Buy, Maybe or Skip, with its reasons. Weigh: "
    "brands visible on tags, condition issues (stains, holes, pilling, fading), the spread of sizes, season and "
    "fabric weight, how much of the box is actually visible against the claimed piece count, and red flags "
    "(stock or reused photos, a single photo for a large lot, contents that do not match the title). Set that "
    "against the box's figures and the seller's own sales of that kind (`kind_*`, `sales_by_category`). Mark "
    "which observations come from the photos (\"from the listing photos\"); they are what the store chose to "
    "show, not verified contents. Say which boxes you actually saw images for. A box whose photos were not "
    "attached this turn, failed to load, or has none is \"not viewed\": never call it fine or clean, and judge "
    "it on its figures alone, saying so. A video marked NOT viewed was not watched.\n"
    "- Sourcing: the summary's box table holds only the tab's best boxes; every box, with its url and every "
    "figure, is in the `sourcing_boxes` lookup table. `recommended_lots` is the buy list Studio recommends; when "
    "it is empty, `empty_reason` says why. To rank boxes against what the seller sells, look up sourcing_boxes "
    "and set each box's projected profit and ROI beside its `kind_*` figures (the seller's own sales of that "
    "kind of piece in the last 12 months: count, average price, margin, median days to sell) and "
    "`sales_by_category`. A box whose kind the seller has sold often, at a good margin and quickly, beats one "
    "with a higher ROI resting on few or no sales of its kind. A box missing the min ROI says by how many points "
    "(`roi_gap_points`) and dollars (`profit_shortfall`). Empty figures are unknown, never zero.\n"
    "- Write plain English and lead with the answer. Use a table when comparing several things. Amounts are US dollars.\n"
)

LOOKUP_INSTRUCTIONS = (
    "Looking things up: the shop is also loaded into a SQLite database. When the summary does not already "
    "answer the question, reply with nothing but one ```sql block holding a single SELECT. Make its first line "
    "a comment saying in plain words what you are looking up, like `-- sales by size`. Studio runs it and "
    "replies with up to {rows} rows, so filter, aggregate, or ORDER BY with a LIMIT. You may do this up to "
    "{limit} times for one question, then answer. Never show SQL or mention the database to the seller.\n"
    "Lookup tables, all of them, with every column:\n{schema}\n"
    "Dates are YYYY-MM-DD text. Amounts are numbers; NULL means not recorded. `margin` is profit as a percent of "
    "revenue over the sales with a cost recorded (`profit_known`); `median_days` is days from listing to sale.\n"
    "`listings` is every listing. `days` is days to sell for a sold listing and days listed so far for an active "
    "one; `listed_on` is where it is cross-listed, `sold_on` where it sold, and `box` the id of the bought box it "
    "came from (`boxes.id`). `form_sizes` is the size each marketplace form holds, `photo_size` the size read "
    "from the photos and where it was read, and `size_issue` why the sizing needs a look (NULL when nothing "
    "stands out). `details` is the listing's full JSON, including each marketplace's fields; read it with "
    "json_extract(details, '$.ebay_specifics'). `category` is the Analytics category; a kind of piece is best "
    "matched on the title too, like title LIKE '%jacket%'.\n"
    "`boxes` is the wholesale boxes the seller bought and what each returned; `price_changes` each asking-price "
    "change made in Studio.\n"
    "`sourcing_boxes` is every box on the Sourcing tab from its last check: `contents` the kind of piece, `landed` "
    "price plus shipping and checkout fee, `projected_profit` and `roi_percent` at the settings that check used, "
    "`on_its_own` how the box fares bought on its own (qualifies, return_target, downside, needs_research, budget), "
    "`buy_list` recommended or what kept it off the list, and `kind_*` the seller's own sales of its kind.\n"
    "`sales_by_category`, `sales_by_brand` and `sales_by_marketplace` are the Analytics breakdowns for each "
    "`period` (30d, 90d, 12m, all). `sales_over_time` is each range's sales per `bucket`, oldest first, as the "
    "Analytics chart draws them; range 12m has one row per calendar month.\n"
    "Every lookup answers: rows, \"0 rows matched\", or \"Lookup ... failed: <reason>\". Read that reply; when a "
    "lookup failed or matched nothing, say so or try a corrected query, and never fill the gap with a guess.\n"
    "Seeing a box's photos: reply with nothing but one ```view_box_media block listing the box ids from the "
    "sourcing_boxes table (or the summary's box table), one per line. Studio fetches each box's product page now "
    "and replies with its photos and frames from its video as images, saying which images belong to which box and "
    "which boxes could not be viewed and why. It counts as one of the lookups.\n"
)

CHANGES_INSTRUCTIONS = (
    "Changing listings: when the seller asks you to change listings, or agrees to a change you suggested, say in a "
    "sentence or two what you are proposing, then end the reply with one ```changes block holding a JSON array with "
    "one entry per listing:\n"
    "```changes\n"
    '[{{"listing": "LISTING-ID", "set": {{"asking": 24, "labels": ["Bin 4", "Sale"]}}}}]\n'
    "```\n"
    "- `set` may hold: asking (whole dollars), cost (dollars), title, description, brand, size, sku, labels (every "
    "label the listing should have afterwards, not only the new ones), and box (a box id from the boxes table, or "
    "null to take the listing out of its box).\n"
    "- Sizes: a listing has one general size, and saving it makes every marketplace form and the title carry that "
    "size. To fix sizing, set `size` to the right value, even when the general size already holds it and only a "
    "form is off. Work out the right size from the photo size (a tag beats a guess), the title, and the "
    "measurements in the description; men's bottoms are the waist alone (34, never 34x32). When the evidence "
    "does not settle a listing's size, leave it out and name it for the seller instead of guessing.\n"
    "- Use listing ids from the snapshot or a lookup, and give final values you worked out yourself, not formulas. "
    "Up to {limit} listings in one proposal.\n"
    "- Studio shows the seller each value before and after with Apply and Dismiss buttons, so do not list every "
    "change in words. Nothing changes until they press Apply: say the changes are ready to review, never that "
    "they were made.\n"
    "- Propose only what was asked for. Do not propose changes the seller has not asked for or agreed to; "
    "suggest them in words first.\n"
    "- Applied changes are saved in Studio. A listing already on Vendoo keeps its old values there until the "
    "seller sends it again from the listing.\n"
    "- Condition, colour, category and other marketplace-specific fields are changed in that listing's own chat; "
    "link the listing instead.\n"
    "- A Studio note after one of your earlier replies says what you proposed and whether the seller applied it. "
    "Never write such a note yourself.\n"
)

BREAKDOWN_PERIODS = ("30d", "90d", "12m", "all")
# What each sales group carries, in the summary and in the breakdown tables.
GROUP_STATS = (
    "count", "revenue", "average_price", "profit", "profit_known", "margin", "median_days", "days_known",
)
CATEGORY_COLUMNS = ("period", "category", *GROUP_STATS)
BRAND_COLUMNS = ("period", "brand", *GROUP_STATS)
MARKETPLACE_COLUMNS = ("period", "marketplace", *GROUP_STATS)
OVER_TIME_COLUMNS = ("range", "bucket", *GROUP_STATS)
# The summary's own tables.
RANGE_COLUMNS = (
    "range", "count", "revenue", "profit", "profit_known", "margin", "average_price", "median_days",
    "sell_through", "ad_spend", "previous_count", "previous_revenue", "previous_profit", "previous_margin",
)
GROUP_COLUMNS = ("label", "count", "revenue", "profit", "profit_known", "margin", "average_price", "median_days")
TOP_BOX_COLUMNS = (
    "id", "store", "title", "url", "contents", "landed", "projected_profit", "roi_percent", "passes_min_roi",
    "kind_sold_12m", "kind_margin",
)
LONGEST_COLUMNS = ("id", "title", "brand", "category", "size", "asking", "cost", "days", "listed", "listed_on")
SALE_COLUMNS = ("id", "title", "brand", "category", "sold_for", "profit", "sold_on", "sold", "days")
ACTIVE_GROUP_COLUMNS = ("category", "active", "asking_value", "median_days_listed", "most_days_listed")
# One listing, as a lookup reads it.
LISTING_COLUMNS = (
    "id", "title", "status", "brand", "category", "size", "condition", "color", "labels", "sku", "listed_on",
    "asking", "cost", "sold_for", "fees", "profit", "sold_on", "listed", "sold", "days", "box",
    "shipping_cost", "shipping_credit", "form_sizes", "photo_size", "size_issue", "description", "details",
)
# A listing whose sizing needs a look, as the summary shows it.
SIZING_COLUMNS = ("id", "title", "status", "category", "size", "form_sizes", "photo_size", "size_issue")
CHANGE_COLUMNS = ("listing_id", "changed_on", "old_price", "new_price")
BOX_COLUMNS = (
    "id", "store", "title", "price", "shipping", "pieces", "unsellable", "sellable", "unsellable_rate",
    "cost_per_piece", "bought_at", "spent", "listings", "listed", "sold", "returned", "profit", "roi",
    "sell_through", "unsold_asking", "median_days", "asp", "sold_premium",
)
AD_COLUMNS = (
    "marketplace", "start_date", "end_date", "spend", "clicks", "orders", "revenue", "roas", "cost_per_click", "notes",
)
EVENT_COLUMNS = (
    "title", "marketplace", "start_date", "end_date", "status", "discount_percent", "listings", "notes",
)
TABLES = {
    "listings": LISTING_COLUMNS,
    "price_changes": CHANGE_COLUMNS,
    "boxes": BOX_COLUMNS,
    "ad_spend": AD_COLUMNS,
    "sale_events": EVENT_COLUMNS,
    "sourcing_boxes": sourcing_boxes.BOX_COLUMNS,
    "sales_by_category": CATEGORY_COLUMNS,
    "sales_by_brand": BRAND_COLUMNS,
    "sales_by_marketplace": MARKETPLACE_COLUMNS,
    "sales_over_time": OVER_TIME_COLUMNS,
}
_RANGE_LABELS = {
    "wtd": "this week so far (Monday to now)",
    "mtd": "this month so far",
    "ytd": "this year so far",
    "7d": "last 7 days",
    "30d": "last 30 days",
    "90d": "last 90 days",
    "12m": "last 12 calendar months",
    "all": "all time",
}
# A lookup is a fenced block in the reply: ```sql (or ```sqlite) holding one SELECT, or
# ```view_box_media holding box ids. A bare ``` fence opening the reply with a SELECT counts too.
_LOOKUP_LANGS = {"sql": "sql", "sqlite": "sql", "view_box_media": "media"}
_LOOKUP_FENCES = ("```sql", "```view_box_media")
_FENCED = re.compile(r"```[ \t]*([A-Za-z_]*)(.*?)(?:```|\Z)", re.DOTALL)
_SELECTISH = re.compile(r"^(?:--|select\b|with\b)", re.IGNORECASE)
_FROM_TABLE = re.compile(r"\bfrom\s+([A-Za-z_]+)", re.IGNORECASE)
_WHERE = re.compile(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|\bhaving\b|;|\Z)",
                    re.IGNORECASE | re.DOTALL)
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


THREAD_TITLE_LIMIT = 80


class UnknownThread(LookupError):
    """The seller named a conversation that is not in Studio."""


def thread_title_from(text: str) -> str:
    title = " ".join(text.split())
    if not title:
        return NEW_THREAD_TITLE
    if len(title) <= THREAD_TITLE_LIMIT:
        return title
    return title[: THREAD_TITLE_LIMIT - 1].rstrip() + "…"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def thread_view(row: AssistantThread) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def list_threads(db: Session) -> list[AssistantThread]:
    return (
        db.query(AssistantThread)
        .order_by(AssistantThread.updated_at.desc(), AssistantThread.id.desc())
        .all()
    )


def latest_thread(db: Session) -> AssistantThread | None:
    return (
        db.query(AssistantThread)
        .order_by(AssistantThread.updated_at.desc(), AssistantThread.id.desc())
        .first()
    )


def create_thread(db: Session) -> AssistantThread:
    row = AssistantThread()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_thread(db: Session, thread_id: str) -> bool:
    thread = db.get(AssistantThread, thread_id)
    if thread is None:
        return False
    db.query(AssistantMessage).filter(AssistantMessage.thread_id == thread_id).delete()
    db.delete(thread)
    db.commit()
    return True


def resolve_thread(db: Session, thread_id: str | None) -> AssistantThread:
    """The conversation a new turn belongs to.

    A named id must already exist. With none, the turn joins the latest
    conversation, or starts one when the seller has not kept any.
    """
    if thread_id:
        thread = db.get(AssistantThread, thread_id)
        if thread is None:
            raise UnknownThread(thread_id)
        return thread
    return latest_thread(db) or create_thread(db)


def list_messages(db: Session, thread_id: str | None = None) -> list[AssistantMessage]:
    if thread_id is None:
        thread = latest_thread(db)
        if thread is None:
            return []
        thread_id = thread.id
    return (
        db.query(AssistantMessage)
        .filter(AssistantMessage.thread_id == thread_id)
        .order_by(AssistantMessage.created_at, AssistantMessage.id)
        .all()
    )


def add_message(
    db: Session, role: str, text: str, *, provider: str | None = None, model: str | None = None,
    changes: dict[str, Any] | None = None, attachments: list[dict[str, Any]] | None = None,
    thread_id: str | None = None,
):
    thread = resolve_thread(db, thread_id)
    row = AssistantMessage(
        thread_id=thread.id, role=role, text=text, provider=provider, model=model,
        changes=changes, attachments=attachments or None,
    )
    if role == "user" and thread.title == NEW_THREAD_TITLE:
        titled = thread_title_from(text)
        if titled != NEW_THREAD_TITLE:
            thread.title = titled
    thread.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def message_view(row: AssistantMessage) -> dict[str, Any]:
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": row.id,
        "role": row.role,
        "text": row.text,
        "created_at": created.isoformat() if created else None,
        "changes": row.changes,
        "attachments": row.attachments or [],
    }


@dataclass
class Shop:
    """The business as of one moment: what the model is told, and what it can look up."""

    brief: str
    lookups: sqlite3.Connection


def read_shop(db: Session, *, now: datetime | None = None) -> Shop:
    """Everything Studio knows about the business, read once for one question.

    Without ``now`` the figures may come from the copy kept for the last
    question (``business_context``), until anything they rest on is written.
    The brief is the same text for the same data, with what the seller says
    and what the assistant remembers last, so a provider's prompt cache keeps
    the whole snapshot from one question to the next.
    """
    from vendoo_studio.services.user_settings import get_business_note

    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    if now is None:
        data, tables = business_context.cached(db, clock.date(), lambda: _read_data(db, clock))
    else:
        data, tables = _read_data(db, clock)
    note = get_business_note()
    sections = [
        data,
        f"--- What the seller says about their business ---\n{note}" if note else "",
        assistant_memory.section(db),
    ]
    return Shop(brief="\n\n".join(section for section in sections if section), lookups=_lookup_database(tables))


def _read_data(db: Session, clock: datetime) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """The summary as text, and the tables a lookup reads."""
    from vendoo_studio.services import ad_spend, boxes, box_scout, scout
    from vendoo_studio.services.inventory_analytics import inventory_analytics, load_rows

    rows = load_rows(db)
    ranges = {
        range_id: inventory_analytics(db, range_id=range_id, now=clock, rows=rows)
        for range_id in ANALYTICS_RANGES
    }
    everything = ranges["all"]
    records = _records(db, rows, clock)
    changes = _price_changes(db)
    box_results = boxes.results(db)
    ads = [ad_spend.entry_view(row) for row in ad_spend.list_entries(db)]
    events = _sale_events(db)
    scout_sales = scout.outcomes(db)
    sourcing_state = box_scout.read_state()
    tab_boxes = sourcing_boxes.box_rows(sourcing_state, rows, clock)
    breakdowns = {
        name: _breakdown(ranges, key, label)
        for name, key, label in (
            ("sales_by_category", "categories", "category"),
            ("sales_by_brand", "brands", "brand"),
            ("sales_by_marketplace", "marketplaces", "marketplace"),
        )
    }
    over_time = [
        {"range": range_id, "bucket": bucket["label"], **{key: bucket[key] for key in GROUP_STATS}}
        for range_id, payload in ranges.items() for bucket in payload["periods"]
    ]
    sections = [
        f"Today is {clock.date().isoformat()} (UTC). "
        f"Sales and statuses were last synced from Vendoo at {everything['last_updated_at'] or 'an unknown time'}.",
        _section("Inventory now", {
            **everything["inventory"],
            "aging_of_active_listings": [
                {key: bucket[key] for key in ("label", "count", "asking_value")} for bucket in everything["aging"]
            ],
            "sold_listings_missing_a_sale_date": everything["undated_sales"],
            "sales_missing_details_in_vendoo": len(everything["incomplete_sales"]),
        }),
        _sales_by_range(ranges),
        _table(
            "Sales by calendar month, last 12 months", "Oldest first. sales_over_time has every range's buckets.",
            GROUP_COLUMNS, [{"label": bucket["label"], **bucket} for bucket in ranges["12m"]["periods"]],
        ),
        _top_groups(ranges, "marketplaces", "Sales by marketplace", "sales_by_marketplace"),
        _top_groups(ranges, "categories", "Sales by category", "sales_by_category"),
        _top_groups(ranges, "brands", "Brands", "sales_by_brand", by_profit=True),
        _top_groups(ranges, "price_tiers", "Sales by price tier", None),
        _listing_summary(records),
        _sizing_rows(records),
        _price_change_rows(changes),
        _section("Wholesale boxes bought, and what each has returned", box_results),
        _sourcing(sourcing_state, tab_boxes, clock),
        _sourcing_box_rows(tab_boxes),
        _section("Ad spend the seller recorded", ads),
        _section("Sale events", events),
        _section("Sourcing checks (is this worth buying?)", {
            "track_record": scout.track_record(db, scout_sales),
            "recent": [
                {key: check[key] for key in ("title", "asking_price", "estimate", "comps_count", "decision",
                                             "sold_price", "created_at")}
                for check in (scout.to_dict(row, scout_sales.get(row.id)) for row in scout.recent(db))
            ],
        }),
    ]
    return (
        "\n\n".join(section for section in sections if section),
        {
            "listings": records,
            "price_changes": changes,
            "boxes": box_results["boxes"],
            "ad_spend": ads,
            "sale_events": events,
            "sourcing_boxes": tab_boxes,
            **breakdowns,
            "sales_over_time": over_time,
        },
    )


def _breakdown(ranges: dict[str, dict[str, Any]], key: str, label: str) -> list[dict[str, Any]]:
    return [
        {"period": range_id, label: group["label"], **{stat: group[stat] for stat in GROUP_STATS}}
        for range_id in BREAKDOWN_PERIODS for group in ranges[range_id][key]
    ]


def _sales_by_range(ranges: dict[str, dict[str, Any]]) -> str:
    """One line per Analytics range: its sales, its sell-through, and the window just before it."""
    lines = []
    for range_id, payload in ranges.items():
        previous = (payload["previous"] or {}).get("sales") or {}
        lines.append({
            "range": range_id,
            **payload["sales"],
            "sell_through": payload["sell_through_rate"],
            "ad_spend": (payload.get("ads") or {}).get("spend"),
            **{f"previous_{key}": previous.get(key) for key in ("count", "revenue", "profit", "margin")},
        })
    note = (
        f"Ranges: {'; '.join(f'{range_id} = {label}' for range_id, label in _RANGE_LABELS.items())}. "
        "`previous_*` is the window of the same length just before (empty for all). `sell_through` is the "
        "range's sales as a percent of those sales plus what is still listed. `profit` and `margin` rest on the "
        "`profit_known` sales with a cost recorded."
    )
    return _table("Sales by range", note, RANGE_COLUMNS, lines)


def _top_groups(
    ranges: dict[str, dict[str, Any]], key: str, title: str, table: str | None, *, by_profit: bool = False,
) -> str:
    """The leading groups of one breakdown for the last 30 days and 12 months; a lookup reads all of them."""
    lines = []
    left_out = 0
    for range_id in ("30d", "12m"):
        groups = list(ranges[range_id][key])
        if by_profit:
            groups.sort(key=lambda group: -(group["profit"] or 0))
        left_out += max(0, len(groups) - TOP_GROUPS)
        lines += [{**group, "label": f"{range_id} {group['label']}"} for group in groups[:TOP_GROUPS]]
    order = "by profit" if by_profit else "as Analytics ranks them"
    note = f"Last 30 days, then last 12 months, {order}."
    if left_out and table:
        note += f" Only the top {TOP_GROUPS} of each are here; {table} has every one, for 30d, 90d, 12m and all."
    elif table:
        note += f" {table} has the same for 90d and all time."
    return _table(title, note, GROUP_COLUMNS, lines)


def _table(title: str, note: str, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"--- {title} ---\nNothing recorded."
    return "\n".join([f"--- {title} ---", note, " | ".join(columns), *(_line(row, columns) for row in rows)])


def lookup_schema() -> str:
    """Every lookup table with every column, one line each, so the model knows what it can fetch."""
    return "\n".join(f"- {name}({', '.join(columns)})" for name, columns in TABLES.items())


def build_messages(db: Session, question: str, shop: Shop, thread_id: str | None = None) -> list[dict]:
    """The prompt for ``question``: instructions, the brief, earlier turns, then the question.

    The seller's question must already be saved; it is the last turn read back.
    History is that conversation only, never another thread or a listing's chat.
    """
    earlier = [
        {"role": row.role, "content": row.text + assistant_changes.history_note(row.changes)}
        for row in list_messages(db, thread_id)[-(HISTORY_MESSAGES + 1):]
    ]
    if not earlier or earlier[-1] != {"role": "user", "content": question}:
        earlier.append({"role": "user", "content": question})
    # The instructions lead so a provider's prefix cache still hits when the figures move.
    lookups = LOOKUP_INSTRUCTIONS.format(limit=MAX_LOOKUPS, rows=LOOKUP_ROWS, schema=lookup_schema())
    system = (
        f"{INSTRUCTIONS}\n{lookups}\n"
        f"{CHANGES_INSTRUCTIONS.format(limit=assistant_changes.MAX_CHANGES)}\n"
        f"{assistant_memory.INSTRUCTIONS}\n{shop.brief}"
    )
    return [{"role": "system", "content": system}, *earlier]


@dataclass
class Lookup:
    """A lookup the model asked for: where its block sits in the reply, and what it holds."""

    kind: str  # "sql" or "media"
    body: str
    start: int
    end: int


async def answer(provider, messages: list[dict], shop: Shop) -> AsyncIterator[tuple[str, str]]:
    """Stream the answer as ``(kind, text)``: ``text``, ``thinking``, ``status``, ``error`` or ``changes``.

    A lookup block in a reply is run and fed back instead of shown, until the
    model answers or runs out of lookups; words written before the block are
    kept. Every lookup comes back to the model with its rows or with a reason,
    never with nothing. A memory block and a changes block the answer ends with
    are kept out of the text and handed over whole, last. Every web address in
    the text is checked against the ones Studio gave the model this turn.
    """
    messages = list(messages)
    links = answer_links.LinkGuard(answer_links.message_texts(messages))
    viewed: list[str] = []
    written = False
    for used in range(MAX_LOOKUPS + 1):
        reply = ""
        released = 0
        lead = "\n\n" if written else ""
        async for item in provider.chat(messages, stream=True):
            kind, text = unpack_stream_item(item)
            if not text:
                continue
            if kind == "thinking":
                yield "thinking", text
                continue
            reply += text
            visible = _visible(reply)
            end = answer_links.safe_end(visible, released)
            if end > released:
                piece = links.clean(visible[released:end])
                released = end
                if piece:
                    yield "text", lead + piece
                    lead, written = "", True
        lookup = find_lookup(reply)
        if lookup is None:
            words, memory, changes = assistant_memory.peel(reply)
            if memory is not None and not words.strip() and not written:
                words = "I'll remember that."
            rest = links.clean(words[released:]) if len(words) > released else ""
            if rest.strip():
                yield "text", lead + rest
            if memory is not None:
                yield "memory", memory
            if changes is not None:
                yield "changes", changes
            return
        before = assistant_memory.visible_text(reply[:lookup.start]).rstrip()
        if len(before) > released:
            piece = links.clean(before[released:])
            if piece.strip():
                yield "text", lead + piece
                written = True
        if used == MAX_LOOKUPS:
            yield "error", "The assistant kept looking things up without answering. Ask again, or ask something narrower."
            return
        yield "status", lookup_label(lookup)
        result = await run_any_lookup(lookup, shop, viewed)
        texts = [result] if isinstance(result, str) else [part["text"] for part in result if part.get("type") == "text"]
        links.allow(texts)
        if used == MAX_LOOKUPS - 1:
            last = "\n\nThat was the last lookup for this question. Answer now with what you have."
            if isinstance(result, str):
                result += last
            else:
                result[0] = {"type": "text", "text": result[0]["text"] + last}
        messages += [{"role": "assistant", "content": reply[:lookup.end]}, {"role": "user", "content": result}]


def _visible(reply: str) -> str:
    """What of a reply still being written the seller may read: never a lookup block or the start of one."""
    shown = assistant_memory.visible_text(reply)
    opening = shown.lstrip()
    if "```".startswith(opening):
        return ""
    if opening.startswith("```"):
        tag = re.match(r"```[ \t]*([A-Za-z_]*)", opening)
        language = tag.group(1).lower()
        settled = len(opening) > tag.end()
        # A reply that opens with a bare fence or a lookup is held whole until it ends.
        if not settled or not language or language in _LOOKUP_LANGS:
            return ""
    lowered = shown.lower()
    at = min((found for fence in _LOOKUP_FENCES if (found := lowered.find(fence)) >= 0), default=len(shown))
    if at < len(shown):
        return shown[:at]
    hold = 0
    for fence in _LOOKUP_FENCES:
        for size in range(min(len(fence) - 1, len(shown)), 0, -1):
            if fence.startswith(lowered[-size:]):
                hold = max(hold, size)
                break
    return shown[:-hold] if hold else shown


def find_lookup(reply: str) -> Lookup | None:
    """The first lookup block in a reply, wherever it sits, or None when the reply is an answer."""
    opening = len(reply) - len(reply.lstrip())
    for match in _FENCED.finditer(reply):
        language = match.group(1).lower()
        body = match.group(2).strip()
        kind = _LOOKUP_LANGS.get(language)
        if kind is None and not language and match.start() == opening and _SELECTISH.match(body):
            kind = "sql"
        if kind is not None:
            return Lookup(kind=kind, body=body, start=match.start(), end=match.end())
    return None


def lookup_sql(reply: str) -> str | None:
    """The query a reply asks for, or None when it asks for none (an empty block is still a request)."""
    lookup = find_lookup(reply)
    return lookup.body if lookup is not None and lookup.kind == "sql" else None


def lookup_label(lookup: Lookup | str) -> str:
    """What the seller sees while a lookup runs: the model's own first-line comment."""
    if isinstance(lookup, Lookup) and lookup.kind == "media":
        return "Looking at the box photos…"
    sql = lookup.body if isinstance(lookup, Lookup) else lookup
    first = sql.lstrip().splitlines()[0].strip() if sql.strip() else ""
    words = first[2:].strip(" .") if first.startswith("--") else ""
    return f"Looking up {words[:80]}…" if words else "Looking up more detail…"


async def run_any_lookup(lookup: Lookup, shop: Shop, viewed: list[str]) -> str | list[dict[str, Any]]:
    """Run one lookup and return what goes back to the model; a failure comes back as its reason."""
    name = "view_box_media" if lookup.kind == "media" else _lookup_name(lookup.body)
    try:
        if lookup.kind == "media":
            return await view_box_media(shop, lookup.body, viewed)
        return await asyncio.to_thread(run_lookup, shop.lookups, lookup.body)
    except Exception as exc:  # noqa: BLE001 - the model is told, so it never answers from nothing
        log.exception("assistant lookup failed")
        return f"Lookup {name} failed: {type(exc).__name__}: {str(exc).strip() or 'no detail'}"


def _lookup_name(sql: str) -> str:
    match = _FROM_TABLE.search(re.sub(r"--[^\n]*", "", sql))
    return match.group(1) if match else "sql"


def _matched(sql: str) -> str:
    where = _WHERE.search(re.sub(r"--[^\n]*", "", sql))
    clause = " ".join(where.group(1).split()) if where else ""
    return f"WHERE {clause[:200]}" if clause else "the query"


def run_lookup(lookups: sqlite3.Connection, sql: str) -> str:
    """Run one SELECT over the in-memory copy and describe the result, or why there is none, as text."""
    sql = (sql or "").strip()
    name = _lookup_name(sql)
    if not re.sub(r"--[^\n]*", "", sql).strip():
        return f"Lookup {name} failed: the sql block was empty. Put one SELECT in it."
    deadline = time.monotonic() + LOOKUP_SECONDS
    lookups.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    try:
        cursor = lookups.execute(sql)
        if cursor.description is None:
            return f"Lookup {name} failed: only a single SELECT can be run."
        header = " | ".join(column[0] for column in cursor.description)
        rows = cursor.fetchmany(LOOKUP_ROWS + 1)
    except sqlite3.Error as exc:
        reason = str(exc).strip() or type(exc).__name__
        if time.monotonic() > deadline:
            reason = f"it ran past the {LOOKUP_SECONDS:g}-second limit. Narrow or aggregate the query"
        elif "no such table" in reason:
            reason += f". The tables are: {', '.join(TABLES)}"
        elif "not authorized" in reason or "prohibited" in reason:
            reason += ". Only a single SELECT over the tables listed can be run"
        return f"Lookup {name} failed: {reason}."
    except Exception as exc:  # noqa: BLE001 - a bad argument is reported like any other failure
        return f"Lookup {name} failed: {type(exc).__name__}: {str(exc).strip() or 'no detail'}."
    finally:
        lookups.set_progress_handler(None, 0)
    if not rows:
        return f"Lookup result: 0 rows matched {_matched(sql)}.\n{header}"
    lines: list[str] = []
    used = 0
    for row in rows[:LOOKUP_ROWS]:
        line = " | ".join("" if value is None else str(value).replace("\n", " ") for value in row)
        if used + len(line) > LOOKUP_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    count = f"{len(lines)} rows"
    if len(lines) < len(rows):
        count = f"the first {len(lines)} rows; there are more, so narrow or aggregate the query to see the rest"
    return "\n".join([f"Lookup result ({count}):", header, *lines])


def _box_ids(body: str) -> list[str]:
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        items = [str(item) for item in parsed]
    elif isinstance(parsed, (str, int)):
        items = [str(parsed)]
    else:
        items = re.split(r"[\n,]+", body)
    ids = [item.strip().lstrip("-*• ").strip().strip("\"'`").strip() for item in items]
    return list(dict.fromkeys(item for item in ids if item))


def _sourcing_rows(lookups: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = lookups.execute("SELECT id, store, title, url FROM sourcing_boxes")
    return [dict(zip(("id", "store", "title", "url"), row, strict=True)) for row in cursor.fetchall()]


async def view_box_media(shop: Shop, body: str, viewed: list[str]) -> str | list[dict[str, Any]]:
    """The photos and video frames of the boxes asked for, as image parts the model can see.

    Up to ``box_media.MAX_BOXES_PER_ANSWER`` boxes in one answer, however many
    lookups ask; ``viewed`` carries the ones already attached.
    """
    ids = _box_ids(body)
    if not ids:
        return ("Lookup view_box_media failed: no box ids were given. Put the id of each box from the "
                "sourcing_boxes table on its own line.")
    rows = await asyncio.to_thread(_sourcing_rows, shop.lookups)
    by_id = {row["id"]: row for row in rows}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_title.setdefault(str(row["title"]).strip().lower(), []).append(row)
    chosen: list[dict[str, Any]] = []
    skipped: list[str] = []
    for ident in ids:
        row = by_id.get(ident)
        if row is None and len(by_title.get(ident.lower(), [])) == 1:
            row = by_title[ident.lower()][0]
        if row is None:
            skipped.append(f"{ident}: NOT VIEWED — no box with that id is on the Sourcing tab.")
        elif row["id"] in viewed or any(row["id"] == other["id"] for other in chosen):
            skipped.append(f"Box {row['id']} ({row['title']}): its images are already attached in this answer.")
        elif len(viewed) + len(chosen) >= box_media.MAX_BOXES_PER_ANSWER:
            skipped.append(f"Box {row['id']} ({row['title']}, {row['store']}): NOT VIEWED — at most "
                           f"{box_media.MAX_BOXES_PER_ANSWER} boxes' photos can be viewed in one answer.")
        else:
            chosen.append(row)
    views = await box_media.view_boxes(chosen) if chosen else []
    viewed.extend(view.box_id for view in views if view.viewed)
    text, parts = box_media.describe(views, skipped=skipped)
    return [{"type": "text", "text": text}, *parts] if parts else text


def _lookup_database(tables: dict[str, list[dict[str, Any]]]) -> sqlite3.Connection:
    """A throwaway copy of the shop that a lookup can read and nothing else can reach."""
    # Lookups run on a worker thread, one at a time.
    lookups = sqlite3.connect(":memory:", check_same_thread=False)
    for name, columns in TABLES.items():
        lookups.execute(f"CREATE TABLE {name} ({', '.join(columns)})")
        lookups.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' for _ in columns)})",
            [[_cell(row.get(column)) for column in columns] for row in tables[name]],
        )
    lookups.commit()
    # Reads only from here on: no ATTACH to a real database, no PRAGMA, no writes.
    lookups.set_authorizer(
        lambda action, *_details: sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY,
    )
    return lookups


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return value
    return json.dumps(value, default=str)


def _section(title: str, payload: Any) -> str:
    if payload in (None, [], {}):
        return f"--- {title} ---\nNothing recorded."
    return f"--- {title} ---\n{json.dumps(payload, separators=(',', ':'), default=str)}"


def _records(db: Session, rows: list[AnalyticsItem], now: datetime) -> list[dict[str, Any]]:
    """Every listing as one record, newest sales first, then what is still for sale."""
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.listing import Listing, ListingRevision
    from vendoo_studio.services import size_audit
    from vendoo_studio.services.vendoo_import import parse_notes, split_vendoo_labels

    listings = dict(
        db.query(Listing.conversation_id, ListingRevision.listing_json)
        .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
        .all()
    )
    notes = {conv_id: parse_notes(raw) for conv_id, raw in db.query(Conversation.id, Conversation.notes).all()}
    photo_sizes = _photo_sizes(db)
    records = []
    for item in _ordered(rows):
        listing = listings.get(item.conversation_id)
        listing = listing if isinstance(listing, dict) else {}
        note = notes.get(item.conversation_id, {})
        sold = item.status == "sold"
        days = item.days_listed
        if item.status == "active" and item.listed_at is not None:
            days = max(0, (now - item.listed_at).days)
        labels = listing.get("labels") or split_vendoo_labels(note.get("vendooLabels"))
        records.append({
            "id": item.conversation_id,
            "title": item.title,
            "status": item.status,
            "brand": item.brand or None,
            "category": item.category or None,
            "size": _text(listing.get("size")),
            "condition": _text(listing.get("condition") or note.get("condition")),
            "color": _text(listing.get("primaryColor") or listing.get("color")),
            "labels": _joined(labels),
            "sku": _text(listing.get("sku")),
            "listed_on": _joined(note.get("vendooMarketplaces")),
            "asking": item.price or None,
            "cost": item.cost,
            "sold_for": item.sold_price,
            "fees": item.fees,
            "profit": round(item.profit, 2) if sold and item.has_cost and item.sold_price is not None else None,
            "sold_on": None if item.marketplace == "unknown" else item.marketplace,
            "listed": item.listed_at.date().isoformat() if item.listed_at else None,
            "sold": item.sold_at.date().isoformat() if item.sold_at else None,
            "days": days,
            "box": item.box_id,
            "shipping_cost": item.shipping_cost if sold else None,
            "shipping_credit": item.shipping_credit if sold else None,
            "form_sizes": ", ".join(f"{name}: {size}" for name, size in size_audit.form_sizes(listing).items()) or None,
            "photo_size": photo_sizes.get(item.conversation_id),
            "size_issue": size_audit.size_problems(listing, photo_sizes.get(item.conversation_id)),
            "description": _text(listing.get("description")),
            "details": json.dumps(listing, default=str) if listing else None,
        })
    return records


def _photo_sizes(db: Session) -> dict[str, str]:
    """The size each listing's latest photo analysis read, with where it read it."""
    from vendoo_studio.models.conversation import Message
    from vendoo_studio.services.size_audit import photo_size

    sizes = {}
    analyses = (
        db.query(Message.conversation_id, Message.text)
        .filter(Message.role == "system", Message.text.like("Photo analysis%"))
        .order_by(Message.created_at, Message.id)
        .all()
    )
    for conv_id, text in analyses:
        size = photo_size(text)
        if size:
            sizes[conv_id] = size
    return sizes


def _ordered(rows: list[AnalyticsItem]) -> list[AnalyticsItem]:
    oldest = datetime.min.replace(tzinfo=UTC)
    sold = sorted(
        (item for item in rows if item.status == "sold"), key=lambda item: item.sold_at or oldest, reverse=True,
    )
    active = sorted(
        (item for item in rows if item.status == "active"), key=lambda item: item.listed_at or oldest,
    )
    rest = [item for item in rows if item.status not in {"sold", "active"}]
    return [*sold, *active, *rest]


def _text(value: Any) -> str | None:
    return str(value).strip() or None if isinstance(value, (str, int, float)) else None


def _joined(values: Any) -> str | None:
    if not isinstance(values, list):
        return None
    return ", ".join(str(value).strip() for value in values if str(value).strip()) or None


def _listing_summary(records: list[dict[str, Any]]) -> str:
    """What is for sale by category, the listings that have sat longest, and the latest sales."""
    statuses = Counter(record["status"] for record in records)
    counted = ", ".join(f"{count} {status}" for status, count in statuses.most_common())
    title = f"--- Listings ({len(records)}{': ' + counted if counted else ''}) ---"
    if not records:
        return f"{title}\nNo listings yet."
    active = [record for record in records if record["status"] == "active"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in active:
        groups.setdefault(record["category"] or "Uncategorized", []).append(record)
    by_category = []
    for category, members in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        days = [record["days"] for record in members if record["days"] is not None]
        by_category.append({
            "category": category,
            "active": len(members),
            "asking_value": round(sum(record["asking"] or 0 for record in members), 2),
            "median_days_listed": statistics.median(days) if days else None,
            "most_days_listed": max(days) if days else None,
        })
    longest = sorted(active, key=lambda record: (record["days"] is None, -(record["days"] or 0), record["id"]))
    latest = [record for record in records if record["status"] == "sold"][:LATEST_SALES]
    note = (
        "Only these rows are here. The `listings` lookup table has every listing with every column: brand, size, "
        "condition, labels, cost, fees, profit, description and each marketplace's fields. `days` is days listed "
        "so far for an active listing and days to sell for a sold one. Empty means not recorded."
    )
    parts = [title, note]
    if active:
        shown = by_category[:TOP_GROUPS]
        parts += [
            f"Active listings by category ({len(shown)} of {len(by_category)} categories, most listings first):",
            " | ".join(ACTIVE_GROUP_COLUMNS), *(_line(group, ACTIVE_GROUP_COLUMNS) for group in shown),
            f"Active listings up longest ({min(LONGEST_LISTED, len(active))} of {len(active)}):",
            " | ".join(LONGEST_COLUMNS), *(_line(record, LONGEST_COLUMNS) for record in longest[:LONGEST_LISTED]),
        ]
    if latest:
        parts += [
            f"Latest sales ({len(latest)} of {statuses['sold']}):",
            " | ".join(SALE_COLUMNS), *(_line(record, SALE_COLUMNS) for record in latest),
        ]
    return "\n".join(parts)


def _sizing_rows(records: list[dict[str, Any]]) -> str:
    """Listings still for sale whose sizing needs a look: forms out of step, or at odds with the photos."""
    flagged = [record for record in records if record["size_issue"] and record["status"] != "sold"]
    title = f"--- Listings whose sizing needs a look ({len(flagged)}) ---"
    if not flagged:
        return f"{title}\nEvery unsold listing has the same size on each marketplace form and in its title."
    note = (
        "`size` is the general size, `form_sizes` what each marketplace form holds, and `photo_size` what the "
        "photos showed and where. A size the photos read differently is not always wrong: M and Medium are the same."
    )
    if len(flagged) > SIZING_ROWS:
        note += f" Only the first {SIZING_ROWS} are shown; the rest are the listings rows with a size_issue that have not sold."
    return "\n".join([
        title, note, " | ".join(SIZING_COLUMNS),
        *(_line(record, SIZING_COLUMNS) for record in flagged[:SIZING_ROWS]),
    ])


def _line(record: dict[str, Any], columns: tuple[str, ...]) -> str:
    return " | ".join(_shown(record.get(column)) for column in columns)


def _shown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "/").replace("\n", " ")


def _price_changes(db: Session) -> list[dict[str, Any]]:
    """Each time a listing's asking price moved between saved revisions, newest first."""
    from sqlalchemy import func

    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.services.inventory_analytics import _number

    changes = []
    last: dict[str, float] = {}
    revisions = (
        db.query(
            ListingRevision.conversation_id,
            func.json_extract(ListingRevision.listing_json, "$.price"),
            ListingRevision.created_at,
        )
        .order_by(ListingRevision.created_at, ListingRevision.id)
        .all()
    )
    for conv_id, raw, created_at in revisions:
        price = _number(raw)
        if not price:
            continue
        before = last.get(conv_id)
        last[conv_id] = price
        if before is not None and before != price:
            changes.append({
                "listing_id": conv_id,
                "changed_on": created_at.date().isoformat() if created_at else None,
                "old_price": before,
                "new_price": price,
            })
    changes.reverse()
    return changes


def _price_change_rows(changes: list[dict[str, Any]]) -> str:
    title = f"--- Asking-price changes ({len(changes)}) ---"
    if not changes:
        return f"{title}\nNo listing's asking price has been changed in Studio."
    note = "Each time a listing's asking price was changed in Studio, newest first."
    if len(changes) > PRICE_CHANGE_ROWS:
        note += f" Only the newest {PRICE_CHANGE_ROWS} are shown; a lookup can read the rest."
    return "\n".join([
        title, note, " | ".join(CHANGE_COLUMNS),
        *(_line(change, CHANGE_COLUMNS) for change in changes[:PRICE_CHANGE_ROWS]),
    ])


def _sourcing(state: dict, tab_boxes: list[dict[str, Any]], now: datetime) -> str:
    """The current buy list: what the seller told Sourcing, what it would buy, and why when nothing."""
    return _section("Sourcing buy list", {
        "current_settings": state.get("prefs"),
        "trending_terms": (state.get("trend") or {}).get("terms"),
        **sourcing_boxes.buy_list_summary(state, tab_boxes, now),
    })


def _sourcing_box_rows(tab_boxes: list[dict[str, Any]]) -> str:
    """The Sourcing tab's best boxes, in the tab's order; the sourcing_boxes lookup has every one."""
    title = f"--- Best boxes on the Sourcing tab ({min(TOP_BOXES, len(tab_boxes))} of {len(tab_boxes)}) ---"
    if not tab_boxes:
        return f"{title}\nNo boxes in the last sourcing check."
    note = (
        "Recommended boxes first, then each supplier's top picks, then the rest by ROI. `url` is the box's product "
        "page as the store gave it; it is the only link to give the seller for that box. `contents` is the kind of "
        "piece, `landed` price plus shipping and fees, `roi_percent` and `projected_profit` at the settings the "
        "check used, and `kind_*` the seller's own sales of that kind in the last 12 months. Empty means unknown."
    )
    if len(tab_boxes) > TOP_BOXES:
        note += (f" The other {len(tab_boxes) - TOP_BOXES} boxes, and every box's other figures (shipping, pieces, "
                 "resale_per_piece, roi_gap_points, on_its_own, buy_list), are in the sourcing_boxes lookup table, "
                 "with their urls.")
    return "\n".join([
        title, note, " | ".join(TOP_BOX_COLUMNS), *(_line(box, TOP_BOX_COLUMNS) for box in tab_boxes[:TOP_BOXES]),
    ])


def _sale_events(db: Session) -> list[dict[str, Any]]:
    from vendoo_studio.models.sale_event import SaleEvent

    return [
        {
            "title": event.title,
            "marketplace": event.marketplace,
            "start_date": event.start_date.isoformat(),
            "end_date": event.end_date.isoformat(),
            "status": event.status,
            "discount_percent": event.discount_percent,
            "listings": len(event.items or []),
            "notes": event.notes or "",
        }
        for event in db.query(SaleEvent).order_by(SaleEvent.start_date.desc()).all()
    ]
