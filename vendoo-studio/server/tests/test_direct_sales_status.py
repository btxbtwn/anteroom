"""Item status and Analytics from Anteroom's own sales; Mark as sold records a sale and never delists."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import marketplace_delist
from vendoo_studio.services.inventory_analytics import inventory_analytics, load_rows
from vendoo_studio.services.item_status import status_for, statuses
from vendoo_studio.services.vendoo_import import parse_notes

EBAY_URL = "https://www.ebay.com/itm/123456789012"
POSH_URL = "https://poshmark.com/listing/Wool-Coat-65a1b2c3d4e5f6a7b8c9d0e1"


@pytest.fixture
def env(monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: db
    # Nothing in these flows may delete a listing.
    remote = AsyncMock(side_effect=AssertionError("Recording a sale must never delist"))
    monkeypatch.setattr(marketplace_delist, "_delete_ebay", remote)
    monkeypatch.setattr(marketplace_delist, "_delete_etsy", remote)
    monkeypatch.setattr(marketplace_delist.browser_bridge, "request", remote)
    client = TestClient(app)
    yield SimpleNamespace(db=db, client=client)
    remote.assert_not_called()
    client.close()
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def item(db, conv_id, *, quantity=1, notes=None, price=40, cost=5):
    db.add(Conversation(id=conv_id, title=f"Item {conv_id}", status="active", notes=json.dumps(notes or {"vendooStatus": "active"})))
    db.commit()
    ListingRepo(db).save_revision(conv_id, {"title": "Coat", "price": price, "quantity": quantity, "cost": cost}, "user")


def line(db, conv_id, marketplace="ebay", *, quantity=1, financials=None, order="o1", order_line=None, when=None,
         listing_id="123456789012"):
    # A marketplace order names its order and its line; Chrome's listing-sold
    # reports name the listing as both (see the cleanup tests below).
    sale = MarketplaceSale(marketplace=marketplace, order_id=order, line_id=order_line or f"{order}-line",
                           listing_id=listing_id, quantity=quantity, conversation_id=conv_id,
                           sold_at=when or datetime.now(UTC).replace(tzinfo=None))
    db.add(sale)
    db.commit()
    if financials:
        from vendoo_studio.services.sales_ledger import order_basis

        sale.financials = {"currency": "USD", **financials, "order_basis": order_basis(sale)}
        db.commit()
    return sale


def status(db, conv_id):
    conv = db.get(Conversation, conv_id)
    db.refresh(conv)
    return status_for(db, conv_id, parse_notes(conv.notes))


def test_a_direct_sale_turns_a_vendoo_active_item_sold(env):
    item(env.db, "coat")
    assert status(env.db, "coat").status == "active"
    line(env.db, "coat")
    assert status(env.db, "coat").status == "sold"
    ConversationRepo(env.db).reconcile_job_statuses()
    assert env.db.get(Conversation, "coat").status == "sold"


def test_a_vendoo_sale_record_is_not_a_detected_sale(env):
    """An imported claim waits for the marketplace itself; Anteroom records no sale from Vendoo."""
    when = datetime.now(UTC) - timedelta(days=1)
    item(env.db, "depop", notes={
        "vendooStatus": "active",
        "vendooMarketplaces": ["depop"],
        "vendooSale": {"marketplace": "depop", "soldAt": when.isoformat(), "price": 38, "fees": 4},
        "vendooDates": {"soldByMarketplace": {"depop": when.isoformat()}},
    })
    result = status(env.db, "depop")
    assert (result.status, result.units_sold) == ("active", 0)
    ConversationRepo(env.db).reconcile_job_statuses()
    assert env.db.get(Conversation, "depop").status == "active"
    assert env.db.query(MarketplaceSale).count() == 0


def test_a_failed_send_does_not_hide_a_sale(env):
    item(env.db, "coat")
    line(env.db, "coat")
    now = datetime.now(UTC)
    env.db.add(Job(conversation_id="coat", approved_revision_id="rev", listing_snapshot={"title": "Coat"},
                   status="failed", created_at=now, updated_at=now))
    env.db.commit()
    ConversationRepo(env.db).reconcile_job_statuses()
    assert env.db.get(Conversation, "coat").status == "sold"


def test_an_old_send_does_not_keep_a_sale_active(env):
    item(env.db, "coat")
    line(env.db, "coat")
    conv = env.db.get(Conversation, "coat")
    conv.updated_at = datetime.now(UTC)
    older = datetime.now(UTC) - timedelta(hours=1)
    env.db.add(Job(conversation_id="coat", approved_revision_id="rev", listing_snapshot={"title": "Coat"},
                   status="completed", created_at=older, updated_at=older))
    env.db.commit()
    ConversationRepo(env.db).reconcile_job_statuses()
    assert env.db.get(Conversation, "coat").status == "sold"


def test_empty_inventory_status_does_not_query_history(env):
    queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    engine = env.db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert statuses(env.db, []) == {}
        assert queries == []
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("quantity", [3, "3", 3.0])
def test_quantity_history_survives_large_unrelated_listing_fields(env, quantity):
    item(env.db, "tees", quantity=quantity)
    line(env.db, "tees")
    ListingRepo(env.db).save_revision("tees", {
        "title": "Coat", "quantity": 1, "description": "Long description " * 10000,
    }, "user")
    partial = status(env.db, "tees")
    assert (partial.status, partial.quantity, partial.units_sold) == ("active", 3, 1)


def test_a_multi_unit_item_is_partly_sold_until_every_unit_sells(env):
    item(env.db, "tees", quantity=3)
    line(env.db, "tees", quantity=1, order="a")
    partial = status(env.db, "tees")
    assert (partial.status, partial.units_sold, partial.quantity, partial.partially_sold) == ("active", 1, 3, True)
    # Lowering the quantity afterwards does not make that one sale the last unit.
    ListingRepo(env.db).save_revision("tees", {"title": "Coat", "price": 40, "quantity": 1}, "user")
    assert status(env.db, "tees").status == "active"
    line(env.db, "tees", quantity=2, order="b")
    assert status(env.db, "tees").status == "sold"


def test_quantity_correction_before_a_direct_sale_removes_fictitious_stock(env):
    item(env.db, "corrected", quantity=10, notes={"vendooStatus": "draft"})
    MarketplaceListingRepo(env.db).save("corrected", "ebay", {"state": "published", "listing_url": EBAY_URL})
    ListingRepo(env.db).save_revision("corrected", {"title": "Coat", "quantity": 1}, "user")
    assert status(env.db, "corrected").quantity == 1
    line(env.db, "corrected", when=datetime.now(UTC) - timedelta(days=2))
    result = status(env.db, "corrected")
    assert (result.status, result.quantity, result.units_sold) == ("sold", 1, 1)


def test_stock_increases_after_a_partial_sale_do_not_restore_an_old_mistake(env):
    item(env.db, "restocked", quantity=10)
    ListingRepo(env.db).save_revision("restocked", {"quantity": 3}, "user")
    line(env.db, "restocked", order="first")
    ListingRepo(env.db).save_revision("restocked", {"quantity": 5}, "user")
    ListingRepo(env.db).save_revision("restocked", {"quantity": 1}, "user")
    assert (status(env.db, "restocked").quantity, status(env.db, "restocked").units_sold) == (5, 1)
    line(env.db, "restocked", quantity=4, order="last")
    assert status(env.db, "restocked").status == "sold"


def test_cancelled_sales_do_not_freeze_an_incorrect_stock_baseline(env):
    item(env.db, "cancelled", quantity=10)
    sale = line(env.db, "cancelled")
    sale.order_status = "cancelled"
    env.db.commit()
    ListingRepo(env.db).save_revision("cancelled", {"quantity": 1}, "user")
    assert status(env.db, "cancelled").quantity == 1


def test_legacy_sold_mark_keeps_the_corrected_pre_sale_baseline(env):
    item(env.db, "legacy", quantity=10)
    ListingRepo(env.db).save_revision("legacy", {"quantity": 3}, "user")
    conv = env.db.get(Conversation, "legacy")
    conv.notes = json.dumps({"marketplaceMarks": {"ebay": {"state": "sold", "at": datetime.now(UTC).isoformat()}},
                             "vendooStatus": "active"})
    env.db.commit()
    ListingRepo(env.db).save_revision("legacy", {"quantity": 1}, "user")
    result = status(env.db, "legacy")
    assert (result.status, result.quantity, result.units_sold) == ("active", 3, 1)


def test_inventory_quantities_do_not_decode_historical_listing_bodies(env):
    from sqlalchemy import event
    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.services.item_status import _quantities

    item(env.db, "tees", quantity=3)
    ListingRepo(env.db).save_revision("tees", {"quantity": 1, "description": "x" * 100000}, "user")
    item(env.db, "coat", quantity=2)

    # Reject a full-body SELECT even when SQLAlchemy cached its JSON decoder.
    def scalar_only(execution):
        assert "json_extract" not in str(execution.statement.compile(dialect=env.db.bind.dialect)).lower()
        for column in execution.statement.selected_columns:
            assert not (getattr(column, "table", None) is ListingRevision.__table__
                        and column.key == "listing_json"), "Inventory loaded a complete historical listing"

    event.listen(env.db, "do_orm_execute", scalar_only)
    try:
        assert _quantities(env.db, None) == {"tees": 1, "coat": 2}
        assert _quantities(env.db, {"tees"}) == {"tees": 1}
        assert _quantities(env.db, set()) == {}
    finally:
        event.remove(env.db, "do_orm_execute", scalar_only)

    if env.db.bind.dialect.name == "sqlite":
        from sqlalchemy import text

        plan = env.db.execute(text("EXPLAIN QUERY PLAN SELECT conversation_id, inventory_quantity, created_at "
                                   "FROM listing_revisions ORDER BY created_at")).all()
        assert any("COVERING INDEX ix_listing_revisions_quantity_history" in row[-1] for row in plan)


def test_refunded_or_cancelled_orders_do_not_sell_the_item(env):
    item(env.db, "coat")
    sale = line(env.db, "coat")
    sale.order_status = "cancelled"
    env.db.commit()
    assert status(env.db, "coat").status == "active"


def test_a_direct_listing_reads_active_and_delisting_everywhere_reads_draft(env):
    item(env.db, "draft", notes={"vendooStatus": "draft"})
    MarketplaceListingRepo(env.db).save("draft", "ebay", {"state": "published", "listing_url": EBAY_URL})
    assert status(env.db, "draft").status == "active"
    item(env.db, "gone", notes={"vendooStatus": "active", "vendooMarketplaces": ["poshmark"], "vendooListingUrls": {"poshmark": POSH_URL},
                                "marketplaceDelists": {"poshmark": {"state": "deleted", "listing_id": "65a1b2c3d4e5f6a7b8c9d0e1"}}})
    assert status(env.db, "gone").status == "draft"


def test_analytics_counts_ledger_sales_with_their_recorded_amounts(env):
    item(env.db, "coat")
    line(env.db, "coat", financials={"item_revenue": "40", "fees": "5", "shipping_cost": "3", "shipping_credit": "2", "refunds": "0"})
    sold = [row for row in load_rows(env.db) if row.status == "sold"]
    assert len(sold) == 1 and sold[0].sold_price == 40 and sold[0].fees == 5 and sold[0].marketplace == "ebay"
    payload = inventory_analytics(env.db, range_id="30d")
    assert payload["sales"]["count"] == 1 and payload["sales"]["revenue"] == 40
    # 40 + 2 shipping paid - 5 fees - 3 label - 5 cost.
    assert payload["sales"]["profit"] == 29
    assert payload["inventory"]["sold"] == 1 and payload["inventory"]["active"] == 0


def test_the_same_order_in_vendoo_and_the_ledger_counts_once(env):
    when = datetime.now(UTC) - timedelta(days=2)
    item(env.db, "coat", notes={"vendooStatus": "sold", "vendooSale": {"price": 38, "marketplace": "ebay", "fees": 4,
                                                                         "soldAt": when.isoformat(), "missing": ["shipping cost"]},
                                "vendooDates": {"sold": when.isoformat()}})
    # The ledger has no amounts yet: Vendoo's fill in.
    line(env.db, "coat", when=(when + timedelta(minutes=5)).replace(tzinfo=None))
    sold = [row for row in load_rows(env.db) if row.status == "sold"]
    assert len(sold) == 1 and sold[0].sold_price == 38 and sold[0].fees == 4
    assert sold[0].missing_sale_details == ("shipping cost",)
    assert inventory_analytics(env.db, range_id="30d")["sales"]["count"] == 1


def test_recorded_ledger_amounts_win_over_vendoo_and_a_sale_elsewhere_is_its_own_order(env):
    when = datetime.now(UTC) - timedelta(days=2)
    item(env.db, "coat", notes={"vendooStatus": "sold", "vendooSale": {"price": 38, "marketplace": "ebay", "soldAt": when.isoformat()}})
    line(env.db, "coat", financials={"item_revenue": "41", "fees": "4.5"}, when=when.replace(tzinfo=None))
    rows = [row for row in load_rows(env.db) if row.status == "sold"]
    assert [(row.marketplace, row.sold_price) for row in rows] == [("ebay", 41)]
    line(env.db, "coat", "etsy", order="etsy-order", listing_id="555")
    assert sorted(row.marketplace for row in load_rows(env.db) if row.status == "sold") == ["ebay", "etsy"]


def test_a_partly_sold_item_is_active_inventory_plus_its_sales(env):
    item(env.db, "tees", quantity=3)
    line(env.db, "tees", financials={"item_revenue": "20"})
    rows = [row for row in load_rows(env.db) if row.conversation_id == "tees"]
    assert sorted(row.status for row in rows) == ["active", "sold"]
    payload = inventory_analytics(env.db, range_id="30d")
    assert payload["inventory"]["active"] == 1 and payload["sales"]["revenue"] == 20


def test_unlinked_ledger_lines_are_not_counted(env):
    item(env.db, "coat")
    line(env.db, None, "depop", order="unknown", listing_id="x")
    assert inventory_analytics(env.db, range_id="30d")["sales"]["count"] == 0


def mark_sold(env, conv_id, **body):
    payload = {"marketplace": "other", "sold_on": datetime.now(UTC).date().isoformat(), "currency": "USD",
               "request_id": uuid4().hex, **body}
    return env.client.post(f"/api/conversations/{conv_id}/marketplace-listings/sold", json=payload)


def test_mark_as_sold_in_person_records_amounts_and_offers_to_delist_the_rest(env):
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL, "poshmark": POSH_URL}})
    response = mark_sold(env, "coat", item_revenue="30", fees="0", shipping_cost="0")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sold" and sorted(body["delist_offer"]) == ["ebay", "poshmark"]
    assert body["sale"]["marketplace"] == "other" and body["sale"]["source"] == "seller"
    assert body["sale"]["financials"]["item_revenue"] == "30"
    # Offered, not done: both listings are still live.
    assert all(entry["state"] == "live" for entry in body["listings"].values())
    assert inventory_analytics(env.db, range_id="30d")["sales"]["revenue"] == 30
    assert env.client.get("/api/sales-ledger", params={"start": "2026-01-01", "end": datetime.now(UTC).date().isoformat(),
                                                        "marketplace": "other"}).json()["summary"]["lines"] == 1


def test_mark_as_sold_on_a_marketplace_protects_that_listing_from_delist(env):
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL, "poshmark": POSH_URL}})
    body = mark_sold(env, "coat", marketplace="ebay", item_revenue="45").json()
    assert body["listings"]["ebay"]["sold_here"] is True
    assert body["delist_offer"] == ["poshmark"]
    assert env.db.query(MarketplaceSale).one().listing_id == "123456789012"


def test_the_same_submission_records_one_sale(env):
    item(env.db, "coat")
    request_id = uuid4().hex
    assert mark_sold(env, "coat", request_id=request_id).status_code == 200
    assert mark_sold(env, "coat", request_id=request_id).status_code == 200
    assert env.db.query(MarketplaceSale).count() == 1


def test_a_partial_sale_offers_no_delist_and_marks_nothing(env):
    item(env.db, "tees", quantity=3, notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    body = mark_sold(env, "tees", marketplace="ebay").json()
    assert (body["status"], body["units_sold"], body["delist_offer"]) == ("active", 1, [])
    assert body["listings"]["ebay"]["sold_here"] is False


@pytest.mark.parametrize("change", [{"marketplace": "grailed"}, {"sold_on": "2999-01-01"}, {"quantity": 0},
                                    {"request_id": "short"}, {"item_revenue": "-4"}])
def test_invalid_sales_are_refused(env, change):
    item(env.db, "coat")
    assert mark_sold(env, "coat", **change).status_code in (400, 422)
    assert env.db.query(MarketplaceSale).count() == 0


def test_analytics_counts_a_sold_item_once_however_many_sales_it_had(env):
    item(env.db, "tees", quantity=2)
    line(env.db, "tees", quantity=1, order="a")
    line(env.db, "tees", quantity=1, order="b")
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 2
    assert payload["inventory"]["sold"] == 1
    assert payload["inventory"]["active"] == 0


def test_analytics_counts_a_partly_sold_item_as_active_not_sold(env):
    item(env.db, "tees", quantity=3)
    line(env.db, "tees", quantity=1)
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 1
    assert payload["inventory"]["active"] == 1
    assert payload["inventory"]["sold"] == 0


def test_a_listing_marked_sold_elsewhere_is_not_a_second_sale(env):
    """The seller clearing other marketplaces after a sale is not another sale."""
    item(env.db, "coat")
    line(env.db, "coat", "ebay", order="o1", order_line="l1", listing_id="123456789012",
         financials={"item_revenue": "40", "fees": "5"})
    # What a Chrome check records for a listing it reads sold: the listing as
    # its own order, no amounts, no order date.
    env.db.add(MarketplaceSale(marketplace="depop", order_id="shop-tee-136d", line_id="shop-tee-136d",
                               listing_id="shop-tee-136d", conversation_id="coat"))
    env.db.commit()
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 1
    assert payload["sales"]["revenue"] == 40
    assert payload["inventory"]["sold"] == 1
    assert env.db.query(MarketplaceSale).count() == 2


def test_two_real_orders_on_one_item_both_count(env):
    item(env.db, "tees", quantity=2)
    line(env.db, "tees", "ebay", order="o1", order_line="l1", financials={"item_revenue": "20"})
    line(env.db, "tees", "depop", order="o2", order_line="l2", listing_id="shop-tee-136d",
         financials={"item_revenue": "30"})
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 2
    assert payload["sales"]["revenue"] == 50


def test_cleanup_reports_on_other_marketplaces_count_one_sale(env):
    """A sold item with only "listing reads sold" reports is one sale, not one per marketplace."""
    item(env.db, "coat")
    for marketplace, listing_id in (("depop", "shop-tee-136d"), ("poshmark", "65a1b2c3d4e5f6a7b8c9d0e1")):
        env.db.add(MarketplaceSale(marketplace=marketplace, order_id=listing_id, line_id=listing_id,
                                   listing_id=listing_id, conversation_id="coat"))
    env.db.commit()
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 1
    assert payload["inventory"]["sold"] == 1


def test_a_multi_unit_item_keeps_one_inferred_sale_per_unit(env):
    item(env.db, "tees", quantity=2)
    for marketplace, listing_id in (("depop", "shop-tee-136d"), ("poshmark", "65a1b2c3d4e5f6a7b8c9d0e1")):
        env.db.add(MarketplaceSale(marketplace=marketplace, order_id=listing_id, line_id=listing_id,
                                   listing_id=listing_id, conversation_id="tees"))
    env.db.commit()
    assert inventory_analytics(env.db, range_id="all")["sales"]["count"] == 2


def test_real_orders_are_never_capped(env):
    item(env.db, "coat")
    line(env.db, "coat", "ebay", order="o1", order_line="l1", financials={"item_revenue": "20"})
    line(env.db, "coat", "etsy", order="o2", order_line="l2", financials={"item_revenue": "30"})
    payload = inventory_analytics(env.db, range_id="all")
    assert payload["sales"]["count"] == 2
    assert payload["sales"]["revenue"] == 50
