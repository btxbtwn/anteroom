"""Seller-reviewed bulk delete from Anteroom: refuses live items and never delists."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import JobRepo, ListingRepo
from vendoo_studio.routes import bulk_delete as route

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
def client(db, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("bulk delete must never delist")

    monkeypatch.setattr("vendoo_studio.services.marketplace_delist.delist", never)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def _item(db, title, *, status="draft", live=False):
    conv = Conversation(title=title, status=status)
    db.add(conv)
    db.commit()
    ListingRepo(db).save_revision(conv.id, {"title": title, "price": 20}, "test")
    if live:
        MarketplaceListingRepo(db).save(conv.id, "ebay", {"state": "published", "listing_id": "123456789012", "listing_url": EBAY_URL})
    return conv


def _preview(client, ids):
    response = client.post("/api/listings/bulk-delete/preview", json={"conversation_ids": ids})
    assert response.status_code == 200, response.text
    return {row["title"]: row for row in response.json()["rows"]}


def _delete(client, rows, confirm=True):
    body = {"rows": [{"conversation_id": row["conversation_id"], "review_token": row["review_token"]} for row in rows], "confirm": confirm}
    return client.post("/api/listings/bulk-delete/delete", json=body)


def test_live_items_are_refused_and_vendoo_listed_items_warned(db, client):
    draft, live, vendoo = _item(db, "Draft"), _item(db, "Live", live=True), _item(db, "Vendoo", status="active")
    rows = _preview(client, [draft.id, live.id, vendoo.id, "missing"])
    assert rows["Draft"]["review_token"] and not rows["Draft"]["warning"]
    assert "Still listed on eBay" in rows["Live"]["skip_reason"] and rows["Live"]["review_token"] is None
    assert rows["Vendoo"]["review_token"] and "Vendoo" in rows["Vendoo"]["warning"]
    assert db.get(Conversation, draft.id) is not None


def test_delete_needs_confirmation_and_the_reviewed_item(db, client):
    draft, other = _item(db, "Draft"), _item(db, "Other")
    rows = _preview(client, [draft.id, other.id])
    assert _delete(client, [rows["Draft"]], confirm=False).status_code == 400
    ListingRepo(db).save_revision(other.id, {"title": "Other", "price": 25}, "user_form")
    result = _delete(client, [rows["Draft"], rows["Other"]]).json()
    assert result["deleted"] == [draft.id]
    assert "changed since the review" in result["skipped"][0]["reason"]
    assert db.get(Conversation, draft.id) is None and db.get(Conversation, other.id) is not None


def test_an_item_that_went_live_or_got_a_job_after_review_is_kept(db, client):
    first, second = _item(db, "First"), _item(db, "Second")
    rows = _preview(client, [first.id, second.id])
    MarketplaceListingRepo(db).save(first.id, "ebay", {"state": "published", "listing_id": "123456789012", "listing_url": EBAY_URL})
    JobRepo(db).create(second.id, ListingRepo(db).get_current(second.id).current_revision_id, {}, status="dispatched")
    result = _delete(client, [rows["First"], rows["Second"]]).json()
    assert result["deleted"] == []
    assert db.get(Conversation, first.id) is not None and db.get(Conversation, second.id) is not None
