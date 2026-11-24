"""MCP catalog reads expose marketplace fields and account settings."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_mcp_http import _call_tool, _clean_mcp_tables, _rpc  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.catalog import CategoryFieldSchema, CategoryTree, CategoryTreeNode
from anteroom.services import mcp_catalog_tools, mcp_http
from anteroom.services.native_category_match import native_rows

CATALOG_TOOLS = {
    "search_categories", "get_category_attributes", "search_attribute_options",
    "get_marketplace_form", "get_account_settings",
}


@pytest.fixture(autouse=True)
def _no_chrome_or_marketplace(monkeypatch):
    """Every tool here answers from Anteroom's own data: reaching Chrome or a marketplace fails the test."""
    from anteroom.services import browser_bridge, marketplace_schemas

    async def refuse(*_args, **_kwargs):
        raise AssertionError("an MCP catalog read reached Chrome or a marketplace")

    monkeypatch.setattr(browser_bridge, "request", refuse)
    monkeypatch.setattr(marketplace_schemas, "fields", refuse)
    monkeypatch.setattr(marketplace_schemas, "etsy_properties", refuse)
    monkeypatch.setattr(marketplace_schemas, "ebay_aspects", refuse)


@pytest.fixture
def ebay_tree():
    with SessionLocal() as db:
        db.query(CategoryTreeNode).filter_by(marketplace="ebay").delete()
        db.query(CategoryTree).filter_by(marketplace="ebay").delete()
        db.query(CategoryFieldSchema).delete()
        db.add(CategoryTree(marketplace="ebay", status="complete"))
        for category_id, path in [
            ("11483", "Clothing, Shoes & Accessories > Men > Men's Clothing > Jeans"),
            ("11554", "Clothing, Shoes & Accessories > Women > Women's Clothing > Jeans"),
            ("15687", "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"),
        ]:
            db.add(CategoryTreeNode(marketplace="ebay", category_id=category_id, parent_id="1",
                                    label=path.rsplit(" > ", 1)[1], path=path, is_leaf=True, has_children=False))
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(CategoryTreeNode).filter_by(marketplace="ebay").delete()
        db.query(CategoryTree).filter_by(marketplace="ebay").delete()
        db.query(CategoryFieldSchema).delete()
        db.commit()


def _ebay_aspects_cached(category_id: str = "11483") -> None:
    from anteroom.services.category_fields import save_rows

    save_rows("ebay_aspects_v3", category_id, [
        {"id": "Brand", "name": "Brand", "required": True, "usage": "REQUIRED", "mode": "free_text",
         "multi": False, "max_values": None, "max_length": 65, "values": [
             {"id": "Levi's", "label": "Levi's"}, {"id": "Wrangler", "label": "Wrangler"}]},
        {"id": "Size Type", "name": "Size Type", "required": True, "usage": "REQUIRED", "mode": "selection",
         "multi": False, "max_values": None, "max_length": None, "values": [
             {"id": "Regular", "label": "Regular"}, {"id": "Big & Tall", "label": "Big & Tall"}]},
        {"id": "Color", "name": "Color", "required": False, "usage": "RECOMMENDED", "mode": "free_text",
         "multi": True, "max_values": None, "max_length": 65,
         "values": [{"id": f"Shade {n}", "label": f"Shade {n}"} for n in range(80)] + [
             {"id": "Indigo", "label": "Indigo"}]},
    ])
    save_rows("ebay_conditions", category_id, [{"required": True, "conditions": [
        {"id": "1000", "label": "New with tags"}, {"id": "3000", "label": "Pre-owned"}]}])


def _tool_names(client: TestClient, extra_headers: dict | None = None) -> set[str]:
    listed = _rpc(client, "tools/list", extra_headers=extra_headers)
    assert listed.status_code == 200, listed.text
    return {tool["name"] for tool in listed.json()["result"]["tools"]}


def test_catalog_tools_are_listing_reads_in_the_catalog():
    definitions = {tool["name"]: tool for tool in mcp_http.tool_definitions()}
    assert CATALOG_TOOLS <= set(definitions)
    assert set(mcp_catalog_tools.PERMISSIONS) == CATALOG_TOOLS == set(mcp_catalog_tools.HANDLERS)
    for name in CATALOG_TOOLS:
        assert mcp_http.TOOL_PERMISSIONS[name] == ("listings", "read")
        annotations = definitions[name]["annotations"]
        assert annotations["readOnlyHint"] is True and annotations["destructiveHint"] is False
        assert annotations["title"] == definitions[name]["title"]
    # The write tools send agents to the lookups before they fill marketplace fields.
    for writer in ("create_draft_listing", "edit_listing"):
        text = definitions[writer]["description"]
        for name in ("search_categories", "get_category_attributes", "search_attribute_options"):
            assert name in text, (writer, name)
        assert "get_listing_guidelines" not in text


def test_a_token_needs_listings_read_for_catalog_tools():
    client = TestClient(app)
    reader = client.post("/api/settings/mcp-tokens", json={"name": "Reader"}).json()
    assert CATALOG_TOOLS <= _tool_names(client, {"Authorization": f"Bearer {reader['token']}"})

    blind = client.post(
        "/api/settings/mcp-tokens", json={"name": "Analytics", "permissions": {"listings": "none"}},
    ).json()
    bearer = {"Authorization": f"Bearer {blind['token']}"}
    assert not CATALOG_TOOLS & _tool_names(client, bearer)
    refused, is_error = _call_tool(client, "get_account_settings", {}, extra_headers=bearer)
    assert is_error and "Listings to Read" in refused["error"]


def test_search_categories_returns_ids_the_form_accepts():
    client = TestClient(app)
    found, is_error = _call_tool(client, "search_categories", {"marketplace": "depop", "query": "men t-shirts"})
    assert not is_error, found
    assert found["marketplace"] == "depop"
    assert found["write_to"] == "direct_category_ids.depop"
    depop_ids = {row["id"] for row in native_rows()["depop"]}
    assert found["categories"] and all(hit["id"] in depop_ids for hit in found["categories"])
    assert found["categories"][0] == {"id": "menswear_tshirts", "path": "Men > Tops > T-shirts"}

    poshmark, is_error = _call_tool(client, "search_categories", {"marketplace": "poshmark", "query": "women belts", "limit": 3})
    assert not is_error
    assert len(poshmark["categories"]) <= 3
    assert poshmark["categories"][0]["path"] == "Women > Accessories > Belts"

    bad, is_error = _call_tool(client, "search_categories", {"marketplace": "amazon", "query": "jeans"})
    assert is_error and "marketplace" in bad["error"]


def test_search_categories_reads_the_synced_ebay_tree(ebay_tree):
    client = TestClient(app)
    found, is_error = _call_tool(client, "search_categories", {"marketplace": "ebay", "query": "women jeans"})
    assert not is_error, found
    assert found["categories"][0]["id"] == "11554"
    assert found["write_to"] == "direct_category_ids.ebay"


def test_category_attributes_for_a_form_built_from_anteroom_catalogs():
    client = TestClient(app)
    result, is_error = _call_tool(
        client, "get_category_attributes", {"marketplace": "depop", "category_id": "menswear_tshirts"},
    )
    assert not is_error, result
    assert result["available"] is True
    fields = {row["path"]: row for row in result["fields"]}
    condition = fields["depop_specifics.condition"]
    assert condition["required"] is True and condition["selection_only"] is True
    assert {"label": "Used - Excellent", "value": "used_excellent"} in condition["options"]
    assert "value" not in condition  # No listing: only the field definitions.
    brand = fields["depop_specifics.brand"]
    assert brand["options_truncated"] is True and brand["option_count"] > 1000
    assert "search_attribute_options" in brand["options_hint"]
    assert len(brand.get("options") or []) <= mcp_catalog_tools.MAX_INLINE_OPTIONS

    unknown, is_error = _call_tool(
        client, "get_category_attributes", {"marketplace": "depop", "category_id": "not-a-category"},
    )
    assert is_error and "search_categories" in unknown["error"]


def test_ebay_attributes_come_from_the_saved_schema_and_say_when_it_was_read(ebay_tree):
    client = TestClient(app)
    missing, is_error = _call_tool(client, "get_category_attributes", {"marketplace": "ebay", "category_id": "11483"})
    assert not is_error, missing
    assert missing["available"] is False
    assert "has not read" in missing["message"]

    _ebay_aspects_cached()
    result, is_error = _call_tool(client, "get_category_attributes", {"marketplace": "ebay", "category_id": "11483"})
    assert not is_error, result
    assert result["available"] is True and result["read_at"]
    fields = {row["path"]: row for row in result["fields"]}
    assert fields["ebay_specifics.Brand"]["required"] is True
    assert fields["ebay_specifics.Brand"]["selection_only"] is False
    assert fields["ebay_specifics.Size Type"]["options"] == ["Regular", "Big & Tall"]
    assert fields["ebay_specifics.Color"]["options_truncated"] is True
    assert result["condition"]["options"] == [
        {"label": "New with tags", "value": "1000"}, {"label": "Pre-owned", "value": "3000"}]

    options, is_error = _call_tool(client, "search_attribute_options", {
        "marketplace": "ebay", "category_id": "11483", "field": "Color", "query": "indigo",
    })
    assert not is_error, options
    assert options["path"] == "ebay_specifics.Color"
    assert options["options"] == ["Indigo"]
    assert options["selection_only"] is False


def test_etsy_property_options_are_the_labels_the_form_saves(ebay_tree):
    from anteroom.services import etsy_form, etsy_listings
    from anteroom.services.category_fields import save_rows

    category_id = next(iter(etsy_form.category_choices()["option_values"].values()))
    color = {"id": "200", "name": "Primary color", "required": True, "mode": "selection", "multi": False,
             "values": [{"id": 1, "label": "Beige"}, {"id": 2, "label": "Black"}]}
    save_rows("etsy_properties", str(category_id), [color])
    client = TestClient(app)
    result, is_error = _call_tool(
        client, "get_category_attributes", {"marketplace": "etsy", "category_id": str(category_id)})
    assert not is_error, result
    row = {row["path"]: row for row in result["fields"]}[f"{etsy_form.PROPERTIES_PATH}.Primary color"]
    assert row["options"] == ["Beige", "Black"]
    # Saving what the tool offers makes List send that property.
    def assigned(saved):
        return etsy_listings.property_values({"etsy_specifics": {"category_specifics": {"Primary color": saved}}}, [color])

    assert assigned(row["options"][1])[0]["value_ids"] == [2]
    assert assigned("2") == []  # An ID is not matched, so it must never be offered as the value.


def test_search_attribute_options_finds_values_in_a_long_list():
    client = TestClient(app)
    result, is_error = _call_tool(client, "search_attribute_options", {
        "marketplace": "depop", "category_id": "menswear_tshirts", "field": "depop_specifics.brand", "query": "nike",
    })
    assert not is_error, result
    assert result["options"][0] == "Nike"
    assert result["total_matches"] >= 1

    by_label, is_error = _call_tool(client, "search_attribute_options", {
        "marketplace": "depop", "category_id": "menswear_tshirts", "field": "Condition",
    })
    assert not is_error, by_label
    assert {"label": "Brand new", "value": "brand_new"} in by_label["options"]

    missing, is_error = _call_tool(client, "search_attribute_options", {
        "marketplace": "depop", "category_id": "menswear_tshirts", "field": "Sleeve length",
    })
    assert is_error and "get_category_attributes" in missing["error"]


def test_marketplace_form_shows_the_editor_rows_and_values_without_chrome():
    client = TestClient(app)
    draft, is_error = _call_tool(client, "create_draft_listing", {
        "title": "Vintage band tee", "price": 30, "condition": "Pre-owned - Excellent",
        "depop_specifics": {"condition": "used_excellent"},
    })
    assert not is_error, draft
    conv_id = draft["conversation_id"]
    _call_tool(client, "edit_listing", {
        "conversation_id": conv_id, "fields": {"direct_category_ids": {"depop": "menswear_tshirts"}},
    })

    form, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": conv_id, "marketplace": "depop"})
    assert not is_error, form
    assert form["available"] is True
    assert form["source"] == "catalog"
    assert form["catalog_read"]
    rows = {row["path"]: row for row in form["fields"]}
    assert rows["depop_specifics.condition"]["value"] == "used_excellent"
    assert rows["direct_category_ids.depop"]["value"] == "menswear_tshirts"
    assert rows["price"]["value"] in (30, 30.0, "30")

    # eBay's form is built from eBay's fields; until Anteroom has read them it says so.
    ebay, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": conv_id, "marketplace": "ebay"})
    assert not is_error, ebay
    assert ebay["available"] is False
    assert "eBay" in ebay["message"]

    missing, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": "nope", "marketplace": "depop"})
    assert is_error and "not found" in missing["error"].lower()


def test_saved_ebay_form_is_returned_with_when_its_fields_were_read(ebay_tree):
    from anteroom.repositories.queries import ListingRepo
    from anteroom.services import listing_persistence, native_contract

    client = TestClient(app)
    draft, _ = _call_tool(client, "create_draft_listing", {"title": "Levi's 501 jeans", "price": 45})
    conv_id = draft["conversation_id"]
    _ebay_aspects_cached()
    with SessionLocal() as db:
        repo = ListingRepo(db)
        revision = repo.get_revision(repo.get_current(conv_id).current_revision_id)
        listing = native_contract.copy_listing(revision.listing_json)
        listing.setdefault("direct_category_ids", {})["ebay"] = "11483"
        listing.setdefault("ebay_specifics", {})["Brand"] = "Levi's"
        form = {"known": True, "page_labels": True, "category_id": "11483", "fields": [
            {"key": "ebay_specifics.Brand", "path": "ebay_specifics.Brand", "label": "Brand", "required": True,
             "multi": False, "selection_only": False, "options": ["Levi's", "Wrangler"], "section": "Item specifics: Required"},
        ]}
        listing[native_contract.KEY] = native_contract.capture(listing, {"ebay": form}, ["ebay"])
        listing_persistence.save_revision(db, conv_id, listing, "native_schema",
                                          parent_revision_id=revision.id, expected_revision_id=revision.id)
        db.commit()

    result, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": conv_id, "marketplace": "ebay"})
    assert not is_error, result
    assert result["available"] is True and result["source"] == "saved"
    assert result["category_fields_read_at"]
    assert {row["path"]: row["value"] for row in result["fields"]}["ebay_specifics.Brand"] == "Levi's"


def test_account_settings_report_connections_without_secrets(monkeypatch):
    from anteroom.services import marketplace_accounts

    monkeypatch.setattr(marketplace_accounts, "get_marketplace_accounts", lambda: {
        "ebay": {"accessToken": "v^1.1#secret-token", "refreshToken": "refresh-secret", "label": "seller@example.com",
                 "accountId": "ebay-user-1", "connectedAt": 1_700_000_000},
        "etsy": {"accessToken": "etsy-secret", "authError": "Sign in to Etsy again.", "label": "Shop Owner"},
    })
    client = TestClient(app)
    result, is_error = _call_tool(client, "get_account_settings", {})
    assert not is_error, result
    assert result["region"] == "US" and result["currency"] == "USD"
    assert set(result["marketplaces"]) == {"ebay", "etsy", "poshmark", "mercari", "depop"}
    assert result["marketplaces"]["ebay"]["connected"] is True
    assert result["marketplaces"]["ebay"]["connected_at"]
    assert result["marketplaces"]["etsy"]["connected"] is False
    assert result["marketplaces"]["etsy"]["needs_sign_in"] is True
    assert result["marketplaces"]["poshmark"]["available_in_region"] is True
    text = json.dumps(result)
    for secret in ("secret", "seller@example.com", "Shop Owner", "ebay-user-1", "accessToken", "authorizeUrl"):
        assert secret not in text


def _assert_guidelines_are_private(client: TestClient, bearer: dict[str, str]) -> None:
    assert "get_listing_guidelines" not in _tool_names(client, bearer)
    response = _rpc(
        client, "tools/call", {"name": "get_listing_guidelines", "arguments": {}}, extra_headers=bearer,
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602

def test_listing_guidelines_are_private_even_with_listings_read_access():
    client = TestClient(app)
    token = client.post("/api/settings/mcp-tokens", json={"name": "Reader"}).json()["token"]
    _assert_guidelines_are_private(client, {"Authorization": f"Bearer {token}"})


def test_mcp_validation_keeps_formula_details_private(monkeypatch):
    from anteroom.services import skill_formulas, validation

    monkeypatch.setattr(skill_formulas, "get_listing_formulas", lambda: {"description": "Private Header: {DETAILS}"})
    fields = {"title": "Cotton jacket", "brand": "Example Brand", "size": "M", "description": "Woven cotton."}
    editor_validation = validation.validate_listing(fields.copy())
    assert any("Private Header" in item["message"] for item in editor_validation.errors)
    client = TestClient(app)
    token = client.post(
        "/api/settings/mcp-tokens", json={"name": "Writer", "permissions": {"listings": "write"}},
    ).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}

    draft, is_error = _call_tool(client, "create_draft_listing", fields, bearer)
    assert not is_error, draft
    identity = {"conversation_id": draft["conversation_id"]}
    edited, is_error = _call_tool(client, "edit_listing", {**identity, "fields": {"price": 20}}, bearer)
    assert not is_error, edited
    read, is_error = _call_tool(client, "get_listing", identity, bearer)
    assert not is_error, read
    for result in (draft, edited, read):
        errors = result["validation"]["errors"]
        assert any(item["field"] == "description" for item in errors)
        assert "Private Header" not in json.dumps(errors)
        assert "Brand Size Vibe Item Color Fit" not in json.dumps(errors)


def test_hosted_catalog_tools_are_seller_scoped(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys, USER_ID)
    bob = headers(signing_keys, BOB)
    write = {"permissions": {"listings": "write"}}
    alice_token = client.post("/api/settings/mcp-tokens", headers=alice, json={"name": "Alice", **write}).json()
    bob_token = client.post("/api/settings/mcp-tokens", headers=bob, json={"name": "Bob", **write}).json()
    alice_mcp = {"Authorization": f"Bearer {alice_token['token']}"}
    bob_mcp = {"Authorization": f"Bearer {bob_token['token']}"}

    draft, is_error = _call_tool(client, "create_draft_listing", {
        "title": f"Alice tee {uuid.uuid4().hex[:6]}", "price": 20, "depop_specifics": {"condition": "brand_new"},
    }, extra_headers=alice_mcp)
    assert not is_error, draft
    conv_id = draft["conversation_id"]

    form, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": conv_id, "marketplace": "depop"},
                                extra_headers=alice_mcp)
    assert not is_error, form
    assert {row["path"]: row["value"] for row in form["fields"]}["depop_specifics.condition"] == "brand_new"

    stolen, is_error = _call_tool(client, "get_marketplace_form", {"conversation_id": conv_id, "marketplace": "depop"},
                                  extra_headers=bob_mcp)
    assert is_error and "not found" in stolen["error"].lower()

    for token in (alice_mcp, bob_mcp):
        settings, is_error = _call_tool(client, "get_account_settings", {}, extra_headers=token)
        assert not is_error, settings
        assert settings["currency"]
        found, is_error = _call_tool(client, "search_categories", {"marketplace": "mercari", "query": "dresses"},
                                     extra_headers=token)
        assert not is_error, found
        assert found["categories"]
        _assert_guidelines_are_private(client, token)
