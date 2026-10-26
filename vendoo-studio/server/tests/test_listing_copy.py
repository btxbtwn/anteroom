import copy
from io import BytesIO
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import Job
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import listing_copy, photos
from vendoo_studio.services.listing_delete import delete_listing


@pytest.fixture
def source(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(photos, "PHOTOS_DIR", tmp_path)
    notes = {"sku": "OLD-SKU", "cog": "9", "sellerNotes": "Seller fact", "measurements": {"top": {"length": "25"}},
             "vendooItemId": "vendoo-old", "vendooUrl": "https://web.vendoo.co/app/item/vendoo-old", "vendooSale": {"price": 80},
             "marketplaceMarks": {"ebay": {"status": "SOLD", "url": "https://www.ebay.com/itm/123456789012"}},
             "vendooListingUrls": {"ebay": "https://www.ebay.com/itm/123456789012"}, "vendooDates": {"sold": "2026-10-01"},
             "vendooSyncedRevision": "old", "privateUnrecognized": "do-not-copy"}
    conv = Conversation(id="source", title="Original coat", status="sold", notes=json.dumps(notes))
    db.add(conv)
    db.commit()
    data = {"title": "Wool coat M", "description": "Good coat", "price": 40, "cost": 9, "quantity": 0, "sku": "OLD-SKU", "labels": ["Winter"],
            "ebay_specifics": {"Size": "M", "listing_id": "old-ebay", "bestOffer": {"minimumOfferPrice": "30"}, "customAspect": "Keep"},
            "vinted_specifics": {"brand": "Brand", "listingId": "old-vinted"}, "direct_category_ids": {"ebay": "123"},
            "marketplace_categories": {"ebay": "Clothing > Coats"}, "listing_id": "old", "form": {"status": "SOLD"},
            "job_id": "old-job", "photos": ["old-photo"], "access_token": "do-not-copy"}
    ListingRepo(db).save_revision("source", data, "user_form")
    db.add(Message(conversation_id="source", role="user", text="old chat"))
    db.add(MarketplaceSale(marketplace="ebay", order_id="old-order", line_id="old-line", conversation_id="source", quantity=1))
    db.commit()
    MarketplaceListingRepo(db).save("source", "ebay", {"state": "published", "listing_id": "123456789012", "listing_url": "https://www.ebay.com/itm/123456789012"})
    for color in ("red", "blue"):
        image = BytesIO()
        Image.new("RGB", (24, 36), color).save(image, "PNG")
        meta = photos.process_bytes(image.getvalue(), f"{color}.png", "image/png")
        ConversationRepo(db).add_photo("source", **meta)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(db=db, client=client, directory=tmp_path, data=data)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def preview(source):
    response = source.client.get("/api/conversations/source/copy-preview")
    assert response.status_code == 200, response.text
    return response.json()


def duplicate(source, **changes):
    body = {"expected_basis": preview(source)["expected_basis"], "title": "Copy of coat", "sku": "NEW-SKU", "quantity": 2, **changes}
    return source.client.post("/api/conversations/source/copy", json=body)


def test_copy_is_a_clean_draft_with_new_identity_and_only_reusable_fields(source):
    original = copy.deepcopy(ListingRepo(source.db).get_revisions("source")[0].listing_json)
    response = duplicate(source)
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["id"] != "source" and new["status"] == "draft" and new["box_id"] is None
    assert new["title"] == "Copy of coat" and not new["vendoo_listing_urls"] and not new["direct_marketplaces"]
    notes = json.loads(new["notes"])
    assert notes["sku"] == "NEW-SKU" and notes["sellerNotes"] == "Seller fact"
    assert not {"vendooItemId", "vendooSale", "marketplaceMarks", "vendooDates", "cog", "privateUnrecognized"} & notes.keys()
    revisions = ListingRepo(source.db).get_revisions(new["id"])
    assert len(revisions) == 1 and revisions[0].source == "copy" and revisions[0].parent_revision_id is None
    listing = revisions[0].listing_json
    assert listing["sku"] == "NEW-SKU" and listing["quantity"] == 2 and "cost" not in listing
    assert listing["ebay_specifics"]["customAspect"] == "Keep" and "listing_id" not in listing["ebay_specifics"]
    assert "listingId" not in listing["vinted_specifics"]
    assert listing["direct_category_ids"] == {"ebay": "123"}
    assert not {"listing_id", "job_id", "form", "photos", "access_token"} & listing.keys()
    assert source.db.query(Job).filter_by(conversation_id=new["id"]).count() == 0
    assert source.db.query(Message).filter_by(conversation_id=new["id"]).count() == 0
    assert source.db.query(MarketplaceSale).filter_by(conversation_id=new["id"]).count() == 0
    assert not MarketplaceListingRepo(source.db).for_conversation(new["id"])
    assert ListingRepo(source.db).get_revisions("source")[0].listing_json == original


def test_photos_are_independent_and_keep_their_order(source, monkeypatch):
    from vendoo_studio.services import listing_delete
    monkeypatch.setattr(listing_delete, "PHOTOS_DIR", source.directory)
    response = duplicate(source)
    assert response.status_code == 200, response.text
    key = response.json()["id"]
    old, copied = ConversationRepo(source.db).get_photos("source"), ConversationRepo(source.db).get_photos(key)
    assert [row.original_filename for row in copied] == ["red.png", "blue.png"]
    assert all(a.id != b.id and a.stored_filename != b.stored_filename and a.checksum == b.checksum for a, b in zip(old, copied, strict=True))
    original_bytes = [(source.directory / row.stored_filename).read_bytes() for row in old]
    copied_names = [row.stored_filename for row in copied]
    delete_listing(source.db, key)
    source.db.commit()
    assert [(source.directory / row.stored_filename).read_bytes() for row in old] == original_bytes
    assert not any((source.directory / name).exists() for name in copied_names)


def test_cost_and_photos_are_explicit_options_and_sku_defaults_to_empty(source):
    response = duplicate(source, include_photos=False, include_cost=True, sku="")
    assert response.status_code == 200, response.text
    key = response.json()["id"]
    listing = ListingRepo(source.db).get_revisions(key)[0].listing_json
    assert listing["cost"] == 9 and listing["sku"] == ""
    assert json.loads(response.json()["notes"])["cog"] == "9"
    assert not ConversationRepo(source.db).get_photos(key)


@pytest.mark.parametrize("change", ["revision", "notes", "photos"])
def test_source_changes_reject_old_review_without_creating_anything(source, change):
    before = preview(source)
    if change == "revision":
        ListingRepo(source.db).save_revision("source", {**source.data, "description": "New"}, "user_form")
    elif change == "notes":
        source.db.get(Conversation, "source").notes = '{"sellerNotes":"New"}'
    else:
        ConversationRepo(source.db).get_photos("source")[0].display_order = 5
    source.db.commit()
    files = set(source.directory.iterdir())
    result = duplicate(source, expected_basis=before["expected_basis"])
    assert result.status_code == 409
    assert source.db.query(Conversation).count() == 1 and set(source.directory.iterdir()) == files


def test_missing_photo_rolls_back_new_rows_and_any_files_already_copied(source):
    images = ConversationRepo(source.db).get_photos("source")
    (source.directory / images[1].stored_filename).unlink()
    before = set(source.directory.iterdir())
    response = duplicate(source)
    assert response.status_code == 422, response.text
    assert source.db.query(Conversation).count() == 1 and source.db.query(Photo).count() == 2
    assert set(source.directory.iterdir()) == before
    assert duplicate(source, include_photos=False).status_code == 200


def test_running_marketplace_action_and_missing_source_are_rejected(source, monkeypatch):
    monkeypatch.setattr(listing_copy, "running_marketplace", lambda db, key: "ebay")
    assert source.client.get("/api/conversations/source/copy-preview").status_code == 409
    assert source.client.get("/api/conversations/missing/copy-preview").status_code == 404


@pytest.mark.parametrize("change", [{"title": " "}, {"quantity": 0}, {"quantity": True}, {"include_photos": "yes"}, {"status": "active"}])
def test_invalid_requests_cannot_create_copies(source, change):
    assert duplicate(source, **change).status_code == 422
    assert source.db.query(Conversation).count() == 1



def test_photo_only_item_becomes_a_partial_draft_with_its_new_sku_and_quantity(source):
    source.db.add(Conversation(id="empty", title="Photos next", notes='{"sellerNotes":"Known fact"}'))
    source.db.commit()
    response = source.client.get("/api/conversations/empty/copy-preview")
    assert response.status_code == 200 and response.json()["has_listing"] is False
    copied = source.client.post("/api/conversations/empty/copy", json={"expected_basis": response.json()["expected_basis"], "title": "New partial", "sku": "NEW", "quantity": 3})
    assert copied.status_code == 200, copied.text
    listing = ListingRepo(source.db).get_revisions(copied.json()["id"])[0].listing_json
    assert listing == {"sku": "NEW", "quantity": 3}
    assert copied.json()["status"] == "draft"
