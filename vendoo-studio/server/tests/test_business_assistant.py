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

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.models.ad_spend import AdSpend
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.services import assistant_changes, assistant_memory, boxes, business_assistant

NOW = datetime(2026, 10, 1, tzinfo=UTC)
FAILED = re.compile(r"Lookup \w+ failed: ")


def add_listing(db, title, listing, notes=None, box_id=None):
    conv = ConversationRepo(db).create(title=title, notes=json.dumps(notes) if notes else None, box_id=box_id)
    ListingRepo(db).save_revision(conv.id, {"title": title, **listing}, source="user")
    return conv


def sold(price, market, listed, sold_at, **sale):
    return {
        "vendooStatus": "sold",
        "vendooSale": {"price": price, "marketplace": market, "soldAt": sold_at, **sale},
        "vendooDates": {"listed": listed, "sold": sold_at},
    }


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_route, "SessionLocal", factory)
    monkeypatch.setattr("vendoo_studio.services.box_scout.state_path", lambda: tmp_path / "sourcing.json")
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
    ListingRepo(db).save_revision(coat.id, {"title": coat.title, "price": 120, "cost": 25, "brand": "Carhartt",
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
                    "Sale events", "Sourcing checks"):
        assert f"--- {heading}" in brief
    assert "\n30d | 1 | 48 | 32 | 1 | " in brief.split("--- Sales by range ---")[1]
    assert "\n30d Levi's | 1 | 48 | 32 | 1 | " in brief.split("--- Brands ---")[1]
    assert '"store":"Raghouse"' in brief and '"spend":31.0' in brief
    assert "No sourcing check has finished yet" in brief.split("--- Sourcing buy list ---")[1]
    assert "--- Best boxes on the Sourcing tab (0 of 0) ---\nNo boxes in the last sourcing check." in brief
    assert "What the seller says" not in brief
    shop.lookups.close()


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
        lookups, "-- sold sizes\nSELECT size, sold_for, json_extract(details, '$.ebay_specifics.Rise') AS rise,"
                 " description FROM listings WHERE status = 'sold';",
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
    media = business_assistant.find_lookup("```view_box_media\nraghouse:1\n```")
    assert (media.kind, media.body) == ("media", "raghouse:1")
    assert business_assistant.lookup_label("-- sales by size.\nSELECT 1") == "Looking up sales by size…"
    assert business_assistant.lookup_label("SELECT 1") == "Looking up more detail…"
    assert business_assistant.lookup_label(media) == "Looking at the box photos…"


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
     "Lookup sourcing_box failed: no such table: sourcing_box. The tables are: listings, price_changes"),
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
    assert "event: error\ndata: provider went away" in response.text
    assert [row["text"] for row in client.get("/api/assistant/messages").json()] == ["Anything stale?", "Half an"]


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
    from vendoo_studio.services.streaming import start_generation

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
            await assistant_route.ask(assistant_route.AssistantQuestion(text="And yesterday?"), db=db)
        except HTTPException as refused:
            status = refused.status_code
        await assistant_route.stop()
        await asyncio.gather(run.task, return_exceptions=True)
        return status

    assert asyncio.run(scenario()) == 409
    assert [row.text for row in business_assistant.list_messages(db)] == ["What sold?", "Half of it "]


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
    assert saved["changes"] == {"status": "proposed", "items": [
        # The brand it already has and a field the assistant may not set are left out.
        {"listing_id": coat, "title": "Carhartt | Chore Coat",
         "set": {"asking": 100, "brand": "Carhartt", "labels": ["Sale", "Bin 2"]},
         "fields": [
             {"field": "asking", "old": 120, "new": 100},
             {"field": "labels", "old": [], "new": ["Sale", "Bin 2"]},
         ]},
        # Saving the jeans also puts their size in the title and on the eBay form, so those are shown too.
        {"listing_id": jeans, "title": "Levi's 501 Jeans", "set": {"cost": 4.0, "box": None}, "fields": [
            {"field": "cost", "old": None, "new": 4.0},
            {"field": "title", "old": "Levi's 501 Jeans", "new": "Levi's 34 501 Jeans"},
            {"field": "ebay_size", "old": None, "new": "34"},
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
    # Poshmark keeps the higher was-price, as a price drop in the listing does.
    assert newest.listing_json["poshmark_specifics"]["originalPrice"] == 120
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
    monkeypatch.setattr("vendoo_studio.services.price_markdown.listing_busy", lambda conv_id, db: True)
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
    from vendoo_studio.services import business_context

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
