"""Import from Poshmark, Mercari, Depop, Sellwild and Vinted through a fake Chrome bridge."""
import copy
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import browser_bridge, inventory_import
from vendoo_studio.services.marketplace_delist import listings
from vendoo_studio.services.photo_store import LocalPhotoStore

POST = "b" * 24

LISTINGS = {
    "poshmark": {"id": POST, "url": f"https://poshmark.com/listing/{POST}", "title": "Levi's denim jacket", "description": "Classic fit",
                 "price": 45, "currency": "USD", "quantity": 2, "brand": "Levi's", "size": "M", "condition": "not_nwt",
                 "category_path": "Women > Jackets & Coats > Jean Jackets", "category_id": "d1/c1/f1", "sku": "JKT-1",
                 "colors": ["Blue"], "photos": ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg", "http://insecure.example/3.jpg"]},
    "mercari": {"id": "m12345678901", "title": "Denim jacket", "description": "Classic fit", "price": 45, "currency": "USD", "quantity": 1,
                "brand": None, "size": "M", "condition": "Like new", "category_path": "Women > Coats", "category_id": "111", "photos": []},
    "depop": {"id": "shop-denim-jacket-136d", "title": "Denim jacket", "description": "Denim jacket #levis", "price": 45, "currency": "USD",
              "quantity": 1, "condition": "used_excellent", "category_id": "womenswear_denim-jackets", "photos": ["https://cdn.example/d.jpg"]},
    "sellwild": {"id": "812", "title": "Denim jacket", "description": "Classic fit", "price": 45, "currency": "USD", "quantity": 1,
                 "condition": "4", "category_id": "61", "photos": ["https://cdn.example/s.jpg"]},
    "vinted": {"id": "4401", "title": "Denim jacket", "description": "Classic fit", "price": 45, "currency": "USD", "quantity": 1,
               "condition": "Very good", "category_path": "Women > Jackets", "category_id": "1904", "photos": ["https://cdn.example/v.jpg"]},
}


@pytest.fixture
def chrome(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    state = SimpleNamespace(calls=[], downloads=[], listings=copy.deepcopy(LISTINGS), page=None, reply=None)

    async def request(_job, message_type, payload=None, *, timeout=0):
        state.calls.append((message_type, dict(payload or {})))
        assert message_type in {"marketplace.import.page", "marketplace.import.listing"}, "Import must only read"
        if state.reply is not None:
            return state.reply
        marketplace = payload["marketplace"]
        if message_type == "marketplace.import.page":
            return state.page or {"ok": True, "rows": [], "next_cursor": None}
        listing = state.listings[marketplace]
        return {"ok": True, "listing": copy.deepcopy(listing)} if listing["id"] == payload["listing_id"] else {"ok": False, "error": "missing"}

    monkeypatch.setattr(browser_bridge, "request", request)
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    image = BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="JPEG")

    async def fetch(_client, url, **kwargs):
        state.downloads.append(url)
        return image.getvalue(), "image/jpeg", url

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(client=client, db=db, remote=state)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def post(chrome, marketplace, listing, **extra):
    return chrome.client.post(f"/api/chrome-import/{marketplace}", json={"listing": listing, **extra})


def test_poshmark_import_saves_listing_photos_and_link(chrome):
    response = post(chrome, "poshmark", f"https://poshmark.com/listing/Levis-Denim-Jacket-{POST.upper()}?utm=x")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["reused"], body["linked"], body["photo_count"]) == (False, False, 2)
    revision = chrome.db.query(ListingRevision).one()
    snapshot = revision.listing_json
    assert revision.source == "poshmark_import"
    assert (snapshot["title"], snapshot["price"], snapshot["quantity"], snapshot["brand"], snapshot["size"], snapshot["sku"]) == (
        "Levi's denim jacket", 45.0, 2, "Levi's", "M", "JKT-1")
    assert snapshot["condition"] == "Pre-Owned - Good"
    assert snapshot["primaryColor"] == "Blue"
    assert snapshot["marketplace_categories"] == {"poshmark": "Women > Jackets & Coats > Jean Jackets"}
    assert snapshot["marketplace_category_ids"] == {"poshmark": "d1/c1/f1"}
    binding = chrome.db.query(MarketplaceListing).one()
    assert (binding.marketplace, binding.listing_id, binding.state) == ("poshmark", POST, "published")
    assert binding.listing_url == f"https://poshmark.com/listing/{POST}"
    assert chrome.remote.downloads == ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"]
    assert chrome.db.query(Photo).count() == 2
    # The listing is live in Anteroom's own record, so sale detection watches it.
    assert listings(chrome.db, body["conversation_id"])["poshmark"]["state"] == "live"
    assert [call[0] for call in chrome.remote.calls] == ["marketplace.import.listing"]


@pytest.mark.parametrize(("marketplace", "condition", "url"), [
    ("mercari", "Pre-Owned - Excellent", "https://www.mercari.com/us/item/m12345678901/"),
    ("depop", "Pre-Owned - Excellent", "https://www.depop.com/products/shop-denim-jacket-136d/"),
    ("sellwild", "Pre-Owned - Excellent", "https://sellwild.com/product/812"),
    ("vinted", "Pre-Owned - Excellent", "https://www.vinted.com/items/4401"),
])
def test_each_chrome_marketplace_imports_with_its_public_link(chrome, marketplace, condition, url):
    response = post(chrome, marketplace, LISTINGS[marketplace]["id"])
    assert response.status_code == 200, response.text
    snapshot = chrome.db.query(ListingRevision).one().listing_json
    assert snapshot["condition"] == condition
    assert snapshot["price"] == 45.0
    assert snapshot["marketplace_category_ids"] == {marketplace: LISTINGS[marketplace]["category_id"]}
    assert chrome.db.query(MarketplaceListing).one().listing_url == url
    if marketplace == "mercari":
        assert response.json()["warnings"] == ["The marketplace did not provide photos. Add photos before crosslisting."]
    if marketplace == "sellwild":
        assert snapshot["sellwild_specifics"] == {"nativeCategoryId": 61, "nativeConditionId": 4}
        assert snapshot["category_path"] == "Home & Garden > Furniture"


def test_repeat_import_keeps_local_edits_without_reading_chrome(chrome):
    first = post(chrome, "vinted", "4401").json()
    ListingRepo(chrome.db).save_revision(first["conversation_id"], {"title": "Edited", "price": 99}, "user")
    chrome.remote.calls.clear()
    second = post(chrome, "vinted", "https://www.vinted.com/items/4401-denim-jacket").json()
    assert second["reused"] and second["conversation_id"] == first["conversation_id"]
    assert chrome.remote.calls == []
    assert chrome.db.query(Conversation).count() == 1


def test_existing_seller_link_is_reused(chrome):
    conv = ConversationRepo(chrome.db).create(title="Linked jacket")
    chrome.db.add(MarketplaceListing(conversation_id=conv.id, marketplace="depop", state="published",
                                     listing_url="https://www.depop.com/products/shop-denim-jacket-136d/"))
    chrome.db.commit()
    body = post(chrome, "depop", "shop-denim-jacket-136d").json()
    assert body["reused"] and body["conversation_id"] == conv.id
    assert chrome.remote.downloads == []


@pytest.mark.parametrize("change", [{"currency": "GBP"}, {"price": 0}, {"title": ""}, {"id": "other-slug"}])
def test_unusable_listing_creates_nothing(chrome, change):
    chrome.remote.listings["depop"].update(change)
    if "id" in change:
        chrome.remote.reply = {"ok": True, "listing": {**LISTINGS["depop"], **change}}
    assert post(chrome, "depop", "shop-denim-jacket-136d").status_code == 409
    assert chrome.db.query(Conversation).count() == 0 and chrome.remote.downloads == []


def test_chrome_refusals_become_seller_errors(chrome):
    chrome.remote.reply = {"ok": False, "error": "This listing does not belong to the signed-in Mercari account.", "error_code": "rejected"}
    response = post(chrome, "mercari", "m12345678901")
    assert response.status_code == 409 and "does not belong" in response.json()["detail"]
    chrome.remote.reply = {"ok": False, "error": "Sign in to Vinted in Chrome first.", "error_code": "signed_out"}
    assert post(chrome, "vinted", "4401").status_code == 403
    assert post(chrome, "ebay", "123456789012").status_code == 404
    assert post(chrome, "poshmark", "not-a-listing").status_code == 422
    assert chrome.db.query(Conversation).count() == 0


def test_preview_marks_owned_listings_and_suggests_a_match_for_review(chrome):
    owned = ConversationRepo(chrome.db).create(title="Already here")
    chrome.db.add(MarketplaceListing(conversation_id=owned.id, marketplace="poshmark", state="published",
                                     listing_url=f"https://poshmark.com/listing/{POST}"))
    similar = ConversationRepo(chrome.db).create(title="Levi's Denim Jacket Blue")
    ListingRepo(chrome.db).save_revision(similar.id, {"title": "Levi's Denim Jacket Blue", "price": 80}, "user")
    ConversationRepo(chrome.db).create(title="Wool scarf")
    chrome.db.commit()
    other = "c" * 24
    chrome.remote.page = {"ok": True, "next_cursor": "next-1", "rows": [
        {"id": POST, "title": "Denim jacket", "price": 45, "quantity": 2},
        {"id": other, "title": "Levis denim jacket blue", "price": 45, "quantity": 1},
        {"id": "d" * 24, "title": "Pricey thing", "price": None},
        {"id": "../etc", "title": "bad"},
    ]}
    response = chrome.client.get("/api/chrome-import/poshmark/active", params={"cursor": "prev"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert chrome.remote.calls[-1] == ("marketplace.import.page", {"marketplace": "poshmark", "cursor": "prev"})
    assert (body["has_more"], body["next_cursor"]) == (True, "next-1")
    rows = {row["id"]: row for row in body["rows"]}
    assert set(rows) == {POST, other, "d" * 24}
    assert rows[POST]["conversation_id"] == owned.id and rows[POST]["match"] is None
    match = rows[other]["match"]
    assert match["conversation_id"] == similar.id and match["price_differs"] is True
    assert rows["d" * 24]["reason"] == "This import supports listings priced in USD."


def test_linking_a_reviewed_match_keeps_the_item_and_records_the_listing(chrome):
    conv = ConversationRepo(chrome.db).create(title="My jacket")
    ListingRepo(chrome.db).save_revision(conv.id, {"title": "My jacket", "price": 50}, "user")
    response = post(chrome, "vinted", "4401", link_conversation_id=conv.id)
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True
    assert listings(chrome.db, conv.id)["vinted"]["listing_id"] == "4401"
    assert ListingRepo(chrome.db).get_revisions(conv.id)[0].listing_json["title"] == "My jacket"
    assert chrome.db.query(Conversation).count() == 1 and chrome.remote.downloads == []
    # A second item cannot take the same listing.
    other = ConversationRepo(chrome.db).create(title="Other")
    assert post(chrome, "vinted", "4401", link_conversation_id=other.id).status_code == 409
    # Linking checks the listing with Chrome first: a refusal links nothing.
    chrome.remote.reply = {"ok": False, "error": "This Mercari listing is not on sale.", "error_code": "rejected"}
    assert post(chrome, "mercari", "m12345678901", link_conversation_id=other.id).status_code == 409
    assert "mercari" not in listings(chrome.db, other.id)
