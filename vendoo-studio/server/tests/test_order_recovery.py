import copy
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

import pytest
import test_etsy_import
import test_ebay_import
from test_marketplace_inventory import read_listings

from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import marketplace_accounts, order_recovery
from vendoo_studio.services.ebay_client import NS

etsy_imported = test_etsy_import.imported
ebay_imported = test_ebay_import.imported


def dates():
    today = datetime.now(UTC).date()
    return {"start": (today - timedelta(days=29)).isoformat(), "end": today.isoformat()}


def receipt(status="paid", receipt_id=100):
    return {"receipt_id": receipt_id, "status": status, "is_paid": True,
            "create_timestamp": int(datetime.now(UTC).timestamp()) - 1000,
            "updated_timestamp": int(datetime.now(UTC).timestamp()) - 100,
            "buyer_email": "private@example.test", "formatted_address": "Private address",
            "transactions": [{"transaction_id": 200, "listing_id": 555, "quantity": 1}]}


@pytest.fixture
def recovery(etsy_imported, monkeypatch):
    fixture = etsy_imported
    fixture.remote.receipts = [receipt()]
    fixture.remote.order_calls = []
    fixture.remote.total = 1
    fixture.remote.after_orders = None
    original = marketplace_accounts.etsy_api

    async def read(method, path, *, params=None):
        if path != "shops/42/receipts":
            return await original(method, path, params=params)
        assert method == "GET", "Recovery must never write to Etsy"
        fixture.remote.order_calls.append(params)
        result = {"count": fixture.remote.total, "results": copy.deepcopy(fixture.remote.receipts)}
        if fixture.remote.after_orders:
            fixture.remote.after_orders()
        return result

    monkeypatch.setattr(marketplace_accounts, "etsy_api", read)
    fixture.autodelist = AsyncMock()
    monkeypatch.setattr(marketplace_accounts, "autodelist_for_sales", fixture.autodelist)
    return fixture


def recover(fixture, **changes):
    return fixture.client.post("/api/marketplace-sales/recover", json={"marketplace": "etsy", "connected_at": "1", **dates(), **changes})


def test_recovery_records_direct_sale_and_reuses_its_webhook_identity(recovery):
    imported = recovery.client.post("/api/etsy/import", json={"listing_id": "555"}).json()
    result = recover(recovery)
    assert result.status_code == 200, result.text
    assert result.json()["recorded"] == 1 and result.json()["nextPage"] is None
    sale = recovery.db.query(MarketplaceSale).one()
    assert (sale.order_id, sale.line_id, sale.listing_id, sale.quantity, sale.order_status) == ("100", "200", "555", 1, "paid")
    assert sale.conversation_id == imported["conversation_id"]
    assert sale.order_observed_at and sale.order_updated_at
    duplicate = recover(recovery).json()
    assert duplicate["recorded"] == 0 and duplicate["unchanged"] == 1
    assert recovery.db.query(MarketplaceSale).count() == 1
    assert ListingRepo(recovery.db).get_revisions(sale.conversation_id)[0].listing_json["quantity"] == 3
    assert "private@example" not in str(sale.__dict__) and "Private address" not in str(sale.__dict__)
    recovery.autodelist.assert_not_called()


def test_recovery_fills_missing_webhook_quantity_but_preserves_dismissal(recovery):
    sale = MarketplaceSale(marketplace="etsy", order_id="100", line_id="200", listing_id="555", state="dismissed")
    recovery.db.add(sale)
    recovery.db.commit()
    assert recover(recovery).json()["updated"] == 1
    recovery.db.refresh(sale)
    assert sale.quantity == 1 and sale.state == "dismissed"
    assert recovery.db.query(MarketplaceSale).count() == 1


@pytest.mark.parametrize("status,expected", [("canceled", "cancelled"), ("fully refunded", "refunded"), ("partially refunded", "partially_refunded")])
def test_changed_order_status_updates_ledger_without_relisting_or_restocking(recovery, status, expected):
    imported = recovery.client.post("/api/etsy/import", json={"listing_id": "555"}).json()
    assert recover(recovery).status_code == 200
    recovery.remote.receipts[0]["status"] = status
    recovery.remote.receipts[0]["updated_timestamp"] += 1
    assert recover(recovery).json()["updated"] == 1
    sale = recovery.db.query(MarketplaceSale).one()
    assert sale.order_status == expected
    assert ListingRepo(recovery.db).get_revisions(imported["conversation_id"])[0].listing_json["quantity"] == 3
    recovery.autodelist.assert_not_called()


def test_cancellation_tombstone_prevents_a_delayed_paid_webhook_resurrecting_the_sale(recovery):
    from vendoo_studio.services.relay_sales import record_sale

    recovery.remote.receipts = [receipt("canceled")]
    assert recover(recovery).json()["recorded"] == 1
    assert record_sale(recovery.db, marketplace="etsy", order_id="100", line_id="200", listing_id="555", quantity=1, sold_at=None) is None
    assert recovery.db.query(MarketplaceSale).one().order_status == "cancelled"


def test_older_order_payload_cannot_reverse_a_cancellation(recovery):
    recovery.remote.receipts = [receipt("canceled")]
    recover(recovery)
    recovery.remote.receipts[0]["status"] = "paid"
    recovery.remote.receipts[0]["updated_timestamp"] -= 10
    assert recover(recovery).json()["unchanged"] == 1
    assert recovery.db.query(MarketplaceSale).one().order_status == "cancelled"


def test_older_inflight_read_cannot_overwrite_a_newer_local_observation(recovery):
    recover(recovery)
    sale = recovery.db.query(MarketplaceSale).one()
    old = sale.order_observed_at - timedelta(seconds=1)
    line = order_recovery.OrderLine("100", "200", "555", 5, "paid", None, sale.order_updated_at)
    result = order_recovery._save(recovery.db, "etsy", [line], old)
    assert result["unchanged"] == 1 and recovery.db.query(MarketplaceSale).one().quantity == 1


@pytest.mark.parametrize("status", ["open", "payment processing", "new-unknown-state"])
def test_unpaid_or_unknown_orders_are_counted_as_skipped_not_recorded(recovery, status):
    recovery.remote.receipts[0]["status"] = status
    result = recover(recovery).json()
    assert result["skippedOrders"] == 1 and result["recorded"] == 0
    assert recovery.db.query(MarketplaceSale).count() == 0


def test_pagination_uses_modification_dates_and_pins_the_connection(recovery):
    recovery.remote.total = 26
    first = recover(recovery)
    assert first.status_code == 200
    assert first.json()["nextPage"] == 2
    params = recovery.remote.order_calls[0]
    assert params["sort_on"] == "updated" and params["offset"] == 0
    assert "min_last_modified" in params and "max_last_modified" in params
    assert "min_created" not in params and "was_canceled" not in params
    second = recover(recovery, page=2, connection=first.json()["connection"], through=first.json()["through"])
    assert second.status_code == 200 and second.json()["nextPage"] is None
    assert recovery.remote.order_calls[-1]["offset"] == 25
    assert recovery.remote.order_calls[-1]["max_last_modified"] == params["max_last_modified"]
    recovery.remote.accounts["etsy"]["connectedAt"] = 2
    assert recover(recovery, page=2, connection=first.json()["connection"], through=first.json()["through"]).status_code == 409
    assert len(recovery.remote.order_calls) == 2


def test_account_change_during_read_prevents_all_local_writes(recovery):
    recovery.remote.after_orders = lambda: recovery.remote.accounts["etsy"].update(connectedAt=2)
    assert recover(recovery).status_code == 409
    assert recovery.db.query(MarketplaceSale).count() == 0


def test_malformed_line_rejects_the_entire_page_before_any_write(recovery):
    recovery.remote.receipts.append(receipt(receipt_id=101))
    del recovery.remote.receipts[1]["transactions"][0]["transaction_id"]
    assert recover(recovery).status_code == 502
    assert recovery.db.query(MarketplaceSale).count() == 0


def test_existing_identity_conflict_is_visible_and_not_reassigned(recovery):
    recovery.db.add(MarketplaceSale(marketplace="etsy", order_id="100", line_id="200", listing_id="556", quantity=3))
    recovery.db.commit()
    result = recover(recovery).json()
    assert result["conflicts"] == 1 and result["recorded"] == 0
    assert recovery.db.query(MarketplaceSale).one().listing_id == "556"


@pytest.mark.parametrize("change", [{"page": 0}, {"page": 4001}, {"page": 2}, {"marketplace": "vendoo"}, {"start": "2000-01-01"}, {"connection": "private-token"}])
def test_invalid_requests_do_not_contact_marketplace(recovery, change):
    assert recover(recovery, **change).status_code == 422
    assert recovery.remote.order_calls == []


def test_cancelled_sale_does_not_mark_the_item_sold(recovery):
    imported = recovery.client.post("/api/etsy/import", json={"listing_id": "555"}).json()
    recovery.remote.receipts[0]["status"] = "canceled"
    recover(recovery)
    result = recovery.client.get(f"/api/conversations/{imported['conversation_id']}/marketplace-listings").json()
    assert result["etsy"]["sold_here"] is False
    alert = recovery.client.get("/api/marketplace-sales").json()["sales"][0]
    assert alert["orderStatus"] == "cancelled" and alert["stockStatus"] == "unknown"


@pytest.fixture
def ebay_recovery(ebay_imported, monkeypatch):
    fixture = ebay_imported
    root = ET.Element("GetOrdersResponse", xmlns=NS["e"])
    ET.SubElement(root, "HasMoreOrders").text = "false"
    order = ET.SubElement(ET.SubElement(root, "OrderArray"), "Order")
    values = {"OrderID": "12-12345-12345", "OrderStatus": "Completed", "PaidTime": "2026-10-25T12:00:00Z",
              "CheckoutStatus/Status": "Complete", "CheckoutStatus/eBayPaymentStatus": "NoPaymentFailure",
              "CheckoutStatus/LastModifiedTime": "2026-10-25T12:30:00Z"}
    for path, value in values.items():
        parent = order
        for name in path.split("/"):
            node = parent.find(name)
            parent = node if node is not None else ET.SubElement(parent, name)
        parent.text = value
    txn = ET.SubElement(ET.SubElement(order, "TransactionArray"), "Transaction")
    ET.SubElement(txn, "OrderLineItemID").text = "123456789012-1234567"
    ET.SubElement(txn, "QuantityPurchased").text = "2"
    ET.SubElement(ET.SubElement(txn, "Item"), "ItemID").text = "123456789012"
    ET.SubElement(order, "BuyerEmail").text = "private@example.test"
    calls = []

    fixture.order_detail = {"orderId": "12-12345-12345", "orderPaymentStatus": "PAID", "lastModifiedDate": "2026-10-25T12:30:00Z"}
    fixture.order = order
    fixture.recovery_calls = calls

    async def request(method, path, *, xml=None, call=None):
        if method == "GET":
            assert path == "/sell/fulfillment/v1/order/12-12345-12345"
            calls.append("getOrder")
            return copy.deepcopy(fixture.order_detail)
        assert (method, path, call) == ("POST", "/ws/api.dll", "GetOrders")
        payload = ET.fromstring(xml)
        assert payload.findtext("e:OrderRole", namespaces=NS) == "Seller"
        assert payload.findtext("e:Pagination/e:EntriesPerPage", namespaces=NS) == "25"
        assert payload.findtext("e:ModTimeFrom", namespaces=NS)
        calls.append(call)
        return {"xml": ET.fromstring(ET.tostring(root))}

    fixture.remote.request = request
    fixture.remote.seller = ("account-one", "1")
    monkeypatch.setattr(order_recovery, "assert_identity", lambda _: None)
    return fixture


def test_ebay_recovery_uses_trading_order_line_ids_and_only_reads(ebay_recovery):
    fixture = ebay_recovery
    result = fixture.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 200, result.text
    sale = fixture.db.query(MarketplaceSale).one()
    assert sale.line_id == "123456789012-1234567" and sale.quantity == 2
    assert fixture.recovery_calls == ["GetOrders", "getOrder"] and "private@example" not in json.dumps(result.json())


def test_legacy_notice_with_a_guessed_line_id_is_reported_as_conflict_not_duplicated(recovery):
    recovery.db.add(MarketplaceSale(marketplace="etsy", order_id="100", line_id="555", listing_id="555", quantity=1))
    recovery.db.commit()
    result = recover(recovery).json()
    assert result["conflicts"] == 1 and result["recorded"] == 0
    assert recovery.db.query(MarketplaceSale).count() == 1


def test_conflicting_duplicate_lines_reject_page_atomically(recovery):
    recovery.remote.receipts.append(receipt())
    recovery.remote.receipts[1]["transactions"][0]["quantity"] = 5
    recovery.remote.total = 2
    assert recover(recovery).status_code == 502
    assert recovery.db.query(MarketplaceSale).count() == 0


def test_initial_page_also_requires_the_account_the_seller_started_with(recovery):
    assert recover(recovery, connected_at="another-connection").status_code == 409
    assert recovery.remote.order_calls == []


def test_quantity_change_invalidates_stock_read_without_editing_local_quantity(recovery):
    from vendoo_studio.services.sale_inventory import stock_after_sale

    conv_id = recovery.client.post("/api/etsy/import", json={"listing_id": "555"}).json()["conversation_id"]
    recover(recovery)
    read_listings(recovery, conv_id)
    assert stock_after_sale(recovery.db, conv_id, "etsy", "555")["status"] == "remaining"
    recovery.remote.receipts[0]["transactions"][0]["quantity"] = 2
    recovery.remote.receipts[0]["updated_timestamp"] += 1
    recover(recovery)
    assert stock_after_sale(recovery.db, conv_id, "etsy", "555")["status"] == "unknown"
    assert ListingRepo(recovery.db).get_revisions(conv_id)[0].listing_json["quantity"] == 3


def test_cancellation_clears_the_direct_sale_flag_without_relisting(recovery):
    recovery.remote.offering["quantity"] = 1
    conv_id = recovery.client.post("/api/etsy/import", json={"listing_id": "555"}).json()["conversation_id"]
    recover(recovery)
    recovery.remote.offering["quantity"] = 0
    read_listings(recovery, conv_id)
    assert recovery.client.get(f"/api/conversations/{conv_id}/marketplace-listings").json()["etsy"]["sold_here"] is True
    recovery.remote.receipts[0]["status"] = "canceled"
    recovery.remote.receipts[0]["updated_timestamp"] += 1
    recover(recovery)
    assert recovery.client.get(f"/api/conversations/{conv_id}/marketplace-listings").json()["etsy"]["sold_here"] is False
    assert ListingRepo(recovery.db).get_revisions(conv_id)[0].listing_json["quantity"] == 1
    recovery.autodelist.assert_not_called()


@pytest.mark.parametrize("payment,status", [("FULLY_REFUNDED", "refunded"), ("PARTIALLY_REFUNDED", "partially_refunded")])
def test_ebay_refunds_use_verified_payment_status_without_changing_notification_identity(ebay_recovery, payment, status):
    ebay_recovery.order_detail["orderPaymentStatus"] = payment
    result = ebay_recovery.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 200, result.text
    sale = ebay_recovery.db.query(MarketplaceSale).one()
    assert sale.order_status == status and sale.line_id == "123456789012-1234567"


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "FUTURE_UNKNOWN_STATE"])
def test_unverified_ebay_payment_does_not_create_a_paid_sale(ebay_recovery, status):
    ebay_recovery.order_detail["orderPaymentStatus"] = status
    result = ebay_recovery.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 200 and result.json()["skippedOrders"] == 1
    assert ebay_recovery.db.query(MarketplaceSale).count() == 0


def test_mismatched_ebay_payment_order_rejects_the_whole_page(ebay_recovery):
    ebay_recovery.order_detail["orderId"] = "another-order"
    result = ebay_recovery.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 502
    assert ebay_recovery.db.query(MarketplaceSale).count() == 0


@pytest.mark.parametrize("state,status", [("Cancelled", "cancelled"), ("CancelPending", "cancellation_pending")])
def test_ebay_cancellations_use_order_state_without_creating_paid_lines(ebay_recovery, state, status):
    ebay_recovery.order.find("OrderStatus").text = state
    result = ebay_recovery.client.post("/api/marketplace-sales/recover", json={"marketplace": "ebay", "connected_at": "1", **dates()})
    assert result.status_code == 200
    assert ebay_recovery.db.query(MarketplaceSale).one().order_status == status
    assert ebay_recovery.recovery_calls == ["GetOrders"]


def test_unreadable_empty_page_with_more_orders_is_not_treated_as_complete(recovery):
    recovery.remote.receipts = []
    assert recover(recovery).status_code == 502
    assert recovery.db.query(MarketplaceSale).count() == 0


@pytest.mark.parametrize("through", ["2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z", "2026-10-26T00:00:00"])
def test_invalid_recovery_boundaries_are_rejected_before_reading_orders(recovery, through):
    assert recover(recovery, through=through).status_code == 422
    assert recovery.remote.order_calls == []


@pytest.mark.parametrize("status", ["cancelled", "refunded", "cancellation_pending"])
def test_reversed_or_pending_order_cannot_trigger_delisting(ebay_imported, monkeypatch, status):
    from vendoo_studio.repositories.queries import ConversationRepo
    from vendoo_studio.services import marketplace_delist, user_settings

    conv = ConversationRepo(ebay_imported.db).create(title="Coat")
    sale = MarketplaceSale(marketplace="ebay", order_id="old", line_id="one", listing_id="123456789012", conversation_id=conv.id, order_status=status)
    ebay_imported.db.add(sale)
    ebay_imported.db.commit()
    monkeypatch.setattr(user_settings, "autodelist_enabled", lambda: True)
    delete = AsyncMock()
    monkeypatch.setattr(marketplace_delist, "delist", delete)
    import asyncio
    result = asyncio.run(marketplace_accounts.autodelist_for_sales([sale.id], session_factory=lambda: ebay_imported.db))
    assert "Nothing was delisted" in result[sale.id]
    delete.assert_not_called()


def test_recovery_respects_ebays_30_day_modification_window(recovery):
    today = datetime.now(UTC).date()
    assert recover(recovery, start=(today - timedelta(days=30)).isoformat()).status_code == 422
    assert recovery.remote.order_calls == []
