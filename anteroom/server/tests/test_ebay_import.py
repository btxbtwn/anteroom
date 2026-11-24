from io import BytesIO
from types import SimpleNamespace
import asyncio
import xml.etree.ElementTree as ET

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.services import listing_persistence
from anteroom.services.photo_store import LocalPhotoStore
from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.listing import ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.repositories import inventory_imports
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import ebay_import, inventory_import, keychain, photos
from anteroom.services.ebay_client import EbayFailure, NS

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
        self.inventory = {}

    async def trading(self, call, fields):
        self.calls.append((call, fields))
        assert call == "GetItem", "Import must not write to eBay"
        assert fields == {"ItemID": ITEM_ID, "DetailLevel": "ReturnAll", "IncludeItemSpecifics": "true"}
        return {"xml": response("GetItemResponse", self.item)}

    async def request(self, method, path, *, xml=None, call=None, params=None):
        if method == "GET" and path.startswith("/sell/inventory/v1/"):
            self.calls.append((path, params))
            return self.inventory.get(path, {})
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
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    image = BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="JPEG")
    downloads = []

    async def fetch(_client, url, **kwargs):
        downloads.append(url)
        return image.getvalue(), "image/jpeg", url

    monkeypatch.setattr(inventory_import, "fetch_public", fetch)
    app.dependency_overrides[get_db] = lambda: db
    # Import steps open their own sessions on worker threads.
    monkeypatch.setattr("anteroom.database.SessionLocal", sessionmaker(bind=db.get_bind()))
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
    assert binding.listed_price == 45.5
    assert binding.ebay_offer_id is None and binding.ebay_inventory_written is None
    saved_photos = imported.db.query(Photo).order_by(Photo.display_order).all()
    assert [photo.display_order for photo in saved_photos] == [0, 1]
    assert all((imported.directory / photo.stored_filename).exists() for photo in saved_photos)
    # Read only: GetItem, then the listing's inventory offer (its SKU), nothing written.
    assert [call[0] for call in imported.remote.calls] == ["GetItem", "/sell/inventory/v1/offer"]


def test_repeated_import_does_not_overwrite_local_edits_or_download_photos_again(imported):
    first = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID}).json()
    listing_persistence.save_revision(imported.db, first["conversation_id"], {"title": "My edited coat", "price": 99}, "user")
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
    # Only reads: GetItem and the fake's Inventory API GETs (it records nothing else).
    assert all(call[0] == "GetItem" or call[0].startswith("/sell/inventory/v1/") for call in imported.remote.calls)


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
        await asyncio.to_thread(inventory_imports.create_import, imported.db, "ebay", {"title": "First request won"},
                                ITEM_ID, [], identity="winning-import")
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


def test_active_list_suggests_an_existing_item_and_links_it_without_copying(imported):
    from anteroom.services.marketplace_delist import listings

    conv = ConversationRepo(imported.db).create(title="Vintage Wool Coat")
    listing_persistence.save_revision(imported.db, conv.id, {"title": "Vintage Wool Coat", "price": 45}, "user")
    imported.db.commit()
    row = imported.client.get("/api/ebay/import/active?page=1").json()["rows"][0]
    assert row["match"]["conversation_id"] == conv.id and row["match"]["price_differs"] is False
    response = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID, "link_conversation_id": conv.id})
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True
    assert listings(imported.db, conv.id)["ebay"]["listing_id"] == ITEM_ID
    assert imported.db.query(Conversation).count() == 1 and imported.downloads == []
    assert ListingRepo(imported.db).get_revisions(conv.id)[0].listing_json["title"] == "Vintage Wool Coat"
    # Once linked, the listing names its item and no longer suggests one.
    row = imported.client.get("/api/ebay/import/active?page=1").json()["rows"][0]
    assert row["conversation_id"] == conv.id and row["match"] is None
    other = ConversationRepo(imported.db).create(title="Other")
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID, "link_conversation_id": other.id}).status_code == 409


def test_linking_refuses_a_listing_that_is_not_the_sellers_or_not_active(imported):
    conv = ConversationRepo(imported.db).create(title="Coat")
    imported.remote.item.find("Seller/UserID").text = "someone-else"
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID, "link_conversation_id": conv.id}).status_code == 409
    imported.remote.item.find("Seller/UserID").text = "seller"
    imported.remote.item.find("SellingStatus/ListingStatus").text = "Completed"
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID, "link_conversation_id": conv.id}).status_code == 409
    assert imported.db.query(MarketplaceListing).count() == 0
    assert imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID, "link_conversation_id": "missing"}).status_code == 404


def full_item():
    """A listing with every field eBay's GetItem can return for a fixed-price item."""
    item = item_xml(**{
        "ListingDuration": "GTC", "SubTitle": "Fully lined", "LotSize": "2", "PrivateListing": "true", "PostalCode": "94110",
        "SecondaryCategory/CategoryID": "11450", "SecondaryCategory/CategoryName": "Clothing:Vintage",
        "ShippingPackageDetails/WeightMajor": "2", "ShippingPackageDetails/WeightMinor": "6",
        "ShippingPackageDetails/PackageLength": "14", "ShippingPackageDetails/PackageWidth": "10.5",
        "ShippingPackageDetails/PackageDepth": "4", "ShippingPackageDetails/ShippingIrregular": "true",
        "SellerProfiles/SellerShippingProfile/ShippingProfileID": "111",
        "SellerProfiles/SellerReturnProfile/ReturnProfileID": "222",
        "SellerProfiles/SellerPaymentProfile/PaymentProfileID": "333",
        "BestOfferDetails/BestOfferEnabled": "true", "ListingDetails/BestOfferAutoAcceptPrice": "40.00",
        "ListingDetails/MinimumBestOfferPrice": "35.00", "Charity/CharityID": "1234", "Charity/DonationPercent": "10",
        "ProductListingDetails/UPC": "012345678905", "VideoDetails/VideoID": "vid-1",
    })
    specifics = item.find("ItemSpecifics")
    for name, value in (("Color", "Blue"), ("Department", "Women"), ("Size Type", "Regular"), ("Size", "M")):
        pair = ET.SubElement(specifics, "NameValueList")
        ET.SubElement(pair, "Name").text = name
        ET.SubElement(pair, "Value").text = value
    return item


def test_import_brings_over_every_field_ebay_has_into_the_keys_the_ebay_tab_reads(imported):
    imported.remote.item = full_item()
    imported.remote.inventory["/sell/inventory/v1/location"] = {"locations": [
        {"merchantLocationKey": "home", "location": {"address": {"postalCode": "94110", "country": "US"}}},
        {"merchantLocationKey": "other", "location": {"address": {"postalCode": "10001", "country": "US"}}}]}
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert result.status_code == 200, result.text
    assert result.json()["warnings"] == []
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert snapshot["primaryColor"] == "Blue" and snapshot["department"] == "Women"
    assert snapshot["sizeType"] == "Regular" and snapshot["size"] == "M"
    assert snapshot["weight_lb"] == 2 and snapshot["weight_oz"] == 6
    assert snapshot["package_dimensions_in"] == "14x10.5x4"
    assert snapshot["direct_category_ids"] == {"ebay": "63862", "ebay_path": "Clothing > Women > Coats"}
    ebay = snapshot["ebay_specifics"]
    assert ebay["Color"] == ["Blue"] and ebay["Brand"] == ["Pendleton"]
    assert ebay["fulfillmentPolicyId"] == "111" and ebay["returnPolicyId"] == "222" and ebay["paymentPolicyId"] == "333"
    assert ebay["merchantLocationKey"] == "home"
    assert ebay["bestOffer"] == {"enabled": True, "autoAcceptPrice": "40.00", "minimumOfferPrice": "35.00"}
    assert ebay["listingFormat"] == "FIXED_PRICE" and ebay["listingDuration"] == "GTC"
    assert ebay["subtitle"] == "Fully lined" and ebay["lotSize"] == "2" and ebay["privateListing"] is True
    assert ebay["secondaryCategoryId"] == "11450" and ebay["secondaryCategoryPath"] == "Clothing > Vintage"
    assert ebay["irregularPackage"] is True and ebay["upc"] == "012345678905" and ebay["videoId"] == "vid-1"
    assert ebay["charityId"] == "1234" and ebay["donationPercent"] == "10"
    # Read only: GetItem and the seller's inventory locations, nothing written.
    assert [call[0] for call in imported.remote.calls] == ["GetItem", "/sell/inventory/v1/offer", "/sell/inventory/v1/location"]


def test_imported_fields_round_trip_into_what_ebay_update_sends(imported):
    from anteroom.services import ebay_listings

    imported.remote.item = full_item()
    imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert ebay_listings.own_listing_choices(snapshot) == {
        "fulfillmentPolicyId": "111", "paymentPolicyId": "333", "returnPolicyId": "222"}
    assert ebay_listings.listing_format(snapshot) == {"format": "FIXED_PRICE", "duration": "GTC"}
    assert ebay_listings.best_offer_terms({}, "45.50", snapshot) == {
        "bestOfferEnabled": True, "autoAcceptPrice": {"value": "40.00", "currency": "USD"},
        "autoDeclinePrice": {"value": "35.00", "currency": "USD"}}
    assert ebay_listings.package_ounces(snapshot, {}) == "38"
    options = ebay_listings.listing_options(snapshot)
    assert options["subtitle"] == "Fully lined" and options["secondary_category_id"] == "11450"
    assert options["lot_size"] == 2 and options["private"] is True and options["irregular"] is True
    assert options["charity"] == {"charityId": "1234", "donationPercentage": "10.0"}


def test_offers_turned_off_on_ebay_stay_off(imported):
    imported.remote.item = item_xml(**{"BestOfferDetails/BestOfferEnabled": "false"})
    imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert snapshot["ebay_specifics"]["bestOffer"] == {"enabled": False}
    from anteroom.services import ebay_listings
    assert ebay_listings.best_offer_terms({}, "45.50", snapshot) == {"bestOfferEnabled": False}


def test_a_listing_without_a_category_id_takes_it_from_its_inventory_offer(imported):
    imported.remote.item = item_xml(**{"PrimaryCategory/CategoryID": "", "PrimaryCategory/CategoryName": ""})
    imported.remote.inventory["/sell/inventory/v1/offer"] = {"offers": [
        {"sku": "COAT-1", "categoryId": "99999", "merchantLocationKey": "elsewhere", "listing": {"listingId": "1"}},
        {"sku": "COAT-1", "categoryId": "63862", "merchantLocationKey": "home", "listing": {"listingId": ITEM_ID}}]}
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert result.status_code == 200 and result.json()["warnings"] == []
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert snapshot["direct_category_ids"]["ebay"] == "63862"
    assert snapshot["marketplace_category_ids"] == {"ebay": "63862"}
    assert snapshot["ebay_specifics"]["merchantLocationKey"] == "home"


def test_a_listing_whose_category_cannot_be_found_says_so_and_never_stores_an_empty_one(imported):
    imported.remote.item = item_xml(**{"PrimaryCategory/CategoryID": "", "PrimaryCategory/CategoryName": ""})
    result = imported.client.post("/api/ebay/import", json={"listing_id": ITEM_ID})
    assert result.status_code == 200
    assert any("category" in warning for warning in result.json()["warnings"])
    snapshot = imported.db.query(ListingRevision).one().listing_json
    assert "ebay" not in (snapshot.get("direct_category_ids") or {})
    assert "ebay" not in (snapshot.get("marketplace_category_ids") or {})
