import copy
from io import BytesIO
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.services.photo_store import LocalPhotoStore
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import inventory_import, keychain, marketplace_accounts


def money(amount=4550):
    return {"amount": amount, "divisor": 100, "currency_code": "USD"}


@pytest.fixture
def imported(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    item = {"listing_id": 555, "shop_id": 42, "state": "active", "listing_type": "physical", "has_variations": False,
            "title": "Wool &amp; silk coat", "description": "Vintage coat", "quantity": 99, "price": money(100),
            "taxonomy_id": 111, "tags": ["vintage"], "materials": ["wool", "silk"], "style": ["classic"],
            "who_made": "someone_else", "when_made": "1990_1999", "is_supply": False, "shipping_profile_id": 88,
            "images": [{"rank": 2, "url_fullxfull": "https://i.etsystatic.com/second.jpg"},
                       {"rank": 1, "url_fullxfull": "https://i.etsystatic.com/first.jpg"}], "private": "never-store"}
    offering = {"quantity": 3, "price": money(), "is_enabled": True, "is_deleted": False, "readiness_state_id": 77}
    product = {"sku": "COAT-1", "is_deleted": False, "offerings": [offering]}
    inventory = {"results": [{"listing_id": 555, "inventory": {"products": [product]}}]}
    state = SimpleNamespace(item=item, inventory=inventory, product=product, offering=offering, calls=[], downloads=[], after_read=None,
                            accounts={"etsy": {"accountId": "42", "connectedAt": 1, "accessToken": "fixture-token"}})
    monkeypatch.setattr(keychain, "get_marketplace_accounts", lambda: state.accounts)

    async def read(method, path, *, params=None):
        assert method == "GET", "Import must never write to Etsy"
        state.calls.append((path, params))
        payloads = {"listings/555": item, "listings/batch/inventory": inventory,
                    "shops/42/listings/555/properties": {"results": [{"property_name": "Brand", "values": ["Pendleton"]}]},
                    "shops/42/listings": {"count": 26, "results": [item]}}
        result = copy.deepcopy(payloads[path])
        if state.after_read:
            state.after_read()
        return result

    monkeypatch.setattr(marketplace_accounts, "etsy_api", read)
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    image = BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="JPEG")

    async def fetch(_client, url, **kwargs):
        state.downloads.append(url)
        return image.getvalue(), "image/jpeg", url

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(client=client, db=db, remote=state, directory=tmp_path)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def run(imported):
    return imported.client.post("/api/etsy/import", json={"listing_id": "555"})


def test_import_uses_exact_offering_and_saves_only_public_inventory(imported):
    response = run(imported)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["photo_count"] == 2
    revision = imported.db.query(ListingRevision).one()
    snapshot = revision.listing_json
    assert revision.source == "etsy_import"
    assert snapshot["title"] == "Wool & silk coat"
    assert (snapshot["price"], snapshot["quantity"], snapshot["sku"], snapshot["brand"]) == (45.5, 3, "COAT-1", "Pendleton")
    assert snapshot["marketplace_category_ids"] == {"etsy": "111"}
    assert snapshot["etsy_specifics"]["readiness_state_id"] == 77
    assert snapshot["etsy_specifics"]["what_is"] == "0"
    assert "never-store" not in str(snapshot) and "fixture-token" not in str(snapshot)
    binding = imported.db.query(MarketplaceListing).one()
    assert (binding.listing_id, binding.etsy_shop_id, binding.state) == ("555", "42", "published")
    assert binding.listing_url == "https://www.etsy.com/listing/555"
    assert imported.db.query(Photo).count() == 2
    assert imported.remote.downloads[0].endswith("first.jpg")
    assert imported.remote.calls[:2] == [("listings/555", {"includes": "Images"}), ("listings/batch/inventory", {"listing_ids": "555"})]


def test_repeat_keeps_local_edits_and_photos(imported):
    first = run(imported).json()
    listing_persistence.save_revision(imported.db, first["conversation_id"], {"title": "Edited", "price": 99}, "user")
    second = run(imported).json()
    assert second["reused"] and second["conversation_id"] == first["conversation_id"]
    assert len(imported.remote.downloads) == 2
    assert imported.db.query(Conversation).count() == 1
    assert ListingRepo(imported.db).get_revisions(first["conversation_id"])[0].listing_json["price"] == 99


@pytest.mark.parametrize("identity", ["url", "draft", "id"])
def test_import_reuses_existing_marketplace_identity(imported, identity):
    conv = ConversationRepo(imported.db).create(title="Existing")
    fields = {"url": {"listing_url": "https://www.etsy.com/listing/555"},
              "draft": {"draft_url": "https://www.etsy.com/your/shops/me/listing-editor/edit/555"},
              "id": {"listing_id": "555"}}[identity]
    imported.db.add(MarketplaceListing(conversation_id=conv.id, marketplace="etsy", state="unknown", **fields))
    imported.db.commit()
    response = run(imported).json()
    assert response["reused"] and response["conversation_id"] == conv.id
    assert imported.remote.downloads == []


def test_ambiguous_identity_is_rejected(imported):
    for _ in range(2):
        conv = ConversationRepo(imported.db).create(title="Duplicate")
        imported.db.add(MarketplaceListing(conversation_id=conv.id, marketplace="etsy", state="published", listing_id="555"))
    imported.db.commit()
    assert run(imported).status_code == 409
    assert imported.remote.downloads == []


@pytest.mark.parametrize(("key", "value"), [("shop_id", 99), ("listing_id", 556), ("state", "draft"),
    ("listing_type", "download"), ("has_variations", True), ("has_variations", None), ("taxonomy_id", None)])
def test_unsupported_listing_never_creates_local_inventory(imported, key, value):
    imported.remote.item[key] = value
    assert run(imported).status_code == 409
    assert imported.db.query(Conversation).count() == 0 and imported.remote.downloads == []


@pytest.mark.parametrize("mutation", ["products", "offerings", "disabled", "deleted", "currency", "quantity", "divisor", "missing", "wrong-id"])
def test_unreliable_inventory_is_rejected(imported, mutation):
    remote = imported.remote
    if mutation == "products":
        remote.inventory["results"][0]["inventory"]["products"].append(copy.deepcopy(remote.product))
    elif mutation == "offerings":
        remote.product["offerings"].append(copy.deepcopy(remote.offering))
    elif mutation == "disabled":
        remote.offering["is_enabled"] = False
    elif mutation == "deleted":
        remote.product["is_deleted"] = True
    elif mutation == "currency":
        remote.offering["price"]["currency_code"] = "EUR"
    elif mutation == "quantity":
        remote.offering["quantity"] = -1
    elif mutation == "divisor":
        remote.offering["price"]["divisor"] = 0
    elif mutation == "missing":
        del remote.inventory["results"][0]["inventory"]
    else:
        remote.inventory["results"][0]["listing_id"] = 556
    assert run(imported).status_code == 409
    assert imported.db.query(Conversation).count() == 0 and remote.downloads == []


def test_explicit_legacy_null_inventory_uses_listing_values(imported):
    imported.remote.inventory["results"][0]["inventory"] = None
    imported.remote.item["skus"] = ["LEGACY"]
    assert run(imported).status_code == 200
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert (snapshot["price"], snapshot["quantity"], snapshot["sku"]) == (1, 99, "LEGACY")


def test_account_change_during_read_is_rejected(imported):
    imported.remote.after_read = lambda: imported.remote.accounts["etsy"].update(connectedAt=2)
    assert run(imported).status_code == 409
    assert imported.remote.downloads == []


def test_account_change_after_photos_cleans_files(imported, monkeypatch):
    original = inventory_import.download_photos

    async def change(urls, collection_id):
        result = await original(urls, collection_id)
        imported.remote.accounts["etsy"]["connectedAt"] = 2
        return result

    monkeypatch.setattr(inventory_import, "download_photos", change)
    assert run(imported).status_code == 409
    assert imported.db.query(Conversation).count() == 0
    assert list(imported.directory.iterdir()) == []


def test_partial_photo_failure_keeps_item_with_warning(imported, monkeypatch):
    original = inventory_import.fetch_public

    async def fetch(client, url, **kwargs):
        if "second" in url:
            raise httpx.ConnectError("offline")
        return await original(client, url, **kwargs)

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    response = run(imported)
    assert response.status_code == 200
    assert response.json()["photo_count"] == 1
    assert "1 of 2 photos" in response.json()["warnings"][0]


def test_active_page_marks_existing_inventory_and_paginates(imported):
    result = run(imported).json()
    response = imported.client.get("/api/etsy/import/active?page=2")
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["conversation_id"] == result["conversation_id"]
    assert response.json()["has_more"] is False
    assert imported.remote.calls[-1][1]["offset"] == 25


@pytest.mark.parametrize("identifier", ["0", "../555", "https://www.etsy.com/listing/555", "٥٥٥", "1" * 21])
def test_bad_ids_are_rejected_before_remote_reads(imported, identifier):
    assert imported.client.post("/api/etsy/import", json={"listing_id": identifier}).status_code == 422
    assert imported.remote.calls == []


def test_import_reconciles_old_sales_without_marketplace_writes(imported):
    sale = MarketplaceSale(marketplace="etsy", order_id="old", line_id="one", listing_id="555")
    imported.db.add(sale)
    imported.db.commit()
    response = run(imported).json()
    imported.db.refresh(sale)
    assert sale.conversation_id == response["conversation_id"]
