"""Seller-reviewed bulk List, Delist and Relist: one confirmed request per reviewed item."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.routes import bulk_marketplace as route
from vendoo_studio.services import bulk_marketplace

EBAY_URL = "https://www.ebay.com/itm/123456789012"


@pytest.fixture
def db():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake_delist(db, conv_id, marketplaces, confirm):
        seen.append(("delist", conv_id, tuple(marketplaces), confirm))
        for name in marketplaces:
            MarketplaceListingRepo(db).save(conv_id, name, {"state": "not_saved", "listing_id": None, "listing_url": None})
        return {name: {"state": "deleted"} for name in marketplaces}

    async def fake_list(conv_id, name, body, db):
        seen.append(("list", conv_id, name, body.confirm, body.revision_id))
        MarketplaceListingRepo(db).save(conv_id, name, {"state": "published", "listing_id": f"{name}-1",
                                                        "listing_url": EBAY_URL if name == "ebay" else None})
        return {"ok": True, "state": "published"}

    monkeypatch.setattr("vendoo_studio.services.marketplace_delist.delist", fake_delist)
    monkeypatch.setattr("vendoo_studio.services.native_marketplaces.list_now", fake_list)
    return seen


def _item(db, title="Coat", *, live_on_ebay=False, status="active"):
    conv = Conversation(title=title, status=status)
    db.add(conv)
    db.commit()
    ListingRepo(db).save_revision(conv.id, {"title": title, "price": 40}, "test")
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


def test_preview_names_what_each_item_would_do_and_sends_nothing(db, client, calls):
    live = _item(db, "Live coat", live_on_ebay=True)
    fresh = _item(db, "Fresh coat")
    sold = _item(db, "Sold coat", status="sold")

    rows = {row["title"]: row for row in _preview(client, [live.id, fresh.id, sold.id], "list", ["ebay", "poshmark"])}

    assert rows["Live coat"]["marketplaces"] == ["poshmark"]
    assert "Choose Relist" in rows["Live coat"]["skipped"][0]["reason"]
    assert rows["Fresh coat"]["marketplaces"] == ["ebay", "poshmark"] and rows["Fresh coat"]["review_token"]
    assert rows["Sold coat"]["skip_reason"] and rows["Sold coat"]["review_token"] is None

    delist = {row["title"]: row for row in _preview(client, [live.id, fresh.id], "delist", ["ebay", "poshmark"])}
    assert delist["Live coat"]["marketplaces"] == ["ebay"]
    assert delist["Fresh coat"]["skip_reason"] == "Nothing to do on the chosen marketplaces."
    assert calls == []


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
        ListingRepo(db).save_revision(conv.id, {"title": "New coat", "price": 30}, "user_form")
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

    async def refuse(conv_id, name, body, db):
        seen.append(name)
        return {"ok": False, "state": "not_saved", "error": f"{name} refused"}

    monkeypatch.setattr("vendoo_studio.services.native_marketplaces.list_now", refuse)
    conv = _item(db)
    row = _preview(client, [conv.id], "list", ["ebay", "poshmark"])[0]

    result = _perform(client, _body(row)).json()

    assert result["ok"] is False and set(result["errors"]) == {"ebay", "poshmark"}
    assert seen == ["ebay", "poshmark"]


def test_busy_slot_refuses_without_sending(db, client, calls, monkeypatch):
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "delist", ["ebay"])[0]
    monkeypatch.setattr(JobRepo, "get_active", lambda self: object())
    assert _perform(client, _body(row)).status_code == 409
    monkeypatch.setattr(bulk_marketplace, "_running", True)
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
def updates(monkeypatch, calls):
    async def fake_update(conv_id, name, body, db):
        calls.append(("update", conv_id, name, body.confirm, body.revision_id))
        MarketplaceListingRepo(db).save(conv_id, name, {"state": "published", "revision_id": body.revision_id})
        return {"ok": True, "state": "published"}

    monkeypatch.setattr("vendoo_studio.services.native_marketplaces.update_live", fake_update)
    return calls


def test_update_reviews_only_live_listings_on_marketplaces_that_support_it(db, client, updates):
    live = _item(db, "Live", live_on_ebay=True)
    fresh = _item(db, "Fresh")
    MarketplaceListingRepo(db).save(live.id, "mercari", {"state": "published", "listing_id": "m1", "listing_url": "https://www.mercari.com/us/item/m1/"})

    rows = {row["title"]: row for row in _preview(client, [live.id, fresh.id], "update", ["ebay", "poshmark", "mercari"])}

    assert rows["Live"]["marketplaces"] == ["ebay"] and rows["Live"]["review_token"]
    reasons = " ".join(entry["reason"] for entry in rows["Live"]["skipped"])
    assert "Not listed on Poshmark" in reasons and "cannot update a live Mercari" in reasons
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
    ListingRepo(db).save_revision(conv.id, {"title": "Edited", "price": 35}, "user_form")
    assert _perform(client, _body(row)).status_code == 409
    assert updates == []


def test_a_failed_update_is_reported_and_not_retried(db, client, monkeypatch, calls):
    seen = []

    async def refuse(conv_id, name, body, db):
        seen.append(name)
        return {"ok": False, "state": "published", "error": "eBay refused"}

    monkeypatch.setattr("vendoo_studio.services.native_marketplaces.update_live", refuse)
    conv = _item(db, live_on_ebay=True)
    row = _preview(client, [conv.id], "update", ["ebay"])[0]
    result = _perform(client, _body(row)).json()
    assert result["ok"] is False and result["errors"] == {"ebay": "eBay refused"} and result["down"] == []
    assert seen == ["ebay"] and calls == []


def test_mark_not_listed_changes_only_anteroom(db, client, calls):
    from vendoo_studio.services.marketplace_delist import listings

    conv = _item(db, live_on_ebay=True)
    fresh = _item(db, "Fresh")
    rows = {row["title"]: row for row in _preview(client, [conv.id, fresh.id], "not_listed", ["ebay"])}
    assert rows["Coat"]["marketplaces"] == ["ebay"]
    assert "no eBay listing recorded" in rows["Fresh"]["skipped"][0]["reason"]

    result = _perform(client, _body(rows["Coat"]))
    assert result.status_code == 200, result.text and result.json()["ok"] is True
    assert "ebay" not in listings(db, conv.id)
    assert calls == []
