"""The business assistant: questions about the whole shop, answered from Studio's data.

A listing's chat knows one item. This one is handed a summary of the business
on every question — the same sales and inventory totals Analytics shows, by
range and by month, the leading brands and categories, the listings that have
sat longest and the latest sales, the boxes bought and what they returned, ad
spend, and sale events — and the exact tables it can look the rest up in. The
brief is read fresh each time, so an answer reflects Anteroom's records as they
are now.

Every row behind the summary can be looked up. Each listing, its description and
full field values (in ``listing_details``), and the sales breakdowns are copied
into a throwaway in-memory SQLite database on first lookup, and the model may
ask for a ``SELECT`` over it before it answers. The copy is the only thing a
lookup can touch. The summary stays small, so a question does not spend the
seller's model usage on rows it never needed.

It reads, and it proposes. A reply may end with changes to listings, which are
kept with the reply for the seller to apply or dismiss
(``services/assistant_changes.py``). Nothing here writes a listing or reaches
a marketplace.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import sqlite3
import statistics
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from anteroom.database import json_value
from anteroom.models.assistant import NEW_THREAD_TITLE, AssistantMessage, AssistantThread
from anteroom.models.conversation import utcnow
from anteroom.providers.xiaomi_mimo import unpack_stream_item
from anteroom.services import (
    answer_links,
    assistant_changes,
    assistant_memory,
    business_context,
)
from anteroom.services.inventory_analytics import ANALYTICS_RANGES, AnalyticsItem

log = logging.getLogger("anteroom.business_assistant")

# Earlier turns sent back with a question, so a follow-up can lean on them.
# The window moves in steps so the prompt prefix stays steady across turns for provider caching.
HISTORY_MESSAGES = 20
HISTORY_STEP = 10
# How many rows of each kind the summary shows; a lookup reads every one.
LONGEST_LISTED = 15
LATEST_SALES = 10
TOP_BOXES = 15
SIZING_ROWS = 10
PRICE_CHANGE_ROWS = 10
AD_ROWS = 10
EVENT_ROWS = 10
TOP_GROUPS = 12
OVER_TIME_BUCKETS = 24
# Listings read per statement for a question: a few megabytes, well inside the
# hosted statement timeout even when the database is busy.
LISTING_BATCH = 200
# What the whole prompt may cost on a large shop, in tokens (about 3.5 characters each). The tests hold a
# shop of hundreds of listings to it, so a new section cannot quietly bring every row back.
PROMPT_TOKEN_BUDGET = 20_000
# Lookups one question may run (rounds, and blocks per round), and what one may cost and return.
MAX_LOOKUPS = 6
LOOKUPS_PER_ROUND = 3
LOOKUP_SECONDS = 5.0
LOOKUP_ROWS = 200
LOOKUP_CHARS = 24_000

INSTRUCTIONS = (
    "You are the business assistant inside Anteroom, talking to a reseller about their own shop. "
    "They sell clothing and similar goods on marketplaces such as eBay, Poshmark, Mercari, Depop and Etsy, "
    "listed on each one directly from Anteroom.\n\n"
    "Below is a summary of the business, taken now: the totals Analytics shows and the few rows most often "
    "wanted. Every row behind it is in the lookup tables described below.\n"
    "- Answer from the summary only when it holds everything the question needs. When the question needs rows "
    "it leaves out, look them up first and then answer: comparing bought boxes, a specific listing's "
    "details, every stale listing, sales of one brand, category, size, marketplace or month, a breakdown the "
    "summary does not show. The summary's short tables are the top rows only; never present one as the whole "
    "list or rank from it alone. Never tell the seller a figure is unavailable until a lookup has failed to "
    "find it.\n"
    "- Work figures out from the rows when no total is given, and check a total against the rows when both exist.\n"
    "- Say what a figure rests on when the data is partial: how many sales had a cost recorded, "
    "how many listings had a list date, and so on. Unknown is not zero.\n"
    "- When neither the summary nor a lookup can answer the question, say what is missing and where in Anteroom "
    "it gets recorded. Never invent a sale, a price, a cost or a date.\n"
    "- Profit is the sold price plus shipping the buyer paid, less fees, shipping the seller paid, and the item's cost.\n"
    "- Give advice when asked — what to reprice, discount or drop — and tie each point to the numbers behind it. "
    "Fit it to what the seller says about their business and to what you remember about them.\n"
    "- You can propose changes to listings, described below, and the seller applies them. You cannot change a setting, "
    "and you cannot list, delist, publish or send anything yourself. When the seller asks for a scheduled "
    "automation, propose it as described below; it runs only after they confirm it.\n"
    "- When you name a specific listing, link it so the seller can open it: [its title](#listing-ID), "
    "with the id from its row. Use only ids that appear in the summary or in a lookup result.\n"
    "- Other ids only tie rows together. Name a box by its store and title, never by its id.\n"
    "- Web links: never build, guess or complete a URL. Give only a `url` "
    "Anteroom returned, exactly as it came, from the summary or a lookup this turn. Anteroom removes any other "
    "link from the answer.\n"
    "- Write plain English and lead with the answer. Use a table when comparing several things. Amounts are US dollars.\n"
)

LOOKUP_INSTRUCTIONS = (
    "Looking things up: the shop is also loaded into a SQLite database. When the summary does not already "
    "answer the question, reply with nothing but one ```sql block holding a single SELECT, or up to {per_round} "
    "```sql blocks, each one SELECT, when the figures are independent. Make each block's first line "
    "a comment saying in plain words what you are looking up, like `-- sales by size`. Anteroom runs them and "
    "replies with up to {rows} rows each, so filter, aggregate, or ORDER BY with a LIMIT. You may do this up to "
    "{limit} rounds for one question, then answer. Never show SQL or mention the database to the seller.\n"
    "Lookup tables, all of them, with every column:\n{schema}\n"
    "Dates are YYYY-MM-DD text. Amounts are numbers; NULL means not recorded. `margin` is profit as a percent of "
    "revenue over the sales with a cost recorded (`profit_known`); `median_days` is days from listing to sale.\n"
    "`listings` is every listing. `days` is days to sell for a sold listing and days listed so far for an active "
    "one; `listed_on` is where it is cross-listed, `sold_on` where it sold, and `box` the id of the bought box it "
    "came from (`boxes.id`). `form_sizes` is the size each marketplace form holds, `photo_size` the size read "
    "from the photos and where it was read, and `size_issue` why the sizing needs a look (NULL when nothing "
    "stands out, and for sold listings). `listing_details(listing_id, description, details)` holds each listing's "
    "description and full JSON (including each marketplace's fields; read with json_extract(details, '$.ebay_specifics')), "
    "one row per listing; join `listing_details.listing_id = listings.id` and select it only for the listings you need. "
    "`category` is the Analytics category; a kind of piece is best matched on the title too, like title LIKE '%jacket%'.\n"
    "`boxes` is the wholesale boxes the seller bought and what each returned; `price_changes` each asking-price "
    "change made in Anteroom.\n"
    "`sales_by_category`, `sales_by_brand` and `sales_by_marketplace` are the Analytics breakdowns for each "
    "`period` (30d, 90d, 12m, all). `sales_over_time` is each range's sales per `bucket`, as the Analytics chart "
    "draws them, `position` 1 the oldest; range all reaches back to the first sale and range 12m is one row per "
    "calendar month.\n"
    "`median(column)` is available alongside `avg`, `sum`, `min`, `max`, and `count`.\n"
    "Every lookup answers: rows, \"0 rows matched\" (with the closest real values in that column when any exist), or "
    "\"Lookup ... failed: <reason>\". Read that reply; when a lookup failed or matched nothing, say so or try a "
    "corrected query using a listed value only if it names the same thing, and never fill the gap with a guess.\n"
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
    "- Sizes: a listing has one general size, and the title carries that size. Men's bottoms keep the waist as "
    "the general size (34). Each marketplace form then uses the size that marketplace accepts: eBay waist plus "
    "inseam, Poshmark Waist 32, women's 0-24, and Petite, Plus, or Tall. To fix the general size, set `size`. "
    "Work it out from the photo size (a tag beats a guess), the title, and the measurements in the description. "
    "When the evidence does not settle a listing's size, leave it out and name it for the seller instead of guessing.\n"
    "- Use listing ids from the snapshot or a lookup, and give final values you worked out yourself, not formulas. "
    "Up to {limit} listings in one proposal.\n"
    "- Anteroom shows the seller each value before and after with Apply and Dismiss buttons, so do not list every "
    "change in words. Nothing changes until they press Apply: say the changes are ready to review, never that "
    "they were made.\n"
    "- Propose only what was asked for. Do not propose changes the seller has not asked for or agreed to; "
    "suggest them in words first.\n"
    "- Applied changes are saved in Anteroom. A listing already live on a marketplace keeps its old values there "
    "until the seller updates it from the listing.\n"
    "- Condition, colour, category and other marketplace-specific fields are changed in that listing's own chat; "
    "link the listing instead.\n"
    "- An Anteroom note after one of your earlier replies says what you proposed and whether the seller applied it. "
    "Never write such a note yourself.\n"
)

AUTOMATION_INSTRUCTIONS = (
    "Scheduled automations: when the seller asks you to schedule a delist, relist, regenerate, or price drop, "
    "or a rule such as relisting an item after a number of days, say in a sentence what you will set up and end "
    "the reply with one ```automation block. It is one JSON object:\n"
    "```automation\n"
    '{"prompt": "When an item reaches 60 days listed on eBay and Poshmark, delist and relist it and drop the price by 10%. Don\'t go below $15."}\n'
    "```\n"
    "- Name every marketplace in the prompt. Never say all marketplaces. Never include sold items.\n"
    "- Anteroom shows a preview. Nothing runs until the seller presses Confirm.\n"
    "- To change one, {\"op\": \"update\", \"id\": \"TASK-ID\", \"prompt\": \"...\"}. To pause one, "
    "{\"op\": \"pause\", \"id\": \"TASK-ID\"}. Ids are listed under Scheduled automations. Do not invent one.\n"
    "- Propose only an automation the seller asked for.\n"
)

BREAKDOWN_PERIODS = ("30d", "90d", "12m", "all")
# What each sales group carries, in the summary and in the breakdown tables.
GROUP_STATS = (
    "count", "revenue", "average_price", "profit", "profit_known", "margin", "median_days", "days_known",
)
CATEGORY_COLUMNS = ("period", "category", *GROUP_STATS)
BRAND_COLUMNS = ("period", "brand", *GROUP_STATS)
MARKETPLACE_COLUMNS = ("period", "marketplace", *GROUP_STATS)
OVER_TIME_COLUMNS = ("range", "position", "bucket", *GROUP_STATS)
# The summary's own tables.
RANGE_COLUMNS = (
    "range", "count", "revenue", "profit", "profit_known", "margin", "average_price", "median_days",
    "sell_through", "ad_spend", "previous_count", "previous_revenue", "previous_profit", "previous_margin",
)
GROUP_COLUMNS = ("label", "count", "revenue", "profit", "profit_known", "margin", "average_price", "median_days")
LONGEST_COLUMNS = ("id", "title", "brand", "category", "size", "asking", "cost", "days", "listed", "listed_on")
SALE_COLUMNS = ("id", "title", "brand", "category", "sold_for", "profit", "sold_on", "sold", "days")
ACTIVE_GROUP_COLUMNS = ("category", "active", "asking_value", "median_days_listed", "most_days_listed")
# One listing, as a lookup reads it.
LISTING_COLUMNS = (
    "id", "title", "status", "brand", "category", "size", "condition", "color", "labels", "sku", "listed_on",
    "asking", "cost", "sold_for", "fees", "profit", "sold_on", "listed", "sold", "days", "box",
    "shipping_cost", "shipping_credit", "form_sizes", "photo_size", "size_issue",
)
LISTING_DETAIL_COLUMNS = ("listing_id", "description", "details")
# A listing whose sizing needs a look, as the summary shows it.
SIZING_COLUMNS = ("id", "title", "status", "category", "size", "form_sizes", "photo_size", "size_issue")
CHANGE_COLUMNS = ("listing_id", "changed_on", "old_price", "new_price")
BOX_COLUMNS = (
    "id", "store", "title", "price", "shipping", "pieces", "unsellable", "sellable", "unsellable_rate",
    "cost_per_piece", "bought_at", "spent", "listings", "listed", "sold", "returned", "profit", "roi",
    "sell_through", "sell_through_by_age", "unsold_asking", "median_days", "asp", "sold_premium",
)
BOXES_SHOWN_COLUMNS = (
    "id", "store", "title", "bought_at", "spent", "pieces", "listed", "sold", "profit", "roi", "sell_through",
)
AD_COLUMNS = (
    "marketplace", "start_date", "end_date", "spend", "clicks", "orders", "revenue", "roas", "cost_per_click", "notes",
)
EVENT_COLUMNS = (
    "title", "marketplace", "start_date", "end_date", "status", "discount_percent", "listings", "notes",
)
TABLES = {
    "listings": LISTING_COLUMNS,
    "listing_details": LISTING_DETAIL_COLUMNS,
    "price_changes": CHANGE_COLUMNS,
    "boxes": BOX_COLUMNS,
    "ad_spend": AD_COLUMNS,
    "sale_events": EVENT_COLUMNS,
    "sales_by_category": CATEGORY_COLUMNS,
    "sales_by_brand": BRAND_COLUMNS,
    "sales_by_marketplace": MARKETPLACE_COLUMNS,
    "sales_over_time": OVER_TIME_COLUMNS,
    "business_expenses": ("incurred_date", "category", "accounting_type", "currency", "amount_cents"),
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
# A lookup is a fenced block in the reply: ```sql (or ```sqlite) holding one SELECT.
# A bare ``` fence opening the reply with a SELECT counts too.
_LOOKUP_LANGS = {"sql": "sql", "sqlite": "sql"}
_LOOKUP_FENCES = ("```sql",)
_FENCED = re.compile(r"```[ \t]*([A-Za-z_]*)(.*?)(?:```|\Z)", re.DOTALL)
_SELECTISH = re.compile(r"^(?:--|select\b|with\b)", re.IGNORECASE)
_FROM_TABLE = re.compile(r"\bfrom\s+([A-Za-z_]+)", re.IGNORECASE)
_WHERE = re.compile(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|\bhaving\b|;|\Z)",
                    re.IGNORECASE | re.DOTALL)
_FILTER_LITERAL = re.compile(
    r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|like)\s*'((?:[^']|'')*)'",
    re.IGNORECASE,
)
# Only these (table, column) pairs are ever queried for 0-row hints; identifiers come from here, never from SQL text.
_HINT_COLUMNS: dict[str, frozenset[str]] = {
    "listings": frozenset({"brand", "category", "size", "condition", "color", "sold_on", "status"}),
    "sales_by_category": frozenset({"category"}),
    "sales_by_brand": frozenset({"brand"}),
    "sales_by_marketplace": frozenset({"marketplace"}),
}
_HINT_MATCHES = 5
_HINT_CHARS = 600
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
    automation: dict[str, Any] | None = None, thread_id: str | None = None,
    on_committed: Callable[[], None] | None = None,
):
    thread = resolve_thread(db, thread_id)
    row = AssistantMessage(
        thread_id=thread.id, role=role, text=text, provider=provider, model=model,
        changes=changes, attachments=attachments or None, automation=automation,
    )
    if role == "user" and thread.title == NEW_THREAD_TITLE:
        titled = thread_title_from(text)
        if titled != NEW_THREAD_TITLE:
            thread.title = titled
    thread.updated_at = utcnow()
    db.add(row)
    db.commit()
    # This commit owns the message, unlike earlier memory/thread commits.
    # Confirm before refresh: cancellation can abandon the caller's thread wait.
    if on_committed is not None:
        on_committed()
    db.refresh(row)
    return row


def _automation_lines(db: Session) -> str:
    from anteroom.services.automations import brief_lines

    return brief_lines(db)


def _automation_note(payload: dict | None) -> str:
    if not payload:
        return ""
    proposal = payload.get("proposal") if isinstance(payload, dict) else None
    name = (proposal or {}).get("name") or "an automation"
    status = payload.get("status") or "proposed"
    return f"\n\n[Anteroom note: you drafted {name}. It is {status}.]"


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
        "automation": row.automation,
        "attachments": row.attachments or [],
    }


@dataclass
class Shop:
    """The business as of one moment: what the model is told, and what it can look up.

    ``tables`` may be shared across questions via ``business_context``; never mutate it.
    The in-memory SQLite copy is built on first access to ``lookups`` so answers that
    need no lookup never pay to create it.
    """

    brief: str
    tables: dict[str, list[dict[str, Any]]]
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _details_loaded: bool = field(default=False, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def _ensure_conn(self, *, need_details: bool) -> sqlite3.Connection:
        if self._conn is None or (need_details and not self._details_loaded):
            with self._lock:
                if self._conn is None:
                    tables = self.tables if need_details else {**self.tables, "listing_details": []}
                    self._conn = _lookup_database(tables)
                    self._details_loaded = need_details
                elif need_details and not self._details_loaded:
                    _populate_lookup_table(self._conn, "listing_details", self.tables["listing_details"])
                    self._details_loaded = True
        return self._conn

    @property
    def lookups(self) -> sqlite3.Connection:
        return self._ensure_conn(need_details=True)

    def read_lookup[T](self, read: Callable[[sqlite3.Connection], T], *, need_details: bool = False) -> T:
        # Cancellation of the async waiter does not stop this worker. Hold the
        # lock for the whole read so close() cannot interrupt it.
        with self._lock:
            return read(self._ensure_conn(need_details=need_details))

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            self._details_loaded = False
        if conn is not None:
            conn.close()


def read_shop(db: Session, *, now: datetime | None = None) -> Shop:
    """Everything Studio knows about the business, read once for one question.

    Without ``now`` the figures may come from the copy kept for the last
    question (``business_context``), until anything they rest on is written.
    The brief is the same text for the same data, with what the seller says
    and what the assistant remembers last, so a provider's prompt cache keeps
    the whole snapshot from one question to the next.
    """
    from anteroom.services.user_settings import get_business_note

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
    return Shop(brief="\n\n".join(section for section in sections if section), tables=tables)


def _read_data(db: Session, clock: datetime) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """The summary as text, and the tables a lookup reads."""
    from anteroom.services import ad_spend, boxes
    from anteroom.database import end_read
    from anteroom.services.business_expenses import entries
    from anteroom.services.inventory_analytics import analytics_ranges, read_analytics_inputs, rows_from_inputs
    from anteroom.services.profit_and_loss import report

    # Release each read before calculations or a potentially blocking lock.
    inputs = read_analytics_inputs(db)
    end_read(db, keep=[line for lines in inputs.sales.values() for line in lines])
    rows = rows_from_inputs(inputs)
    ad_entries: list[Any] = []
    sale_lines: list[dict[str, Any]] = []
    ranges = analytics_ranges(db, ANALYTICS_RANGES, now=clock, rows=rows,
                              out_ad_entries=ad_entries, out_sale_records=sale_lines)
    everything = ranges["all"]
    parsed_notes = {conv_id: note for conv_id, _title, _status, note, _box in inputs.conversations}
    records, detail_rows = _records(db, rows, clock, parsed_notes=parsed_notes)
    changes = _price_changes(db)
    box_results = boxes.results(db, rows=rows, release=True)
    ads = [ad_spend.entry_view(row) for row in ad_entries]
    events = _sale_events(db)
    expense_rows = [
        {"incurred_date": row.incurred_date.isoformat(), "category": row.category,
         "accounting_type": row.accounting_type, "currency": row.currency, "amount_cents": row.amount_cents}
        for row in db.scalars(entries(date.min, date.max))
    ]
    year_start = clock.date().replace(month=1, day=1)
    year_sales = [
        sale for sale in sale_lines
        if year_start.isoformat() <= str(sale["sold_at"] or sale["received_at"])[:10] <= clock.date().isoformat()
    ]
    profit_and_loss = {
        mode: [currency.model_dump() for currency in report(db, year_start, clock.date(), mode, year_sales)]
        for mode in ("accrual", "cash")
    }
    end_read(db)
    automation_lines = _automation_lines(db)
    end_read(db)
    breakdowns = {
        name: _breakdown(ranges, key, label)
        for name, key, label in (
            ("sales_by_category", "categories", "category"),
            ("sales_by_brand", "brands", "brand"),
            ("sales_by_marketplace", "marketplaces", "marketplace"),
        )
    }
    over_time = [
        {"range": range_id, "position": position, "bucket": bucket["label"], **{key: bucket[key] for key in GROUP_STATS}}
        for range_id, payload in ranges.items() for position, bucket in enumerate(payload["periods"], 1)
    ]
    sections = [
        f"Today is {clock.date().isoformat()} (UTC).",
        _section("Inventory now", {
            **everything["inventory"],
            "aging_of_active_listings": [
                {key: bucket[key] for key in ("label", "count", "asking_value")} for bucket in everything["aging"]
            ],
            "sold_listings_missing_a_sale_date": everything["undated_sales"],
            "sales_missing_sale_data": len(everything["incomplete_sales"]),

        }),
        _sales_by_range(ranges),
        _section("Profit and loss this year by currency (amounts in cents)", profit_and_loss),
        _sales_over_time(everything["periods"]),
        _top_groups(ranges, "marketplaces", "Sales by marketplace", "sales_by_marketplace"),
        _top_groups(ranges, "categories", "Sales by category", "sales_by_category"),
        _top_groups(ranges, "brands", "Brands", "sales_by_brand", by_profit=True),
        _top_groups(ranges, "price_tiers", "Sales by price tier", None),
        _listing_summary(records),
        _sizing_rows(records),
        _price_change_rows(changes),
        _box_rows(box_results),
        _ad_rows(ads),
        _event_rows(events),
        automation_lines,
    ]
    return (
        "\n\n".join(section for section in sections if section),
        {
            "listings": records,
            "listing_details": detail_rows,
            "price_changes": changes,
            "boxes": box_results["boxes"],
            "ad_spend": ads,
            "sale_events": events,
            "business_expenses": expense_rows,
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


def _sales_over_time(buckets: list[dict[str, Any]]) -> str:
    """Every sale since the first, bucketed as the Analytics chart draws all time: by month for most shops."""
    shown = buckets[-OVER_TIME_BUCKETS:]
    note = ("All time, oldest first, as the Analytics chart buckets it. A label without a year is this year. "
            "sales_over_time has each range's buckets.")
    if len(shown) < len(buckets):
        note += f" Only the latest {OVER_TIME_BUCKETS} of {len(buckets)} are here; sales_over_time has every one."
    return _table("Sales over time", note, GROUP_COLUMNS, shown)


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
    rows = list_messages(db, thread_id)
    start = max(0, len(rows) - (HISTORY_MESSAGES + 1))
    start -= start % HISTORY_STEP
    while start + 1 < len(rows) and rows[start].role != "user":
        start += 1
    earlier = [
        {"role": row.role, "content": row.text + assistant_changes.history_note(row.changes) + _automation_note(row.automation)}
        for row in rows[start:]
    ]
    if not earlier or earlier[-1] != {"role": "user", "content": question}:
        earlier.append({"role": "user", "content": question})
    # The instructions lead so a provider's prefix cache still hits when the figures move.
    lookups = LOOKUP_INSTRUCTIONS.format(
        limit=MAX_LOOKUPS, per_round=LOOKUPS_PER_ROUND, rows=LOOKUP_ROWS, schema=lookup_schema(),
    )
    system = (
        f"{INSTRUCTIONS}\n{lookups}\n"
        f"{CHANGES_INSTRUCTIONS.format(limit=assistant_changes.MAX_CHANGES)}\n"
        f"{AUTOMATION_INSTRUCTIONS}\n"
        f"{assistant_memory.INSTRUCTIONS}\n{shop.brief}"
    )
    return [{"role": "system", "content": system}, *earlier]


TIMING_CAP_MS = 600_000
# An answer this slow logs its timing as a warning, which the hosted app keeps; faster ones log at INFO.
SLOW_ANSWER_MS = 30_000


def elapsed_ms(since: float) -> int:
    """Whole milliseconds since a ``time.monotonic()`` reading, capped so a log line stays bounded."""
    return min(TIMING_CAP_MS, max(0, round((time.monotonic() - since) * 1000)))


@dataclass
class AnswerTiming:
    """Where one answer's time went, as numbers only: no question, answer, query or provider text.

    Each model round is one provider call: the first answers the question, each
    later one follows a lookup. ``first_token_ms`` is when the round's first
    piece (thinking included) arrived, or None when none did.
    """

    started: float = field(default_factory=time.monotonic)
    queue_wait_ms: int | None = None
    prepare_ms: int = 0
    first_token_ms: list[int | None] = field(default_factory=list)
    round_ms: list[int] = field(default_factory=list)
    lookups: int = 0
    lookup_ms: int = 0
    save_ms: int | None = None

    def line(self, outcome: str, total_ms: int) -> str:
        def shown(value: int | None) -> str:
            return "-" if value is None else str(value)

        return (
            f"assistant answer timing: outcome={outcome} queue_wait_ms={shown(self.queue_wait_ms)} "
            f"prepare_ms={self.prepare_ms} rounds={len(self.round_ms)} "
            f"first_token_ms={','.join(shown(value) for value in self.first_token_ms)} "
            f"round_ms={','.join(str(value) for value in self.round_ms)} lookups={self.lookups} "
            f"lookup_ms={self.lookup_ms} save_ms={shown(self.save_ms)} total_ms={total_ms}"
        )


@dataclass
class Lookup:
    """A lookup the model asked for: where its block sits in the reply, and what it holds."""

    kind: str  # "sql"
    body: str
    start: int
    end: int


async def answer(
    provider, messages: list[dict], shop: Shop, timing: AnswerTiming | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Stream the answer as ``(kind, text)``: ``text``, ``thinking``, ``status``, ``error`` or ``changes``.

    A lookup block in a reply is run and fed back instead of shown, until the
    model answers or runs out of lookups; words written before the block are
    kept. Every lookup comes back to the model with its rows or with a reason,
    never with nothing. A memory block and a changes block the answer ends with
    are kept out of the text and handed over whole, last. Every web address in
    the text is checked against the ones Studio gave the model this turn.
    ``timing``, when given, records each round and lookup as it happens.
    """
    timing = timing or AnswerTiming()
    messages = list(messages)
    links = answer_links.LinkGuard(answer_links.message_texts(messages))
    written = False
    for used in range(MAX_LOOKUPS + 1):
        reply = ""
        released = 0
        lead = "\n\n" if written else ""
        round_started = time.monotonic()
        timing.first_token_ms.append(None)
        timing.round_ms.append(0)
        try:
            async for item in provider.chat(messages, stream=True):
                kind, text = unpack_stream_item(item)
                if not text:
                    continue
                if timing.first_token_ms[-1] is None:
                    timing.first_token_ms[-1] = elapsed_ms(round_started)
                timing.round_ms[-1] = elapsed_ms(round_started)
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
        finally:
            timing.round_ms[-1] = elapsed_ms(round_started)
        lookups = find_lookups(reply)
        if not lookups:
            words, memory, changes, automation = assistant_memory.peel(reply)
            if memory is not None and not words.strip() and not written:
                words = "I'll remember that."
            rest = links.clean(words[released:]) if len(words) > released else ""
            if rest.strip():
                yield "text", lead + rest
            if memory is not None:
                yield "memory", memory
            if changes is not None:
                yield "changes", changes
            if automation is not None:
                yield "automation", automation
            return
        before = assistant_memory.visible_text(reply[:lookups[0].start]).rstrip()
        if len(before) > released:
            piece = links.clean(before[released:])
            if piece.strip():
                yield "text", lead + piece
                written = True
        if used == MAX_LOOKUPS:
            yield "error", "The assistant kept looking things up without answering. Ask again, or ask something narrower."
            return
        batch = lookups[:LOOKUPS_PER_ROUND]
        skipped = len(lookups) - len(batch)
        results: list[str] = []
        for item in batch:
            yield "status", lookup_label(item)
            lookup_started = time.monotonic()
            timing.lookups += 1
            try:
                lookup_result = await run_any_lookup(item, shop)
            finally:
                timing.lookup_ms = min(TIMING_CAP_MS, timing.lookup_ms + elapsed_ms(lookup_started))
            results.append(lookup_result)
        result = _combined(batch, results, skipped)
        links.allow([result])
        if used == MAX_LOOKUPS - 1:
            result += "\n\nThat was the last lookup for this question. Answer now with what you have."
        messages += [{"role": "assistant", "content": reply[:lookups[-1].end]}, {"role": "user", "content": result}]


def _lookup_heading(lookup: Lookup) -> str:
    first = lookup.body.lstrip().splitlines()[0].strip() if lookup.body.strip() else ""
    words = first[2:].strip(" .") if first.startswith("--") else ""
    return words[:80] if words else _lookup_name(lookup.body)


def _combined(batch: list[Lookup], results: list[str], skipped: int) -> str:
    if len(batch) == 1 and not skipped:
        return results[0]
    sections: list[str] = []
    total = len(batch)
    for index, (lookup, body) in enumerate(zip(batch, results, strict=True), 1):
        header = f"Lookup {index} of {total} ({_lookup_heading(lookup)}):"
        sections.append(f"{header}\n{body}")
    if skipped:
        noun = "lookup was" if skipped == 1 else "lookups were"
        sections.append(f"{skipped} more {noun} not run: at most {LOOKUPS_PER_ROUND} run at once.")
    return "\n\n".join(sections)


def _visible(reply: str) -> str:
    """What of a reply still being written the seller may read: never a lookup block or the start of one."""
    shown = assistant_memory.visible_text(reply)
    opening = shown.lstrip()
    if "```".startswith(opening):
        return ""
    if opening.startswith("```"):
        tag = re.match(r"```[ \t]*([A-Za-z_]*)", opening)
        assert tag is not None  # opening starts with the matched fence.
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


def find_lookups(reply: str) -> list[Lookup]:
    """Every lookup block in a reply, in order, or an empty list when the reply is an answer."""
    opening = len(reply) - len(reply.lstrip())
    found: list[Lookup] = []
    for match in _FENCED.finditer(reply):
        language = match.group(1).lower()
        body = match.group(2).strip()
        kind = _LOOKUP_LANGS.get(language)
        if kind is None and not language and match.start() == opening and _SELECTISH.match(body):
            kind = "sql"
        if kind is not None:
            found.append(Lookup(kind=kind, body=body, start=match.start(), end=match.end()))
    return found


def find_lookup(reply: str) -> Lookup | None:
    """The first lookup block in a reply, wherever it sits, or None when the reply is an answer."""
    found = find_lookups(reply)
    return found[0] if found else None


def lookup_sql(reply: str) -> str | None:
    """The query a reply asks for, or None when it asks for none (an empty block is still a request)."""
    lookup = find_lookup(reply)
    return lookup.body if lookup is not None and lookup.kind == "sql" else None


def lookup_label(lookup: Lookup | str) -> str:
    """What the seller sees while a lookup runs: the model's own first-line comment."""
    sql = lookup.body if isinstance(lookup, Lookup) else lookup
    first = sql.lstrip().splitlines()[0].strip() if sql.strip() else ""
    words = first[2:].strip(" .") if first.startswith("--") else ""
    return f"Looking up {words[:80]}…" if words else "Looking up more detail…"


def _run_sql(shop: Shop, body: str) -> str:
    need_details = "listing_details" in (body or "").lower()
    return shop.read_lookup(lambda conn: run_lookup(conn, body), need_details=need_details)


async def run_any_lookup(lookup: Lookup, shop: Shop) -> str:
    """Run one lookup and return what goes back to the model; a failure comes back as its reason."""
    name = _lookup_name(lookup.body)
    try:
        return await asyncio.to_thread(_run_sql, shop, lookup.body)
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


def _match_values(target: str, by_lower: dict[str, list[str]]) -> list[str]:
    matches: list[str] = []

    def add(value: str) -> None:
        if value not in matches and len(matches) < _HINT_MATCHES:
            matches.append(value)

    for value in by_lower.get(target, ()):
        add(value)
    if len(target) >= 2:
        for lower_val, values in by_lower.items():
            if target in lower_val or (len(lower_val) >= 3 and lower_val in target):
                for value in values:
                    add(value)
    for close in difflib.get_close_matches(target, list(by_lower), n=_HINT_MATCHES, cutoff=0.6):
        for value in by_lower[close]:
            add(value)
    return matches


def _closest_values(lookups: sqlite3.Connection, sql: str) -> str:
    """When a SELECT matches 0 rows, suggest close real values for whitelisted filter columns."""
    cleaned = re.sub(r"--[^\n]*", "", sql)
    primary = _lookup_name(cleaned).lower()
    seen: set[tuple[str, str, str]] = set()
    lines: list[str] = []
    used = 0
    for match in _FILTER_LITERAL.finditer(cleaned):
        col = match.group(1).lower()
        needle = match.group(2).replace("''", "'").strip("%_").strip()
        if not needle:
            continue
        table = primary if primary in _HINT_COLUMNS and col in _HINT_COLUMNS[primary] else (
            "listings" if col in _HINT_COLUMNS["listings"] else None
        )
        if table is None:
            continue
        safe_table = next(name for name in _HINT_COLUMNS if name == table)
        safe_col = next(name for name in _HINT_COLUMNS[safe_table] if name == col)
        key = (safe_table, safe_col, needle.lower())
        if key in seen:
            continue
        seen.add(key)
        try:
            distinct = lookups.execute(
                f"SELECT {safe_col}, count(*) FROM {safe_table} "
                f"WHERE {safe_col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 500",
            ).fetchall()
        except sqlite3.Error:
            continue
        if not distinct:
            continue
        counts: dict[str, int] = {}
        by_lower: dict[str, list[str]] = {}
        for raw_val, count in distinct:
            val = str(raw_val).replace("\n", " ")
            counts[val] = int(count)
            by_lower.setdefault(val.lower(), []).append(val)
        if needle in counts:
            continue
        matches = _match_values(needle.lower(), by_lower)
        if not matches:
            continue
        prefix = f"Values in {safe_table}.{safe_col} closest to '{needle}': "
        suffix = ". Use one only if it names the same thing."
        items: list[str] = []
        for value in matches:
            count = counts[value]
            entry = f"{value} ({count} {'row' if count == 1 else 'rows'})"
            candidate = prefix + ", ".join([*items, entry]) + suffix
            if used + len(candidate) + (1 if lines else 0) > _HINT_CHARS:
                break
            items.append(entry)
        if not items:
            continue
        line = prefix + ", ".join(items) + suffix
        lines.append(line)
        used += len(line) + (1 if len(lines) > 1 else 0)
    return "\n".join(lines)


def run_lookup(lookups: sqlite3.Connection, sql: str) -> str:
    """Run one SELECT over the in-memory copy and describe the result, or why there is none, as text."""
    sql = (sql or "").strip()
    name = _lookup_name(sql)
    if not re.sub(r"--[^\n]*", "", sql).strip():
        return f"Lookup {name} failed: the sql block was empty. Put one SELECT in it."
    deadline = time.monotonic() + LOOKUP_SECONDS
    lookups.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    hints = ""
    try:
        cursor = lookups.execute(sql)
        if cursor.description is None:
            return f"Lookup {name} failed: only a single SELECT can be run."
        header = " | ".join(column[0] for column in cursor.description)
        rows = cursor.fetchmany(LOOKUP_ROWS + 1)
        if not rows:
            hints = _closest_values(lookups, sql)
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
        base = f"Lookup result: 0 rows matched {_matched(sql)}.\n{header}"
        return f"{base}\n{hints}" if hints else base
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


class _Median:
    """SQLite aggregate returning the median of numeric values, ignoring NULLs and non-numbers."""

    def __init__(self) -> None:
        self._values: list[int | float] = []

    def step(self, value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self._values.append(value)

    def finalize(self) -> Any:
        return statistics.median(self._values) if self._values else None


def _read_only_authorizer(action: int, *_details: Any) -> int:
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


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
    lookups.create_aggregate("median", 1, _Median)
    # Reads only from here on: no ATTACH to a real database, no PRAGMA, no writes.
    lookups.set_authorizer(_read_only_authorizer)
    return lookups


def _populate_lookup_table(lookups: sqlite3.Connection, name: str, rows: list[dict[str, Any]]) -> None:
    """Populate a deferred lookup table once on demand, restoring the read-only authorizer."""
    if not rows:
        return
    columns = TABLES[name]
    lookups.set_authorizer(None)
    try:
        lookups.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' for _ in columns)})",
            [[_cell(row.get(column)) for column in columns] for row in rows],
        )
        lookups.commit()
    finally:
        lookups.set_authorizer(_read_only_authorizer)


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return value
    return json.dumps(value, default=str)


def _section(title: str, payload: Any) -> str:
    if payload in (None, [], {}):
        return f"--- {title} ---\nNothing recorded."
    return f"--- {title} ---\n{json.dumps(payload, separators=(',', ':'), default=str)}"


def _records(
    db: Session,
    rows: list[AnalyticsItem],
    now: datetime,
    *,
    parsed_notes: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every listing as one slim record, plus one detail row per listing with description and full JSON."""
    from anteroom.models.conversation import Conversation
    from anteroom.models.listing import Listing, ListingRevision
    from anteroom.services import size_audit
    from anteroom.services.vendoo_import import parse_notes, split_vendoo_labels

    listings: dict[str, Any] = {}
    after = ""
    # A large shop's listings run to tens of megabytes. Read them a batch at a
    # time, so each statement stays well inside the hosted statement timeout
    # when the database is busy, however big the shop grows.
    while True:
        batch = (
            db.query(Listing.conversation_id, ListingRevision.listing_json)
            .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
            .filter(Listing.conversation_id > after)
            .order_by(Listing.conversation_id)
            .limit(LISTING_BATCH)
            .all()
        )
        listings.update(batch)
        if len(batch) < LISTING_BATCH:
            break
        after = batch[-1][0]
    if parsed_notes is None:
        notes = {conv_id: parse_notes(raw) for conv_id, raw in db.query(Conversation.id, Conversation.notes).all()}
    else:
        notes = parsed_notes
    photo_sizes = _photo_sizes(db)
    unsold_ids = {item.conversation_id for item in rows if item.status != "sold"}
    ebay_size_choices = size_audit.prefetch_ebay_size_choices(
        db, (listings[cid] for cid in unsold_ids if isinstance(listings.get(cid), dict)),
    )
    # These inputs are plain values. A large shop's size audits can take longer
    # than Postgres permits an idle transaction; return the connection before
    # calculating, so the following price-history read starts a fresh transaction.
    db.rollback()
    records = []
    detail_rows = []
    seen_details: set[str] = set()
    with size_audit.with_ebay_size_choices(ebay_size_choices):
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
                "size_issue": None if sold else size_audit.size_problems(listing, photo_sizes.get(item.conversation_id)),
            })
            if item.conversation_id not in seen_details:
                seen_details.add(item.conversation_id)
                detail_rows.append({
                    "listing_id": item.conversation_id,
                    "description": _text(listing.get("description")),
                    # Kept as a dict here; _cell serializes it only when a lookup builds the SQLite copy.
                    "details": listing or None,
                })
    return records, detail_rows


def _photo_sizes(db: Session) -> dict[str, str]:
    """The size each listing's latest photo analysis read, with where it read it."""
    from anteroom.models.conversation import Message
    from anteroom.services.size_audit import photo_size

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
        "Only these rows are here. The `listings` lookup table has every listing with every column (brand, size, "
        "condition, labels, cost, fees, profit); `listing_details` has each listing's description and marketplace "
        "fields. `days` is days listed so far for an active listing and days to sell for a sold one. Empty means not recorded."
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

    from anteroom.models.listing import ListingRevision
    from anteroom.services.inventory_analytics import _number

    changes = []
    last: dict[str, float] = {}
    revisions = (
        db.query(
            ListingRevision.conversation_id,
            # This stored projection carries every revision's original price;
            # extracting it from listing_json decompresses all archived forms.
            json_value(ListingRevision.inventory_fields, "price"),
            ListingRevision.created_at,
        )
        .order_by(ListingRevision.created_at, ListingRevision.id)
        .all()
    )
    # History is materialized too: grouping it must not keep a pooled connection.
    db.rollback()
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
        return f"{title}\nNo listing's asking price has been changed in Anteroom."
    note = "Each time a listing's asking price was changed in Anteroom, newest first."
    if len(changes) > PRICE_CHANGE_ROWS:
        note += f" Only the newest {PRICE_CHANGE_ROWS} are shown; a lookup can read the rest."
    return "\n".join([
        title, note, " | ".join(CHANGE_COLUMNS),
        *(_line(change, CHANGE_COLUMNS) for change in changes[:PRICE_CHANGE_ROWS]),
    ])


def _box_rows(box_results: dict[str, Any]) -> str:
    """Bought wholesale boxes: per-store totals in full, and the latest boxes as a table."""
    bought = list(box_results.get("boxes") or [])
    stores = box_results.get("stores") or []
    if not bought and not stores:
        return "--- Wholesale boxes bought, and what each has returned ---\nNothing recorded."
    ordered = sorted(bought, key=lambda box: (box.get("bought_at") or "", str(box.get("id") or "")), reverse=True)
    shown = ordered[:TOP_BOXES]
    note = "Newest first; the `boxes` lookup table has every box and every column."
    if len(ordered) > TOP_BOXES:
        note = (
            f"Only the latest {TOP_BOXES} of {len(ordered)} boxes are shown; "
            "the `boxes` lookup table has every box and every column."
        )
    parts = []
    if stores:
        parts.append(_section("Wholesale boxes by store", stores))
    parts.append(_table("Wholesale boxes bought, and what each has returned", note, BOXES_SHOWN_COLUMNS, shown))
    return "\n\n".join(parts)


def _ad_rows(ads: list[dict[str, Any]]) -> str:
    note = "Most recent ad spend entries recorded."
    if len(ads) > AD_ROWS:
        note = f"Only the latest {AD_ROWS} of {len(ads)} entries are shown; the `ad_spend` lookup table has every one."
    return _table("Ad spend the seller recorded", note, AD_COLUMNS, ads[:AD_ROWS])


def _event_rows(events: list[dict[str, Any]]) -> str:
    note = "Most recent sale events recorded."
    if len(events) > EVENT_ROWS:
        note = f"Only the latest {EVENT_ROWS} of {len(events)} events are shown; the `sale_events` lookup table has every one."
    return _table("Sale events", note, EVENT_COLUMNS, events[:EVENT_ROWS])


def _sale_events(db: Session) -> list[dict[str, Any]]:
    from anteroom.models.sale_event import SaleEvent

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
