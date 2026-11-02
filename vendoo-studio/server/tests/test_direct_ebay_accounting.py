import json

import pytest
import test_ebay_workspace

from ebay_workspace_fake import ITEM_ID
from vendoo_studio.services import listing_persistence
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.services import ebay_workspace

workspace = test_ebay_workspace.workspace


def direct(workspace, **fields):
    conv = workspace.db.get(Conversation, "coat")
    conv.notes = None
    workspace.db.commit()
    MarketplaceListingRepo(workspace.db).save("coat", "ebay", {"state": "published", **fields})


def finance(workspace):
    response = workspace.client.get("/api/ebay/read/finances")
    assert response.status_code == 200, response.text
    return response.json()["rows"][0]


@pytest.mark.parametrize("fields", [
    {"listing_url": f"https://www.ebay.com/itm/{ITEM_ID}"},
    {"listing_id": ITEM_ID}, {"ebay_listing_id": ITEM_ID},
])
def test_direct_listing_costs_are_used_without_any_vendoo_notes(workspace, fields):
    direct(workspace, **fields)
    result = finance(workspace)
    assert result["amount"] == "40.00"
    assert result["fields"]["Item cost"] == "10.00"
    assert result["fields"]["Profit"] == "30.00"
    assert result["fields"]["Original selling fees"] == "10.00"
    assert workspace.remote.writes == []


def test_seller_linked_listing_costs_are_used_without_vendoo(workspace):
    conv = workspace.db.get(Conversation, "coat")
    conv.notes = json.dumps({"marketplaceMarks": {"ebay": {"state": "listed", "url": f"https://www.ebay.com/itm/{ITEM_ID}"}}})
    workspace.db.commit()
    assert finance(workspace)["fields"]["Profit"] == "30.00"


def test_direct_and_imported_links_on_same_item_do_not_duplicate_costs(workspace):
    MarketplaceListingRepo(workspace.db).save("coat", "ebay", {"state": "published", "listing_id": ITEM_ID,
                                                                "listing_url": f"https://www.ebay.com/itm/{ITEM_ID}"})
    assert finance(workspace)["fields"]["Item cost"] == "10.00"


def test_ambiguous_direct_identity_keeps_cost_and_profit_unknown(workspace):
    direct(workspace, listing_id=ITEM_ID)
    workspace.db.add(Conversation(id="duplicate", title="Different item", status="active"))
    workspace.db.commit()
    listing_persistence.save_revision(workspace.db, "duplicate", {"title": "Different item", "price": 99, "cost": 50}, "user")
    MarketplaceListingRepo(workspace.db).save("duplicate", "ebay", {"state": "published", "ebay_listing_id": ITEM_ID})
    result = finance(workspace)
    assert result["amount"] == "40.00"
    assert result["fields"]["Item cost"] is None and result["fields"]["Profit"] is None


def test_ambiguous_direct_and_seller_links_do_not_guess_a_cost(workspace):
    direct(workspace, listing_id=ITEM_ID)
    workspace.db.add(Conversation(id="duplicate", title="Different item", status="active", notes=json.dumps({
        "marketplaceMarks": {"ebay": {"state": "listed", "url": f"https://www.ebay.com/itm/{ITEM_ID}"}}})))
    workspace.db.commit()
    assert finance(workspace)["fields"]["Profit"] is None


def test_missing_direct_cost_stays_unknown(workspace):
    direct(workspace, listing_id=ITEM_ID)
    listing_persistence.save_revision(workspace.db, "coat", {"title": "Coat", "price": 50}, "user")
    assert finance(workspace)["fields"]["Profit"] is None


@pytest.mark.parametrize("fields", [{"listing_url": f"https://www.ebay.com/itm/{ITEM_ID}"}, {"listing_id": ITEM_ID}, {"ebay_listing_id": ITEM_ID}])
def test_traffic_uses_direct_imported_identities_without_vendoo(workspace, fields):
    direct(workspace, **fields)
    response = workspace.client.get("/api/ebay/read/traffic")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["rows"][0]["title"] == "Test wool coat"
    assert workspace.remote.writes == []


def test_traffic_unifies_all_links_for_one_item_and_does_not_assign_ambiguous_titles(workspace):
    MarketplaceListingRepo(workspace.db).save("coat", "ebay", {"state": "published", "listing_id": ITEM_ID,
                                                                "ebay_listing_id": ITEM_ID, "listing_url": f"https://www.ebay.com/itm/{ITEM_ID}"})
    assert ebay_workspace.traffic_bindings(workspace.db) == {ITEM_ID: "Test wool coat"}
    workspace.db.add(Conversation(id="duplicate", title="Different item", status="active"))
    workspace.db.commit()
    MarketplaceListingRepo(workspace.db).save("duplicate", "ebay", {"state": "published", "listing_id": ITEM_ID})
    assert ebay_workspace.traffic_bindings(workspace.db) == {ITEM_ID: f"Listing {ITEM_ID}"}


def test_traffic_accepts_seller_links_and_rejects_untrusted_saved_references(workspace):
    direct(workspace, listing_url=f"https://ebay.com.attacker.test/itm/{ITEM_ID}", listing_id="not-a-listing")
    assert ebay_workspace.traffic_bindings(workspace.db) == {}
    conv = workspace.db.get(Conversation, "coat")
    conv.notes = json.dumps({"marketplaceMarks": {"ebay": {"state": "listed", "url": f"https://www.ebay.com/itm/{ITEM_ID}"}}})
    workspace.db.commit()
    assert ebay_workspace.traffic_bindings(workspace.db) == {ITEM_ID: "Test wool coat"}


def test_exact_duplicate_order_lines_do_not_double_item_costs(workspace):
    from ebay_workspace_fake import order
    import copy

    direct(workspace, listing_id=ITEM_ID)
    payload = order()
    payload["lineItems"].append(copy.deepcopy(payload["lineItems"][0]))
    workspace.remote.overrides["/sell/fulfillment/v1/order"] = {"total": 1, "orders": [payload]}
    assert finance(workspace)["fields"]["Item cost"] == "10.00"


def test_distinct_order_lines_for_same_listing_each_count_their_quantity(workspace):
    from ebay_workspace_fake import order
    import copy

    direct(workspace, listing_id=ITEM_ID)
    payload = order()
    second = copy.deepcopy(payload["lineItems"][0])
    second.update(lineItemId="line-2", quantity=1)
    payload["lineItems"].append(second)
    workspace.remote.overrides["/sell/fulfillment/v1/order"] = {"total": 1, "orders": [payload]}
    result = finance(workspace)
    assert result["fields"]["Item cost"] == "15.00" and result["fields"]["Profit"] == "25.00"


def test_conflicting_duplicate_lines_leave_profit_unknown(workspace):
    from ebay_workspace_fake import order
    import copy

    direct(workspace, listing_id=ITEM_ID)
    payload = order()
    second = copy.deepcopy(payload["lineItems"][0])
    second["quantity"] = 3
    payload["lineItems"].append(second)
    workspace.remote.overrides["/sell/fulfillment/v1/order"] = {"total": 1, "orders": [payload]}
    result = finance(workspace)
    assert result["fields"]["Profit"] is None
    assert "conflicting order lines" in result["fields"]["Cost details"]


@pytest.mark.parametrize("change", [{"legacyItemId": None}, {"legacyItemId": "not-an-id"}, {"quantity": True}, {"quantity": 0}, {"lineItemId": ""}])
def test_unverifiable_order_items_explain_why_profit_is_unavailable(workspace, change):
    from ebay_workspace_fake import order

    direct(workspace, listing_id=ITEM_ID)
    payload = order()
    payload["lineItems"][0].update(change)
    workspace.remote.overrides["/sell/fulfillment/v1/order"] = {"total": 1, "orders": [payload]}
    result = finance(workspace)
    assert result["fields"]["Profit"] is None and result["fields"]["Cost details"]
