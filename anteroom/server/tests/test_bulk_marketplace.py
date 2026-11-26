"""Seller-reviewed bulk List, Delist and Relist: one confirmed request per reviewed item."""
from __future__ import annotations

import asyncio

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anteroom.services import listing_persistence
from anteroom.database import Base, get_db, in_worker_session, load_models
from anteroom.models.conversation import Conversation, Photo
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import JobRepo, ListingRepo
from anteroom.routes import bulk_marketplace as route
from anteroom.services import bulk_marketplace

EBAY_URL = "https://www.ebay.com/itm/123456789012"


@pytest.fixture
def db(monkeypatch, tmp_path):
    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'bulk.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    # Perform opens its own short sessions (database.in_worker_session).
    monkeypatch.setattr("anteroom.database.SessionLocal", factory)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_concurrent_bulk_sessions_have_separate_connections(db):
    engine = db.get_bind()
    with engine.connect() as first, engine.connect() as second:
        assert first.connection.driver_connection is not second.connection.driver_connection


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def calls(monkeypatch, db):
    seen = []

    # Like the real ones, the fakes keep their database writes off the event loop.
    async def fake_delist(conv_id, marketplaces, confirm):
        seen.append(("delist", conv_id, tuple(marketplaces), confirm))

        def save(session):
            for name in marketplaces:
                MarketplaceListingRepo(session).save(conv_id, name, {"state": "not_saved", "listing_id": None, "listing_url": None})
        await in_worker_session(save)
        return {name: {"state": "deleted"} for name in marketplaces}

    async def fake_list(conv_id, name, body):
        seen.append(("list", conv_id, name, body.confirm, body.revision_id))
        await in_worker_session(lambda session: MarketplaceListingRepo(session).save(
            conv_id, name, {"state": "published", "listing_id": f"{name}-1", "listing_url": EBAY_URL if name == "ebay" else None}))
        return {"ok": True, "state": "published"}

    monkeypatch.setattr("anteroom.services.marketplace_delist.delist", fake_delist)
    monkeypatch.setattr("anteroom.services.native_marketplaces.list_now", fake_list)
    return seen


def _item(db, title="Coat", *, live_on_ebay=False, status="active"):
    conv = Conversation(title=title, status=status)
    db.add(conv)
    db.commit()
    listing_persistence.save_revision(db, conv.id, {"title": title, "price": 40}, "test")
    if live_on_ebay:
        MarketplaceListingRepo(db).save(conv.id, "ebay", {"state": "published", "listing_id": "123456789012", "listing_url": EBAY_URL})
    return conv


def _preview(client, ids, action, marketplaces):
    response = client.post("/api/listings/marketplace-actions/preview",
                           json={"conversation_ids": ids, "action": action, "marketplaces": marketplaces})
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def _body(row, **changes):
    return {"conversation_id": row["conversation_id"], "action": row["action"], "marketplaces": row["marketplaces"],
            "revision_id": row["revision_id"], "review_token": row["review_token"], "confirm": True, **changes}


def _perform(client, body):
    return client.post("/api/listings/marketplace-actions/perform", json=body)


@pytest.mark.parametrize("action", ["list", "update"])
def test_reviewed_price_uses_one_rules_read_until_dispatch(db, client, monkeypatch, action):
    from anteroom.services import pricing_rules
    from anteroom.services.seller_overrides import price_for

    item = _item(db, live_on_ebay=action == "update")
    config = pricing_rules.normalize({"enabled": True, "rules": {"ebay": {"percent": 10, "fixed": 0, "rounding": "none"}}})
    monkeypatch.setattr(pricing_rules, "get_pricing_rules", lambda: config)
    old, = _preview(client, [item.id], action, ["ebay"])
    sent = []

    async def dispatch(_body):
        # Settings changed after the token was checked but before dispatch.
        nonlocal config
        config = pricing_rules.normalize({"enabled": True, "rules": {"ebay": {"percent": 30, "fixed": 0, "rounding": "none"}}})
        sent.append(await asyncio.to_thread(price_for, {"price": 40}, "ebay"))
        return {"ok": True}

    monkeypatch.setattr(bulk_marketplace, "_dispatch", dispatch)
    config["rules"]["ebay"]["percent"] = 20
    assert _perform(client, _body(old)).status_code == 409
    assert sent == []
    reviewed, = _preview(client, [item.id], action, ["ebay"])
    assert _perform(client, _body(reviewed)).status_code == 200
    assert sent == [48]
    assert price_for({"price": 40}, "ebay") == 52


def test_preview_names_what_each_item_would_do_and_sends_nothing(db, client, calls):
    live = _item(db, "Live coat", live_on_ebay=True)
    fresh = _item(db, "Fresh coat")
    sold = _item(db, "Sold coat", status="sold")

    rows = {row["title"]: row for row in _preview(client, [live.id, fresh.id, sold.id], "list", ["ebay", "poshmark"])}

    assert rows["Live coat"]["marketplaces"] == ["poshmark"]
    assert "Choose Relist" in rows["Live coat"]["skipped"][0]["reason"]
    assert rows["Fresh coat"]["marketplaces"] == ["ebay", "poshmark"] and rows["Fresh coat"]["review_token"]
    assert "ebay_ad_rate" not in rows["Fresh coat"]
    assert rows["Sold coat"]["skip_reason"] and rows["Sold coat"]["review_token"] is None

    delist = {row["title"]: row for row in _preview(client, [live.id, fresh.id], "delist", ["ebay", "poshmark"])}
    assert delist["Live coat"]["marketplaces"] == ["ebay"]
    assert delist["Fresh coat"]["skip_reason"] == "Nothing to do on the chosen marketplaces."
    assert calls == []


@pytest.mark.parametrize("source", ["mcp", "bulk_edit", "generation"])
def test_bulk_review_only_uses_the_sellers_persisted_promotion(db, client, source):
    conv = _item(db)
    attempted = {"title": "Coat", "price": 40, "ebay_specifics": {
        "promotion": {"enabled": True, "campaignId": "42", "adRate": "7.5"},
    }}
    unauthorized = listing_persistence.save_revision(db, conv.id, attempted, source)
    assert "promotion" not in unauthorized.listing_json["ebay_specifics"]
    row, = _preview(client, [conv.id], "list", ["ebay"])
    assert "ebay_ad_rate" not in row

    approved = listing_persistence.save_revision(db, conv.id, attempted, "user_form")
    edited = listing_persistence.save_revision(db, conv.id, {"title": "New title", "price": 40}, source,
                                              parent_revision_id=approved.id)
    assert edited.listing_json["ebay_specifics"]["promotion"] == attempted["ebay_specifics"]["promotion"]
    row, = _preview(client, [conv.id], "list", ["ebay"])
    assert row["ebay_ad_rate"] == "7.5"


def test_perform_requires_confirmation_and_the_exact_reviewed_action(db, client, calls):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "delist", ["ebay"])[0]

    for body in (_body(row, confirm=False), _body(row, action="relist"), _body(row, marketplaces=["ebay", "poshmark"]),
                 _body(row, review_token="0" * 64), _body(row, revision_id="other")):
        assert _perform(client, body).status_code in (400, 409, 422)
    body = _body(row)
    del body["confirm"]
    assert _perform(client, body).status_code == 400
    assert calls == []

    response = _perform(client, _body(row))
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert calls == [("delist", conv.id, ("ebay",), True)]
    # A repeated or replayed request finds nothing left to delist and sends nothing.
    assert _perform(client, _body(row)).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["revision", "photo", "link", "notes"])
def test_a_changed_item_needs_a_new_review(db, client, calls, change):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "relist", ["ebay"])[0]
    if change == "revision":
        listing_persistence.save_revision(db, conv.id, {"title": "New coat", "price": 30}, "user_form")
    elif change == "photo":
        db.add(Photo(conversation_id=conv.id, stored_filename="n.png", original_filename="n.png", mime_type="image/png",
                     size_bytes=1, checksum="n"))
    elif change == "link":
        MarketplaceListingRepo(db).save(conv.id, "ebay", {"listing_id": "999999999999", "listing_url": "https://www.ebay.com/itm/999999999999"})
    else:
        db.get(Conversation, conv.id).notes = '{"sku":"NEW"}'
    db.commit()

    assert _perform(client, _body(row)).status_code == 409
    assert calls == []


def test_list_never_takes_down_a_live_listing(db, client, calls):
    conv = _item(db, live_on_ebay=True)
    fresh = _item(db, "Fresh")
    stale = _preview(client, [fresh.id], "list", ["ebay"])[0]
    forged = {**stale, "conversation_id": conv.id}

    assert _perform(client, _body(forged)).status_code == 409
    row = _preview(client, [conv.id], "list", ["ebay"])[0]
    assert row["review_token"] is None
    assert calls == []


def test_list_and_relist_publish_only_the_named_marketplaces_once(db, client, calls):
    fresh = _item(db, "Fresh")
    live = _item(db, "Live", live_on_ebay=True)
    listed = _preview(client, [fresh.id], "list", ["ebay"])[0]
    relisted = _preview(client, [live.id], "relist", ["ebay"])[0]

    assert _perform(client, _body(listed)).json()["ok"] is True
    assert _perform(client, _body(relisted)).json()["ok"] is True

    assert calls == [
        ("list", fresh.id, "ebay", True, listed["revision_id"]),
        ("delist", live.id, ("ebay",), True),
        ("list", live.id, "ebay", True, relisted["revision_id"]),
    ]
    assert _perform(client, _body(listed)).status_code == 409
    assert len(calls) == 3


def test_a_failed_marketplace_is_reported_and_not_retried(db, client, monkeypatch):
    seen = []

    async def refuse(conv_id, name, body):
        seen.append(name)
        return {"ok": False, "state": "not_saved", "error": f"{name} refused"}

    monkeypatch.setattr("anteroom.services.native_marketplaces.list_now", refuse)
    conv = _item(db)
    row = _preview(client, [conv.id], "list", ["ebay", "poshmark"])[0]

    result = _perform(client, _body(row)).json()

    assert result["ok"] is False and set(result["errors"]) == {"ebay", "poshmark"}
    # Both start at once, each tried once.
    assert sorted(seen) == ["ebay", "poshmark"]


def test_busy_slot_refuses_without_sending(db, client, calls, monkeypatch):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "delist", ["ebay"])[0]
    monkeypatch.setattr(JobRepo, "get_active", lambda self: object())
    assert _perform(client, _body(row)).status_code == 409
    monkeypatch.setattr(bulk_marketplace, "_running", {"other-item"})
    monkeypatch.setattr(JobRepo, "get_active", lambda self: None)
    assert _perform(client, _body(row)).status_code == 409
    assert calls == []


def test_delist_still_applies_to_other_marketplaces_after_a_sale(db, client, calls):
    conv = _item(db, live_on_ebay=True, status="sold")
    row = _preview(client, [conv.id], "delist", ["ebay"])[0]
    assert row["marketplaces"] == ["ebay"]
    relist = _preview(client, [conv.id], "relist", ["ebay"])[0]
    assert relist["skip_reason"] and relist["review_token"] is None


@pytest.fixture
def updates(monkeypatch, calls, db):
    async def fake_update(conv_id, name, body):
        calls.append(("update", conv_id, name, body.confirm, body.revision_id))
        MarketplaceListingRepo(db).save(conv_id, name, {"state": "published", "revision_id": body.revision_id})
        return {"ok": True, "state": "published"}

    monkeypatch.setattr("anteroom.services.native_marketplaces.update_live", fake_update)
    return calls


def test_update_reviews_only_live_listings(db, client, updates):
    live = _item(db, "Live", live_on_ebay=True)
    fresh = _item(db, "Fresh")
    MarketplaceListingRepo(db).save(live.id, "mercari", {"state": "published", "listing_id": "m123456789",
                                                         "listing_url": "https://www.mercari.com/us/item/m123456789/"})

    rows = {row["title"]: row for row in _preview(client, [live.id, fresh.id], "update", ["ebay", "poshmark", "mercari"])}

    assert rows["Live"]["marketplaces"] == ["ebay", "mercari"] and rows["Live"]["review_token"]
    reasons = " ".join(entry["reason"] for entry in rows["Live"]["skipped"])
    assert "Not listed on Poshmark" in reasons
    assert rows["Fresh"]["skip_reason"] and rows["Fresh"]["review_token"] is None
    assert updates == []


def test_update_writes_the_reviewed_revision_once_and_never_lists_or_delists(db, client, updates):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "update", ["ebay"])[0]
    assert _perform(client, _body(row, confirm=False)).status_code == 400
    assert updates == []

    result = _perform(client, _body(row))
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is True
    assert updates == [("update", conv.id, "ebay", True, row["revision_id"])]
    # The live listing now has this revision: a replay or a new review finds nothing to send.
    assert _perform(client, _body(row)).status_code == 409
    again = _preview(client, [conv.id], "update", ["ebay"])[0]
    assert again["review_token"] is None and "Already up to date on eBay" in again["skipped"][0]["reason"]
    assert len(updates) == 1


def test_update_after_an_edit_needs_a_new_review(db, client, updates):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "update", ["ebay"])[0]
    listing_persistence.save_revision(db, conv.id, {"title": "Edited", "price": 35}, "user_form")
    assert _perform(client, _body(row)).status_code == 409
    assert updates == []


def test_a_failed_update_is_reported_and_not_retried(db, client, monkeypatch, calls):
    seen = []

    async def refuse(conv_id, name, body):
        seen.append(name)
        return {"ok": False, "state": "published", "error": "eBay refused"}

    monkeypatch.setattr("anteroom.services.native_marketplaces.update_live", refuse)
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "update", ["ebay"])[0]
    result = _perform(client, _body(row)).json()
    assert result["ok"] is False and result["errors"] == {"ebay": "eBay refused"} and result["down"] == []
    assert seen == ["ebay"] and calls == []


def test_mark_not_listed_changes_only_anteroom(db, client, calls):
    from anteroom.services.marketplace_delist import listings

    conv = _item(db, live_on_ebay=True)
    fresh = _item(db, "Fresh")
    rows = {row["title"]: row for row in _preview(client, [conv.id, fresh.id], "not_listed", ["ebay"])}
    assert rows["Coat"]["marketplaces"] == ["ebay"]
    assert "no eBay listing recorded" in rows["Fresh"]["skipped"][0]["reason"]

    result = _perform(client, _body(rows["Coat"]))
    assert result.status_code == 200, result.text and result.json()["ok"] is True
    assert "ebay" not in listings(db, conv.id)
    assert calls == []


def test_list_starts_every_marketplace_at_once_and_takes_turns_on_each(db, monkeypatch):
    from anteroom.services import automation_actions

    events = []

    async def fake_list(conv_id, name, _body):
        events.append(("start", conv_id, name))
        await asyncio.sleep(0.01)
        events.append(("end", conv_id, name))
        return {"ok": True, "state": "published"}

    monkeypatch.setattr("anteroom.services.native_marketplaces.list_now", fake_list)
    monkeypatch.setattr(automation_actions, "_turns", {})
    a, b = _item(db, "A"), _item(db, "B")

    def revision(conv):
        return ListingRepo(db).get_current(conv.id).current_revision_id

    async def both():
        return await asyncio.gather(automation_actions._list_remaining(a.id, ["ebay", "poshmark"], revision(a)),
                                    automation_actions._list_remaining(b.id, ["ebay"], revision(b)))

    assert all(result["ok"] for result in asyncio.run(both()))
    # One item's marketplaces run together.
    assert set(events[:2]) == {("start", a.id, "ebay"), ("start", a.id, "poshmark")}
    # A second item waits for its turn on a marketplace already in use.
    assert events.index(("start", b.id, "ebay")) > events.index(("end", a.id, "ebay"))


def test_bulk_list_runs_beside_another_item_but_delist_waits(db, client, calls, monkeypatch):
    listed = _item(db, "To list")
    live = _item(db, "Live coat", live_on_ebay=True)
    list_row = _preview(client, [listed.id], "list", ["ebay"])[0]
    delist_row = _preview(client, [live.id], "delist", ["ebay"])[0]
    monkeypatch.setattr(bulk_marketplace, "_running", {"another-item"})
    assert _perform(client, _body(delist_row)).status_code == 409
    response = _perform(client, _body(list_row))
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert bulk_marketplace._running == {"another-item"}


def test_update_reviews_a_changed_effective_price_on_the_same_revision(db, client, monkeypatch):
    from anteroom.services import pricing_rules

    conv = _item(db, live_on_ebay=True)
    current = ListingRepo(db).get_current(conv.id)
    MarketplaceListingRepo(db).save(conv.id, "ebay", {"revision_id": current.current_revision_id, "listed_price": 44})
    config = pricing_rules.normalize({"enabled": True, "rules": {"ebay": {"percent": 10, "fixed": 0}}})
    monkeypatch.setattr(pricing_rules, "get_pricing_rules", lambda: config)
    assert _preview(client, [conv.id], "update", ["ebay"])[0]["review_token"] is None

    config = pricing_rules.normalize({"enabled": True, "rules": {"ebay": {"percent": 20, "fixed": 0}}})
    changed = _preview(client, [conv.id], "update", ["ebay"])[0]
    assert changed["revision_id"] == current.current_revision_id
    assert changed["marketplaces"] == ["ebay"]
    assert changed["review_token"]
    config["enabled"] = False
    assert _perform(client, _body(changed)).status_code == 409
