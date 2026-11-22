"""Shipping presets: settings, one-click apply, and the AI's pick during generation."""
import pytest
from fastapi.testclient import TestClient

from anteroom.main import app
from anteroom.providers.sandbox import _sandbox_preset
from anteroom.services import shipping_presets
from anteroom.services.chat_prompts import _shipping_presets_prompt
from anteroom.services.shipping_presets import SHIPPING_PRESETS_KEY
from anteroom.services.user_settings import update_settings

SHOES = {"name": "Shoes", "usage_condition": "Use for shoes and boots", "length": 14, "width": 10, "height": 6,
         "weight_lb": 3, "weight_oz": 0, "marketplaces": {"depop": {"parcelSize": "Large"},
                                                          "mercari": {"shippingMethod": "MERCARI", "shippingPayerId": "1"}}}
TOPS = {"name": "Light tops", "usage_condition": "Use for lightweight tops under 1 lb", "length": 10, "width": 8,
        "height": 2, "weight_lb": 0, "weight_oz": 12, "marketplaces": {"ebay": {"fulfillmentPolicyId": "123456"}}}


@pytest.fixture(autouse=True)
def _no_presets():
    update_settings(lambda payload: payload.pop(SHIPPING_PRESETS_KEY, None))
    yield
    update_settings(lambda payload: payload.pop(SHIPPING_PRESETS_KEY, None))


def test_presets_are_created_edited_listed_and_deleted():
    client = TestClient(app)
    empty = client.get("/api/settings/shipping-presets").json()
    assert empty["presets"] == []
    assert "parcelSize" in empty["fields"]["depop"]
    created = client.post("/api/settings/shipping-presets", json=SHOES)
    assert created.status_code == 200, created.text
    preset = created.json()["presets"][0]
    assert preset["name"] == "Shoes" and preset["marketplaces"]["depop"] == {"parcelSize": "Large"}
    assert client.post("/api/settings/shipping-presets", json={**TOPS, "name": "shoes"}).status_code == 400
    edited = client.put(f"/api/settings/shipping-presets/{preset['id']}", json={**SHOES, "usage_condition": "Boots only"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["presets"][0]["usage_condition"] == "Boots only"
    assert edited.json()["presets"][0]["id"] == preset["id"]
    assert client.put("/api/settings/shipping-presets/missing", json=SHOES).status_code == 400
    assert client.delete(f"/api/settings/shipping-presets/{preset['id']}").json()["presets"] == []
    assert client.delete(f"/api/settings/shipping-presets/{preset['id']}").status_code == 404


@pytest.mark.parametrize("change,message", [
    ({"name": ""}, "name"),
    ({"length": 12, "width": None, "height": None}, "length, width and height"),
    ({"weight_oz": 16}, "ounces"),
    ({"marketplaces": {"depop": {"parcelSize": "Huge"}}}, "Depop parcel size"),
    ({"marketplaces": {"ebay": {"fulfillmentPolicyId": "abc"}}}, "digits"),
    ({"marketplaces": {"vinted": {"x": "1"}}}, "eBay, Etsy, Mercari or Depop"),
    ({"length": None, "width": None, "height": None, "weight_lb": None, "weight_oz": None, "marketplaces": {}}, "package size"),
])
def test_invalid_presets_are_refused(change, message):
    response = TestClient(app).post("/api/settings/shipping-presets", json={**SHOES, **change})
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_apply_returns_the_listing_with_package_and_shipping_locked_and_saves_nothing():
    preset = shipping_presets.save_shipping_preset(SHOES)[0]
    listing = {"title": "Boots", "price": 40, "package_dimensions_in": "13x10x3", "seller_fields": ["title"]}
    response = TestClient(app).post(f"/api/settings/shipping-presets/{preset['id']}/apply", json={"listing": listing})
    assert response.status_code == 200, response.text
    updated = response.json()["listing"]
    assert updated["package_dimensions_in"] == "14x10x6"
    assert (updated["weight_lb"], updated["weight_oz"]) == (3, 0)
    assert updated["depop_specifics"]["parcelSize"] == "Large"
    assert updated["mercari_specifics"] == {"shippingMethod": "MERCARI", "shippingPayerId": 1}
    assert {"package_dimensions_in", "weight_lb", "depop_specifics.parcelSize", "title"} <= set(updated["seller_fields"])
    assert listing["package_dimensions_in"] == "13x10x3"
    assert TestClient(app).post("/api/settings/shipping-presets/nope/apply", json={"listing": listing}).status_code == 404


def test_generation_prompt_lists_each_condition_and_asks_for_one_name():
    assert _shipping_presets_prompt() == ""
    shipping_presets.save_shipping_preset(SHOES)
    shipping_presets.save_shipping_preset(TOPS)
    prompt = _shipping_presets_prompt()
    assert '- "Shoes": Use for shoes and boots (box 14x10x6 in, 3 lb 0 oz)' in prompt
    assert '- "Light tops": Use for lightweight tops under 1 lb' in prompt
    assert '"shipping_preset"' in prompt
    # The sandbox stands in for the model: its T-shirt matches the tops preset.
    assert _sandbox_preset(prompt) == "Light tops"


def test_the_model_names_a_preset_by_name_in_any_case():
    shipping_presets.save_shipping_preset(TOPS)
    assert shipping_presets.find_preset("  light   TOPS ")["name"] == "Light tops"
    assert shipping_presets.find_preset("") is None
    assert shipping_presets.find_preset("Pants") is None


def test_generation_applies_the_picked_preset(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from anteroom.database import Base, load_models
    from anteroom.models.conversation import Conversation
    from anteroom.services.listing_generate import persist_generated_listing

    shipping_presets.save_shipping_preset(TOPS)
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Conversation(id="tee", title="Tee", status="draft", notes="{}"))
    db.commit()
    try:
        listing = persist_generated_listing(
            db, "tee", "", parsed={"title": "Blue tee", "price": 20, "shipping_preset": "Light tops",
                                   "ebay_specifics": {"type": "T-Shirt"}}, announce=False)
        assert listing is not None
        assert "shipping_preset" not in listing
        assert listing["package_dimensions_in"] == "10x8x2"
        assert (listing["weight_lb"], listing["weight_oz"]) == (0, 12)
        assert listing["ebay_specifics"]["fulfillmentPolicyId"] == "123456"
        unknown = persist_generated_listing(db, "tee", "", parsed={"title": "Blue tee", "price": 20,
                                                                    "shipping_preset": "Nothing"}, announce=False)
        assert unknown is not None and "shipping_preset" not in unknown
        assert unknown["package_dimensions_in"] != "10x8x2"
    finally:
        db.close()
        engine.dispose()
