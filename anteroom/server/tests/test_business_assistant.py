"""The business assistant: what it is told about the shop, its chat, and the changes it proposes."""
import asyncio
import functools
import json
import re
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.services import listing_persistence
from anteroom.database import Base, get_db, load_models
from anteroom.models.ad_spend import AdSpend
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.routes import assistant as assistant_route
from anteroom.services import assistant_changes, assistant_memory, boxes, business_assistant

NOW = datetime(2026, 10, 1, tzinfo=UTC)
FAILED = re.compile(r"Lookup \w+ failed: ")


def add_listing(db, title, listing, notes=None, box_id=None):
    conv = ConversationRepo(db).create(title=title, notes=json.dumps(notes) if notes else None, box_id=box_id)
    listing_persistence.save_revision(db, conv.id, {"title": title, **listing}, source="user")
    return conv


def sold(price, market, listed, sold_at, **sale):
    return {
        "vendooStatus": "sold",
        "vendooSale": {"price": price, "marketplace": market, "soldAt": sold_at, **sale},
        "vendooDates": {"listed": listed, "sold": sold_at},
    }


@pytest.fixture
def workspace(monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_route, "SessionLocal", factory)
    monkeypatch.setattr("anteroom.database.SessionLocal", factory)
    db = factory()
    app = FastAPI()
    app.include_router(assistant_route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield db, client
    db.close()
    engine.dispose()


def seed(db):
    box = boxes.create_box(db, store="Raghouse", title="Mixed denim", price=80, shipping=20, pieces=10)
    jeans = add_listing(
        db, "Levi's 501 Jeans",
        {"price": 60, "brand": "Levi's", "category_path": "Men > Jeans", "size": "34", "sku": "A-12",
         "condition": "Pre-Owned - Good", "primaryColor": "Blue", "labels": ["Bin 4"],
         "description": "Classic straight leg denim.", "ebay_specifics": {"Rise": "Mid"}},
        {**sold(48, "ebay", "2026-08-01T00:00:00+00:00", "2026-09-10T00:00:00+00:00", fees=6),
         "vendooMarketplaces": ["ebay", "depop"]},
        box_id=box.id,
    )
    coat = add_listing(
        db, "Carhartt | Chore Coat", {"price": 150, "cost": 25, "brand": "Carhartt", "category_path": "Men > Coats"},
        {"vendooStatus": "active", "vendooDates": {"listed": "2026-07-03T00:00:00+00:00"}},
    )
    listing_persistence.save_revision(db, coat.id, {"title": coat.title, "price": 120, "cost": 25, "brand": "Carhartt",
                                            "category_path": "Men > Coats"}, source="user")
    add_listing(db, "Unfinished draft", {"price": 15})
    db.add(AdSpend(marketplace="etsy", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), spend=31.0))
    db.commit()
    return box, jeans, coat


def test_the_brief_summarizes_the_shop_and_points_to_the_rows(workspace):
    db, _client = workspace
    box, jeans, coat = seed(db)
    shop = business_assistant.read_shop(db, now=NOW)
    brief = shop.brief

    assert "--- Listings (3: 1 sold, 1 active, 1 draft) ---" in brief
    # The latest sale, with what it cleared, and the active listing that has been up longest; a pipe cannot
    # split a row's columns.
    assert f"{jeans.id} | Levi's 501 Jeans | Levi's | Jeans | 48 | 32 | ebay | 2026-09-10 | 40" in brief
    assert f"{coat.id} | Carhartt / Chore Coat | Carhartt | Coats |  | 120 | 25 | 90 | 2026-07-03 | " in brief
    assert "\nCoats | 1 | 120 | 90 | 90\n" in brief
    # Row-level detail is the lookup's: the draft is counted but not listed, and nothing a lookup reads is lost.
    assert "Unfinished draft" not in brief and "Classic straight leg denim." not in brief
    assert "Unfinished draft" in business_assistant.run_lookup(shop.lookups, "SELECT title FROM listings")
    assert f"{box.id}" in business_assistant.run_lookup(shop.lookups, "SELECT box FROM listings WHERE status = 'sold'")
    # The coat's asking price was cut once; nothing else moved.
    assert "--- Asking-price changes (1) ---" in brief
    assert f"{coat.id} | " in brief.split("--- Asking-price changes")[1] and " | 150 | 120" in brief
    for heading in ("Inventory now", "Sales by range", "Sales over time", "Sales by marketplace",
                    "Sales by category", "Brands", "Wholesale boxes bought", "Ad spend the seller recorded",
                    "Sale events"):
        assert f"--- {heading}" in brief
    assert "\n30d | 1 | 48 | 32 | 1 | " in brief.split("--- Sales by range ---")[1]
    assert "\n30d Levi's | 1 | 48 | 32 | 1 | " in brief.split("--- Brands ---")[1]
    assert '"store":"Raghouse"' in brief.split("--- Wholesale boxes by store ---")[1]
    assert "| Raghouse | Mixed denim |" in brief.split("--- Wholesale boxes bought")[1]
    assert "etsy | 2026-09-01 | 2026-09-30 | 31" in brief.split("--- Ad spend the seller recorded ---")[1]
    assert "What the seller says" not in brief
    shop.lookups.close()


def test_snapshot_size_audits_release_the_database_before_calculating(workspace, monkeypatch):
    from anteroom.services import size_audit

    db, _client = workspace
    add_listing(db, 'Coat M', {'price': 40, 'size': 'M', 'title': 'Coat M'})
    original = size_audit.size_problems
    audited = []

    def audit(listing, photo):
        assert not db.in_transaction(), 'Snapshot calculations must not idle a database transaction'
        audited.append(listing['title'])
        return original(listing, photo)

    monkeypatch.setattr(size_audit, 'size_problems', audit)
    shop = business_assistant.read_shop(db, now=NOW)
    assert audited == ['Coat M']
    assert 'Coat M' in business_assistant.run_lookup(shop.lookups, 'SELECT title FROM listings')
    shop.lookups.close()


def test_the_snapshot_waits_and_calculates_outside_a_database_transaction(workspace, monkeypatch):
    """No idle transaction while the snapshot waits for the sales archive lock or calculates.

    Postgres ends a transaction left idle past its deadline; a large shop's
    totals, or a wait behind another thread's archive, can outlast it.
    """
    from anteroom.services import inventory_analytics, sales_records, vendoo_sales_archive
    from anteroom.services.inventory_analytics import ANALYTICS_RANGES

    db, _client = workspace
    seed(db)
    checked: dict[str, int] = {}

    def outside_a_transaction(name, real):
        def check(*args, **kwargs):
            assert not db.in_transaction(), f"{name} ran inside an open database transaction"
            checked[name] = checked.get(name, 0) + 1
            return real(*args, **kwargs)
        return check

    for module, name in ((inventory_analytics, "rows_from_inputs"), (inventory_analytics, "summarize"),
                         (sales_records, "records_from_lines"), (boxes, "_box_row")):
        monkeypatch.setattr(module, name, outside_a_transaction(name, getattr(module, name)))
    real_lock = vendoo_sales_archive._archive_lock

    class Watched:
        def __enter__(self):
            outside_a_transaction("the archive lock wait", lambda: None)()
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(vendoo_sales_archive, "_archive_lock", Watched())
    business_assistant.read_shop(db, now=NOW).lookups.close()
    assert checked == {"the archive lock wait": 2, "rows_from_inputs": 1, "summarize": len(ANALYTICS_RANGES),
                       "records_from_lines": 1, "_box_row": 1}


def test_price_history_releases_the_database_before_calculating(workspace, monkeypatch):
    from anteroom.services import inventory_analytics

    db, _client = workspace
    seed(db)
    original = inventory_analytics._number

    def number(raw):
        assert not db.in_transaction(), 'Price history calculations must not idle a database transaction'
        return original(raw)

    monkeypatch.setattr(inventory_analytics, '_number', number)
    changes = business_assistant._price_changes(db)
    assert [(row['old_price'], row['new_price']) for row in changes] == [(150, 120)]


def test_the_breakdowns_behind_the_summary_can_be_looked_up(workspace):
    db, _client = workspace
    seed(db)
    lookups = business_assistant.read_shop(db, now=NOW).lookups
    run = functools.partial(business_assistant.run_lookup, lookups)

    assert run("SELECT period, count, profit FROM sales_by_brand WHERE brand = 'Levi''s' ORDER BY period") == (
        "Lookup result (4 rows):\nperiod | count | profit\n12m | 1 | 32.0\n30d | 1 | 32.0\n90d | 1 | 32.0\nall | 1 | 32.0"
    )
    assert run("SELECT count FROM sales_by_marketplace WHERE period = '30d' AND marketplace = 'ebay'").endswith("\n1")
    assert run("SELECT sum(count) FROM sales_over_time WHERE range = '12m'").endswith("\n1")
    lookups.close()


def test_the_prompt_names_every_lookup_table_and_column(workspace):
    db, _client = workspace
    seed(db)
    shop = business_assistant.read_shop(db, now=NOW)
    system = business_assistant.build_messages(db, "Hi", shop)[0]["content"]

    schema = business_assistant.lookup_schema()
    assert f"Lookup tables, all of them, with every column:\n{schema}\n" in system
    # Stable text ahead of the figures, so a prompt cache keeps it.
    assert system.index(schema) < system.index(shop.brief)
    assert len(schema.splitlines()) == len(business_assistant.TABLES)
    for name, columns in business_assistant.TABLES.items():
        assert f"- {name}({', '.join(columns)})" in schema
        # Each table is there to read, with exactly those columns.
        header = business_assistant.run_lookup(shop.lookups, f"SELECT * FROM {name} LIMIT 1").splitlines()[1]
        assert header == " | ".join(columns), name
    shop.lookups.close()


def test_the_brief_stays_small_however_big_the_shop_grows(workspace):
    """The summary is capped, not the shop: hundreds of listings cost what a handful do, give or take."""
    db, _client = workspace
    seed(db)
    small = business_assistant.read_shop(db, now=NOW)
    brands = [f"Brand {n:02d}" for n in range(60)]
    categories = [f"Women > Category {n:02d}" for n in range(30)]
    for n in range(400):
        listing = {"price": 20 + n % 30, "cost": 3, "brand": brands[n % 60], "category_path": categories[n % 30],
                   "size": "M", "description": "Measurements and a long description. " * 20}
        when = f"2026-{1 + n % 9:02d}-{1 + n % 27:02d}T00:00:00+00:00"
        notes = (sold(18 + n % 20, ("ebay", "depop", "poshmark")[n % 3], when, "2026-09-28T00:00:00+00:00", fees=3)
                 if n % 2 else {"vendooStatus": "active", "vendooDates": {"listed": when}})
        add_listing(db, f"{brands[n % 60]} Womens M Long Descriptive Listing Title Number {n} Blue Regular Fit",
                    listing, notes)
    big = business_assistant.read_shop(db, now=NOW)
    system = business_assistant.build_messages(db, "How is the shop doing?", big)[0]["content"]

    assert "--- Listings (403: " in big.brief
    assert business_assistant.run_lookup(big.lookups, "SELECT count(*) FROM listings").endswith("\n403")
    # About 3.5 characters to a token: the whole prompt stays under the 20k-token target.
    assert len(system) / 3.5 < business_assistant.PROMPT_TOKEN_BUDGET
    assert len(big.brief) < len(small.brief) + 16_000
    for shop in (small, big):
        shop.lookups.close()


def test_the_seller_sets_the_name_the_home_screen_greets_them_by(workspace):
    _db, client = workspace
    assert client.get("/api/assistant/name").json() == {"name": ""}
    assert client.put("/api/assistant/name", json={"name": "  Cristian   Ariel "}).json() == {"name": "Cristian Ariel"}
    assert client.get("/api/assistant/name").json() == {"name": "Cristian Ariel"}
    assert client.put("/api/assistant/name", json={"name": "x" * 41}).status_code == 422
    assert client.get("/api/assistant/name").json() == {"name": "Cristian Ariel"}
    assert client.put("/api/assistant/name", json={"name": ""}).json() == {"name": ""}
    assert client.get("/api/assistant/name").json() == {"name": ""}


def test_the_sellers_note_about_the_business_leads_the_brief(workspace, monkeypatch):
    db, client = workspace
    assert client.get("/api/assistant/note").json() == {"note": ""}
    assert client.put("/api/assistant/note", json={"note": "  I want 50% margins.  "}).json() == {
        "note": "I want 50% margins.",
    }
    brief = business_assistant.read_shop(db, now=NOW).brief
    assert "--- What the seller says about their business ---\nI want 50% margins." in brief
    assert client.put("/api/assistant/note", json={"note": "x" * 4001}).status_code == 422
    assert client.put("/api/assistant/note", json={"note": ""}).json() == {"note": ""}
    assert "What the seller says" not in business_assistant.read_shop(db, now=NOW).brief


def test_a_lookup_reads_the_copy_and_nothing_else(workspace, tmp_path):
    db, _client = workspace
    _box, jeans, coat = seed(db)
    lookups = business_assistant.read_shop(db, now=NOW).lookups

    assert business_assistant.run_lookup(
        lookups, "-- sold sizes\nSELECT l.size, l.sold_for, json_extract(d.details, '$.ebay_specifics.Rise') AS rise,"
                 " d.description FROM listings l JOIN listing_details d ON d.listing_id = l.id WHERE l.status = 'sold';",
    ) == "Lookup result (1 rows):\nsize | sold_for | rise | description\n34 | 48.0 | Mid | Classic straight leg denim."
    assert f"{coat.id} | 150.0 | 120.0" in business_assistant.run_lookup(
        lookups, "SELECT listing_id, old_price, new_price FROM price_changes")
    assert "Raghouse | 1" in business_assistant.run_lookup(lookups, "SELECT store, sold FROM boxes")

    # Nothing but reading the copy is allowed, and a mistake comes back as text the model can fix.
    for refused in (
        "DELETE FROM listings",
        "UPDATE listings SET asking = 1",
        "DROP TABLE listings",
        f"ATTACH DATABASE '{tmp_path / 'real.db'}' AS real",
        "PRAGMA database_list",
        "SELECT 1; DELETE FROM listings",
        "SELECT nope FROM listings",
    ):
        assert FAILED.match(business_assistant.run_lookup(lookups, refused)), refused
    assert not (tmp_path / "real.db").exists()
    assert f"{jeans.id}" in business_assistant.run_lookup(lookups, "SELECT id FROM listings")


def test_a_long_result_is_cut_and_says_so(workspace, monkeypatch):
    db, _client = workspace
    seed(db)
    monkeypatch.setattr(business_assistant, "LOOKUP_ROWS", 2)
    result = business_assistant.run_lookup(business_assistant.read_shop(db, now=NOW).lookups, "SELECT title FROM listings")
    assert result.startswith("Lookup result (the first 2 rows; there are more") and len(result.splitlines()) == 4


def test_a_runaway_lookup_is_stopped(workspace, monkeypatch):
    db, _client = workspace
    monkeypatch.setattr(business_assistant, "LOOKUP_SECONDS", 0.05)
    runaway = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    assert "ran past the" in business_assistant.run_lookup(business_assistant.read_shop(db, now=NOW).lookups, runaway)


def test_a_question_is_sent_with_the_brief_and_earlier_turns(workspace):
    db, _client = workspace
    seed(db)
    business_assistant.add_message(db, "user", "What sold?")
    business_assistant.add_message(db, "assistant", "One pair of jeans.")
    business_assistant.add_message(db, "user", "For how much?")
    messages = business_assistant.build_messages(db, "For how much?", business_assistant.read_shop(db, now=NOW))
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(business_assistant.INSTRUCTIONS)
    assert "- price_changes(listing_id, changed_on, old_price, new_price)" in messages[0]["content"]
    assert "Levi's 501 Jeans | Levi's | Jeans | 48" in messages[0]["content"]
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "What sold?"), ("assistant", "One pair of jeans."), ("user", "For how much?"),
    ]


def test_every_way_a_model_writes_a_lookup_is_read_as_one():
    sql = business_assistant.lookup_sql
    assert sql("  ```sql\n-- sales by size\nSELECT 1\n```") == "-- sales by size\nSELECT 1"
    assert sql("```SQL\nSELECT 2") == "SELECT 2"
    # Words before the block, the query on the fence line, another language tag, or no tag at all.
    assert sql("Let me check.\n```sql\nSELECT 1\n```") == "SELECT 1"
    assert sql("```sql SELECT 3```") == "SELECT 3"
    assert sql("```sqlite\nSELECT 4\n```") == "SELECT 4"
    assert sql("```\nSELECT 5\n```") == "SELECT 5"
    # An empty block is still a lookup, so it gets an answer instead of being shown.
    assert sql("```sql\n```") == ""
    assert sql("No lookup here.") is None and sql("```python\nprint(1)\n```") is None
    assert business_assistant.lookup_label("-- sales by size.\nSELECT 1") == "Looking up sales by size…"
    assert business_assistant.lookup_label("SELECT 1") == "Looking up more detail…"


class Provider:
    """Replies with each scripted turn in order, in pieces, and records what it was sent."""

    name = "chatgpt"
    listing_model = "test-model"

    def __init__(self, *turns, fail=False):
        self.turns, self.fail, self.calls = list(turns) or [("You sold ", "one item.")], fail, []

    @property
    def seen(self):
        return self.calls[-1]

    async def chat(self, messages, stream=True):
        self.calls.append(list(messages))
        for piece in self.turns[min(len(self.calls), len(self.turns)) - 1]:
            yield piece
        if self.fail:
            raise RuntimeError("provider went away")


def test_a_lookup_is_run_and_fed_back_before_the_answer(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    provider = Provider(
        ("``", "`sql\n-- sales by size\nSELECT size, count(*) ", "FROM listings WHERE status = 'sold' GROUP BY size\n```"),
        ("Size 34 ", "sold once."),
    )
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)
    response = client.post("/api/assistant/messages", json={"text": "Which sizes sell?"})

    # The seller sees what is being looked up and then the answer, never the query.
    assert "event: status\ndata: Looking up sales by size…" in response.text
    assert "sql" not in response.text and "SELECT" not in response.text
    assert provider.calls[1][-2]["role"] == "assistant" and provider.calls[1][-2]["content"].startswith("```sql")
    assert provider.calls[1][-1] == {
        "role": "user", "content": "Lookup result (1 rows):\nsize | count(*)\n34 | 1",
    }
    assert [row["text"] for row in client.get("/api/assistant/messages").json()] == ["Which sizes sell?", "Size 34 sold once."]


def test_lookups_are_capped(workspace, monkeypatch):
    _db, client = workspace
    provider = Provider(("```sql\nSELECT 1\n```",))
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)
    response = client.post("/api/assistant/messages", json={"text": "Loop forever"})
    assert "event: error\ndata: The assistant kept looking things up" in response.text
    assert len(provider.calls) == business_assistant.MAX_LOOKUPS + 1
    assert provider.calls[-1][-1]["content"].endswith("Answer now with what you have.")
    assert [row["text"] for row in client.get("/api/assistant/messages").json()] == ["Loop forever"]


FENCE = "`" * 3


def ask_through(client, monkeypatch, *turns):
    provider = Provider(*turns)
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)
    return provider, client.post("/api/assistant/messages", json={"text": "Which sizes sell?"}).text


@pytest.mark.parametrize(("lookup", "told"), [
    (f"{FENCE}sql\n{FENCE}", "Lookup sql failed: the sql block was empty. Put one SELECT in it."),
    (f"{FENCE}sql\n-- just a comment\n{FENCE}", "Lookup sql failed: the sql block was empty."),
    (f"{FENCE}sql\nSELECT * FROM sourcing_box\n{FENCE}",
     "Lookup sourcing_box failed: no such table: sourcing_box. The tables are: listings, listing_details, price_changes"),
    (f"{FENCE}sql\nSELECT title FROM listings WHERE size = '99'\n{FENCE}",
     "Lookup result: 0 rows matched WHERE size = '99'.\ntitle"),
    (f"{FENCE}sql\nSELECT nope FROM listings\n{FENCE}", "Lookup listings failed: no such column: nope."),
    (f"{FENCE}sql\nDELETE FROM listings\n{FENCE}", "Lookup listings failed: not authorized."),
    (f"{FENCE}sql\nSELECT title FROM listings WHERE title = 'x\x00'\n{FENCE}", "Lookup listings failed: the query contains a null character."),
])
def test_every_lookup_answers_the_model_with_rows_or_a_reason(workspace, monkeypatch, lookup, told):
    db, client = workspace
    seed(db)
    provider, response = ask_through(client, monkeypatch, (lookup,), ("Nothing matched.",))
    assert provider.calls[1][-1]["content"].startswith(told), provider.calls[1][-1]["content"]
    assert "SELECT" not in response and "Nothing matched." in response


def test_a_lookup_that_raises_still_answers_the_model(workspace, monkeypatch):
    db, client = workspace
    seed(db)

    def broken(*_args):
        raise MemoryError("out of room")
    monkeypatch.setattr(business_assistant, "run_lookup", broken)
    provider, _ = ask_through(client, monkeypatch, (f"{FENCE}sql\nSELECT 1 FROM listings\n{FENCE}",), ("Sorry.",))
    assert provider.calls[1][-1]["content"] == "Lookup listings failed: MemoryError: out of room"


def test_a_lookup_after_words_or_on_the_fence_line_runs_and_is_never_shown(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    provider, response = ask_through(
        client, monkeypatch,
        ("Let me check that. ", f"{FENCE}sql SELECT size FROM listings WHERE status = 'sold'{FENCE}",
         " It is 34, probably."),
        ("Size 34 sold.",),
    )
    assert provider.calls[1][-1]["content"] == "Lookup result (1 rows):\nsize\n34"
    # The words before the block stay; the guess after it is dropped with the block.
    assert "data: Let me check that. \n\n" in response and "probably" not in response and "SELECT" not in response
    assert [row["text"] for row in client.get("/api/assistant/messages").json()][-1] == (
        "Let me check that. \n\nSize 34 sold.")


def test_a_short_answer_is_not_held_back(workspace, monkeypatch):
    _db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("No.",)))
    assert "data: No.\n\n" in client.post("/api/assistant/messages", json={"text": "Any sales?"}).text


def test_asking_streams_the_answer_and_keeps_both_turns(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    provider = Provider()
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)

    response = client.post("/api/assistant/messages", json={"text": "  What sold last month?  "})
    assert response.status_code == 200
    assert "event: status\ndata: Reading your shop…" in response.text
    assert response.text.endswith("data: You sold \n\ndata: one item.\n\n")
    assert provider.seen[-1] == {"role": "user", "content": "What sold last month?"}
    assert "Levi's 501 Jeans | Levi's | Jeans | 48" in provider.seen[0]["content"]

    saved = client.get("/api/assistant/messages").json()
    assert [(row["role"], row["text"]) for row in saved] == [
        ("user", "What sold last month?"), ("assistant", "You sold one item."),
    ]
    # Starting over is a new thread or deleting this one; there is no clearing a conversation in place.
    assert client.delete("/api/assistant/messages").status_code == 405


def test_a_failed_answer_reports_the_error_and_keeps_what_arrived(workspace, monkeypatch):
    _db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("Half an",), fail=True))
    response = client.post("/api/assistant/messages", json={"text": "Anything stale?"})
    assert "could not finish the assistant answer. Try again." in response.text
    assert [row["text"] for row in client.get("/api/assistant/messages").json()] == ["Anything stale?", "Half an"]


@pytest.mark.parametrize('stopped', [False, True])
def test_a_lost_or_stopped_answer_lease_is_not_reported_as_a_provider_error(monkeypatch, caplog, stopped):
    from anteroom.services import background_tasks

    failure = background_tasks.TaskStopped() if stopped else background_tasks.TaskLeaseExpired()

    def prepare(*_args):
        raise failure

    monkeypatch.setattr(assistant_route, '_prepare', prepare)
    run = assistant_route._Journal()
    with pytest.raises(type(failure)):
        asyncio.run(assistant_route._answer(run, Provider(), 'What sold?'))
    assert all(not frame.startswith('event: error') for frame in run.take())
    assert not [record for record in caplog.records if record.name == 'anteroom.assistant']


def test_an_unanswered_question_is_retried_once_without_asking_it_again(workspace, monkeypatch):
    _db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("",), fail=True))
    thread = client.post("/api/assistant/threads").json()["id"]
    client.post("/api/assistant/messages", json={"text": "What sold?", "thread_id": thread})
    assert [row["role"] for row in client.get(f"/api/assistant/messages?thread_id={thread}").json()] == ["user"]

    provider = Provider(("You sold ", "one item."))
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: provider)
    answer = client.post(f"/api/assistant/messages/retry?thread_id={thread}")
    assert answer.status_code == 200 and "one item." in answer.text
    assert provider.seen[-1] == {"role": "user", "content": "What sold?"}
    turns = client.get(f"/api/assistant/messages?thread_id={thread}").json()
    assert [(row["role"], row["text"]) for row in turns] == [("user", "What sold?"), ("assistant", "You sold one item.")]
    assert client.post(f"/api/assistant/messages/retry?thread_id={thread}").status_code == 409
    assert client.post("/api/assistant/messages/retry?thread_id=missing").status_code == 404


def test_no_provider_or_no_question_saves_nothing(workspace, monkeypatch):
    _db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: None)
    assert client.post("/api/assistant/messages", json={"text": "Hello"}).status_code == 400
    assert client.post("/api/assistant/messages", json={"text": "   "}).status_code == 422
    assert client.post("/api/assistant/messages", json={"text": ""}).status_code == 422
    assert client.get("/api/assistant/messages").json() == []


class SlowProvider:
    """Writes half an answer, then waits to be let go."""

    name = "chatgpt"
    listing_model = "test-model"

    def __init__(self):
        self.release = asyncio.Event()

    async def chat(self, messages, stream=True):
        yield "Half of it "
        await self.release.wait()
        yield "done."


async def start_answer(db, provider, question="What sold?"):
    from anteroom.services.streaming import start_generation

    business_assistant.add_message(db, "user", question)
    run = start_generation(
        assistant_route.ANSWER_RUN, functools.partial(assistant_route._answer, provider=provider, question=question),
    )
    for _ in range(200):
        if any("Half" in item for item in run.history):
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("the answer never started")


async def read(response) -> str:
    return "".join([chunk async for chunk in response.body_iterator])


def test_the_answer_finishes_with_nobody_connected_and_a_late_follower_gets_all_of_it(workspace):
    db, _client = workspace

    async def scenario():
        provider = SlowProvider()
        run = await start_answer(db, provider)
        # Whoever asked has gone; someone reattaches while it is still being written.
        follower = asyncio.create_task(read(await assistant_route.resume()))
        await asyncio.sleep(0.05)
        provider.release.set()
        await run.task
        return await follower, await read(await assistant_route.resume())

    followed, afterwards = asyncio.run(scenario())
    assert followed.endswith("data: Half of it \n\ndata: done.\n\n") and "Reading your shop…" in followed
    # Once it has ended there is nothing to follow, and nothing starts a second answer.
    assert afterwards == "data: [DONE]\n\n"
    assert [row.text for row in business_assistant.list_messages(db)] == ["What sold?", "Half of it done."]


def test_stop_keeps_what_was_written_and_a_second_question_waits_its_turn(workspace, monkeypatch):
    db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: SlowProvider())

    async def scenario():
        from fastapi import HTTPException

        run = await start_answer(db, SlowProvider())
        try:
            await assistant_route.ask(assistant_route.AssistantQuestion(text="And yesterday?"), request=None)
        except HTTPException as refused:
            status = refused.status_code
        await assistant_route.stop()
        await asyncio.gather(run.task, return_exceptions=True)
        return status

    assert asyncio.run(scenario()) == 409
    assert [row.text for row in business_assistant.list_messages(db)] == ["What sold?", "Half of it "]


@pytest.mark.parametrize("stop_while_streaming", [False, True], ids=["stop-during-close", "repeated-stop"])
def test_stop_during_lookup_cleanup_preserves_streamed_text(workspace, monkeypatch, stop_while_streaming):
    import threading

    db, _client = workspace
    release_close = threading.Event()
    close = business_assistant.Shop.close

    async def scenario():
        loop = asyncio.get_running_loop()
        closing = asyncio.Event()

        def paused_close(shop):
            loop.call_soon_threadsafe(closing.set)
            assert release_close.wait(5), "test did not release lookup cleanup"
            close(shop)

        monkeypatch.setattr(business_assistant.Shop, "close", paused_close)
        provider = SlowProvider()
        await start_answer(db, provider)
        if stop_while_streaming:
            first_stop = asyncio.create_task(assistant_route.stop())
        else:
            provider.release.set()
            first_stop = None
        try:
            await asyncio.wait_for(closing.wait(), 5)
            second_stop = asyncio.create_task(assistant_route.stop())
            # Let the Stop handler request cancellation before releasing cleanup.
            await asyncio.sleep(0)
        finally:
            release_close.set()
        assert await asyncio.wait_for(second_stop, 5) == {"ok": True}
        if first_stop is not None:
            assert await asyncio.wait_for(first_stop, 5) == {"ok": True}

    asyncio.run(scenario())
    expected = "Half of it " if stop_while_streaming else "Half of it done."
    assert [row.text for row in business_assistant.list_messages(db)] == ["What sold?", expected]


def changes_reply(*entries):
    return f"Ready to review.\n\n```changes\n{json.dumps(list(entries))}\n```"


def propose(client, monkeypatch, *pieces, question="Change them"):
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(tuple(pieces)))
    response = client.post("/api/assistant/messages", json={"text": question})
    return response, client.get("/api/assistant/messages").json()[-1]


def test_a_reply_with_changes_becomes_a_proposal_and_changes_nothing(workspace, monkeypatch):
    db, client = workspace
    box, jeans, coat = (row.id for row in seed(db))
    reply = changes_reply(
        {"listing": coat, "set": {"asking": 99.6, "labels": "Sale, Bin 2, Sale", "brand": "Carhartt", "condition": "New"}},
        {"listing": jeans, "set": {"box": None, "cost": 4}},
        {"listing": "no-such-listing", "set": {"asking": 5}},
    )
    # The fence arrives split across pieces; none of the block reaches the seller.
    cut = reply.index("```changes") + 4
    response, saved = propose(client, monkeypatch, reply[:cut], reply[cut:cut + 20], reply[cut + 20:])

    assert "changes" not in response.text and "asking" not in response.text
    assert saved["text"].strip() == "Ready to review."
    # Each item keeps the revision the seller reviews, so Apply can refuse a listing edited since.
    reviewed = {listing: ListingRepo(db).get_revisions(listing)[0].id for listing in (coat, jeans)}
    assert saved["changes"] == {"status": "proposed", "items": [
        # The brand it already has and a field the assistant may not set are left out.
        {"listing_id": coat, "title": "Carhartt | Chore Coat", "revision_id": reviewed[coat],
         "set": {"asking": 100, "brand": "Carhartt", "labels": ["Sale", "Bin 2"]},
         "fields": [
             {"field": "asking", "old": 120, "new": 100},
             {"field": "labels", "old": [], "new": ["Sale", "Bin 2"]},
         ]},
        # Saving the jeans also puts their size in the title and on the eBay form, so those are shown too.
        {"listing_id": jeans, "title": "Levi's 501 Jeans", "revision_id": reviewed[jeans],
         "set": {"cost": 4.0, "box": None}, "fields": [
            {"field": "cost", "old": None, "new": 4.0},
            {"field": "title", "old": "Levi's 501 Jeans", "new": "Levi's 34 501 Jeans"},
            {"field": "ebay_size", "old": None, "new": "34"},
            {"field": "poshmark_size", "old": None, "new": "Waist 34"},
            {"field": "mercari_size", "old": None, "new": "34"},
            {"field": "depop_size", "old": None, "new": "34"},
            {"field": "etsy_size", "old": None, "new": "34"},
            {"field": "box", "old": "Raghouse · Mixed denim", "new": None},
        ]},
    ]}
    db.expire_all()
    assert ListingRepo(db).get_revisions(coat)[0].listing_json["price"] == 120
    assert ConversationRepo(db).get(jeans).box_id == box


def test_applying_saves_each_listing_as_a_revision_and_skips_what_it_cannot_change(workspace, monkeypatch):
    db, client = workspace
    _box, jeans, coat = (row.id for row in seed(db))
    gone = add_listing(db, "Sold elsewhere", {"price": 30}).id
    _response, saved = propose(client, monkeypatch, changes_reply(
        {"listing": coat, "set": {"asking": 100, "title": "Carhartt Chore Coat Brown"}},
        {"listing": jeans, "set": {"box": None, "sku": "A-13"}},
        {"listing": gone, "set": {"asking": 20}},
    ))
    db.delete(ConversationRepo(db).get(gone))
    db.commit()

    applied = client.post(f"/api/assistant/messages/{saved['id']}/changes/apply").json()
    assert applied["changes"]["status"] == "applied"
    assert [item.get("skipped") for item in applied["changes"]["items"]] == [None, None, "It is no longer in Anteroom."]

    db.expire_all()
    newest = ListingRepo(db).get_revisions(coat)[0]
    assert newest.source == "assistant"
    assert (newest.listing_json["price"], newest.listing_json["title"]) == (100, "Carhartt Chore Coat Brown")
    assert newest.listing_json["poshmark_specifics"]["originalPrice"] == 0
    assert ListingRepo(db).get_revisions(jeans)[0].listing_json["sku"] == "A-13"
    assert ConversationRepo(db).get(jeans).box_id is None

    # A proposal is settled once; the model is told what became of it.
    assert client.post(f"/api/assistant/messages/{saved['id']}/changes/apply").status_code == 409
    assert client.post(f"/api/assistant/messages/{saved['id']}/changes/dismiss").status_code == 409
    note = business_assistant.build_messages(db, "And now?", business_assistant.read_shop(db, now=NOW))[-2]["content"]
    assert "The seller applied them; 2 listings changed and 1 were skipped." in note
    assert f'{{"listing":"{coat}","set":{{"asking":100,"title":"Carhartt Chore Coat Brown"}}}}' in note


def test_a_busy_listing_is_skipped(workspace, monkeypatch):
    db, client = workspace
    _box, _jeans, coat = (row.id for row in seed(db))
    _response, saved = propose(client, monkeypatch, changes_reply({"listing": coat, "set": {"asking": 100}}))
    monkeypatch.setattr("anteroom.services.price_markdown.listing_busy", lambda conv_id, db: True)
    applied = client.post(f"/api/assistant/messages/{saved['id']}/changes/apply").json()
    assert applied["changes"]["items"][0]["skipped"] == "Anteroom was generating or sending it."
    db.expire_all()
    assert ListingRepo(db).get_revisions(coat)[0].listing_json["price"] == 120


def test_dismissing_changes_nothing(workspace, monkeypatch):
    db, client = workspace
    _box, _jeans, coat = (row.id for row in seed(db))
    _response, saved = propose(client, monkeypatch, changes_reply({"listing": coat, "set": {"asking": 100}}))
    dismissed = client.post(f"/api/assistant/messages/{saved['id']}/changes/dismiss").json()
    assert dismissed["changes"]["status"] == "dismissed"
    assert client.post(f"/api/assistant/messages/{saved['id']}/changes/apply").status_code == 409
    assert client.post("/api/assistant/messages/nope/changes/apply").status_code == 404
    db.expire_all()
    assert len(ListingRepo(db).get_revisions(coat)) == 2


def test_changes_studio_cannot_read_say_so_and_propose_nothing(workspace, monkeypatch):
    db, client = workspace
    _box, _jeans, coat = (row.id for row in seed(db))
    for block in ("not json", json.dumps([{"listing": coat, "set": {"asking": -5, "title": ""}}])):
        response, saved = propose(client, monkeypatch, f"Here you go.\n```changes\n{block}\n```")
        assert saved["changes"] is None
        assert saved["text"].endswith(assistant_route.UNREADABLE_CHANGES)
        assert "nothing is waiting for review" in response.text


def test_only_a_changes_fence_is_held_back():
    assert assistant_changes.visible_text("Use `code` and ``") == "Use `code` and "
    assert assistant_changes.visible_text("```\ntable\n```\nDone. ```chang") == "```\ntable\n```\nDone. "
    assert assistant_changes.visible_text("Done.\n```Changes\n[") == "Done.\n"
    assert assistant_changes.split_reply("Just words. ```") == ("Just words. ```", None)
    assert assistant_changes.split_reply("Words\n```changes\n[1]") == ("Words\n", "[1]")


def test_sizing_out_of_step_is_pointed_out_and_one_size_fixes_every_form(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    shorts = add_listing(
        db, "ASOS M 34 Board Shorts Navy",
        {"price": 20, "brand": "ASOS", "size": "M", "category_path": "Men > Swimwear",
         "ebay_specifics": {"size": "34"}, "poshmark_specifics": {"size": "L"}, "depop_specifics": {"size": "M"}},
        {"vendooStatus": "active"},
    )
    ConversationRepo(db).add_message(shorts.id, "system", "Photo analysis:\n\n- brand: ASOS\n- size: M (source: tag)\n")
    tee = add_listing(db, "Gap L Tee", {"price": 9, "brand": "Gap", "size": "L", "depop_specifics": {"size": "L"}},
                      {"vendooStatus": "active"})
    ConversationRepo(db).add_message(tee.id, "system", "Photo analysis:\n- size: XL (source: tag)")
    shorts, tee = shorts.id, tee.id

    shop = business_assistant.read_shop(db, now=NOW)
    assert "--- Listings whose sizing needs a look (2) ---" in shop.brief
    assert (
        f"{shorts} | ASOS M 34 Board Shorts Navy | active | Swimwear | M | ebay: 34, poshmark: L, depop: M"
        " | M (source: tag) | ebay, poshmark differ from the general size"
    ) in shop.brief
    assert f"{tee} | Gap L Tee | active |  | L | depop: L | XL (source: tag) | the photos read a different size" in shop.brief
    assert business_assistant.run_lookup(
        shop.lookups, "SELECT count(*) FROM listings WHERE size_issue IS NOT NULL AND status = 'active'",
    ).endswith("\n2")
    shop.lookups.close()

    # The general size is already right; setting it is what carries it to the forms.
    _response, saved = propose(client, monkeypatch, changes_reply(
        {"listing": shorts, "set": {"size": "M"}}, {"listing": tee, "set": {"size": " XL "}},
    ))
    assert [(item["set"], item["fields"]) for item in saved["changes"]["items"]] == [
        ({"size": "M"}, [
            {"field": "ebay_size", "old": "34", "new": "M"},
            {"field": "poshmark_size", "old": "L", "new": "M"},
        ]),
        ({"size": "XL"}, [
            {"field": "title", "old": "Gap L Tee", "new": "Gap XL Tee"},
            {"field": "size", "old": "L", "new": "XL"},
            {"field": "depop_size", "old": "L", "new": "XL"},
        ]),
    ]
    client.post(f"/api/assistant/messages/{saved['id']}/changes/apply")
    db.expire_all()
    fixed = ListingRepo(db).get_revisions(shorts)[0].listing_json
    assert {fixed["size"], fixed["ebay_specifics"]["size"], fixed["poshmark_specifics"]["size"]} == {"M"}
    assert ListingRepo(db).get_revisions(tee)[0].listing_json["title"] == "Gap XL Tee"
    shop = business_assistant.read_shop(db, now=NOW)
    assert "--- Listings whose sizing needs a look (0) ---" in shop.brief
    shop.lookups.close()


def test_each_thread_keeps_its_own_conversation(workspace, monkeypatch):
    _db, client = workspace
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("Noted.",)))
    first = client.post("/api/assistant/threads").json()
    second = client.post("/api/assistant/threads").json()
    assert first["title"] == "New thread"

    client.post("/api/assistant/messages", json={"text": "How did last month do?", "thread_id": first["id"]})
    client.post("/api/assistant/messages", json={"text": "Which box paid off?", "thread_id": second["id"]})

    assert [row["text"] for row in client.get("/api/assistant/messages", params={"thread_id": first["id"]}).json()] == [
        "How did last month do?", "Noted.",
    ]
    assert [row["text"] for row in client.get("/api/assistant/messages", params={"thread_id": second["id"]}).json()] == [
        "Which box paid off?", "Noted.",
    ]
    titles = {row["id"]: row["title"] for row in client.get("/api/assistant/threads").json()}
    assert titles[first["id"]] == "How did last month do?"
    assert titles[second["id"]] == "Which box paid off?"

    assert client.delete(f"/api/assistant/threads/{first['id']}").json() == {"ok": True}
    assert client.get("/api/assistant/messages", params={"thread_id": first["id"]}).status_code == 404
    assert client.get("/api/assistant/messages", params={"thread_id": second["id"]}).json()[0]["text"] == "Which box paid off?"
    assert client.post("/api/assistant/messages", json={"text": "Again", "thread_id": "missing"}).status_code == 404


def test_saving_the_answer_does_not_stall_studio_and_a_follow_up_goes_straight_through(workspace, monkeypatch):
    """A save waiting on SQLite's write lock (a sync sweep holding it) used to freeze the whole server.

    The answer's stream could not end, so the page stayed busy and the follow-up never sent.
    """
    import threading
    import time as clock

    db, _client = workspace
    saved = business_assistant.add_message
    on_loop = []

    def slow_save(session, role, *args, **kwargs):
        if role == "assistant":
            on_loop.append(threading.current_thread() is threading.main_thread())
            clock.sleep(0.4)
        return saved(session, role, *args, **kwargs)

    monkeypatch.setattr(business_assistant, "add_message", slow_save)

    async def scenario():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker = asyncio.create_task(tick())
        provider = SlowProvider()
        run = await start_answer(db, provider)
        provider.release.set()
        before = ticks
        await run.task
        during = ticks - before
        ticker.cancel()
        # The run is over once its stream ends: a follow-up is not refused.
        assert assistant_route.active_generation(assistant_route.ANSWER_RUN) is None
        return during

    assert asyncio.run(scenario()) >= 5
    assert on_loop == [False]
    assert [row.text for row in business_assistant.list_messages(db)] == ["What sold?", "Half of it done."]


def test_the_shop_is_read_once_until_something_it_rests_on_changes(workspace, monkeypatch):
    from anteroom.services import business_context

    db, _client = workspace
    seed(db)
    reads = []
    real = business_assistant._read_data
    monkeypatch.setattr(business_assistant, "_read_data", lambda *args: reads.append(1) or real(*args))

    first = business_assistant.read_shop(db)
    second = business_assistant.read_shop(db)
    assert len(reads) == 1 and first.brief == second.brief
    # Each question still gets its own lookup copy.
    first.lookups.close()
    assert business_assistant.run_lookup(second.lookups, "SELECT count(*) FROM listings").endswith("\n3")

    add_listing(db, "Pendleton Board Shirt", {"price": 45})
    third = business_assistant.read_shop(db)
    assert len(reads) == 2 and "--- Listings (4: " in third.brief

    # What the seller says and what it remembers are always read fresh, and come last.
    assistant_memory.apply(db, json.dumps([{"op": "add", "text": "Ships from Dallas."}]))
    db.commit()
    fourth = business_assistant.read_shop(db)
    assert len(reads) == 2 and fourth.brief.rstrip().endswith("Ships from Dallas.")

    business_context.invalidate_business_context()
    business_assistant.read_shop(db)
    assert len(reads) == 3
    for shop in (second, third, fourth):
        shop.lookups.close()


def test_the_brief_reads_the_same_through_the_day_so_a_prompt_cache_can_keep_it(workspace):
    db, _client = workspace
    seed(db)
    morning = business_assistant.read_shop(db, now=datetime(2026, 10, 1, 9, 0, 1, 1234, tzinfo=UTC))
    later = business_assistant.read_shop(db, now=datetime(2026, 10, 1, 9, 47, 33, 98765, tzinfo=UTC))
    assert morning.brief == later.brief
    for shop in (morning, later):
        shop.lookups.close()


TIMING = re.compile(
    r"^assistant answer timing: outcome=(\w+) queue_wait_ms=(-|\d+) prepare_ms=(\d+) rounds=(\d+) "
    r"first_token_ms=([\d,-]*) round_ms=([\d,]*) lookups=(\d+) lookup_ms=(\d+) save_ms=(-|\d+) total_ms=(\d+)$")


def timing_lines(caplog):
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith("assistant answer timing")]


def test_each_answer_logs_where_its_time_went_and_nothing_the_seller_wrote(workspace, monkeypatch, caplog):
    db, client = workspace
    seed(db)
    caplog.set_level("INFO", logger="anteroom.assistant")
    ask_through(client, monkeypatch, (f"{FENCE}sql\n-- sizes\nSELECT size FROM listings WHERE status = 'sold'\n{FENCE}",),
                ("Size 34 sold.",))
    [line] = timing_lines(caplog)
    match = TIMING.match(line)
    assert match, line
    outcome, queued, _prepare, rounds, first, each, lookups, _lookup, saved, _total = match.groups()
    assert (outcome, queued, rounds, lookups) == ("answered", "-", "2", "1")
    assert len(first.split(",")) == 2 and len(each.split(",")) == 2 and saved != "-"
    for private in ("Which sizes sell?", "Size 34", "SELECT", "sizes", "Levi"):
        assert private not in line


def test_a_failed_answer_still_logs_its_timing(workspace, monkeypatch, caplog):
    _db, client = workspace
    caplog.set_level("INFO", logger="anteroom.assistant")
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("Half an",), fail=True))
    client.post("/api/assistant/messages", json={"text": "Anything stale?"})
    [line] = timing_lines(caplog)
    assert TIMING.match(line) and " outcome=error " in line and " rounds=1 " in line


def test_queue_wait_is_measured_from_when_the_question_was_queued(workspace, monkeypatch, caplog):
    from datetime import timedelta

    db, _client = workspace
    caplog.set_level("INFO", logger="anteroom.assistant")
    thread = business_assistant.resolve_thread(db, None)
    business_assistant.add_message(db, "user", "What sold?", thread_id=thread.id)
    journal = assistant_route._Journal()
    queued_at = datetime.now(UTC) - timedelta(seconds=3)
    error = asyncio.run(assistant_route._answer(journal, Provider(), "What sold?", None, thread.id, queued_at=queued_at))
    assert error is None
    [line] = timing_lines(caplog)
    waited = int(TIMING.match(line).group(2))
    assert 3_000 <= waited < 60_000
    # A clock that runs ahead of the database's never reports a negative wait; values are capped.
    assert assistant_route.elapsed_ms_since(datetime.now(UTC) + timedelta(seconds=5)) == 0
    assert assistant_route.elapsed_ms_since(datetime.now(UTC) - timedelta(days=2)) == 600_000


def test_only_a_slow_answer_logs_its_timing_as_a_warning(workspace, monkeypatch, caplog):
    _db, client = workspace
    caplog.set_level("INFO", logger="anteroom.assistant")
    ask_through(client, monkeypatch, ("Quick.",))
    monkeypatch.setattr(business_assistant, "SLOW_ANSWER_MS", 0)
    ask_through(client, monkeypatch, ("Slow.",))
    levels = [record.levelname for record in caplog.records if record.getMessage().startswith("assistant answer timing")]
    assert levels == ["INFO", "WARNING"]


def test_one_question_reads_each_part_of_the_shop_once(workspace, monkeypatch):
    """Eight sales ranges share one read of the listings, the sale ledger, sale events and ad spend."""
    from anteroom.services import ad_spend, inventory_analytics, sale_events, sales_records

    db, _client = workspace
    seed(db)
    calls: dict[str, int] = {}
    for module, name in ((inventory_analytics, "read_analytics_inputs"), (sales_records, "read_sale_lines"),
                         (sale_events, "windows"), (ad_spend, "list_entries")):
        real = getattr(module, name)

        def counted(*args, _real=real, _name=name, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    shop = business_assistant.read_shop(db, now=NOW)
    shop.lookups.close()
    # Ad spend is read once and shared by the eight ranges and the seller's own entries table.
    assert calls == {"read_analytics_inputs": 1, "read_sale_lines": 1, "windows": 1, "list_entries": 1}


def test_every_range_the_assistant_reads_matches_analytics(workspace):
    """Sharing the reads changes no figure: each range equals Analytics' own answer for it."""
    from anteroom.services import ad_spend
    from anteroom.services.inventory_analytics import ANALYTICS_RANGES, analytics_ranges, inventory_analytics, load_rows

    db, _client = workspace
    _box, jeans, _coat = seed(db)
    add_listing(db, "Undated sale", {"price": 30}, {"vendooStatus": "sold", "vendooSale": {"marketplace": "ebay"}})
    db.add(AdSpend(marketplace="ebay", start_date=date(2026, 9, 20), end_date=date(2026, 9, 29), spend=12.5))
    db.commit()
    assert ad_spend.list_entries(db)
    rows = load_rows(db)
    shared = analytics_ranges(db, ANALYTICS_RANGES, now=NOW, rows=rows)
    for range_id in ANALYTICS_RANGES:
        assert shared[range_id] == inventory_analytics(db, range_id=range_id, now=NOW, rows=rows), range_id
    assert shared["all"]["incomplete_sales"] and shared["30d"]["ads"]["spend"]
    assert jeans.id in {row.conversation_id for row in rows}


@pytest.mark.parametrize('pieces', [False, True])
def test_failed_round_records_wait_until_failure(monkeypatch, pieces):
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr(business_assistant, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    failure = RuntimeError('provider failed')

    class FailingProvider:
        async def chat(self, *_args, **_kwargs):
            if pieces:
                clock[0] = 1.0
                yield 'Hello.'
            clock[0] = 80.0
            raise failure

    timing = business_assistant.AnswerTiming()

    async def consume():
        async for _ in business_assistant.answer(FailingProvider(), [], None, timing):
            pass

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(consume())
    assert caught.value is failure
    assert timing.round_ms == [80_000]
    assert timing.first_token_ms == [1000 if pieces else None]


def test_cancelled_lookup_records_attempt_and_wait(monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr(business_assistant, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def cancelled(*_args, **_kwargs):
        clock[0] = 8.0
        raise asyncio.CancelledError()

    monkeypatch.setattr(business_assistant, 'run_any_lookup', cancelled)
    timing = business_assistant.AnswerTiming()

    async def consume():
        async for _ in business_assistant.answer(Provider(('```sql\nSELECT 1\n```',)), [], None, timing):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume())
    assert timing.lookups == 1
    assert timing.lookup_ms == 8000


def test_cancelled_preparation_records_wait(monkeypatch, caplog):
    caplog.set_level('INFO', logger='anteroom.assistant')

    async def cancelled(_run, work, *_args):
        work.close()
        raise asyncio.CancelledError()

    monkeypatch.setattr(assistant_route, 'await_with_pulses', cancelled)
    monkeypatch.setattr(business_assistant, 'elapsed_ms', lambda _start: 7000)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(assistant_route._answer(assistant_route._Journal(), Provider(), 'Question'))
    [line] = timing_lines(caplog)
    assert 'outcome=cancelled ' in line and 'prepare_ms=7000 ' in line


@pytest.mark.parametrize('failure_name, outcome', [
    ('RuntimeError', 'error'), ('TaskLeaseExpired', 'lease_lost'),
    ('TaskStopped', 'cancelled'), ('CancelledError', 'cancelled'),
])
def test_failed_save_records_outcome_and_duration(workspace, monkeypatch, caplog, failure_name, outcome):
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')
    failure = (RuntimeError('save failed') if failure_name == 'RuntimeError' else
               asyncio.CancelledError() if failure_name == 'CancelledError' else
               getattr(background_tasks, failure_name)())

    def fail_save(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(assistant_route, '_save_answer', fail_save)
    monkeypatch.setattr(business_assistant, 'elapsed_ms', lambda _start: 9000)
    with pytest.raises(type(failure)) as caught:
        asyncio.run(assistant_route._answer(assistant_route._Journal(), Provider(), 'Question'))
    assert caught.value is failure
    [line] = timing_lines(caplog)
    assert f'outcome={outcome} ' in line and 'save_ms=9000 ' in line


@pytest.mark.parametrize('stopped', [False, True])
def test_journal_stop_reason_survives_worker_cancellation(monkeypatch, caplog, stopped):
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')
    failure = background_tasks.TaskStopped() if stopped else background_tasks.TaskLeaseExpired()

    async def preparing(_run, work, *_args):
        work.close()
        await asyncio.Future()

    def reject(*_args, **_kwargs):
        raise failure

    def unexpected(*_args, **_kwargs):
        pytest.fail('Preparation was cancelled; no provider round or save may start')

    monkeypatch.setattr(assistant_route, 'await_with_pulses', preparing)
    monkeypatch.setattr(assistant_route, '_require_provider', Provider)
    monkeypatch.setattr(assistant_route, '_save_answer', unexpected)
    monkeypatch.setattr(business_assistant, 'answer', unexpected)
    monkeypatch.setattr(background_tasks, 'EVENT_INTERVAL', 0.001)
    monkeypatch.setattr(background_tasks, 'append_events', reject)
    monkeypatch.setattr(background_tasks, 'finish', reject)
    task = SimpleNamespace(payload={'question': 'Question'}, assistant_thread_id=None, created_at=datetime.now(UTC))
    assert asyncio.run(assistant_route.answer_task('task', 'token', task)) is True
    [line] = timing_lines(caplog)
    outcome = 'cancelled' if stopped else 'lease_lost'
    assert f'outcome={outcome} ' in line
    assert 'rounds=0 ' in line and 'lookups=0 ' in line and 'save_ms=- ' in line


@pytest.mark.parametrize('failure_name, expected', [
    (None, 'error'), ('TaskStopped', 'cancelled'), ('TaskLeaseExpired', 'lease_lost'),
])
def test_empty_hosted_answer_logs_after_finish(monkeypatch, caplog, failure_name, expected):
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')
    monkeypatch.setattr(assistant_route, '_require_provider', Provider)
    monkeypatch.setattr(assistant_route, '_prepare', lambda *_: ([], SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(background_tasks, 'append_events', lambda *_: None)

    async def empty(*_):
        if False:
            yield

    def finish(*_, **kwargs):
        assert timing_lines(caplog) == []
        if failure_name:
            raise getattr(background_tasks, failure_name)()
        assert kwargs['error']

    monkeypatch.setattr(business_assistant, 'answer', empty)
    monkeypatch.setattr(background_tasks, 'finish', finish)
    task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
    asyncio.run(assistant_route.answer_task('task', 'token', task))
    [line] = timing_lines(caplog)
    assert f'outcome={expected} ' in line


def test_journal_database_failure_logs_error(monkeypatch, caplog):
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')

    async def preparing(_run, work, *_):
        work.close()
        await asyncio.Future()

    def fail(*_):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(assistant_route, 'await_with_pulses', preparing)
    monkeypatch.setattr(assistant_route, '_require_provider', Provider)
    monkeypatch.setattr(background_tasks, 'EVENT_INTERVAL', .001)
    monkeypatch.setattr(background_tasks, 'append_events', fail)
    monkeypatch.setattr(background_tasks, 'finish', lambda *_, **kwargs: None)
    task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
    asyncio.run(assistant_route.answer_task('task', 'token', task))
    [line] = timing_lines(caplog)
    assert 'outcome=error ' in line


@pytest.mark.parametrize('saved', [False, True])
def test_concurrent_lease_loss_waits_for_save_confirmation(monkeypatch, caplog, saved):
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')

    async def exercise():
        saving, release = asyncio.Event(), asyncio.Event()

        async def answer(journal, *_, settle, **kwargs):
            journal.publish('data: answer\n\n')
            saving.set()
            await release.wait()
            # The append took frames before settle started.
            settle(None, None)
            await asyncio.sleep(0)
            if saved:
                journal.confirm_saved('answered')
            journal.timing_sample = (business_assistant.AnswerTiming(),
                                     'answered' if saved else 'lease_lost', 100)

        def append(*_):
            asyncio.run_coroutine_threadsafe(unblock(), loop).result()
            raise background_tasks.TaskLeaseExpired()

        async def unblock():
            release.set()
            await asyncio.sleep(0)

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(assistant_route, '_answer', answer)
        monkeypatch.setattr(background_tasks, 'append_events', append)
        monkeypatch.setattr(background_tasks, 'complete_in', lambda *_, **kwargs: None)
        monkeypatch.setattr(background_tasks, 'finish', lambda *_, **kwargs: None)
        task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
        await assistant_route.answer_task('task', 'token', task)

    monkeypatch.setattr(assistant_route, '_require_provider', Provider)
    monkeypatch.setattr(background_tasks, 'EVENT_INTERVAL', .001)
    asyncio.run(exercise())
    [line] = timing_lines(caplog)
    assert f"outcome={'answered' if saved else 'lease_lost'} " in line


@pytest.mark.parametrize('commit_failed', [False, True])
def test_precommit_settle_does_not_confirm_answer_save(workspace, monkeypatch, caplog, commit_failed):
    caplog.set_level('INFO', logger='anteroom.assistant')
    journal = assistant_route._Journal()
    settled = []
    failure = RuntimeError('commit failed')

    def save(_provider, _answer, _changes, _memory, _automation, _thread, settle, _error, *, on_committed):
        settle(None, None)
        assert journal.saved_outcome is None
        if commit_failed:
            raise failure
        on_committed()

    monkeypatch.setattr(assistant_route, '_save_answer', save)

    async def exercise():
        return await assistant_route._answer(
            journal, Provider(), 'Question', settle=lambda *_: settled.append(True))

    if commit_failed:
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(exercise())
        assert caught.value is failure
    else:
        assert asyncio.run(exercise()) is None
    assert settled == [True]
    assert journal.saved_outcome == (None if commit_failed else 'answered')
    assert journal.timing_sample[1] == ('error' if commit_failed else 'answered')
    assert timing_lines(caplog) == []  # Hosted emission belongs to answer_task.


@pytest.mark.parametrize('stop', [None, 'cancelled', 'lease_lost'])
def test_cleanup_cancels_unfinished_save_even_after_settle(monkeypatch, caplog, stop):
    from types import SimpleNamespace
    from anteroom.services import background_tasks
    from anteroom.services.service_context import ServiceContext, use

    caplog.set_level('INFO', logger='anteroom.assistant')
    context = ServiceContext('seller', lambda: None, None, None, None)
    other_task = ServiceContext('seller', lambda: None, None, None, None)

    async def exercise():
        saving, cancelled = asyncio.Event(), asyncio.Event()

        async def answer(journal, *_, settle, **kwargs):
            settle(None, None)  # The callback ran, but the commit has not returned.
            saving.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
                journal.timing_sample = (business_assistant.AnswerTiming(), 'cancelled', 100)

        monkeypatch.setattr(assistant_route, '_answer', answer)
        task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
        with use(context):
            worker = asyncio.create_task(assistant_route.answer_task('task', 'token', task))
            await asyncio.wait_for(saving.wait(), 1)
            if stop is not None:
                context.task_outcome['stop'] = stop
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(worker, 1)
        assert cancelled.is_set()

    monkeypatch.setattr(assistant_route, '_require_provider', Provider)
    monkeypatch.setattr(background_tasks, 'complete_in', lambda *_, **kwargs: None)
    asyncio.run(exercise())
    [line] = timing_lines(caplog)
    assert f'outcome={stop or "interrupted"} ' in line
    assert other_task.task_outcome == {}  # Same seller, independent claimed-task state.


@pytest.mark.parametrize('failure, expected', [
    (None, 'answered'), ('commit', 'error'), ('fence', 'lease_lost'),
])
def test_answer_commit_is_confirmed_before_blocked_refresh(workspace, monkeypatch, caplog, failure, expected):
    from threading import Event
    from types import SimpleNamespace
    from anteroom.models.assistant import AssistantMessage
    from anteroom.services import background_tasks

    caplog.set_level('INFO', logger='anteroom.assistant')
    append_started, save_terminal, release_refresh, save_closed = (Event() for _ in range(4))
    factory = assistant_route.SessionLocal
    journals = []
    earlier_commits = []

    class Journal(assistant_route._Journal):
        def __init__(self):
            super().__init__()
            journals.append(self)

    def sessions():
        db = factory()
        commit, refresh, close = db.commit, db.refresh, db.close

        def commit_answer():
            final = any(isinstance(row, AssistantMessage) for row in db.new)
            assert journals[0].saved_outcome is None
            if final and failure == 'commit':
                save_terminal.set()
                raise RuntimeError('final commit failed')
            commit()  # Real SQLite commit, including any earlier thread creation.
            if not final:
                assert journals[0].saved_outcome is None
                earlier_commits.append(True)

        def refresh_answer(row, *args, **kwargs):
            if isinstance(row, AssistantMessage):
                # add_message has committed and confirmed, but has not returned.
                assert journals[0].saved_outcome == 'answered'
                save_terminal.set()
                assert release_refresh.wait(5), 'test must release the save thread'
            return refresh(row, *args, **kwargs)

        def close_save():
            try:
                close()
            finally:
                save_closed.set()

        monkeypatch.setattr(db, 'commit', commit_answer)
        monkeypatch.setattr(db, 'refresh', refresh_answer)
        monkeypatch.setattr(db, 'close', close_save)
        return db

    class WaitingProvider:
        async def chat(self, *_args, **_kwargs):
            yield 'Answer.'
            assert await asyncio.to_thread(append_started.wait, 5)
            yield '\n```memory\n[{"op":"add","text":"Prefers denim."}]\n```'

    def append(*_args):
        # The save now waits for this append, so it must finish first.
        append_started.set()

    def settle(*_args, **_kwargs):
        if failure == 'fence':
            save_terminal.set()
            raise background_tasks.TaskLeaseExpired()

    monkeypatch.setattr(assistant_route, '_Journal', Journal)
    monkeypatch.setattr(assistant_route, 'SessionLocal', sessions)
    monkeypatch.setattr(assistant_route, '_prepare', lambda *_: ([], SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(assistant_route, '_require_provider', WaitingProvider)
    monkeypatch.setattr(background_tasks, 'EVENT_INTERVAL', .001)
    monkeypatch.setattr(background_tasks, 'append_events', append)
    monkeypatch.setattr(background_tasks, 'complete_in', settle)
    monkeypatch.setattr(background_tasks, 'finish', lambda *_, **kwargs: None)

    async def exercise():
        task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
        worker = asyncio.create_task(assistant_route.answer_task('task', 'token', task))
        try:
            if failure is None:
                assert await asyncio.to_thread(save_terminal.wait, 3)
                # Interrupt while post-commit refresh is still blocked.
                worker.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(worker, 3)
            else:
                await asyncio.wait_for(worker, 3)
            [line] = timing_lines(caplog)
            assert f'outcome={expected} ' in line
            assert journals[0].saved_outcome == ('answered' if failure is None else None)
            # Memory only flushes; thread creation commits after the fence accepts.
            assert bool(earlier_commits) is (failure != 'fence')
            if failure is None:
                assert not save_closed.is_set(), 'cleanup must not wait for post-commit refresh'
        finally:
            release_refresh.set()
            await asyncio.gather(worker, return_exceptions=True)
            assert await asyncio.to_thread(save_closed.wait, 5)

    asyncio.run(exercise())


def test_listing_details_are_read_a_batch_at_a_time(workspace, monkeypatch):
    """A big shop's listings are many megabytes: one statement for all of them ran past
    the hosted statement timeout under load, so each statement reads a bounded batch."""
    from sqlalchemy import event
    from anteroom.services.inventory_analytics import load_rows

    db, _client = workspace
    for n in range(5):
        add_listing(db, f"Tee {n}", {"price": 10 + n, "description": f"Shirt number {n}"})
    db.commit()
    monkeypatch.setattr(business_assistant, "LISTING_BATCH", 2)
    statements = []

    def seen(_conn, _cursor, statement, *_args):
        if "listing_revisions.listing_json" in statement and "FROM listings JOIN listing_revisions" in statement:
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", seen)
    try:
        records, detail_rows = business_assistant._records(db, load_rows(db), NOW)
    finally:
        event.remove(engine, "before_cursor_execute", seen)

    assert len(statements) == 3 and all("LIMIT" in statement for statement in statements)
    assert len(records) == 5
    assert sorted(detail["details"]["description"] for detail in detail_rows) == [
        f"Shirt number {n}" for n in range(5)]


def test_the_lookup_copy_is_built_only_when_a_lookup_runs(workspace, monkeypatch):
    db, _client = workspace
    seed(db)
    built = 0
    real = business_assistant._lookup_database

    def counted(tables):
        nonlocal built
        built += 1
        return real(tables)

    monkeypatch.setattr(business_assistant, "_lookup_database", counted)
    shop = business_assistant.read_shop(db, now=NOW)
    assert built == 0
    shop.close()
    assert built == 0

    first = shop.lookups
    second = shop.lookups
    assert first is second and built == 1
    shop.close()


def test_stopping_during_lookup_build_keeps_the_event_loop_responsive(monkeypatch):
    import sqlite3
    import threading

    tables = {name: [] for name in business_assistant.TABLES}
    shop = business_assistant.Shop(brief="", tables=tables)
    monkeypatch.setattr(assistant_route, "_prepare", lambda *_: ([], shop))
    provider = Provider((f"{FENCE}sql\nSELECT 1\n{FENCE}",))
    real_build = business_assistant._lookup_database
    real_close = shop.close
    release = threading.Event()
    built = []

    async def exercise():
        loop = asyncio.get_running_loop()
        building = asyncio.Event()
        closing = asyncio.Event()

        def build(tables):
            loop.call_soon_threadsafe(building.set)
            # Bound the wait so a regression cannot deadlock the test.
            if not release.wait(3):
                raise TimeoutError("The event loop did not release the lookup build")
            conn = real_build(tables)
            built.append(conn)
            return conn

        def close():
            loop.call_soon_threadsafe(closing.set)
            real_close()

        monkeypatch.setattr(business_assistant, "_lookup_database", build)
        monkeypatch.setattr(shop, "close", close)
        work = asyncio.create_task(assistant_route._answer(assistant_route._Journal(), provider, "What sold?"))
        try:
            await asyncio.wait_for(building.wait(), 5)
            work.cancel()
            await asyncio.wait_for(closing.wait(), 5)
            assert not work.done(), "Cleanup blocked the event loop until the build timed out"
        finally:
            release.set()
            if not work.done():
                await asyncio.gather(work, return_exceptions=True)
        assert work.cancelled()

    asyncio.run(exercise())
    (conn,) = built
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")


def test_an_answer_without_a_lookup_never_builds_the_copy(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    built = 0
    real = business_assistant._lookup_database

    def counted(tables):
        nonlocal built
        built += 1
        return real(tables)

    monkeypatch.setattr(business_assistant, "_lookup_database", counted)
    monkeypatch.setattr(assistant_route, "get_listing_provider", lambda: Provider(("No.",)))
    response = client.post("/api/assistant/messages", json={"text": "Any sales?"})
    assert response.status_code == 200 and "No." in response.text
    assert built == 0


def test_the_copy_is_built_off_the_event_loop(workspace, monkeypatch):
    db, _client = workspace
    seed(db)
    real = business_assistant._lookup_database
    built_on_loop: list[bool] = []

    def checked(tables):
        try:
            asyncio.get_running_loop()
            built_on_loop.append(True)
        except RuntimeError:
            built_on_loop.append(False)
        return real(tables)

    monkeypatch.setattr(business_assistant, "_lookup_database", checked)

    async def exercise():
        shop_sql = business_assistant.read_shop(db, now=NOW)
        try:
            result = await business_assistant.run_any_lookup(
                business_assistant.Lookup("sql", "SELECT count(*) FROM listings", 0, 1), shop_sql,
            )
            assert isinstance(result, str) and result.endswith("\n3")
        finally:
            shop_sql.close()

    asyncio.run(exercise())
    assert built_on_loop == [False]


def test_sold_listings_skip_the_size_audit(workspace, monkeypatch):
    from anteroom.services import size_audit

    db, _client = workspace
    sold_item = add_listing(
        db, "Sold Shorts M",
        {"price": 25, "size": "M", "ebay_specifics": {"size": "34"}, "poshmark_specifics": {"size": "L"}},
        sold(25, "ebay", "2026-08-01T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
    )
    add_listing(
        db, "Active Shorts M",
        {"price": 25, "size": "M", "ebay_specifics": {"size": "34"}},
        {"vendooStatus": "active"},
    )
    audited: list[str] = []
    real = size_audit.size_problems

    def tracked(listing, photo):
        audited.append(str(listing.get("title") or ""))
        return real(listing, photo)

    monkeypatch.setattr(size_audit, "size_problems", tracked)
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        assert audited == ["Active Shorts M"]
        result = business_assistant.run_lookup(
            shop.lookups, f"SELECT size_issue FROM listings WHERE id = '{sold_item.id}'",
        )
        assert result == "Lookup result (1 rows):\nsize_issue\n"
    finally:
        shop.close()


def test_select_star_on_listings_fits_a_large_shop(workspace):
    db, _client = workspace
    seed(db)
    long_description = "Measurements and fabric notes. " * 70
    for n in range(60):
        add_listing(
            db, f"Vintage Tee {n}",
            {"price": 24, "brand": "Nike", "size": "M", "description": long_description,
             "ebay_specifics": {"Department": "Men", "Material": "Cotton"}},
            {"vendooStatus": "active", "vendooDates": {"listed": "2026-09-01T00:00:00+00:00"}},
        )
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        result = business_assistant.run_lookup(shop.lookups, "SELECT * FROM listings")
        assert result.startswith("Lookup result (63 rows):\n")
        assert "there are more" not in result
    finally:
        shop.close()


def test_listing_details_has_one_row_per_listing_including_drafts(workspace):
    db, _client = workspace
    _box, jeans, coat = seed(db)
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        assert business_assistant.run_lookup(
            shop.lookups, "SELECT count(*) FROM listing_details",
        ).endswith("\n3")
        joined = business_assistant.run_lookup(
            shop.lookups,
            "SELECT l.status, d.description, json_extract(d.details, '$.price') FROM listings l "
            "JOIN listing_details d ON d.listing_id = l.id ORDER BY l.status",
        )
        assert "active |  | 120" in joined
        assert "draft |  | 15" in joined
        assert "sold | Classic straight leg denim. | 60" in joined
        assert f"{jeans.id}" in business_assistant.run_lookup(shop.lookups, "SELECT listing_id FROM listing_details")
        assert f"{coat.id}" in business_assistant.run_lookup(shop.lookups, "SELECT listing_id FROM listing_details")
    finally:
        shop.close()


def test_boxes_ads_and_events_stay_capped_in_the_brief(workspace):
    from datetime import timedelta

    from anteroom.models.sale_event import SaleEvent

    db, _client = workspace
    small = business_assistant.read_shop(db, now=NOW)
    try:
        small_len = len(small.brief)
    finally:
        small.close()

    for n in range(80):
        boxes.create_box(
            db,
            store=f"Supplier {n % 4}",
            title=f"Wholesale Lot Number {n}",
            price=100 + n,
            shipping=15,
            pieces=20,
            bought_at=NOW - timedelta(days=80 - n),
        )
    for n in range(40):
        start = date(2026, 1, 1) + timedelta(days=n * 5)
        db.add(AdSpend(
            marketplace=("ebay", "poshmark", "etsy")[n % 3],
            start_date=start,
            end_date=start + timedelta(days=4),
            spend=20.0 + n,
            notes=f"Campaign note {n}",
        ))
    for n in range(30):
        start = date(2026, 1, 1) + timedelta(days=n * 7)
        db.add(SaleEvent(
            title=f"Promo Event {n}",
            marketplace="ebay",
            start_date=start,
            end_date=start + timedelta(days=3),
            timezone="UTC",
            discount_percent=15.0,
            items=[],
            status="ended",
            notes=f"Event note {n}",
        ))
    db.commit()

    big = business_assistant.read_shop(db, now=NOW)
    try:
        assert len(big.brief) < small_len + 10_000
        assert "Only the latest 15 of 80 boxes are shown" in big.brief
        assert "Wholesale Lot Number 79" in big.brief
        assert "Wholesale Lot Number 0" not in big.brief
        assert "Only the latest 10 of 40 entries are shown" in big.brief
        assert "Only the latest 10 of 30 events are shown" in big.brief
        assert business_assistant.run_lookup(big.lookups, "SELECT count(*) FROM boxes").endswith("\n80")
        assert business_assistant.run_lookup(big.lookups, "SELECT count(*) FROM ad_spend").endswith("\n40")
        assert business_assistant.run_lookup(big.lookups, "SELECT count(*) FROM sale_events").endswith("\n30")
        assert "sell_through_by_age" in business_assistant.run_lookup(
            big.lookups, "SELECT sell_through_by_age FROM boxes LIMIT 1",
        )
    finally:
        big.close()


def test_median_aggregate_handles_numbers_nulls_empty_and_text(workspace):
    db, _client = workspace
    seed(db)
    # Seed asking prices: 15, 60, 120 -> median 60.
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        assert business_assistant.run_lookup(
            shop.lookups, "SELECT median(asking) AS med FROM listings",
        ) == "Lookup result (1 rows):\nmed\n60.0"
        assert business_assistant.run_lookup(
            shop.lookups, "SELECT median(asking) AS med FROM listings WHERE 1 = 0",
        ) == "Lookup result (1 rows):\nmed\n"
        assert business_assistant.run_lookup(
            shop.lookups, "SELECT median(sold_for) AS med FROM listings WHERE status = 'draft'",
        ) == "Lookup result (1 rows):\nmed\n"
        assert business_assistant.run_lookup(
            shop.lookups, "SELECT median(title) AS med FROM listings",
        ) == "Lookup result (1 rows):\nmed\n"
    finally:
        shop.close()

    add_listing(db, "Extra Shirt", {"price": 30}, {"vendooStatus": "active"})
    even_shop = business_assistant.read_shop(db, now=NOW)
    try:
        # Asking prices: 15, 30, 60, 120 -> median 45.0.
        assert business_assistant.run_lookup(
            even_shop.lookups, "SELECT median(asking) AS med FROM listings",
        ) == "Lookup result (1 rows):\nmed\n45.0"
    finally:
        even_shop.close()


def test_zero_row_lookup_suggests_closest_whitelisted_values(workspace):
    db, _client = workspace
    seed(db)
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        # Case-only difference on a whitelisted column gets a hint.
        case_diff = business_assistant.run_lookup(
            shop.lookups, "SELECT title FROM listings WHERE brand = 'levi''s'",
        )
        assert case_diff.startswith("Lookup result: 0 rows matched WHERE brand = 'levi''s'.\ntitle\n")
        assert "Values in listings.brand closest to 'levi's': Levi's (1 row). Use one only if it names the same thing." in case_diff

        # Close spelling on sales_by_brand resolves that table instead of listings.
        by_brand = business_assistant.run_lookup(
            shop.lookups, "SELECT * FROM sales_by_brand WHERE brand = 'levis'",
        )
        assert "Values in sales_by_brand.brand closest to 'levis': Levi's (" in by_brand

        # Unrelated value gets no hint.
        no_match = business_assistant.run_lookup(
            shop.lookups, "SELECT title FROM listings WHERE size = '99'",
        )
        assert no_match == "Lookup result: 0 rows matched WHERE size = '99'.\ntitle"

        # Non-whitelisted column (title) gets no hint.
        non_whitelisted = business_assistant.run_lookup(
            shop.lookups, "SELECT id FROM listings WHERE title = 'Levi''s 501'",
        )
        assert non_whitelisted == "Lookup result: 0 rows matched WHERE title = 'Levi''s 501'.\nid"

        # A literal shaped like SQL injection cannot alter the hint query.
        attack = business_assistant.run_lookup(
            shop.lookups,
            "SELECT title FROM listings WHERE brand = 'x'' UNION SELECT name FROM sqlite_master --'",
        )
        assert attack.startswith("Lookup result: 0 rows matched")
        assert "sqlite_master" not in attack.split("\n", 2)[-1]
        assert "Values in " not in attack
    finally:
        shop.close()


def test_multiple_sql_lookups_run_in_one_round(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    two_blocks = (
        f"{FENCE}sql\n-- sold sizes\nSELECT size, count(*) FROM listings WHERE status = 'sold' GROUP BY size\n{FENCE}\n"
        f"{FENCE}sql\n-- active coats\nSELECT title, asking FROM listings WHERE status = 'active'\n{FENCE}"
    )
    provider, response = ask_through(client, monkeypatch, (two_blocks,), ("Size 34 sold and the coat is $120.",))

    assert len(provider.calls) == 2
    assert "event: status\ndata: Looking up sold sizes…" in response
    assert "event: status\ndata: Looking up active coats…" in response
    assert "SELECT" not in response and "Size 34 sold and the coat is $120." in response
    assert provider.calls[1][-1] == {
        "role": "user",
        "content": (
            "Lookup 1 of 2 (sold sizes):\n"
            "Lookup result (1 rows):\nsize | count(*)\n34 | 1\n\n"
            "Lookup 2 of 2 (active coats):\n"
            "Lookup result (1 rows):\ntitle | asking\nCarhartt | Chore Coat | 120.0"
        ),
    }


def test_more_than_three_lookups_in_one_round_are_capped(workspace):
    db, _client = workspace
    seed(db)
    four_blocks = "\n".join(
        f"{FENCE}sql\n-- query {n}\nSELECT {n} AS n\n{FENCE}" for n in range(1, 5)
    )
    provider = Provider((four_blocks,), ("Done.",))
    shop = business_assistant.read_shop(db, now=NOW)
    timing = business_assistant.AnswerTiming()
    try:
        async def collect():
            return [event async for event in business_assistant.answer(
                provider, [{"role": "user", "content": "Check four things"}], shop, timing=timing,
            )]
        events = asyncio.run(collect())
    finally:
        shop.close()

    assert len(provider.calls) == 2
    assert timing.lookups == 3
    statuses = [text for kind, text in events if kind == "status"]
    assert statuses == ["Looking up query 1…", "Looking up query 2…", "Looking up query 3…"]
    fed_back = provider.calls[1][-1]["content"]
    assert "Lookup 3 of 3 (query 3):" in fed_back
    assert "Lookup 4 of" not in fed_back
    assert "1 more lookup was not run: at most 3 run at once." in fed_back


def test_failing_and_good_lookups_in_one_round_both_report(workspace, monkeypatch):
    db, client = workspace
    seed(db)
    blocks = (
        f"{FENCE}sql\n-- broken query\nSELECT nope FROM listings\n{FENCE}\n"
        f"{FENCE}sql\n-- listing count\nSELECT count(*) FROM listings\n{FENCE}"
    )
    provider, response = ask_through(client, monkeypatch, (blocks,), ("There are 3 listings.",))

    assert len(provider.calls) == 2
    assert "There are 3 listings." in response
    fed_back = provider.calls[1][-1]["content"]
    assert "Lookup 1 of 2 (broken query):\nLookup listings failed: no such column: nope." in fed_back
    assert "Lookup 2 of 2 (listing count):\nLookup result (1 rows):\ncount(*)\n3" in fed_back


def test_history_window_moves_in_steps_starting_on_a_user_turn(workspace):
    db, _client = workspace
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        thread = business_assistant.create_thread(db)
        for index in range(21):
            role = "user" if index % 2 == 0 else "assistant"
            business_assistant.add_message(db, role, f"turn {index}", thread_id=thread.id)
        at_21 = business_assistant.build_messages(db, "turn 20", shop, thread_id=thread.id)
        assert len(at_21) == 22  # system + all 21 saved rows
        assert at_21[1] == {"role": "user", "content": "turn 0"}

        first_seen: list[dict] = []
        for length in range(22, 41):
            role = "user" if (length - 1) % 2 == 0 else "assistant"
            text = f"turn {length - 1}"
            business_assistant.add_message(db, role, text, thread_id=thread.id)
            if length >= 31:
                messages = business_assistant.build_messages(db, text, shop, thread_id=thread.id)
                first_seen.append(messages[1])
        assert len(first_seen) == 10
        assert all(first == {"role": "user", "content": "turn 10"} for first in first_seen)

        # If a stepped boundary lands on an assistant row, advance to the next user turn.
        shifted = business_assistant.create_thread(db)
        business_assistant.add_message(db, "user", "extra user 0", thread_id=shifted.id)
        for index in range(1, 32):
            role = "user" if index % 2 == 1 else "assistant"
            business_assistant.add_message(db, role, f"shifted {index}", thread_id=shifted.id)
        # Index 10 is assistant; history must start at index 11 (user).
        shifted_msgs = business_assistant.build_messages(db, "shifted 31", shop, thread_id=shifted.id)
        assert shifted_msgs[1] == {"role": "user", "content": "shifted 11"}
    finally:
        shop.close()


def test_size_audit_batches_ebay_schema_reads_and_never_opens_extra_sessions(workspace, monkeypatch):
    from sqlalchemy import event
    from anteroom.models.catalog import CategoryFieldSchema
    from anteroom.services import category_fields

    db, _client = workspace
    category_fields.clear_memory_cache()
    for cid in ("15687", "53159"):
        db.add(CategoryFieldSchema(
            marketplace="ebay",
            category_id=cid,
            fields=[{
                "key": "Size", "display": "Size", "required": True, "selection_only": True,
                "multi": False, "options": {"1": "M", "2": "L"}, "scales": {},
            }],
        ))
    for index in range(10):
        cid = "15687" if index % 2 == 0 else "53159"
        add_listing(
            db,
            f"Tee {index} M",
            {
                "title": f"Tee {index} M",
                "price": 25,
                "size": "M",
                "marketplace_category_ids": {"ebay": cid},
                "ebay_specifics": {"size": "L" if index == 0 else "M"},
            },
            {"vendooStatus": "active"},
        )
    db.commit()

    def forbid_extra_session(*_args, **_kwargs):
        raise AssertionError("size audit must reuse the snapshot session instead of opening SessionLocal per listing")

    monkeypatch.setattr(category_fields, "SessionLocal", forbid_extra_session)
    schema_queries: list[str] = []

    def seen(_conn, _cursor, statement, *_args):
        if "category_field_schemas" in statement:
            schema_queries.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", seen)
    try:
        shop = business_assistant.read_shop(db, now=NOW)
        try:
            assert len(schema_queries) == 1
            issues = business_assistant.run_lookup(
                shop.lookups, "SELECT title, size_issue FROM listings WHERE size_issue IS NOT NULL",
            )
            assert "Tee 0 M | ebay differs from the general size" in issues
        finally:
            shop.close()

        # Second cold snapshot hits category_fields' in-memory cache with 0 schema queries.
        shop2 = business_assistant.read_shop(db, now=NOW)
        shop2.close()
        assert len(schema_queries) == 1
    finally:
        event.remove(engine, "before_cursor_execute", seen)
        category_fields.clear_memory_cache()


def test_listing_details_table_populates_lazily_only_when_referenced(workspace):
    db, _client = workspace
    seed(db)
    shop = business_assistant.read_shop(db, now=NOW)
    try:
        res_listings = asyncio.run(business_assistant.run_any_lookup(
            business_assistant.Lookup("sql", "SELECT count(*) FROM listings", 0, 1), shop,
        ))
        assert isinstance(res_listings, str) and res_listings.endswith("\n3")
        assert shop._details_loaded is False

        res_details = asyncio.run(business_assistant.run_any_lookup(
            business_assistant.Lookup("sql", "SELECT count(*) FROM listing_details", 0, 1), shop,
        ))
        assert isinstance(res_details, str) and res_details.endswith("\n3")
        assert shop._details_loaded is True

        # Read-only authorizer stays active after on-demand table population.
        denied = asyncio.run(business_assistant.run_any_lookup(
            business_assistant.Lookup("sql", "DELETE FROM listing_details", 0, 1), shop,
        ))
        assert isinstance(denied, str) and "not authorized" in denied.lower()
    finally:
        shop.close()


def test_hosted_answer_flushes_status_and_first_text_immediately(monkeypatch):
    import time
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    flushed: list[tuple[float, list[str]]] = []
    release_finish = asyncio.Event()

    async def exercise():
        started = time.monotonic()
        saw_first_text = asyncio.Event()

        async def fake_answer(journal, *_args, settle, **_kwargs):
            journal.publish("event: status\ndata: Reading your shop…\n\n")
            await asyncio.sleep(0.02)
            journal.publish("data: Hello\n\n")
            await asyncio.wait_for(saw_first_text.wait(), timeout=2.0)
            await asyncio.wait_for(release_finish.wait(), timeout=2.0)
            settle(None, None)

        def append(_task_id, _token, frames):
            flushed.append((time.monotonic() - started, list(frames)))
            if any("data: Hello" in frame for frame in frames):
                loop.call_soon_threadsafe(saw_first_text.set)
                loop.call_soon_threadsafe(release_finish.set)

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(assistant_route, "_require_provider", Provider)
        monkeypatch.setattr(assistant_route, "_answer", fake_answer)
        monkeypatch.setattr(background_tasks, "EVENT_INTERVAL", 10.0)
        monkeypatch.setattr(background_tasks, "append_events", append)
        monkeypatch.setattr(background_tasks, "complete_in", lambda *_, **kwargs: None)
        monkeypatch.setattr(background_tasks, "finish", lambda *_, **kwargs: None)
        task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
        await asyncio.wait_for(assistant_route.answer_task("task", "token", task), timeout=3.0)

    asyncio.run(exercise())
    assert any("Reading your shop…" in frame for _elapsed, frames in flushed for frame in frames)
    assert any("data: Hello" in frame for _elapsed, frames in flushed for frame in frames)
    assert all(elapsed < 2.0 for elapsed, _frames in flushed)


@pytest.mark.parametrize("append_fails", [False, True])
def test_hosted_answer_settlement_waits_for_inflight_first_text(monkeypatch, append_fails):
    from threading import Event
    from types import SimpleNamespace
    from anteroom.services import background_tasks

    append_started, release_append, answer_done, settled = (Event() for _ in range(4))
    stored = []

    async def answer(*_args):
        yield "text", "Hello"
        assert await asyncio.to_thread(append_started.wait, 3)
        answer_done.set()

    def append(_task_id, _token, frames):
        if any("Hello" in frame for frame in frames):
            append_started.set()
            assert release_append.wait(3)
            if append_fails:
                raise background_tasks.TaskLeaseExpired()
        assert not settled.is_set()
        stored.extend(frames)

    def complete(_db, _task_id, _token, frames, **_kwargs):
        stored.extend(frames)
        settled.set()

    def save(_provider, _answer, _changes, _memory, _automation, _thread, settle, error, *, on_committed):
        settle(None, error)
        on_committed()

    monkeypatch.setattr(assistant_route, "_require_provider", Provider)
    monkeypatch.setattr(assistant_route, "_prepare", lambda *_: ([], SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(assistant_route, "_save_answer", save)
    monkeypatch.setattr(business_assistant, "answer", answer)
    monkeypatch.setattr(background_tasks, "append_events", append)
    monkeypatch.setattr(background_tasks, "complete_in", complete)
    monkeypatch.setattr(background_tasks, "finish", lambda *_, **_kwargs: None)

    async def exercise():
        task = SimpleNamespace(payload={}, assistant_thread_id=None, created_at=datetime.now(UTC))
        worker = asyncio.create_task(assistant_route.answer_task("task", "token", task))
        try:
            assert await asyncio.to_thread(answer_done.wait, 3)
            await asyncio.sleep(0.05)
            assert not settled.is_set()
        finally:
            release_append.set()
            await asyncio.wait_for(worker, 3)

    asyncio.run(exercise())
    assert settled.is_set() is not append_fails
    assert sum("Hello" in frame for frame in stored) == (0 if append_fails else 1)
