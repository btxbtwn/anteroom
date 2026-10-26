"""Scheduled automations: schedule math, one run per slot, age, original price, dry run, CLI, assistant."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.cli.catalog import build_catalog_from_app
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.models.automation import AutomationRun, ItemPriceBaseline
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job, JobEvent
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.routes import automations as automations_route
from vendoo_studio.services import assistant_memory, automations
from vendoo_studio.services.automation_pricing import drop_price
from vendoo_studio.services.automation_prompt import draft_from_prompt
from vendoo_studio.services.automation_schedule import advance_after_run, first_due, next_after, normalize_schedule, parse_cron
from vendoo_studio.services.automation_scheduler import tick
from vendoo_studio.services.business_assistant import add_message

NOW = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)
PROMPT = (
    "When an item reaches 60 days listed on eBay and Poshmark, delist and relist it "
    "and drop the price by 10%. Don't go below $15."
)


@pytest.fixture
def db():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(automations_route.router)
    app.include_router(assistant_route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def _item(db, *, title, price, department, category, marketplace, days, status="active", brand="Nike"):
    conv = Conversation(title=title, status=status)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    ListingRepo(db).save_revision(conv.id, {
        "title": title, "price": price, "department": department,
        "category_path": category, "brand": brand,
    }, "test")
    db.add(MarketplaceListing(
        conversation_id=conv.id, marketplace=marketplace, state="published", listing_id=f"live-{conv.id}",
        created_at=NOW - timedelta(days=days),
    ))
    db.commit()
    return conv


def _sixty_day(activate=True):
    return {
        "prompt": PROMPT,
        "timezone": "America/Los_Angeles",
        "activate": activate,
    }


def test_schedule_daily_weekly_once_and_cron():
    daily = normalize_schedule({"cron": "30 9 * * *"})
    assert daily == {"kind": "daily", "time": "09:30"}
    weekly = parse_cron("0 9 * * 1,0")
    assert weekly["weekdays"] == [0, 6]
    once = normalize_schedule({"kind": "once", "at": "2026-11-03T09:00"})
    due = first_due(once, "America/Los_Angeles", NOW)
    assert due == datetime(2026, 11, 3, 17, 0, tzinfo=UTC)
    nxt = next_after(daily, "UTC", datetime(2026, 10, 26, 9, 30, tzinfo=UTC))
    assert nxt == datetime(2026, 10, 27, 9, 30, tzinfo=UTC)
    # Three missed mornings collapse to the next future slot after one catch-up.
    jumped = advance_after_run(daily, "UTC", datetime(2026, 10, 23, 9, 30, tzinfo=UTC), NOW)
    assert jumped == datetime(2026, 10, 27, 9, 30, tzinfo=UTC)


def test_prompt_drafts_the_sixty_day_rule_and_refuses_every_marketplace():
    draft = draft_from_prompt(PROMPT, timezone="America/Los_Angeles", now=NOW)
    assert draft["ready"] is True
    assert draft["filters"]["min_age_days"] == 60
    assert draft["marketplaces"] == ["ebay", "poshmark"]
    price = next(action for action in draft["actions"] if action["type"] == "price")
    assert price == {"type": "price", "mode": "percent", "value": 10.0, "floor": 15.0, "repeat": "step"}
    one_time = draft_from_prompt(
        "On 2026-11-03 at 9:00am, drop men's t-shirts by 10% on eBay. Don't go below $15.",
        now=NOW,
    )
    once_price = next(action for action in one_time["actions"] if action["type"] == "price")
    assert once_price["repeat"] == "once"
    assert any(action["type"] == "relist" for action in draft["actions"])
    assert all(action["type"] != "delist" for action in draft["actions"])
    assert draft["schedule"]["kind"] == "daily"
    vague = draft_from_prompt("Delist everything on all marketplaces tomorrow at 9am.", now=NOW)
    assert vague["ready"] is False
    assert any("marketplace" in issue.lower() for issue in vague["issues"])


def test_price_drop_uses_the_original_and_does_not_compound():
    basis, first = drop_price(100, 100, mode="percent", value=10, floor=15)
    assert (basis, first) == (100.0, 90.0)
    _basis, second = drop_price(100, first, mode="percent", value=10, floor=15)
    assert second == 90.0
    _basis, floored = drop_price(100, 100, mode="percent", value=50, floor=60)
    assert floored == 60.0
    _basis, amount = drop_price(40, 40, mode="amount", value=5, floor=15)
    assert amount == 35.0
    _basis, again = drop_price(40, amount, mode="amount", value=5, floor=15)
    assert again == 35.0
    _basis, held = drop_price(100, 81, mode="percent", value=10, floor=15)
    assert held == 81.0
    _basis, first_step = drop_price(100, 100, mode="percent", value=10, floor=15, repeat="step")
    _basis, second_step = drop_price(100, first_step, mode="percent", value=10, floor=15, repeat="step")
    _basis, third_step = drop_price(100, second_step, mode="percent", value=10, floor=15, repeat="step")
    assert (first_step, second_step, third_step) == (90.0, 80.0, 70.0)
    assert second_step != round(first_step * 0.9, 2)
    _basis, floored_step = drop_price(100, 80, mode="percent", value=10, floor=75, repeat="step")
    assert floored_step == 75.0
    _basis, held_floor = drop_price(100, 75, mode="percent", value=10, floor=75, repeat="step")
    assert held_floor == 75.0


def test_a_saved_task_is_still_there_in_a_new_session(db):
    created = automations.create_automation(db, _sixty_day(), now=NOW)
    assert created["status"] == "active"
    db.expire_all()
    again = automations.get_automation(db, created["id"])
    assert again.name
    assert again.next_run_at is not None
    assert again.marketplaces == ["ebay", "poshmark"]


def test_activating_an_ambiguous_draft_is_refused(db):
    with pytest.raises(automations.AutomationError):
        automations.create_automation(db, {
            "prompt": "Delist the old stuff.",
            "activate": True,
        }, now=NOW)
    saved = automations.create_automation(db, {"prompt": "Delist the old stuff."}, now=NOW)
    assert saved["status"] == "draft"
    assert saved["next_run_at"] is None


def test_age_filter_uses_marketplace_created_at_and_skips_sold_and_women(db):
    old = _item(db, title="Nike Men's Tee", price=40, department="Men", category="Tops > T-Shirts", marketplace="ebay", days=70)
    young = _item(db, title="Nike Men's Tee", price=40, department="Men", category="Tops > T-Shirts", marketplace="ebay", days=10)
    women = _item(db, title="Nike Women's Tee", price=40, department="Women", category="Tops > T-Shirts", marketplace="ebay", days=80)
    sold = _item(db, title="Nike Men's Tee", price=40, department="Men", category="Tops > T-Shirts", marketplace="ebay", days=90, status="sold")
    spec = automations.compile_spec({
        "prompt": "When a men's t-shirt reaches 60 days listed on eBay, delist and relist it and drop the price by 10%. Don't go below $15.",
        "timezone": "UTC",
    }, now=NOW)
    items = {item["conversation_id"]: item for item in automations.select_items(db, spec, NOW)}
    assert items[old.id]["include"] is True
    assert items[old.id]["age_days"] == 70
    assert items[old.id]["listed_source"] == "marketplace_listing"
    assert items[young.id]["include"] is False
    assert "under 60" in items[young.id]["skip_reason"]
    assert items[women.id]["include"] is False
    assert items[sold.id]["include"] is False
    assert "Sold" in items[sold.id]["skip_reason"]


def test_age_uses_the_real_listed_date_not_a_backfilled_row(db):
    """The shop's shape: Vendoo imports have no marketplace row, and the rows that exist were backfilled together."""
    imported = _conversation(db, title="Imported tee", price=40, listed="2026-08-01T12:00:00+00:00")
    _vendoo_job(db, imported.id, created_at=datetime(2026, 10, 26, 8, 0))
    _conversation(db, title="Notes only tee", price=40, listed="2026-07-01T12:00:00+00:00", job=False)
    backfilled = _item(db, title="Backfilled tee", price=40, department="Men", category="Tops > T-Shirts", marketplace="ebay", days=1)
    db.add(Job(
        conversation_id=backfilled.id, approved_revision_id="rev", listing_snapshot={},
        status="completed", current_step="native_ebay_list",
        created_at=datetime(2026, 8, 1, 12, 0), updated_at=datetime(2026, 8, 1, 12, 5),
    ))
    failed = _item(db, title="Failed publish tee", price=40, department="Men", category="Tops > T-Shirts", marketplace="ebay", days=1)
    failed_job = Job(
        conversation_id=failed.id, approved_revision_id="rev", listing_snapshot={},
        status="failed", current_step="native_ebay_list", created_at=datetime(2026, 8, 1, 12, 0),
    )
    db.add(failed_job)
    db.flush()
    db.add(JobEvent(
        job_id=failed_job.id, event_type="native_result", step="native_ebay_list",
        payload={"ok": False, "action": "list", "state": "unknown", "marketplace": "ebay"},
        created_at=datetime(2026, 8, 1, 12, 0),
    ))
    db.commit()

    spec = automations.compile_spec({"prompt": PROMPT, "timezone": "UTC"}, now=NOW)
    items = {item["title"]: item for item in automations.select_items(db, spec, NOW)}
    assert items["Imported tee"]["include"] is True
    assert items["Imported tee"]["listed_source"] == "vendoo_job"
    assert items["Imported tee"]["age_days"] == 86
    assert items["Imported tee"]["marketplaces"] == ["ebay", "poshmark"]
    assert items["Notes only tee"]["include"] is True
    assert items["Notes only tee"]["listed_source"] == "item_listed"
    assert items["Notes only tee"]["age_days"] == 117
    assert items["Backfilled tee"]["include"] is True
    assert items["Backfilled tee"]["listed_source"] == "publish_job"
    assert items["Backfilled tee"]["age_days"] == 86
    assert items["Failed publish tee"]["include"] is False
    assert items["Failed publish tee"]["listed_source"] == "marketplace_listing"
    assert "under 60" in items["Failed publish tee"]["skip_reason"]
    depop = _conversation(db, title="Depop only tee", price=40, listed="2026-08-01T12:00:00+00:00", markets=["depop"])
    _vendoo_job(db, depop.id, created_at=NOW)
    again = {item["title"]: item for item in automations.select_items(db, spec, NOW)}
    assert again["Depop only tee"]["include"] is False
    assert again["Depop only tee"]["age_days"] is None
    assert "not listed" in again["Depop only tee"]["skip_reason"]


def test_a_one_time_drop_does_not_step_on_the_next_cycle(db):
    conv = _item(db, title="Nike Men's Tee", price=100, department="Men", category="T-Shirts", marketplace="ebay", days=70)

    async def executor(*_args, **_kwargs):
        return {"ok": True}

    created = automations.create_automation(db, {
        "timezone": "UTC",
        "schedule": {"kind": "daily", "time": "09:00"},
        "filters": {"conversation_ids": [conv.id]},
        "actions": [{"type": "price", "mode": "percent", "value": 10, "floor": 15, "repeat": "once"}],
        "marketplaces": ["ebay"],
        "activate": True,
    }, now=NOW)
    row = automations.get_automation(db, created["id"])
    row.next_run_at = NOW - timedelta(hours=1)
    db.commit()
    assert asyncio.run(tick(db, now=NOW, executor=executor)) == 1
    row = automations.get_automation(db, created["id"])
    row.next_run_at = NOW + timedelta(days=61)
    db.commit()
    assert asyncio.run(tick(db, now=NOW + timedelta(days=61, hours=1), executor=executor)) == 1
    price = ListingRepo(db).get_revision(ListingRepo(db).get_current(conv.id).current_revision_id).listing_json["price"]
    assert price == 90.0


def _conversation(db, *, title, price, listed, job=True, markets=("ebay", "poshmark")):
    conv = Conversation(title=title, status="active", notes=json.dumps({
        "vendooStatus": "active",
        "vendooItemId": f"vendoo-{title}",
        "vendooMarketplaces": list(markets),
        "vendooDates": {
            "listed": listed,
            "listedByMarketplace": {name: listed for name in markets},
        },
    }))
    db.add(conv)
    db.commit()
    db.refresh(conv)
    ListingRepo(db).save_revision(conv.id, {
        "title": title, "price": price, "department": "Men", "category_path": "Tops > T-Shirts", "brand": "Nike",
    }, "test")
    if job:
        _vendoo_job(db, conv.id, created_at=NOW)
    return conv


def _vendoo_job(db, conv_id, *, created_at):
    db.add(Job(
        conversation_id=conv_id, approved_revision_id="rev", listing_snapshot={"price": 40},
        status="imported", current_step="imported", vendoo_item_id=f"vendoo-{conv_id}",
        created_at=created_at, updated_at=created_at,
    ))
    db.commit()


def test_a_run_drops_from_the_stored_original_and_a_second_pass_does_not(db):
    conv = _item(db, title="Nike Men's Tee", price=100, department="Men", category="T-Shirts", marketplace="ebay", days=70)
    calls = []

    async def executor(_db, item, action, marketplaces, revision_id):
        calls.append((item["conversation_id"], action["type"], marketplaces, revision_id))
        return {"ok": True}

    created = automations.create_automation(db, {
        "name": "Sixty",
        "timezone": "UTC",
        "schedule": {"kind": "daily", "time": "09:00"},
        "filters": {"min_age_days": 60, "department": "men", "category": "t-shirt"},
        "actions": [
            {"type": "price", "mode": "percent", "value": 10, "floor": 15},
            {"type": "relist"},
        ],
        "marketplaces": ["ebay"],
        "activate": True,
    }, now=NOW)
    row = automations.get_automation(db, created["id"])
    row.next_run_at = NOW - timedelta(days=3)
    db.commit()

    assert asyncio.run(tick(db, now=NOW, executor=executor)) == 1
    price = ListingRepo(db).get_revision(ListingRepo(db).get_current(conv.id).current_revision_id).listing_json["price"]
    assert price == 90.0
    baseline = db.get(ItemPriceBaseline, conv.id)
    assert baseline.original_price == 100.0
    assert [call[1] for call in calls] == ["relist"]
    jobs = db.query(Job).filter(Job.conversation_id == conv.id, Job.current_step == "automation").all()
    assert len(jobs) == 1
    assert jobs[0].status == "completed"
    assert jobs[0].listing_snapshot["original_price"] == 100.0
    runs = db.query(AutomationRun).all()
    assert len(runs) == 1
    assert runs[0].status == "completed"

    # The age cooldown keeps the next morning from touching it again.
    row = automations.get_automation(db, created["id"])
    row.next_run_at = NOW + timedelta(days=1)
    db.commit()
    later = NOW + timedelta(days=1, hours=1)
    assert asyncio.run(tick(db, now=later, executor=executor)) == 1
    price = ListingRepo(db).get_revision(ListingRepo(db).get_current(conv.id).current_revision_id).listing_json["price"]
    assert price == 90.0
    assert len(calls) == 1

    # After the cooldown, the next rung is 80% of 100, not 90% of 90.
    row = automations.get_automation(db, created["id"])
    row.next_run_at = NOW + timedelta(days=61)
    db.commit()
    much_later = NOW + timedelta(days=61, hours=1)
    assert asyncio.run(tick(db, now=much_later, executor=executor)) == 1
    price = ListingRepo(db).get_revision(ListingRepo(db).get_current(conv.id).current_revision_id).listing_json["price"]
    assert price == 80.0
    assert len(calls) == 2
    assert db.query(Job).filter(Job.current_step == "automation").count() == 2


def test_ticking_twice_does_not_run_the_same_slot_twice(db):
    conv = _item(db, title="Nike Men's Tee", price=40, department="Men", category="T-Shirts", marketplace="ebay", days=80)
    calls = []

    async def executor(*_args, **_kwargs):
        calls.append("go")
        return {"ok": True}

    created = automations.create_automation(db, {
        "timezone": "UTC",
        "schedule": {"kind": "once", "at": "2026-10-26T09:00"},
        "filters": {"conversation_ids": [conv.id]},
        "actions": [{"type": "delist"}],
        "marketplaces": ["ebay"],
        "activate": True,
    }, now=NOW)
    row = automations.get_automation(db, created["id"])
    assert asyncio.run(tick(db, now=NOW, executor=executor)) == 1
    assert asyncio.run(tick(db, now=NOW, executor=executor)) == 0
    assert calls == ["go"]
    db.refresh(row)
    assert row.status == "completed"
    assert db.query(AutomationRun).count() == 1


def test_a_dry_run_preview_does_not_save_a_price_or_a_job(db, client):
    conv = _item(db, title="Nike Men's Tee", price=40, department="Men", category="T-Shirts", marketplace="ebay", days=80)
    before = db.query(ListingRevision).filter(ListingRevision.conversation_id == conv.id).count()
    response = client.post("/api/automations/preview", json={
        "prompt": "Delist men's t-shirts on eBay and drop the price by $5. Don't go below $15. On 2026-11-03 at 9:00am.",
        "timezone": "America/Los_Angeles",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    matched = [item for item in body["items"] if item["conversation_id"] == conv.id]
    assert matched and matched[0]["include"] is True
    assert matched[0]["next_price"] == 35.0
    assert db.query(ListingRevision).filter(ListingRevision.conversation_id == conv.id).count() == before
    assert db.query(Job).count() == 0
    assert db.query(ItemPriceBaseline).count() == 0


def test_cli_catalog_exposes_automation_commands_and_pause_needs_confirm():
    catalog = build_catalog_from_app()
    names = {command["name"] for command in catalog["commands"]}
    for action in ("list", "create", "update", "pause", "resume", "delete", "run", "history", "draft", "preview"):
        assert f"automations {action}" in names
    by_name = {command["name"]: command for command in catalog["commands"]}
    assert by_name["automations pause"]["destructive"] is True
    assert by_name["automations delete"]["destructive"] is True
    assert by_name["automations run"]["destructive"] is False
    assert by_name["automations create"]["destructive"] is False
    flags = {item["name"] for item in by_name["automations create"]["parameters"]}
    assert "prompt" in flags
    assert "activate" in flags
    assert "confirm" not in flags


def test_assistant_block_becomes_a_task_only_after_confirm(db, client):
    words, _memory, _changes, block = assistant_memory.peel(
        'I drafted it.\n```automation\n{"prompt": "' + PROMPT.replace('"', "") + '", "timezone": "America/Los_Angeles"}\n```'
    )
    assert words == "I drafted it."
    proposal = automations.proposal_from_block(db, block, now=NOW)
    assert proposal["ready"] is True
    message = add_message(db, "assistant", "I drafted it.", automation={"status": "proposed", "proposal": proposal})
    response = client.post(f"/api/assistant/messages/{message.id}/automation/confirm")
    assert response.status_code == 200, response.text
    confirmed = response.json()["automation"]
    assert confirmed["status"] == "confirmed"
    saved = client.get("/api/automations").json()
    assert saved[0]["status"] == "active"
    assert saved[0]["marketplaces"] == ["ebay", "poshmark"]
    again = client.post(f"/api/assistant/messages/{message.id}/automation/confirm")
    assert again.status_code == 409


def test_run_now_dry_run_logs_a_job_without_calling_the_marketplace(db, client):
    conv = _item(db, title="Nike Men's Tee", price=40, department="Men", category="T-Shirts", marketplace="ebay", days=90)
    created = client.post("/api/automations", json={
        "timezone": "UTC",
        "schedule": {"kind": "daily", "time": "09:00"},
        "filters": {"conversation_ids": [conv.id]},
        "actions": [{"type": "delist"}],
        "marketplaces": ["ebay"],
        "activate": True,
    })
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]
    ran = client.post(f"/api/automations/{automation_id}/run", json={"dry_run": True})
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["dry_run"] is True
    assert body["status"] == "completed"
    assert body["items"][0]["ok"] is True
    assert body["items"][0]["dry_run"] is True
    job = db.query(Job).one()
    assert job.status == "dry_run"
    assert job.listing_snapshot["dry_run"] is True
    history = client.get(f"/api/automations/{automation_id}/history").json()
    assert history["runs"][0]["id"] == body["id"]
    assert json.loads(json.dumps(history))["runs"][0]["dry_run"] is True
