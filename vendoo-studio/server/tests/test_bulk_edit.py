import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.repositories.queries import ListingRepo, RevisionConflict
from vendoo_studio.services import bulk_edit
from vendoo_studio.services.listing_editor import prepared_listing


@pytest.fixture
def inventory():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    for key in ("coat", "shirt"):
        db.add(Conversation(id=key, title=key, status="active", notes='{"sku":"OLD","private":"keep"}'))
    db.commit()
    original = {"title": "Wool coat M", "description": "Seller description", "brand": "Brand", "size": "M", "price": 50,
                "sku": "OLD", "quantity": 3, "labels": ["Winter", "Storage A"], "ebay_specifics": {"Size": "M"}}
    for key in ("coat", "shirt"):
        ListingRepo(db).save_revision(key, prepared_listing({}, original), "user_form")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(db=db, client=client)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def preview(inventory, edits, ids=None):
    result = inventory.client.post("/api/listings/bulk-edit/preview", json={"conversation_ids": ids or ["coat", "shirt"], "edits": edits})
    assert result.status_code == 200, result.text
    return result.json()["rows"]


def apply(inventory, edits, rows):
    keys = ("conversation_id", "expected_revision_id", "expected_basis", "expected_result")
    return inventory.client.post("/api/listings/bulk-edit/apply", json={"edits": edits, "rows": [{key: row[key] for key in keys} for row in rows if not row["skip_reason"]]})


def current(inventory, key="coat"):
    repo = ListingRepo(inventory.db)
    return repo.get_revision(repo.get_current(key).current_revision_id)


def test_review_is_read_only_and_apply_saves_the_reviewed_editor_result(inventory):
    before = copy.deepcopy(current(inventory).listing_json)
    edits = {"values": {"price": 45, "size": "L", "sku": "NEW"}, "labels_mode": "add", "labels": ["Fall", "Winter"]}
    rows = preview(inventory, edits)
    assert current(inventory).listing_json == before
    assert {change["field"] for change in rows[0]["changes"]} >= {"price", "size", "sku", "labels"}
    response = apply(inventory, edits, rows)
    assert response.status_code == 200, response.text
    assert len(response.json()["applied"]) == 2
    updated = current(inventory)
    assert updated.listing_json == bulk_edit.edited(before, bulk_edit.Edits.model_validate(edits))
    assert updated.source == "bulk_edit" and updated.parent_revision_id == rows[0]["expected_revision_id"]
    assert inventory.db.get(ListingRevision, updated.parent_revision_id).listing_json == before
    assert updated.listing_json["quantity"] == 3
    assert updated.listing_json["labels"] == ["Winter", "Storage A", "Fall"]
    assert '"sku": "NEW"' in inventory.db.get(Conversation, "coat").notes
    assert "keep" in inventory.db.get(Conversation, "coat").notes


@pytest.mark.parametrize("mode,labels,expected", [("remove", ["Winter"], ["Storage A"]), ("replace", [], []), ("replace", ["Summer", "Summer"], ["Summer"])])
def test_label_actions(inventory, mode, labels, expected):
    edits = {"labels_mode": mode, "labels": labels}
    assert apply(inventory, edits, preview(inventory, edits)).json()["applied"]
    assert current(inventory).listing_json["labels"] == expected


def test_changed_rows_skip_independently_and_replay_cannot_overwrite(inventory):
    edits = {"values": {"cost": 4.25}}
    rows = preview(inventory, edits)
    ListingRepo(inventory.db).save_revision("coat", {**current(inventory).listing_json, "description": "Newer edit"}, "user_form")
    result = apply(inventory, edits, rows).json()
    assert [row["conversation_id"] for row in result["applied"]] == ["shirt"]
    assert result["skipped"][0]["conversation_id"] == "coat"
    assert current(inventory).listing_json["description"] == "Newer edit"
    assert "cost" not in current(inventory).listing_json
    assert not apply(inventory, edits, rows).json()["applied"]


def test_changed_request_and_inventory_state_require_new_review(inventory):
    rows = preview(inventory, {"values": {"brand": "Reviewed"}})
    assert not apply(inventory, {"values": {"brand": "Different"}}, rows).json()["applied"]
    inventory.db.get(Conversation, "coat").status = "sold"
    inventory.db.commit()
    result = apply(inventory, {"values": {"brand": "Reviewed"}}, rows).json()
    assert [row["conversation_id"] for row in result["applied"]] == ["shirt"]


def test_busy_missing_empty_and_unchanged_listings_are_explained(inventory, monkeypatch):
    monkeypatch.setattr(bulk_edit, "listing_busy", lambda key, db: key == "shirt")
    inventory.db.add(Conversation(id="empty", title="Empty"))
    inventory.db.commit()
    before = inventory.db.query(ListingRevision).count()
    rows = preview(inventory, {"values": {"sku": "OLD"}}, ["coat", "shirt", "missing", "empty", "coat"])
    assert len(rows) == 4 and all(row["skip_reason"] for row in rows)
    assert rows[0]["skip_reason"] == "Already has these values"
    assert "generating" in rows[1]["skip_reason"]
    assert inventory.db.query(ListingRevision).count() == before


@pytest.mark.parametrize("edits", [{}, {"values": {"title": " "}}, {"values": {"size": ""}}, {"values": {"price": 4.5}},
    {"values": {"price": True}}, {"values": {"quantity": 0}}, {"values": {"cost": -1}}, {"values": {"marketplace_id": "bad"}},
    {"labels_mode": "add", "labels": []}, {"labels_mode": "replace", "labels": ["a" * 121]}])
def test_invalid_requests_write_nothing(inventory, edits):
    before = inventory.db.query(ListingRevision).count()
    assert inventory.client.post("/api/listings/bulk-edit/preview", json={"conversation_ids": ["coat"], "edits": edits}).status_code == 422
    assert inventory.db.query(ListingRevision).count() == before


def test_revision_cas_rolls_back_without_orphans(inventory):
    old = current(inventory)
    ListingRepo(inventory.db).save_revision("coat", {**old.listing_json, "sku": "CURRENT"}, "user_form")
    before = inventory.db.query(ListingRevision).count()
    with pytest.raises(RevisionConflict):
        ListingRepo(inventory.db).save_revision("coat", {**old.listing_json, "sku": "STALE"}, "bulk_edit", expected_revision_id=old.id)
    assert inventory.db.query(ListingRevision).count() == before
    assert current(inventory).listing_json["sku"] == "CURRENT"


def test_direct_marketplace_action_starting_after_review_is_skipped(inventory, monkeypatch):
    from vendoo_studio.services import native_marketplaces

    edits = {"values": {"brand": "Edited"}}
    rows = preview(inventory, edits)
    monkeypatch.setattr(native_marketplaces, "running_marketplace", lambda db, key: "ebay" if key == "coat" else None)
    result = apply(inventory, edits, rows).json()
    assert [row["conversation_id"] for row in result["applied"]] == ["shirt"]
    assert "generating or sending" in result["skipped"][0]["reason"]



def test_saved_cost_and_labels_reach_item_details_and_direct_snapshots(inventory):
    from vendoo_studio.services.job_snapshot import prepare_listing_snapshot

    conv = inventory.db.get(Conversation, "coat")
    conv.notes = json.dumps({"cog": "99", "vendooLabels": "Old label", "private": "keep"})
    inventory.db.commit()
    edits = {"values": {"cost": 4.25}, "labels_mode": "replace", "labels": ["New label"]}
    assert apply(inventory, edits, preview(inventory, edits)).json()["applied"]
    notes = json.loads(conv.notes)
    assert notes["cog"] == "4.25" and notes["vendooLabels"] == "New label" and notes["private"] == "keep"
    snapshot = prepare_listing_snapshot(inventory.db, conv, current(inventory).listing_json)
    assert snapshot["cost"] == 4.25 and snapshot["labels"] == ["New label"]
    cleared = {"values": {"cost": None}, "labels_mode": "replace", "labels": []}
    assert apply(inventory, cleared, preview(inventory, cleared)).json()["applied"]
    snapshot = prepare_listing_snapshot(inventory.db, conv, current(inventory).listing_json)
    assert snapshot["cost"] is None and snapshot["labels"] == []


def _leaf(db, marketplace, category_id, path, *, leaf=True):
    from vendoo_studio.models.catalog import CategoryTreeNode

    db.add(CategoryTreeNode(marketplace=marketplace, category_id=category_id, parent_id="root", path=path,
                            label=path.split(" > ")[-1], is_leaf=leaf, has_children=not leaf))
    db.commit()


def test_condition_colors_and_tags_save_like_the_editor(inventory):
    edits = {"values": {"condition": "Pre-Owned - Excellent", "primaryColor": "Blue", "secondaryColor": ""},
             "tags_mode": "add", "tags": ["wool", "winter"]}
    rows = preview(inventory, edits)
    assert {"condition", "primaryColor", "tags"} <= {change["field"] for change in rows[0]["changes"]}
    assert len(apply(inventory, edits, rows).json()["applied"]) == 2
    saved = current(inventory).listing_json
    assert saved["condition"] == "Pre-Owned - Excellent" and saved["primaryColor"] == "Blue" and saved["secondaryColor"] == ""
    assert saved["tags"] == ["wool", "winter"]
    removed = {"tags_mode": "remove", "tags": ["wool"]}
    assert apply(inventory, removed, preview(inventory, removed)).json()["applied"]
    assert current(inventory).listing_json["tags"] == ["winter"]


@pytest.mark.parametrize("edits", [{"values": {"condition": "Kind of used"}}, {"values": {"primaryColor": "Chartreuse"}},
    {"values": {"secondaryColor": "Teal-ish"}}, {"tags_mode": "remove", "tags": []},
    {"category": {}}, {"category": {"depop": "not-a-depop-id"}}, {"category": {"ebay": {"id": "abc", "path": "Coats"}}}])
def test_invalid_new_fields_are_refused(inventory, edits):
    assert inventory.client.post("/api/listings/bulk-edit/preview", json={"conversation_ids": ["coat"], "edits": edits}).status_code == 422


def test_categories_are_verified_and_written_where_each_marketplace_reads_them(inventory):
    _leaf(inventory.db, "general", "g-1", "Women > Clothing > Coats > Wool coats")
    _leaf(inventory.db, "depop", "womenswear_coats", "Womenswear > Coats")
    stale = {**current(inventory).listing_json, "marketplace_category_objects": {"general": {"id": "old"}},
             "direct_category_ids": {"depop": "womenswear_jackets", "ebay": "1"}, "depop_specifics": {"nativeSizeId": "7"}}
    ListingRepo(inventory.db).save_revision("coat", stale, "user_form")
    edits = {"category": {"general": "g-1", "depop": "womenswear_coats", "ebay": {"id": "57988", "path": "Clothing › Coats"}}}
    rows = preview(inventory, edits)
    assert not rows[0]["skip_reason"], rows[0]
    assert apply(inventory, edits, rows).json()["applied"]
    saved = current(inventory).listing_json
    assert saved["category_path"] == "Women > Clothing > Coats > Wool coats" and saved["category_id"] == "g-1"
    assert "general" not in (saved.get("marketplace_category_objects") or {})
    assert saved["direct_category_ids"]["ebay"] == "57988" and saved["direct_category_ids"]["ebay_path"] == "Clothing › Coats"
    assert saved["direct_category_ids"]["depop"] == "womenswear_coats"
    assert saved["depop_specifics"]["categoryId"] == "womenswear_coats"
    assert "nativeSizeId" not in saved["depop_specifics"]


@pytest.mark.parametrize("category", [{"general": "missing"}, {"general": "parent"}, {"depop": "womenswear_unknown"}])
def test_unknown_or_parent_categories_are_refused_before_review(inventory, category):
    _leaf(inventory.db, "general", "parent", "Women > Clothing", leaf=False)
    before = inventory.db.query(ListingRevision).count()
    response = inventory.client.post("/api/listings/bulk-edit/preview", json={"conversation_ids": ["coat"], "edits": {"category": category}})
    assert response.status_code == 400 and "category" in response.json()["detail"]
    assert inventory.db.query(ListingRevision).count() == before
