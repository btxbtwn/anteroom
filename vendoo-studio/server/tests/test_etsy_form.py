"""The editor's Etsy tab is Etsy's own listing form, built without writing anything to Etsy."""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.config import skills_dir
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import etsy_form, etsy_listings, listing_persistence, marketplace_schemas, native_editor

SECTIONS = ["Photo & video", "Category", "Item details", "Item options", "Pricing & shipping", "How it's made", "Settings"]
TEE = "449"
PROPERTIES = [
    {"id": 200, "name": "Primary color", "alias": "color", "required": False, "mode": "selection", "multi": False,
     "max_values": None, "max_length": None, "values": [{"id": 1, "label": "Blue"}, {"id": 2, "label": "Black"}]},
    {"id": 201, "name": "Sleeve length", "alias": "", "required": True, "mode": "selection", "multi": False,
     "max_values": None, "max_length": None, "values": [{"id": 3, "label": "Long sleeve"}, {"id": 4, "label": "Short sleeve"}]},
    {"id": 202, "name": "Occasion", "alias": "", "required": False, "mode": "selection", "multi": True,
     "max_values": 3, "max_length": None, "values": [{"id": 5, "label": "Birthday"}, {"id": 6, "label": "Wedding"}]},
]


@pytest.fixture
def studio():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    conv = ConversationRepo(db).create(title="Etsy form test")
    listing = {"title": "Blue Nike Tee", "description": "Soft cotton tee", "brand": "Nike", "price": 25, "quantity": 2,
               "primaryColor": "Blue", "weight_oz": 8, "package_dimensions_in": "10x8x2",
               "direct_category_ids": {"etsy": TEE, "etsy_path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts"},
               "etsy_specifics": {"whoMade": "Another company or person", "whatIsIt": "A finished product",
                                  "whenMade": "2020 - 2026 (Recently)", "category_specifics": {"sleeveLength": "Short sleeve"}}}
    revision = listing_persistence.save_revision(db, conv.id, listing, "test")
    app.dependency_overrides[get_db] = lambda: db
    with patch("vendoo_studio.services.browser_bridge.request", new_callable=AsyncMock) as bridge, \
            patch.object(marketplace_schemas, "etsy_properties", AsyncMock(return_value=PROPERTIES)) as properties:
        yield SimpleNamespace(db=db, conv=conv, listing=listing, revision=revision, client=TestClient(app),
                              url=f"/api/conversations/{conv.id}/native-marketplaces/etsy/form", bridge=bridge,
                              properties=properties)
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def _filled(client: TestClient, form: dict) -> dict:
    for row in form["fields"]:
        ref = row.pop("choice_list", None)
        if ref:
            listed = client.get(f"/api/catalog/choice-lists/{ref['name']}?v={ref['version']}")
            assert listed.status_code == 200, listed.text
            row.update(listed.json())
    return form


def test_reference_records_the_page_its_sources_and_etsys_category_leaves():
    reference = json.loads((skills_dir() / "list-this/references/etsy-native-form.json").read_text())
    assert reference["unconfirmed"] and reference["not_offered"]
    assert all(source.startswith("http") or "Anteroom" in source or "Built" in source for source in reference["sources"].values())
    sections = list(dict.fromkeys(row["section"] for row in reference["fields"]))
    assert sections == SECTIONS
    ids = [row["id"] for row in reference["categories"]]
    assert len(ids) == len(set(ids)) > 3000
    assert all(re.fullmatch(r"[1-9]\d*", item) for item in ids)
    assert {"id": TEE, "path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts"} in reference["categories"]


def test_etsy_tab_is_etsys_page_with_this_categorys_attributes(studio):
    answer = studio.client.get(f"{studio.url}?revision_id={studio.revision.id}")
    assert answer.status_code == 200, answer.text
    # The 3,050 categories are a shared list the client downloads once.
    assert len(answer.content) < 100_000
    form = _filled(studio.client, answer.json())
    assert form == native_editor.read_form(studio.db, studio.conv.id, "etsy", studio.revision.id)
    assert form["native"] and form["page_labels"] and form["category_id"] == TEE and form["notes"] == []
    assert list(dict.fromkeys(row["section"] for row in form["fields"])) == SECTIONS
    rows = {row["path"]: row for row in form["fields"]}
    order = [row["path"] for row in form["fields"]]
    # Etsy's attributes sit in Item options, after Description and before Materials and Tags.
    assert order.index("etsy_specifics.description") < order.index("etsy_specifics.category_specifics.Primary color") \
        < order.index("etsy_specifics.materials") < order.index("etsy_specifics.tags")
    category = rows["direct_category_ids.etsy"]
    assert category["value"] == TEE and category["selection_only"] and len(category["options"]) > 3000
    assert category["option_values"]["Clothing > Men's Clothing > Shirts & Tees > T-shirts"] == TEE
    # General's values show until the seller changes them on Etsy.
    assert rows["etsy_specifics.title"]["value"] == "Blue Nike Tee"
    assert rows["etsy_specifics.title"]["max_length"] == 140
    assert rows["etsy_specifics.description"]["value"] == "Soft cotton tee"
    assert rows["price"]["value"] == 25 and rows["quantity"]["value"] == 2
    assert rows["etsy_specifics.category_specifics.Primary color"]["value"] == "Blue"
    # The answer already saved under its own key keeps that key, so an edit replaces it.
    sleeve = rows["etsy_specifics.category_specifics.sleeveLength"]
    assert sleeve["label"] == "Sleeve length" and sleeve["value"] == "Short sleeve" and sleeve["required"]
    assert sleeve["options"] == ["Long sleeve", "Short sleeve"] and sleeve["category_specific"]
    assert rows["etsy_specifics.category_specifics.Occasion"]["multi"] is True
    assert rows["etsy_specifics.category_specifics.Occasion"]["value"] == []
    assert {rows[path]["group"] for path in ("etsy_specifics.category_specifics.Occasion", "etsy_specifics.materials")} == {"Attributes"}
    assert rows["etsy_specifics.whoMade"]["value"] == "Another company or person"
    assert rows["etsy_specifics.whoMade"]["options"] == ["I did", "A member of my shop", "Another company or person"]
    assert rows["etsy_specifics.whoMade"]["save_as_default"] is True
    assert rows["etsy_specifics.whatIsIt"]["save_as_default"] is True
    assert rows["etsy_specifics.whenMade"]["value"] == "2020 - 2026 (Recently)"
    # What List decides without the seller is shown, locked, as List sends it.
    assert rows["etsy_specifics.itemWeight"]["value"] == "8 oz" and rows["etsy_specifics.itemWeight"]["locked"]
    assert rows["etsy_specifics.itemSize"]["value"] == "10 × 8 × 2 in"
    assert rows["etsy_specifics.listingType"]["value"] == "Physical item"
    assert rows["etsy_specifics.renewalOption"]["value"] == "Manual"
    assert rows["etsy_specifics.shipping_profile_id"]["value"] == "Not chosen yet"
    assert rows["etsy_specifics.shipping_profile_id"]["locked"]
    studio.properties.assert_awaited_with(TEE)
    studio.bridge.assert_not_called()
    assert JobRepo(studio.db).list_by_conversation(studio.conv.id) == []


def test_etsy_tab_opens_without_etsy_and_says_what_is_missing(studio):
    studio.properties.side_effect = OSError("Etsy is not connected")
    form = studio.client.get(f"{studio.url}?revision_id={studio.revision.id}").json()
    paths = [row["path"] for row in form["fields"]]
    assert not [path for path in paths if path.startswith("etsy_specifics.category_specifics.")]
    assert "etsy_specifics.title" in paths and "direct_category_ids.etsy" in paths
    assert form["notes"] == ["Etsy's attributes for this category could not be loaded. Connect Etsy in Settings → "
                             "Connections, then reopen the listing."]


def test_a_new_draft_opens_an_empty_etsy_form(studio):
    conv = ConversationRepo(studio.db).create(title="Empty")
    form = native_editor.read_form(studio.db, conv.id, "etsy", None)
    assert form["category_id"] == "" and form["known"] is True
    assert not any(row["path"].startswith("etsy_specifics.category_specifics.") for row in form["fields"])
    studio.properties.assert_not_awaited()


def test_an_unknown_etsy_category_is_named_not_replaced(studio):
    listing = {**studio.listing, "direct_category_ids": {"etsy": "999999999"}}
    revision = listing_persistence.save_revision(studio.db, studio.conv.id, listing, "test")
    form = native_editor.read_form(studio.db, studio.conv.id, "etsy", revision.id)
    assert form["known"] is False and form["category_id"] == ""
    assert "Choose the most specific category again" in form["notes"][-1]


def test_an_etsy_tab_edit_stays_on_etsy_and_the_picked_category_is_kept(studio):
    from vendoo_studio.services.agent_categories import _fill_etsy

    edited = json.loads(json.dumps(studio.listing))
    edited["direct_category_ids"] = {"etsy": "482", "etsy_path": "Clothing > Gender-Neutral Adult Clothing > Tops & Tees > T-shirts"}
    edited["etsy_specifics"].update(title="Etsy only title", price=31,
                                    category_specifics={"sleeveLength": "Long sleeve", "Primary color": "Black"})
    saved = studio.client.put(f"/api/conversations/{studio.conv.id}/listing",
                              json={"listing": edited, "expected_revision_id": studio.revision.id})
    assert saved.status_code == 200, saved.text
    current = ListingRepo(studio.db).get_revision(ListingRepo(studio.db).get_current(studio.conv.id).current_revision_id)
    stored = current.listing_json
    assert stored["title"] == "Blue Nike Tee" and stored["primaryColor"] == "Blue" and stored["price"] == 25
    locks = set(stored["seller_fields"])
    assert {"direct_category_ids.etsy", "etsy_specifics.title"} <= locks
    # A category fill never replaces the seller's pick.
    with patch("vendoo_studio.services.direct_categories._etsy_leaf", AsyncMock(return_value={"id": TEE, "path": "x"})):
        assert asyncio.run(_fill_etsy(json.loads(json.dumps(stored)), "Men > Shirts")) is False
    form = native_editor.read_form(studio.db, studio.conv.id, "etsy", current.id)
    rows = {row["path"]: row["value"] for row in form["fields"]}
    assert rows["direct_category_ids.etsy"] == "482"
    assert rows["etsy_specifics.title"] == "Etsy only title"
    assert rows["etsy_specifics.category_specifics.sleeveLength"] == "Long sleeve"
    assert rows["etsy_specifics.category_specifics.Primary color"] == "Black"


def test_etsy_send_takes_the_etsy_tabs_price_tags_and_attributes():
    snapshot = {"title": "Tee", "description": "d", "price": 25, "quantity": 1, "tags": ["general"],
                "primaryColor": "Blue", "direct_category_ids": {"etsy": TEE},
                "etsy_specifics": {"price": 31, "tags": ["etsy only"], "whoMade": "I did", "whatIsIt": "A finished product",
                                   "whenMade": "2020 - 2026 (Recently)",
                                   "category_specifics": {"sleeveLength": "Long sleeve", "Primary color": "Black"}},
                "seller_fields": ["etsy_specifics.price", "etsy_specifics.tags"],
                "form": {"specifics": {"tags": ["general"]}}}
    fields = etsy_listings.draft_fields(snapshot, creating=True)
    assert fields["price"] == "31.00" and fields["tags"] == "etsy only"
    assigned = {row["name"]: row["values"] for row in etsy_listings.property_values(snapshot, PROPERTIES)}
    assert assigned == {"Primary color": ["Black"], "Sleeve length": ["Long sleeve"]}
    # Without an Etsy edit, Etsy follows General.
    general = {**snapshot, "seller_fields": [], "etsy_specifics": {**snapshot["etsy_specifics"], "price": 25}}
    fields = etsy_listings.draft_fields(general, creating=True)
    assert fields["price"] == "25.00" and fields["tags"] == "general"


def test_etsy_categories_are_one_shared_choice_list():
    from vendoo_studio.services.choice_lists import choice_list

    body, _version = choice_list("etsy-categories")
    assert json.loads(body) == etsy_form.category_choices()
