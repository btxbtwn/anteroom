"""Inventory guardrails: Inactive listings, price drift on rows, Bulk Edit presets and rules,
missing-SKU warnings, and the bulk crosslist text modes."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.models.conversation import Conversation
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo, observed_inactive
from anteroom.repositories.queries import ListingRepo
from anteroom.services import listing_persistence, marketplace_text, pricing_rules, shipping_presets
from anteroom.services.listing_editor import prepared_listing
from anteroom.services.pricing_rules import PRICING_RULES_KEY
from anteroom.services.shipping_presets import SHIPPING_PRESETS_KEY
from anteroom.services.user_settings import LISTING_WARNINGS_KEY, update_settings

KEYS = (PRICING_RULES_KEY, SHIPPING_PRESETS_KEY, LISTING_WARNINGS_KEY)


def _clear(payload):
    for key in KEYS:
        payload.pop(key, None)


@pytest.fixture(autouse=True)
def _clean_settings():
    update_settings(_clear)
    yield
    update_settings(_clear)


@pytest.fixture
def inventory():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    db = session()
    for key in ("coat", "shirt"):
        db.add(Conversation(id=key, title=key, status="active", notes="{}"))
    db.commit()
    long_title = "Vintage Wool Blend Double Breasted Long Winter Overcoat Charcoal Gray Mens Size Medium Classic Fit"
    original = {"title": long_title, "description": "Warm coat. " * 120, "brand": "Brand", "size": "M",
                "price": 50, "quantity": 1, "labels": ["Winter"],
                "ebay_specifics": {"price": 50}, "mercari_specifics": {"price": 50}}
    for key in ("coat", "shirt"):
        listing_persistence.save_revision(db, key, prepared_listing({}, original), "user_form")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield SimpleNamespace(db=db, client=client, session=session, original=original)
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def current(inventory, key="coat"):
    repo = ListingRepo(inventory.db)
    return repo.get_revision(repo.get_current(key).current_revision_id)


def row(inventory, key="coat"):
    rows = inventory.client.get("/api/conversations").json()
    return next(item for item in rows if item["id"] == key)


def test_observed_inactive_needs_a_read_of_this_same_listing():
    assert observed_inactive({"state": "inactive", "listing_id": "9"}, "9")
    assert observed_inactive({"state": "not_for_sale", "listing_id": "9"}, "9")
    assert not observed_inactive({"state": "inactive", "listing_id": "8"}, "9")
    assert not observed_inactive({"state": "active", "listing_id": "9"}, "9")
    assert not observed_inactive({"state": "sold_out", "listing_id": "9"}, "9")
    assert not observed_inactive(None, "9")


@pytest.mark.parametrize("rules_enabled", [False, True])
def test_an_inactive_listing_stays_live_and_is_neither_sold_nor_removed(inventory, rules_enabled):
    from anteroom.routes.hosted_workspace import get_conversation

    pricing_rules.set_pricing_rules(enabled=rules_enabled)
    repo = MarketplaceListingRepo(inventory.db)
    repo.save("coat", "etsy", {"state": "published", "listing_id": "9", "listing_url": "https://www.etsy.com/listing/9",
                               "inventory_observation": {"state": "inactive", "listing_id": "9", "checked_at": "2026-11-20T00:00:00+00:00"}})
    repo.save("coat", "ebay", {"state": "published", "listing_id": "5", "listing_url": "https://www.ebay.com/itm/5"})
    coat = row(inventory)
    assert coat["inactive_marketplaces"] == ["etsy"]
    hosted = get_conversation("coat", inventory.db)
    assert hosted["inactive_marketplaces"] == ["etsy"]
    assert hosted["direct_records"]["etsy"]["inactive"] is True
    assert coat["direct_records"]["etsy"]["inactive"] is True
    assert coat["direct_records"]["ebay"].get("inactive", False) is False
    assert set(coat["direct_marketplaces"]) == {"etsy", "ebay"}
    assert coat["sold_marketplaces"] == [] and coat["removed_marketplaces"] == []
    assert coat["status"] != "sold"
    # A relist under a new ID is no longer the listing that read inactive.
    repo.save("coat", "etsy", {"listing_id": "10", "listing_url": "https://www.etsy.com/listing/10"})
    assert row(inventory)["inactive_marketplaces"] == []


def test_rows_show_price_drift_only_while_rules_are_on(inventory):
    from anteroom.services.seller_overrides import apply_field_updates

    listing = dict(current(inventory).listing_json)
    apply_field_updates(listing, [{"path": "mercari_specifics.price", "value": 70}])
    listing_persistence.save_revision(inventory.db, "coat", listing, "user_form")
    assert row(inventory)["price_drift"] == []
    pricing_rules.set_pricing_rules(enabled=True, rules={"mercari": {"percent": 10, "fixed": 0, "rounding": "none"}})
    drift = row(inventory)["price_drift"]
    assert {"marketplace": "mercari", "expected": 55.0, "price": 70.0, "source": "override"} in drift
    assert row(inventory, "shirt")["price_drift"] == []
    MarketplaceListingRepo(inventory.db).save("shirt", "ebay", {"state": "published", "listing_id": "1", "listed_price": 40,
                                                                "listing_url": "https://www.ebay.com/itm/1"})
    assert [item["source"] for item in row(inventory, "shirt")["price_drift"]] == ["live"]
    single = inventory.client.get("/api/conversations/coat").json()
    assert single["price_drift"] == drift


def test_bulk_edit_applies_a_shipping_preset_and_the_pricing_rules(inventory):
    preset = shipping_presets.save_shipping_preset({"name": "Coats", "length": 18, "width": 14, "height": 6,
                                                    "weight_lb": 4, "marketplaces": {"depop": {"parcelSize": "Large"}}})[0]
    pricing_rules.set_pricing_rules(enabled=True, rules={"mercari": {"percent": 10, "fixed": 0, "rounding": "none"}})
    from anteroom.services.seller_overrides import apply_field_updates

    listing = dict(current(inventory).listing_json)
    apply_field_updates(listing, [{"path": "mercari_specifics.price", "value": 70}])
    listing_persistence.save_revision(inventory.db, "coat", listing, "user_form")
    edits = {"shipping_preset": preset["id"], "pricing_rules": True}
    review = inventory.client.post("/api/listings/bulk-edit/preview", json={"conversation_ids": ["coat"], "edits": edits})
    assert review.status_code == 200, review.text
    rows = review.json()["rows"]
    assert {change["field"] for change in rows[0]["changes"]} >= {"package_dimensions_in", "mercari_specifics", "depop_specifics"}
    keys = ("conversation_id", "expected_revision_id", "expected_basis", "expected_result")
    applied = inventory.client.post("/api/listings/bulk-edit/apply", json={"edits": edits, "rows": [{key: rows[0][key] for key in keys}]})
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["applied"]) == 1
    saved = current(inventory).listing_json
    assert saved["package_dimensions_in"] == "18x14x6" and saved["weight_lb"] == 4
    assert saved["depop_specifics"]["parcelSize"] == "Large"
    assert saved["mercari_specifics"]["price"] == 55
    assert "mercari_specifics.price" not in saved.get("seller_fields", [])
    assert row(inventory)["price_drift"] == []
    gone = inventory.client.post("/api/listings/bulk-edit/preview",
                                 json={"conversation_ids": ["coat"], "edits": {"shipping_preset": "missing"}})
    assert gone.status_code == 400


def test_bulk_edit_rules_need_rules_on(inventory):
    response = inventory.client.post("/api/listings/bulk-edit/preview",
                                     json={"conversation_ids": ["coat"], "edits": {"pricing_rules": True}})
    assert response.status_code == 400


def test_listing_warnings_setting():
    client = TestClient(app)
    assert client.get("/api/settings/listing-warnings").json() == {"ok": True, "missing_sku": False}
    assert client.put("/api/settings/listing-warnings", json={"missing_sku": True}).json()["missing_sku"] is True
    assert client.get("/api/settings/listing-warnings").json()["missing_sku"] is True


def test_text_targets_by_mode():
    listing = {"title": "T" * 90, "description": "Short", "mercari_specifics": {"title": "Own"}}
    assert marketplace_text.targets(listing, ["ebay"], "keep") == []
    shorten = marketplace_text.targets(listing, ["ebay", "mercari", "etsy", "depop"], "shorten")
    # Only eBay's 90-character title is too long: Mercari has its own short title, Etsy allows 140.
    assert [(market, field) for market, field, _text, _limit in shorten] == [("ebay", "title")]
    rewrite = marketplace_text.targets(listing, ["mercari", "depop"], "rewrite")
    assert [(market, field) for market, field, _text, _limit in rewrite] == [
        ("mercari", "title"), ("mercari", "description"), ("depop", "description")]


def test_parse_reply_takes_per_marketplace_or_flat_answers():
    wanted = [("ebay", "title", "x", 80), ("mercari", "description", "y", 1000)]
    per_market = marketplace_text.parse_reply('{"ebay": {"title": "Wool  coat"}, "mercari": {"description": "Warm"}}', wanted)
    assert per_market == {("ebay", "title"): "Wool coat", ("mercari", "description"): "Warm"}
    fenced = ' ```json\n{"ebay": {"title": "Wool  coat"}, "mercari": {"description": "Warm"}}\n```'
    assert marketplace_text.parse_reply(fenced, wanted) == per_market
    flat = marketplace_text.parse_reply('```json\n{"title": "Coat", "description": "Warm"}\n```', wanted)
    assert flat == {("ebay", "title"): "Coat", ("mercari", "description"): "Warm"}
    assert marketplace_text.parse_reply("no json", wanted) == {}


def test_fit_cuts_titles_at_a_word():
    assert marketplace_text.fit("Wool coat charcoal gray", "title", 14) == "Wool coat"
    assert len(marketplace_text.fit("Warm coat. " * 200, "description", 1000)) <= 1000


class _FakeProvider:
    name = "sandbox"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, stream=False):
        self.calls += 1
        yield self.reply


def _run(inventory, monkeypatch, provider, body):
    async def run_db(work, *args):
        with inventory.session() as db:
            return work(db, *args)

    inventory.db.commit()
    monkeypatch.setattr(marketplace_text, "_text_provider", lambda: provider)
    monkeypatch.setattr("anteroom.services.native_marketplaces.running_marketplace", lambda db, conv_id: None)
    return asyncio.run(marketplace_text.prepare(run_db, "coat", marketplace_text.TextRequest.model_validate(body)))


def test_shorten_saves_only_over_limit_fields_locally(inventory, monkeypatch):
    before = current(inventory)
    provider = _FakeProvider('{"ebay": {"title": "Vintage Wool Double Breasted Overcoat Charcoal Mens M"}}')
    result = _run(inventory, monkeypatch, provider, {"marketplaces": ["ebay", "mercari"], "mode": "shorten",
                                                     "expected_revision_id": before.id})
    assert provider.calls == 1
    fields = {(change["marketplace"], change["field"]) for change in result["changes"]}
    # eBay's title was too long; Mercari's description (1,320 characters) was too, and the
    # AI gave none back, so it was cut at a word. Mercari's title is too long as well.
    assert ("ebay", "title") in fields and ("mercari", "description") in fields
    inventory.db.expire_all()
    saved = current(inventory)
    assert saved.id == result["revision_id"] != before.id
    assert saved.source == "marketplace_text"
    assert saved.listing_json["ebay_specifics"]["title"] == "Vintage Wool Double Breasted Overcoat Charcoal Mens M"
    assert len(saved.listing_json["mercari_specifics"]["description"]) <= 1000
    assert saved.listing_json["title"] == inventory.original["title"]
    assert "ebay_specifics.title" in saved.listing_json["seller_fields"]


def test_keep_changes_nothing_and_a_stale_review_is_refused(inventory, monkeypatch):
    provider = _FakeProvider("{}")
    kept = _run(inventory, monkeypatch, provider, {"marketplaces": ["ebay"], "mode": "keep"})
    assert kept["changes"] == [] and provider.calls == 0
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _run(inventory, monkeypatch, provider, {"marketplaces": ["ebay"], "mode": "rewrite", "expected_revision_id": "old"})
    assert refused.value.status_code == 409


def test_rewrite_skips_fields_the_ai_left_out(inventory, monkeypatch):
    provider = _FakeProvider('{"etsy": {"title": "Charcoal wool overcoat, double breasted, mens medium"}}')
    result = _run(inventory, monkeypatch, provider, {"marketplaces": ["etsy"], "mode": "rewrite"})
    assert [change["field"] for change in result["changes"]] == ["title"]
    assert [skip["field"] for skip in result["skipped"]] == ["description"]


def test_text_route_is_mounted_on_desktop_and_hosted():
    from anteroom.routes import hosted_bulk_edit

    response = TestClient(app).post("/api/listings/bulk-edit/marketplace-text/missing-item",
                                    json={"marketplaces": ["ebay"], "mode": "shorten"})
    assert response.status_code == 404 and response.json()["detail"] == "Listing not found"
    path = "/api/listings/bulk-edit/marketplace-text/{conv_id}"
    assert path in {getattr(route, "path", None) for route in hosted_bulk_edit.router.routes}
