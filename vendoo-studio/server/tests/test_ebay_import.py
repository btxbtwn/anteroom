from io import BytesIO
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services.photo_store import LocalPhotoStore
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories import inventory_imports
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import ebay_import, inventory_import, keychain, photos
from vendoo_studio.services.ebay_client import EbayFailure, NS

ITEM_ID = "123456789012"


def item_xml(**changes):
    values = {"ItemID": ITEM_ID, "Title": "Vintage wool coat", "Description": "<p>Wool coat</p><script>hidden()</script><p>Good condition</p>",
              "Quantity": "5", "SKU": "COAT-1", "Site": "US", "ListingType": "FixedPriceItem",
              "Seller/UserID": "seller", "SellingStatus/CurrentPrice": "45.50", "SellingStatus/QuantitySold": "2",
              "SellingStatus/ListingStatus": "Active", "PrimaryCategory/CategoryID": "63862",
              "PrimaryCategory/CategoryName": "Clothing:Women:Coats", "ConditionDisplayName": "Pre-owned - Good"}
    values.update(changes)
    root = ET.Element("Item")
    for path, value in values.items():
        parent = root
        for part in path.split("/"):
            child = parent.find(part)
            parent = child if child is not None else ET.SubElement(parent, part)
        parent.text = value
    root.find("SellingStatus/CurrentPrice").set("currencyID", "USD")
    for url in ("https://i.ebayimg.com/first.jpg", "https://i.ebayimg.com/second.jpg"):
        pictures = root.find("PictureDetails")
        if pictures is None:
            pictures = ET.SubElement(root, "PictureDetails")
        ET.SubElement(pictures, "PictureURL").text = url
    specific = ET.SubElement(ET.SubElement(root, "ItemSpecifics"), "NameValueList")
    ET.SubElement(specific, "Name").text = "Brand"
    ET.SubElement(specific, "Value").text = "Pendleton"
    return root


def response(name, item):
    root = ET.Element(name, xmlns=NS["e"])
    root.append(item)
    return ET.fromstring(ET.tostring(root))


class ReadOnlyEbay:
    seller = ("account-one", "connection-one")

    def __init__(self):
        self.item = item_xml()
        self.calls = []

    async def trading(self, call, fields):
        self.calls.append((call, fields))
        assert call == "GetItem", "Import must not write to eBay"
        assert fields == {"ItemID": ITEM_ID, "DetailLevel": "ReturnAll", "IncludeItemSpecifics": "true"}
        return {"xml": response("GetItemResponse", self.item)}

    async def request(self, method, path, *, xml, call):
        assert (method, path, call) == ("POST", "/ws/api.dll", "GetMyeBaySelling"), "Import must only read eBay"
        request = ET.fromstring(xml)
        assert request.findtext("e:ActiveList/e:Pagination/e:EntriesPerPage", namespaces=NS) == "25"
        assert request.findtext("e:SoldList/e:Include", namespaces=NS) == "false"
        page = request.findtext("e:ActiveList/e:Pagination/e:PageNumber", namespaces=NS)
        self.calls.append((call, page))
        active = ET.Element("ActiveList")
        ET.SubElement(active, "ItemArray").append(self.item)
        pagination = ET.SubElement(active, "PaginationResult")
        ET.SubElement(pagination, "TotalNumberOfEntries").text = "26"
        ET.SubElement(pagination, "TotalNumberOfPages").text = "2"
        return {"xml": response("GetMyeBaySellingResponse", active)}


@pytest.fixture
def imported(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    remote = ReadOnlyEbay()
    monkeypatch.setattr(keychain, "get_marketplace_accounts", lambda: {"ebay": {"label": "seller"}})

    async def connect():
        return remote

    monkeypatch.setattr(ebay_import.EbayClient, "connect", connect)
    monkeypatch.setattr(ebay_import, "assert_identity", lambda seller: None)
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    image = BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="JPEG")
    downloads = []

    async def fetch(_client, url, **kwargs):
        downloads.append(url)
        return image.getvalue(), "image/jpeg", url

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(client=client, db=db, remote=remote, downloads=downloads, directory=tmp_path)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def test_import_creates_local_inventory_photos_and_direct_identity_without_vendoo(imported):
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["reused"] is False and data["photo_count"] == 2 and data["warnings"] == []
    conv = imported.db.get(Conversation, data["conversation_id"])
    assert conv.title == "Vintage wool coat" and conv.notes is None
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert snapshot["price"] == 45.5 and snapshot["quantity"] == 3
    assert snapshot["brand"] == "Pendleton" and snapshot["sku"] == "COAT-1"
    assert "hidden()" not in snapshot["description"] and "<p>" not in snapshot["description"]
    assert snapshot["marketplace_category_ids"] == {"ebay": "63862"}
    binding = imported.db.query(MarketplaceListing).one()
    assert binding.listing_id == binding.ebay_listing_id == ITEM_ID and binding.state == "published"
    assert binding.ebay_offer_id is None and binding.ebay_inventory_written is None
    saved_photos = imported.db.query(Photo).order_by(Photo.display_order).all()
    assert [photo.display_order for photo in saved_photos] == [0, 1]
    assert all((imported.directory / photo.stored_filename).exists() for photo in saved_photos)
    assert [call[0] for call in imported.remote.calls] == ["GetItem"]


def test_repeated_import_does_not_overwrite_local_edits_or_download_photos_again(imported):
    first = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    ListingRepo(imported.db).save_revision(first["conversation_id"], {"title": "My edited coat", "price": 99}, "user")
    second = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    assert second["reused"] and second["conversation_id"] == first["conversation_id"]
    assert len(imported.downloads) == 2
    assert imported.db.query(Conversation).count() == 1
    assert ListingRepo(imported.db).get_revisions(first["conversation_id"])[0].listing_json["price"] == 99


def test_import_reuses_a_saved_vendoo_link_without_overwriting_the_item(imported):
    conv = ConversationRepo(imported.db).create(title="Existing item", notes='{"vendooListingUrls":{"ebay":"https://www.ebay.com/itm/123456789012"}}')
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    assert result["reused"] and result["conversation_id"] == conv.id
    assert imported.downloads == [] and imported.db.query(MarketplaceListing).count() == 0


def test_import_recognizes_a_direct_listing_id_even_before_its_url_was_confirmed(imported):
    conv = ConversationRepo(imported.db).create(title="Unconfirmed direct listing")
    imported.db.add(MarketplaceListing(conversation_id=conv.id, marketplace="ebay", state="unknown", ebay_listing_id=ITEM_ID))
    imported.db.commit()
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    assert result["reused"] and result["conversation_id"] == conv.id
    assert imported.downloads == [] and imported.db.query(Conversation).count() == 1


def test_ambiguous_inventory_is_not_overwritten_or_duplicated(imported):
    for title in ("First duplicate", "Second duplicate"):
        conv = ConversationRepo(imported.db).create(title=title)
        imported.db.add(MarketplaceListing(conversation_id=conv.id, marketplace="ebay", state="published", listing_id=ITEM_ID))
    imported.db.commit()
    response = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert response.status_code == 409 and imported.downloads == []
    assert imported.db.query(Conversation).count() == 2


@pytest.mark.parametrize("listing_id", ["123", "../123456789012", "https://www.ebay.com/itm/123456789012", "١٢٣٤٥٦٧٨٩٠١٢"])
def test_invalid_listing_ids_are_rejected_before_contacting_ebay(imported, listing_id):
    assert imported.client.post("/api/ebay/import", json={"listing_id": listing_id}).status_code == 422
    assert imported.remote.calls == []


@pytest.mark.parametrize(("path", "value"), [("Seller/UserID", "someone-else"), ("Site", "UK"),
    ("SellingStatus/ListingStatus", "Ended"), ("ListingType", "Chinese"), ("Quantity", "invalid"),
    ("SellingStatus/QuantitySold", "6"), ("SellingStatus/CurrentPrice", "NaN"),
    ("SellingStatus/CurrentPrice", "1e9999"), ("ItemID", "987654321098")])
def test_unsupported_or_unowned_listings_never_create_inventory(imported, path, value):
    imported.remote.item = item_xml(**{path: value})
    response = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert response.status_code == 409, response.text
    assert imported.db.query(Conversation).count() == 0 and imported.downloads == []


def test_variation_listings_are_not_collapsed_into_one_item(imported):
    ET.SubElement(imported.remote.item, "Variations")
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).status_code == 409
    assert imported.db.query(Conversation).count() == 0


def test_partial_photo_failure_reports_warning_without_dropping_the_item(imported, monkeypatch):
    fetch = inventory_import.fetch_public

    async def fail_second(client, url, **kwargs):
        if "second" in url:
            raise httpx.ConnectError("offline")
        return await fetch(client, url, **kwargs)

    monkeypatch.setattr(inventory_import, "fetch_public", fail_second)
    response = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert response.status_code == 200
    assert response.json()["photo_count"] == 1
    assert "1 of 2 photos" in response.json()["warnings"][0]


def test_account_change_after_downloading_rolls_back_and_removes_unused_photos(imported, monkeypatch):
    def assert_identity(_seller):
        if imported.downloads:
            raise EbayFailure("The eBay connection changed. Nothing was imported.", 409)

    monkeypatch.setattr(ebay_import, "assert_identity", assert_identity)
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).status_code == 409
    assert imported.db.query(Conversation).count() == 0
    assert list(imported.directory.iterdir()) == []


def test_active_list_paginates_and_marks_existing_inventory(imported):
    first = imported.client.get("/api/ebay/import/active?page=1").json()
    assert first["total"] == 26 and first["has_more"] is True
    assert first["rows"][0]["quantity"] == 3
    assert first["rows"][0]["conversation_id"] is None
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    second = imported.client.get("/api/ebay/import/active?page=2").json()
    assert second["has_more"] is False
    assert second["rows"][0]["conversation_id"] == result["conversation_id"]


def test_import_matches_previously_received_sales_without_delisting(imported):
    sale = MarketplaceSale(marketplace="ebay", order_id="old-order", line_id="one", listing_id=ITEM_ID)
    imported.db.add(sale)
    imported.db.commit()
    response = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    imported.db.refresh(sale)
    assert sale.conversation_id == response["conversation_id"]
    assert all(call[0] == "GetItem" for call in imported.remote.calls)


def test_invalid_photo_does_not_leave_an_orphan_file(imported):
    with pytest.raises(ValueError):
        photos.process_bytes(b"not an image", "bad.jpg", "image/jpeg", collection_id="test-photo")
    assert list(imported.directory.iterdir()) == []


def test_import_database_write_is_atomic(imported):
    def fail(_session):
        raise RuntimeError("Simulated commit failure")

    event.listen(imported.db, "before_commit", fail, once=True)
    with pytest.raises(RuntimeError, match="Simulated"):
        inventory_imports.create_import(imported.db, "ebay", {"title": "Coat"}, ITEM_ID, [], identity="winning-import")
    assert imported.db.query(Conversation).count() == 0
    assert imported.db.query(ListingRevision).count() == 0


def test_two_requests_finishing_the_same_import_reuse_one_item_and_clean_unused_photos(imported, monkeypatch):
    original = inventory_import.download_photos

    async def other_request_finishes(urls, collection_id):
        meta = await original(urls, collection_id)
        inventory_imports.create_import(imported.db, "ebay", {"title": "First request won"}, ITEM_ID, [], identity="winning-import")
        return meta

    monkeypatch.setattr(inventory_import, "download_photos", other_request_finishes)
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    assert result["reused"] and imported.db.query(Conversation).count() == 1
    assert list(imported.directory.iterdir()) == []


def test_an_anteroom_inventory_key_is_not_imported_as_the_item_sku():
    info = {"title": "Tee", "description": "Soft", "price": "12.00", "quantity": 1, "soldQuantity": 0,
            "type": "FixedPriceItem", "condition": "Pre-owned - Good", "categoryName": "Shirts",
            "categoryId": "15687", "specifics": {"Brand": ["Nike"]}}
    assert ebay_import.listing_snapshot({**info, "sku": "ar-abc123"})["sku"] is None
    assert ebay_import.listing_snapshot({**info, "sku": ""})["sku"] is None
    assert ebay_import.listing_snapshot({**info, "sku": "COAT-1"})["sku"] == "COAT-1"
