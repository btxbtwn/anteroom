"""The business assistant: questions about the whole shop, answered from Studio's data.

A listing's chat knows one item. This one is handed everything Studio holds
about the business on every question — each listing as a row, the same sales
and inventory totals Analytics shows, price changes, the boxes bought and what
they returned, the current buy list, ad spend, sale events, and sourcing
checks — and answers from that. The brief is read fresh each time, so an
answer is never older than the last Vendoo sync.

What the brief leaves out, the assistant can look up. The same rows, with each
listing's description and full field values, are copied into a throwaway
in-memory SQLite database, and the model may ask for a ``SELECT`` over it
before it answers. The copy is the only thing a lookup can touch.

It reads, and it proposes. A reply may end with changes to listings, which are
kept with the reply for the seller to apply or dismiss
(``services/assistant_changes.py``). Nothing here writes a listing, calls
Vendoo, or reaches a marketplace.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.assistant import NEW_THREAD_TITLE, AssistantMessage, AssistantThread
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item
from vendoo_studio.services import assistant_changes, assistant_memory, sourcing_boxes
from vendoo_studio.services.inventory_analytics import ANALYTICS_RANGES, AnalyticsItem

# Earlier turns sent back with a question, so a follow-up can lean on them.
HISTORY_MESSAGES = 20
# Room for the listing rows, in characters. Past it the oldest rows are left
# out and the brief says so; the totals above the table still count them, and
# a lookup can still reach them.
LISTING_ROWS_BUDGET = 300_000
PRICE_CHANGE_ROWS = 300
BRAND_GROUPS = 40
# Room for the Sourcing tab's boxes, in characters; a lookup reads every one.
SOURCING_ROWS_BUDGET = 30_000
SIZING_ROWS = 200
# Lookups one question may run, and what one may cost and return.
MAX_LOOKUPS = 6
LOOKUP_SECONDS = 5.0
LOOKUP_ROWS = 200
LOOKUP_CHARS = 24_000

INSTRUCTIONS = (
    "You are the business assistant inside Vendoo Studio, talking to a reseller about their own shop. "
    "They sell clothing and similar goods on marketplaces such as eBay, Poshmark, Mercari, Depop and Etsy, "
    "cross-listed through Vendoo.\n\n"
    "Below is a snapshot, taken now, of everything Studio holds about the business. Answer from it.\n"
    "- Work figures out from the rows when no total is given, and check a total against the rows when both exist.\n"
    "- Say what a figure rests on when the data is partial: how many sales had a cost recorded, "
    "how many listings had a list date, and so on. Unknown is not zero.\n"
    "- When the snapshot cannot answer the question, say what is missing and where in Studio it gets recorded. "
    "Never invent a sale, a price, a cost or a date.\n"
    "- Profit is the sold price plus shipping the buyer paid, less fees, shipping the seller paid, and the item's cost.\n"
    "- Give advice when asked — what to source, reprice, discount or drop — and tie each point to the numbers behind it. "
    "Fit it to what the seller says about their business and to what you remember about them.\n"
    "- You can propose changes to listings, described below, and the seller applies them. You cannot change a setting, "
    "and you cannot list, delist, publish or send anything to Vendoo or a marketplace. "
    "When the seller wants one of those, tell them where in Studio to do it.\n"
    "- When you name a specific listing, link it so the seller can open it: [its title](#listing-ID), "
    "with the id from its row. Use only ids that appear in the snapshot or in a lookup result.\n"
    "- Other ids only tie rows together. Name a box by its store and title, never by its id.\n"
    "- Sourcing: every box on the Sourcing tab is in the snapshot's box table, best first, and all of them, with "
    "the url and every figure, are in the `sourcing_boxes` lookup table. `recommended_lots` is the buy list Studio "
    "recommends; when it is empty, `empty_reason` says why. To rank boxes against what the seller sells, set each "
    "box's projected profit and ROI beside its `kind_*` figures (the seller's own sales of that kind of piece in the "
    "last 12 months: count, average price, margin, median days to sell) and the `categories` in the Sales sections "
    "or the `sales_by_category` table. A box whose kind the seller has sold often, at a good margin and quickly, "
    "beats one with a higher ROI resting on few or no sales of its kind. A box missing the min ROI says by how "
    "many points (`roi_gap_points`) and dollars (`profit_shortfall`). Empty figures are unknown, never zero.\n"
    "- Write plain English and lead with the answer. Use a table when comparing several things. Amounts are US dollars.\n"
)

LOOKUP_INSTRUCTIONS = (
    "Looking things up: the snapshot is also loaded into a SQLite database. When the snapshot does not already "
    "answer the question — listing rows were left out, you need a description or a marketplace's field values, "
    "or a breakdown is easier to compute than to count by hand — reply with nothing but one ```sql block holding "
    "a single SELECT. Make its first line a comment saying in plain words what you are looking up, like "
    "`-- sales by size`. Studio runs it and replies with the rows. You may do this up to {limit} times for one "
    "question, then answer. Never show SQL or mention the database to the seller.\n"
    "Tables:\n{schema}\n"
    "`listings.form_sizes` is the size each marketplace form holds, `photo_size` the size read from the photos "
    "and where it was read, and `size_issue` why the sizing needs a look (NULL when nothing stands out).\n"
    "`listings.details` is the listing's full JSON, including each marketplace's fields; read it with "
    "json_extract(details, '$.ebay_specifics'). Dates are YYYY-MM-DD text. Amounts are numbers; NULL means not recorded.\n"
    "`sourcing_boxes` is every box on the Sourcing tab from its last check: `contents` the kind of piece, `landed` "
    "price plus shipping and checkout fee, `projected_profit` and `roi_percent` at the settings that check used, "
    "`on_its_own` how the box fares bought on its own (qualifies, return_target, downside, needs_research, budget), "
    "`buy_list` recommended or what kept it off the list, and `kind_*` the seller's own sales of its kind.\n"
    "`sales_by_category` is the Analytics category breakdown for each `period` (30d, 90d, 12m, all); `margin` is "
    "profit as a percent of revenue over the sales with a cost recorded (`profit_known`).\n"
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

_SALES_KEYS = ("range", "sell_through_rate", "sales", "previous", "periods", "marketplaces", "categories", "price_tiers", "ads")
# One Sourcing-tab box, as the brief's table shows it.
SOURCING_COLUMNS = (
    "id", "store", "title", "contents", "price", "shipping", "pieces", "resale_per_piece", "projected_profit",
    "roi_percent", "roi_gap_points", "profit_shortfall", "on_its_own", "buy_list", "kind_sold_12m", "kind_average_price",
    "kind_margin", "kind_median_days",
)
CATEGORY_RANGES = ("30d", "90d", "12m", "all")
CATEGORY_COLUMNS = (
    "period", "category", "count", "revenue", "average_price", "profit", "profit_known", "margin", "median_days",
    "days_known",
)
# One listing, as the brief's table shows it.
ROW_COLUMNS = (
    "id", "title", "status", "brand", "category", "size", "condition", "color", "labels", "sku", "listed_on",
    "asking", "cost", "sold_for", "fees", "profit", "sold_on", "listed", "sold", "days", "box",
)
# What a lookup can read as well: too long, or too rarely wanted, for every question.
LISTING_COLUMNS = (
    *ROW_COLUMNS, "shipping_cost", "shipping_credit", "form_sizes", "photo_size", "size_issue", "description",
    "details",
)
# A listing whose sizing needs a look, as the brief shows it.
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
_LOOKUP_FENCE = "```sql"
_LOOKUP_BLOCK = re.compile(r"```sql\s*\n(.*?)(?:```|\Z)", re.DOTALL | re.IGNORECASE)
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


def clear_messages(db: Session, thread_id: str | None = None) -> None:
    """Drop the turns of one conversation and keep the conversation itself."""
    if thread_id is None:
        thread = latest_thread(db)
        if thread is None:
            return
        thread_id = thread.id
    db.query(AssistantMessage).filter(AssistantMessage.thread_id == thread_id).delete()
    db.commit()


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
    """Everything Studio knows about the business, read once for one question."""
    from vendoo_studio.services import ad_spend, boxes, box_scout, scout
    from vendoo_studio.services.inventory_analytics import inventory_analytics, load_rows
    from vendoo_studio.services.user_settings import get_business_note

    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
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
    note = get_business_note()
    sourcing_state = box_scout.read_state()
    tab_boxes = sourcing_boxes.box_rows(sourcing_state, rows, clock)
    categories = [
        {"period": range_id, "category": group["label"], **{key: group[key] for key in CATEGORY_COLUMNS[2:]}}
        for range_id in CATEGORY_RANGES for group in ranges[range_id]["categories"]
    ]
    sections = [
        f"Today is {clock.date().isoformat()} (UTC). "
        f"Sales and statuses were last synced from Vendoo at {everything['last_updated_at'] or 'an unknown time'}.",
        f"--- What the seller says about their business ---\n{note}" if note else "",
        assistant_memory.section(db),
        _section("Inventory now", {
            **everything["inventory"],
            "aging_of_active_listings": [
                {key: bucket[key] for key in ("label", "count", "asking_value")} for bucket in everything["aging"]
            ],
            "sold_listings_missing_a_sale_date": everything["undated_sales"],
            "sales_missing_details_in_vendoo": len(everything["incomplete_sales"]),
        }),
        *(
            _section(f"Sales, {_RANGE_LABELS[range_id]}", {
                **{key: payload[key] for key in _SALES_KEYS},
                "brands": payload["brands"][:BRAND_GROUPS],
            })
            for range_id, payload in ranges.items()
        ),
        _listing_rows(records),
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
    return Shop(
        brief="\n\n".join(section for section in sections if section),
        lookups=_lookup_database({
            "listings": records,
            "price_changes": changes,
            "boxes": box_results["boxes"],
            "ad_spend": ads,
            "sale_events": events,
            "sourcing_boxes": tab_boxes,
            "sales_by_category": categories,
        }),
    )


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
    schema = "\n".join(f"- {name}({', '.join(columns)})" for name, columns in TABLES.items())
    # The instructions lead so a provider's prefix cache still hits when the figures move.
    system = (
        f"{INSTRUCTIONS}\n{LOOKUP_INSTRUCTIONS.format(limit=MAX_LOOKUPS, schema=schema)}\n"
        f"{CHANGES_INSTRUCTIONS.format(limit=assistant_changes.MAX_CHANGES)}\n"
        f"{assistant_memory.INSTRUCTIONS}\n{shop.brief}"
    )
    return [{"role": "system", "content": system}, *earlier]


async def answer(provider, messages: list[dict], shop: Shop) -> AsyncIterator[tuple[str, str]]:
    """Stream the answer as ``(kind, text)``: ``text``, ``thinking``, ``status``, ``error`` or ``changes``.

    A reply that is a lookup is run and fed back instead of shown, until the
    model answers or runs out of lookups. A memory block and a changes block
    the answer ends with are kept out of the text and handed over whole, last.
    """
    messages = list(messages)
    for used in range(MAX_LOOKUPS + 1):
        reply = ""
        shown = 0
        showing = False
        async for item in provider.chat(messages, stream=True):
            kind, text = unpack_stream_item(item)
            if not text:
                continue
            if kind == "thinking":
                yield "thinking", text
                continue
            reply += text
            if not showing:
                # Hold the opening until it is clear whether this is a lookup or the answer.
                opening = reply.lstrip().lower()
                if len(opening) < len(_LOOKUP_FENCE) or opening.startswith(_LOOKUP_FENCE):
                    continue
                showing = True
            visible = assistant_memory.visible_text(reply)
            if len(visible) > shown:
                yield "text", visible[shown:]
                shown = len(visible)
        sql = None if showing else lookup_sql(reply)
        if sql is None:
            words, memory, changes = assistant_memory.peel(reply)
            if memory is not None and not words.strip():
                words = "I'll remember that."
            if len(words) > shown:
                yield "text", words[shown:]
            if memory is not None:
                yield "memory", memory
            if changes is not None:
                yield "changes", changes
            return
        if used == MAX_LOOKUPS:
            yield "error", "The assistant kept looking things up without answering. Ask again, or ask something narrower."
            return
        yield "status", lookup_label(sql)
        result = await asyncio.to_thread(run_lookup, shop.lookups, sql)
        if used == MAX_LOOKUPS - 1:
            result += "\n\nThat was the last lookup for this question. Answer now with what you have."
        messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": result}]


def lookup_sql(reply: str) -> str | None:
    """The query a reply asks for, when the reply is a lookup and nothing else."""
    if not reply.lstrip().lower().startswith(_LOOKUP_FENCE):
        return None
    match = _LOOKUP_BLOCK.search(reply)
    sql = match.group(1).strip() if match else ""
    return sql or None


def lookup_label(sql: str) -> str:
    """What the seller sees while a lookup runs: the model's own first-line comment."""
    first = sql.lstrip().splitlines()[0].strip() if sql.strip() else ""
    words = first[2:].strip(" .") if first.startswith("--") else ""
    return f"Looking up {words[:80]}…" if words else "Looking up more detail…"


def run_lookup(lookups: sqlite3.Connection, sql: str) -> str:
    """Run one SELECT over the in-memory copy and describe the result, or the error, as text."""
    deadline = time.monotonic() + LOOKUP_SECONDS
    lookups.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    try:
        cursor = lookups.execute(sql)
        if cursor.description is None:
            return "Lookup failed: only a single SELECT can be run."
        header = " | ".join(column[0] for column in cursor.description)
        rows = cursor.fetchmany(LOOKUP_ROWS + 1)
    except sqlite3.Error as exc:
        return f"Lookup failed: {exc}"
    finally:
        lookups.set_progress_handler(None, 0)
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


def _listing_rows(records: list[dict[str, Any]]) -> str:
    """One line per listing, as many as fit."""
    title = f"--- Every listing ({len(records)}) ---"
    if not records:
        return f"{title}\nNo listings yet."
    lines: list[str] = []
    used = 0
    for record in records:
        line = _line(record, ROW_COLUMNS)
        if used + len(line) > LISTING_ROWS_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    note = (
        "`days` is days to sell for a sold listing and days listed so far for an active one. "
        "`listed_on` is where it is cross-listed, `sold_on` where it sold, and `box` the id of the wholesale "
        "box it came from. Empty means not recorded."
    )
    left_out = len(records) - len(lines)
    if left_out:
        note += (
            f" {left_out} listings did not fit and are left out, oldest sales and drafts first;"
            " the totals above still count them, and a lookup can read them."
        )
    return "\n".join([title, note, " | ".join(ROW_COLUMNS), *lines])


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
        note += f" Only the first {SIZING_ROWS} are shown; a lookup on size_issue can read the rest."
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
    """One line per box on the Sourcing tab, best first, as many as fit."""
    title = f"--- Boxes on the Sourcing tab ({len(tab_boxes)}) ---"
    if not tab_boxes:
        return f"{title}\nNo boxes in the last sourcing check."
    lines: list[str] = []
    used = 0
    for box in tab_boxes:
        line = _line(box, SOURCING_COLUMNS)
        if used + len(line) > SOURCING_ROWS_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    note = (
        "Recommended boxes first, then each supplier's top picks, then the rest by ROI. `contents` is the kind of "
        "piece, `shipping` the estimate to the seller's ZIP, `roi_percent` and `projected_profit` at the settings the "
        "check used; `roi_gap_points` and `profit_shortfall` are how far a box misses the min ROI. `on_its_own` is how it "
        "fares bought on its own and `buy_list` what kept it off the list. `kind_*` is the seller's own sales of that "
        "kind in the last 12 months. Empty means unknown."
    )
    left_out = len(tab_boxes) - len(lines)
    if left_out:
        note += f" {left_out} more boxes did not fit; the sourcing_boxes lookup has every box, with its url."
    return "\n".join([title, note, " | ".join(SOURCING_COLUMNS), *lines])


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
