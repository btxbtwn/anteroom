"""MCP tools for unsent edits, labels and duplicate decisions (``services/mcp_organize_tools.py``)."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_mcp_http import _call_tool, _rpc
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.home import AttentionDismissal
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_evidence import ListingCorrection
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.mcp_token import McpAccessToken
from anteroom.models.similar_item import SimilarItemReview
from anteroom.services.vendoo_import import parse_notes


@pytest.fixture(autouse=True)
def _clean_tables():
    with SessionLocal() as db:
        for model in (
            McpAccessToken, SimilarItemReview, AttentionDismissal, BackgroundTask, ListingCorrection,
            Message, Job, MarketplaceListing, Photo, Listing, ListingRevision, Conversation,
        ):
            db.query(model).delete()
        db.commit()
    yield


SENT = {
    "title": "Vintage Brass Teapot",
    "description": "Solid brass.",
    "price": 65.0,
    "condition": "Pre-owned - Good",
    "ebay_specifics": {"Material": "Brass"},
    "poshmark_specifics": {"style_tags": ["vintage"]},
}


def _seed_item(db, conv_id: str, *, current: dict | None = None, live_on=("ebay", "poshmark"),
               labels: str | None = None, sku: str | None = None, doc_labels: list | None = None,
               title: str | None = None, **owner) -> None:
    """An item whose ``live_on`` listings were sent from SENT, and whose current revision is ``current``."""
    notes: dict = {}
    if labels is not None:
        notes["vendooLabels"] = labels
    if sku is not None:
        notes["sku"] = sku
    db.add(Conversation(id=conv_id, title=title or (current or SENT)["title"], status="active" if live_on else "draft",
                        notes=json.dumps(notes) if notes else None, **owner))
    db.flush()
    body = {**SENT, "title": title or SENT["title"]}
    if doc_labels is not None:
        body["labels"] = doc_labels
    sent = ListingRevision(id=f"{conv_id}-sent", conversation_id=conv_id, listing_json=body, source="test",
                           created_at=datetime(2026, 11, 1), **owner)
    db.add(sent)
    current_id = sent.id
    if current is not None:
        db.add(ListingRevision(id=f"{conv_id}-new", conversation_id=conv_id, listing_json=current, source="user_form",
                               parent_revision_id=sent.id, created_at=datetime(2026, 11, 2), **owner))
        current_id = f"{conv_id}-new"
    db.flush()
    db.add(Listing(conversation_id=conv_id, current_revision_id=current_id, **owner))
    for market in live_on:
        db.add(MarketplaceListing(
            conversation_id=conv_id, marketplace=market, state="published", listing_id=f"{conv_id}-{market}",
            listing_url=f"https://example.com/{market}/{conv_id}", revision_id=sent.id, listed_price=65.0,
            listed_at=datetime(2026, 11, 1), **owner,
        ))
    db.flush()


EDITED = {**SENT, "title": "Antique Brass Teapot", "price": 59.0, "ebay_specifics": {"Material": "Solid Brass"}}


def _token(client: TestClient, permissions: dict, extra_headers: dict | None = None) -> dict:
    created = client.post("/api/settings/mcp-tokens", headers=extra_headers or {},
                          json={"name": "Organizer", "permissions": permissions})
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _tool_names(client: TestClient, extra_headers: dict) -> set[str]:
    return {tool["name"] for tool in _rpc(client, "tools/list", extra_headers=extra_headers).json()["result"]["tools"]}


def test_tools_need_the_right_permission():
    client = TestClient(app)
    reader = _token(client, {"listings": "read", "attention": "read"})
    names = _tool_names(client, reader)
    assert {"get_pending_edits", "get_labels"} <= names
    assert names.isdisjoint({"discard_pending_edits", "rename_label", "delete_label", "mark_not_duplicate"})
    refused, is_error = _call_tool(client, "rename_label", {"label": "a", "new_label": "b"}, extra_headers=reader)
    assert is_error and "Listings to Read and write" in refused["error"]
    refused, is_error = _call_tool(client, "mark_not_duplicate", {"item_key": "duplicate:a:b"}, extra_headers=reader)
    assert is_error and "Needs Attention to Read and write" in refused["error"]

    writer = _token(client, {"listings": "write", "attention": "write"})
    assert {"discard_pending_edits", "rename_label", "delete_label", "mark_not_duplicate"} <= _tool_names(client, writer)
    nothing = _token(client, {"listings": "none", "attention": "none"})
    assert _tool_names(client, nothing).isdisjoint({"get_pending_edits", "get_labels", "mark_not_duplicate"})


def test_pending_edits_name_the_marketplaces_behind_and_each_changed_field():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_item(db, "edited", current=EDITED)
        _seed_item(db, "in-step")
        _seed_item(db, "draft-only", current=EDITED, live_on=())
        db.commit()

    result, is_error = _call_tool(client, "get_pending_edits")
    assert not is_error, result
    assert result["total"] == 1
    [item] = result["items"]
    assert item["conversation_id"] == "edited"
    assert item["title"] == "Antique Brass Teapot"
    assert item["revision_id"] == "edited-new"
    behind = {row["marketplace"]: row for row in item["marketplaces"]}
    assert set(behind) == {"ebay", "poshmark"}
    ebay = {change["field"]: change for change in behind["ebay"]["changes"]}
    assert ebay["title"] == {"field": "title", "live": "Vintage Brass Teapot", "new": "Antique Brass Teapot"}
    assert ebay["price"]["live"] == 65.0 and ebay["price"]["new"] == 59.0
    assert ebay["ebay_specifics.Material"] == {"field": "ebay_specifics.Material", "live": "Brass", "new": "Solid Brass"}
    # eBay's own attributes are not part of what Poshmark is behind on.
    poshmark = {change["field"] for change in behind["poshmark"]["changes"]}
    assert poshmark == {"title", "price"}
    assert behind["ebay"]["live_revision_id"] == "edited-sent"
    assert behind["ebay"]["listing_url"] == "https://example.com/ebay/edited"

    one, is_error = _call_tool(client, "get_pending_edits", {"conversation_id": "in-step"})
    assert not is_error and one["items"] == []


def test_discard_puts_the_item_back_to_what_is_live_without_touching_marketplaces():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_item(db, "edited", current=EDITED)
        listing = db.query(Listing).filter_by(conversation_id="edited").one()
        listing.scheduled_status = "scheduled"
        listing.scheduled_revision_id = "edited-new"
        db.commit()

    stale, is_error = _call_tool(client, "discard_pending_edits",
                                 {"conversation_id": "edited", "expected_revision_id": "edited-sent"})
    assert is_error and "changed" in stale["error"]

    with SessionLocal() as db:
        db.add(Job(conversation_id="edited", approved_revision_id="edited-new", listing_snapshot={}, status="dispatched"))
        db.commit()
    busy, is_error = _call_tool(client, "discard_pending_edits",
                                {"conversation_id": "edited", "expected_revision_id": "edited-new"})
    assert is_error and "Wait" in busy["error"]
    with SessionLocal() as db:
        db.query(Job).delete()
        db.commit()

    done, is_error = _call_tool(client, "discard_pending_edits",
                                {"conversation_id": "edited", "expected_revision_id": "edited-new"})
    assert not is_error, done
    assert done["discarded"] is True
    assert done["pushed_to_marketplaces"] is False
    assert done["restored_from_revision_id"] == "edited-sent"
    assert done["marketplaces_still_behind"] == []
    assert done["listing"]["title"] == "Vintage Brass Teapot"

    with SessionLocal() as db:
        listing = db.query(Listing).filter_by(conversation_id="edited").one()
        assert listing.current_revision_id == done["revision_id"]
        assert listing.scheduled_status != "scheduled"
        current = db.get(ListingRevision, listing.current_revision_id)
        assert current.source == "restore"
        assert current.listing_json["price"] == 65.0
        assert current.listing_json["ebay_specifics"] == {"Material": "Brass"}
        assert db.get(Conversation, "edited").title == "Vintage Brass Teapot"
        rows = db.query(MarketplaceListing).filter_by(conversation_id="edited").all()
        assert {(row.marketplace, row.state, row.revision_id) for row in rows} == {
            ("ebay", "published", "edited-sent"), ("poshmark", "published", "edited-sent")}

    after, _ = _call_tool(client, "get_pending_edits")
    assert after["items"] == []
    unsent, _ = _call_tool(client, "search_inventory", {"unsent_edits_only": True})
    assert unsent["items"] == []

    again, is_error = _call_tool(client, "discard_pending_edits",
                                 {"conversation_id": "edited", "expected_revision_id": done["revision_id"]})
    assert not is_error and again["discarded"] is False


def test_discard_keeps_the_items_labels_and_cost():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_item(db, "kept", current={**EDITED, "labels": ["Bin 4"], "cost": 12.5},
                   labels="Bin 4", doc_labels=[])
        conv = db.get(Conversation, "kept")
        conv.notes = json.dumps({"vendooLabels": "Bin 4", "cog": "12.5"})
        db.commit()

    pending, _ = _call_tool(client, "get_pending_edits", {"conversation_id": "kept"})
    [item] = pending["items"]
    assert all("labels" not in {c["field"] for c in row["changes"]} for row in item["marketplaces"])

    done, is_error = _call_tool(client, "discard_pending_edits",
                                {"conversation_id": "kept", "expected_revision_id": "kept-new"})
    assert not is_error and done["discarded"] is True, done
    assert done["listing"]["title"] == "Vintage Brass Teapot"
    assert done["listing"]["price"] == 65.0
    assert done["listing"]["labels"] == ["Bin 4"]
    assert done["listing"]["cost"] == 12.5
    assert done["marketplaces_still_behind"] == []
    with SessionLocal() as db:
        notes = parse_notes(db.get(Conversation, "kept").notes)
    assert notes["vendooLabels"] == "Bin 4"
    assert notes["cog"] == "12.5"


def test_discard_restores_the_sent_category_on_an_item_with_native_forms():
    forms = {"ebay": {"fields": []}}
    sent = {**SENT, "direct_category_ids": {"ebay": "111"}, "_native_forms": forms}
    with SessionLocal() as db:
        _seed_item(db, "native", current={**sent, "direct_category_ids": {"ebay": "222"}})
        db.get(ListingRevision, "native-sent").listing_json = sent
        db.commit()

    client = TestClient(app)
    done, is_error = _call_tool(client, "discard_pending_edits",
                                {"conversation_id": "native", "expected_revision_id": "native-new"})
    assert not is_error and done["discarded"] is True, done
    assert done["listing"]["direct_category_ids"] == {"ebay": "111"}
    assert done["marketplaces_still_behind"] == []


def test_discard_refuses_an_item_that_is_not_live_anywhere():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_item(db, "draft-only", current=EDITED, live_on=())
        db.commit()
    refused, is_error = _call_tool(client, "discard_pending_edits",
                                   {"conversation_id": "draft-only", "expected_revision_id": "draft-only-new"})
    assert is_error and "not live" in refused["error"]
    missing, is_error = _call_tool(client, "discard_pending_edits",
                                   {"conversation_id": "gone", "expected_revision_id": "x"})
    assert is_error and "not found" in missing["error"]


def _labels(conv_id: str) -> str | None:
    with SessionLocal() as db:
        return parse_notes(db.get(Conversation, conv_id).notes).get("vendooLabels")


def test_labels_are_counted_renamed_and_deleted_across_the_inventory():
    client = TestClient(app)
    with SessionLocal() as db:
        # The revision keeps the labels it was saved with; the item's notes hold the current ones.
        _seed_item(db, "one", live_on=(), labels="Bin 4, Summer", doc_labels=["Bin 4", "Summer"])
        _seed_item(db, "two", live_on=(), labels="bin 4, Sale")
        _seed_item(db, "three", live_on=("ebay",), labels="Sale")
        _seed_item(db, "four", live_on=())
        db.commit()
        revisions = db.query(ListingRevision).count()

    labels, is_error = _call_tool(client, "get_labels")
    assert not is_error, labels
    assert labels["labels"] == [
        {"label": "Bin 4", "items": 2},
        {"label": "Sale", "items": 2},
        {"label": "Summer", "items": 1},
    ]

    renamed, is_error = _call_tool(client, "rename_label", {"label": "BIN 4", "new_label": "Shelf A"})
    assert not is_error, renamed
    assert renamed["items_changed"] == 2
    assert _labels("one") == "Shelf A, Summer"
    assert _labels("two") == "Shelf A, Sale"

    # Renaming onto a label an item already wears merges the two.
    merged, is_error = _call_tool(client, "rename_label", {"label": "Summer", "new_label": "shelf a"})
    assert not is_error and merged["items_changed"] == 1
    assert _labels("one") == "Shelf A"

    missing, is_error = _call_tool(client, "rename_label", {"label": "Nope", "new_label": "Other"})
    assert is_error and "No item" in missing["error"]
    _, is_error = _call_tool(client, "rename_label", {"label": "Sale", "new_label": "  "})
    assert is_error

    unconfirmed, is_error = _call_tool(client, "delete_label", {"label": "Sale"})
    assert is_error and "confirm" in unconfirmed["error"]
    assert _labels("three") == "Sale"
    deleted, is_error = _call_tool(client, "delete_label", {"label": "sale", "confirm": True})
    assert not is_error and deleted["items_changed"] == 2
    assert _labels("two") == "Shelf A"
    assert _labels("three") == ""

    after, _ = _call_tool(client, "get_labels")
    assert after["labels"] == [{"label": "Shelf A", "items": 2}]
    # Labels are private: renaming one never makes a live item look edited.
    with SessionLocal() as db:
        assert db.query(ListingRevision).count() == revisions
    pending, _ = _call_tool(client, "get_pending_edits")
    assert pending["items"] == []
    # The inventory the web app reads shows the new labels.
    rows = {row["id"]: row for row in client.get("/api/conversations").json()}
    assert rows["one"]["vendoo_labels"] == ["Shelf A"]
    # So do the other MCP tools, and adding a label does not bring an old one back.
    found, _ = _call_tool(client, "search_inventory", {"query": "shelf a"})
    assert {item["conversation_id"]: item["labels"] for item in found["items"]}["one"] == ["Shelf A"]
    listing, _ = _call_tool(client, "get_listing", {"conversation_id": "one"})
    assert listing["labels"] == ["Shelf A"]
    added, is_error = _call_tool(client, "manage_labels_and_notes", {"conversation_id": "one", "add_labels": ["New"]})
    assert not is_error, added
    assert added["labels"] == ["Shelf A", "New"]
    assert _labels("one") == "Shelf A, New"


def _duplicate_keys(client: TestClient) -> set[str]:
    summary, is_error = _call_tool(client, "get_needs_attention")
    assert not is_error, summary
    return {item["key"] for item in summary["needs_attention"] if item["kind"] == "duplicate_listing"}


def test_mark_not_duplicate_stops_the_pair_for_good():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_item(db, "dup-a", live_on=(), sku="SKU-1", title="Levi's 501 Jeans")
        _seed_item(db, "dup-b", live_on=(), sku="SKU-1", title="Navy Wool Peacoat")
        _seed_item(db, "dup-c", live_on=(), sku="SKU-2", title="Ceramic Flower Pitcher")
        _seed_item(db, "dup-d", live_on=(), sku="SKU-2", title="Leather Ankle Boots")
        db.commit()
    assert _duplicate_keys(client) == {"duplicate:dup-a:dup-b", "duplicate:dup-c:dup-d"}

    marked, is_error = _call_tool(client, "mark_not_duplicate", {"item_key": "duplicate:dup-a:dup-b"})
    assert not is_error, marked
    assert marked == {"ok": True, "first_id": "dup-a", "second_id": "dup-b", "not_duplicate": True}
    by_ids, is_error = _call_tool(client, "mark_not_duplicate", {"first_id": "dup-d", "second_id": "dup-c"})
    assert not is_error, by_ids

    assert _duplicate_keys(client) == set()
    assert client.get("/api/similar-items").json()["pairs"] == []
    with SessionLocal() as db:
        decisions = {(row.first_id, row.second_id, row.decision) for row in db.query(SimilarItemReview)}
    assert decisions == {("dup-a", "dup-b", "dismissed"), ("dup-c", "dup-d", "dismissed")}

    for bad in ({"item_key": "failed-job:a:b"}, {"first_id": "dup-a"}, {"first_id": "dup-a", "second_id": "dup-a"},
                {"first_id": "dup-a", "second_id": "gone"}, {}):
        _, is_error = _call_tool(client, "mark_not_duplicate", bad)
        assert is_error, bad


def test_hosted_tools_stay_inside_the_tokens_seller(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys, USER_ID)
    with Session(engine) as db:
        _seed_item(db, "alice-edited", current=EDITED, labels="Bin 4", sku="S-1", user_id=USER_ID)
        _seed_item(db, "alice-twin", live_on=(), sku="S-1", title="Navy Wool Peacoat", user_id=USER_ID)
        _seed_item(db, "bob-edited", current=EDITED, labels="Bin 4", sku="S-1", user_id=BOB)
        _seed_item(db, "bob-twin", live_on=(), sku="S-1", title="Navy Wool Peacoat", user_id=BOB)
        db.commit()
    bearer = _token(client, {"listings": "write", "attention": "write"}, alice)

    pending, is_error = _call_tool(client, "get_pending_edits", extra_headers=bearer)
    assert not is_error, pending
    assert [item["conversation_id"] for item in pending["items"]] == ["alice-edited"]
    labels, _ = _call_tool(client, "get_labels", extra_headers=bearer)
    assert labels["labels"] == [{"label": "Bin 4", "items": 1}]

    _, is_error = _call_tool(client, "discard_pending_edits",
                             {"conversation_id": "bob-edited", "expected_revision_id": "bob-edited-new"},
                             extra_headers=bearer)
    assert is_error
    done, is_error = _call_tool(client, "discard_pending_edits",
                                {"conversation_id": "alice-edited", "expected_revision_id": "alice-edited-new"},
                                extra_headers=bearer)
    assert not is_error and done["discarded"] is True, done

    renamed, is_error = _call_tool(client, "rename_label", {"label": "Bin 4", "new_label": "Shelf"}, extra_headers=bearer)
    assert not is_error and renamed["items_changed"] == 1

    _, is_error = _call_tool(client, "mark_not_duplicate", {"item_key": "duplicate:bob-edited:bob-twin"},
                             extra_headers=bearer)
    assert is_error
    marked, is_error = _call_tool(client, "mark_not_duplicate", {"item_key": "duplicate:alice-edited:alice-twin"},
                                  extra_headers=bearer)
    assert not is_error, marked

    with Session(engine) as db:
        bob = db.get(Listing, "bob-edited")
        assert bob.current_revision_id == "bob-edited-new"
        assert parse_notes(db.get(Conversation, "bob-edited").notes)["vendooLabels"] == "Bin 4"
        assert parse_notes(db.get(Conversation, "alice-edited").notes)["vendooLabels"] == "Shelf"
        reviews = db.execute(select(SimilarItemReview.user_id, SimilarItemReview.first_id)).all()
    assert [(row.user_id, row.first_id) for row in reviews] == [(USER_ID, "alice-edited")]
