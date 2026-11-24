"""Import from Poshmark, Mercari, and Depop through a fake Chrome bridge."""
import asyncio
import copy
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.services import listing_persistence
from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.listing import ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import browser_bridge, inventory_import
from anteroom.services.marketplace_delist import listings
from anteroom.services.photo_store import LocalPhotoStore

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
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    image = BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="JPEG")

    async def fetch(_client, url, **kwargs):
        state.downloads.append(url)
        return image.getvalue(), "image/jpeg", url

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    app.dependency_overrides[get_db] = lambda: db
    # Import steps open their own sessions on worker threads.
    monkeypatch.setattr("anteroom.database.SessionLocal", sessionmaker(bind=db.get_bind()))
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

DEPOP_FULL = {**LISTINGS["depop"], "category_id": "womenswear_jackets", "brand": "Levi's", "size": "6", "sku": "JKT-1", "department": "Women",
              "category_path": "Womenswear > Coats and jackets > Jackets", "colors": ["blue", "black"],
              "style": ["streetwear", "retro"], "age": ["y2k"], "source": ["vintage", "unknown-new-source"],
              "attributes": {"material": ["Denim", "Cotton"], "occasion": ["Casual"]},
              "parcel_size": "Medium", "domestic_shipping": None, "worldwide_shipping": 15, "boost": True,
              "hashtags": ["levis", "vintage"]}


def test_depop_import_fills_every_field_of_the_depop_tab(chrome):
    from anteroom.services import depop_form

    chrome.remote.listings["depop"] = copy.deepcopy(DEPOP_FULL)
    assert post(chrome, "depop", DEPOP_FULL["id"]).status_code == 200
    snapshot = chrome.db.query(ListingRevision).one().listing_json
    assert snapshot["direct_category_ids"] == {"depop": "womenswear_jackets"}
    assert (snapshot["primaryColor"], snapshot["secondaryColor"], snapshot["department"]) == ("blue", "black", "Women")
    assert snapshot["tags"] == ["levis", "vintage"]
    assert snapshot["depop_specifics"] == {
        "condition": "used_excellent", "colour": ["blue", "black"], "style": ["Streetwear", "Retro"], "age": ["00s"],
        "source": ["Vintage", "unknown-new-source"], "attributes": {"material": ["Denim", "Cotton"], "occasion": ["Casual"]},
        "parcelSize": "Medium", "worldwideShipping": {"enabled": True, "price": 15.0}, "boost": True,
    }
    # Depop's values are the seller's; later passes do not pad or replace them.
    assert {"depop_specifics.style", "depop_specifics.age", "depop_specifics.source", "depop_specifics.colour",
            "depop_specifics.condition", "depop_specifics.parcelSize"} <= set(snapshot["seller_fields"])
    rows = {row["path"]: row["value"] for row in depop_form.form(snapshot)["fields"]}
    assert rows["direct_category_ids.depop"] == "womenswear_jackets"
    assert rows["depop_specifics.description"] == "Denim jacket #levis"
    assert rows["depop_specifics.brand"] == "Levi's"
    assert rows["depop_specifics.condition"] == "used_excellent"
    assert rows["depop_specifics.colour"] == ["blue", "black"]
    assert rows["depop_specifics.size"] == "6"
    assert rows["depop_specifics.sku"] == "JKT-1"
    assert rows["depop_specifics.style"] == ["Streetwear", "Retro"]
    assert rows["depop_specifics.age"] == ["00s"]
    assert rows["depop_specifics.source"] == ["Vintage", "unknown-new-source"]
    assert rows["depop_specifics.attributes.material"] == ["Denim", "Cotton"]
    assert rows["depop_specifics.attributes.occasion"] == ["Casual"]
    assert rows["depop_specifics.parcelSize"] == "Medium"
    assert rows["depop_specifics.worldwideShipping.enabled"] is True
    assert rows["depop_specifics.worldwideShipping.price"] == 15.0
    assert rows["depop_specifics.boost"] is True
    assert (rows["price"], rows["quantity"]) == (45.0, 1)


def test_depop_import_keeps_a_seller_paid_domestic_price(chrome):
    chrome.remote.listings["depop"] = {**copy.deepcopy(DEPOP_FULL), "parcel_size": None, "domestic_shipping": 6.5,
                                       "worldwide_shipping": None, "boost": None}
    assert post(chrome, "depop", DEPOP_FULL["id"]).status_code == 200
    specifics = chrome.db.query(ListingRevision).one().listing_json["depop_specifics"]
    assert specifics["domesticShippingPrice"] == 6.5
    assert specifics["worldwideShipping"] == {"enabled": False}
    assert "parcelSize" not in specifics and "boost" not in specifics


def test_repeat_import_keeps_local_edits_without_reading_chrome(chrome):
    first = post(chrome, "depop", "shop-denim-jacket-136d").json()
    listing_persistence.save_revision(chrome.db, first["conversation_id"], {"title": "Edited", "price": 99}, "user")
    chrome.remote.calls.clear()
    second = post(chrome, "depop", "https://www.depop.com/products/shop-denim-jacket-136d/").json()
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
    chrome.remote.reply = {"ok": False, "error": "Sign in to Depop in Chrome first.", "error_code": "signed_out"}
    assert post(chrome, "depop", "shop-denim-jacket-136d").status_code == 403
    assert post(chrome, "ebay", "123456789012").status_code == 404
    assert post(chrome, "poshmark", "not-a-listing").status_code == 422
    assert chrome.db.query(Conversation).count() == 0


def test_preview_marks_owned_listings_and_suggests_a_match_for_review(chrome):
    owned = ConversationRepo(chrome.db).create(title="Already here")
    chrome.db.add(MarketplaceListing(conversation_id=owned.id, marketplace="poshmark", state="published",
                                     listing_url=f"https://poshmark.com/listing/{POST}"))
    similar = ConversationRepo(chrome.db).create(title="Levi's Denim Jacket Blue")
    listing_persistence.save_revision(chrome.db, similar.id, {"title": "Levi's Denim Jacket Blue", "price": 80}, "user")
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
    listing_persistence.save_revision(chrome.db, conv.id, {"title": "My jacket", "price": 50}, "user")
    response = post(chrome, "mercari", "m12345678901", link_conversation_id=conv.id)
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True
    assert listings(chrome.db, conv.id)["mercari"]["listing_id"] == "m12345678901"
    assert ListingRepo(chrome.db).get_revisions(conv.id)[0].listing_json["title"] == "My jacket"
    assert chrome.db.query(Conversation).count() == 1 and chrome.remote.downloads == []
    # A second item cannot take the same listing.
    other = ConversationRepo(chrome.db).create(title="Other")
    assert post(chrome, "mercari", "m12345678901", link_conversation_id=other.id).status_code == 409
    # Linking checks the listing with Chrome first: a refusal links nothing.
    chrome.remote.reply = {"ok": False, "error": "This Mercari listing is not on sale.", "error_code": "rejected"}
    assert post(chrome, "mercari", "m12345678901", link_conversation_id=other.id).status_code == 409
    assert "mercari" not in listings(chrome.db, other.id)


def test_import_all_leaves_a_listing_that_now_matches_an_item_for_review(chrome):
    """Import all checks for a match again as it saves, so two marketplaces importing at once never duplicate an item."""
    # Another marketplace's import brought this item in after the page was read.
    similar = ConversationRepo(chrome.db).create(title="Levi's Denim Jacket")
    listing_persistence.save_revision(chrome.db, similar.id, {"title": "Levi's Denim Jacket", "price": 45}, "user")
    chrome.db.commit()
    response = post(chrome, "poshmark", POST, unless_matched=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"] is None and body["match"]["conversation_id"] == similar.id
    assert chrome.db.query(Conversation).count() == 1 and chrome.db.query(Photo).count() == 0
    assert "poshmark" not in listings(chrome.db, similar.id)
    # Importing it as new on purpose still works.
    assert post(chrome, "poshmark", POST).json()["conversation_id"] not in (None, similar.id)


def test_import_all_drops_photos_when_a_match_appears_while_they_download(chrome, monkeypatch):
    real = inventory_import.download_photos

    async def download_then_match(urls, collection_id):
        downloaded = await real(urls, collection_id)

        def add():
            conv = ConversationRepo(chrome.db).create(title="Levi's denim jacket")
            listing_persistence.save_revision(chrome.db, conv.id, {"title": "Levi's denim jacket", "price": 45}, "user")
            chrome.db.commit()

        await asyncio.to_thread(add)
        return downloaded

    monkeypatch.setattr(inventory_import, "download_photos", download_then_match)
    body = post(chrome, "poshmark", POST, unless_matched=True).json()
    assert body["conversation_id"] is None and body["match"]
    assert chrome.db.query(Conversation).count() == 1 and chrome.db.query(Photo).count() == 0


def test_import_all_holds_a_listing_when_an_item_is_retitled_to_match_while_photos_download(chrome, monkeypatch):
    other = ConversationRepo(chrome.db).create(title="Other")
    chrome.db.commit()
    real = inventory_import.download_photos

    async def download_then_retitle(urls, collection_id):
        downloaded = await real(urls, collection_id)

        def retitle():
            chrome.db.get(Conversation, other.id).title = "Levi's denim jacket"
            chrome.db.commit()

        await asyncio.to_thread(retitle)
        return downloaded

    monkeypatch.setattr(inventory_import, "download_photos", download_then_retitle)
    body = post(chrome, "poshmark", POST, unless_matched=True).json()
    assert body["conversation_id"] is None and body["match"]["conversation_id"] == other.id
    assert chrome.db.query(Conversation).count() == 1 and chrome.db.query(Photo).count() == 0


def test_a_match_is_found_behind_more_than_five_items_already_on_that_marketplace(chrome):
    for index in range(5):
        linked = ConversationRepo(chrome.db).create(title="Levi's denim jacket")
        chrome.db.add(MarketplaceListing(conversation_id=linked.id, marketplace="poshmark", state="published",
                                         listing_url=f"https://poshmark.com/listing/{str(index) * 24}"))
    # A little less alike than the five linked ones, so it ranks after all of them.
    eligible = ConversationRepo(chrome.db).create(title="Levi's denim jacket blue")
    chrome.db.commit()
    body = post(chrome, "poshmark", POST, unless_matched=True).json()
    assert body["conversation_id"] is None and body["match"]["conversation_id"] == eligible.id


def test_import_all_holds_a_listing_when_a_look_alike_leaves_that_marketplace_while_photos_download(chrome, monkeypatch):
    other = "e" * 24
    item = ConversationRepo(chrome.db).create(title="Levi's denim jacket")
    chrome.db.add(MarketplaceListing(conversation_id=item.id, marketplace="poshmark", state="published",
                                     listing_url=f"https://poshmark.com/listing/{other}"))
    chrome.db.commit()
    real = inventory_import.download_photos

    async def download_then_unlink(urls, collection_id):
        downloaded = await real(urls, collection_id)

        def unlink():
            chrome.db.query(MarketplaceListing).filter_by(conversation_id=item.id).delete()
            chrome.db.commit()

        await asyncio.to_thread(unlink)
        return downloaded

    monkeypatch.setattr(inventory_import, "download_photos", download_then_unlink)
    body = post(chrome, "poshmark", POST, unless_matched=True).json()
    assert body["conversation_id"] is None and body["match"]["conversation_id"] == item.id
    assert chrome.db.query(Conversation).count() == 1 and chrome.db.query(Photo).count() == 0


def test_preview_names_the_account_chrome_read_and_import_all_refuses_another(chrome):
    chrome.remote.page = {"ok": True, "next_cursor": None, "account": "seller-one", "rows": [{"id": POST, "title": "Denim jacket", "price": 45}]}
    assert chrome.client.get("/api/chrome-import/poshmark/active").json()["account"] == "seller-one"
    # Chrome is now signed in to another Poshmark account.
    chrome.remote.listings["poshmark"]["account"] = "seller-two"
    refused = post(chrome, "poshmark", POST, unless_matched=True, account="seller-one")
    assert refused.status_code == 409 and "different Poshmark account" in refused.json()["detail"]
    assert chrome.db.query(Conversation).count() == 0 and chrome.remote.downloads == []
    # The same account imports.
    chrome.remote.listings["poshmark"]["account"] = "seller-one"
    assert post(chrome, "poshmark", POST, unless_matched=True, account="seller-one").json()["conversation_id"]
    # A Bridge that does not say which account it read cannot serve import all; one listing at a time still works.
    assert "account" not in chrome.remote.listings["mercari"]
    stale = post(chrome, "mercari", "m12345678901", account="seller-one")
    assert stale.status_code == 409 and "Update Anteroom Bridge" in stale.json()["detail"]
    assert post(chrome, "mercari", "m12345678901").json()["conversation_id"]
