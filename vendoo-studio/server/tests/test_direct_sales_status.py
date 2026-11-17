"""Item status and Analytics from Anteroom's own sales; Mark as sold records a sale and never delists."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import marketplace_delist
from vendoo_studio.services.inventory_analytics import inventory_analytics, load_rows
from vendoo_studio.services.item_status import status_for, statuses
from vendoo_studio.services.vendoo_import import parse_notes

EBAY_URL = "https://www.ebay.com/itm/123456789012"
POSH_URL = "https://poshmark.com/listing/Wool-Coat-65a1b2c3d4e5f6a7b8c9d0e1"


@pytest.fixture
def env(monkeypatch):
    from vendoo_studio.services import user_settings

    settings = {}
    monkeypatch.setattr(user_settings, "read_settings", lambda: settings.copy())
    monkeypatch.setattr(user_settings, "update_settings", lambda mutate: mutate(settings))
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


@pytest.fixture
def undo_env(env, migration_engine):  # noqa: F811
    """Exercise sale undo on the selected dialect, not env's SQLite stand-in."""
    from vendoo_studio.services import schema_migrations

    schema_migrations.ensure_schema(migration_engine)
    with Session(bind=migration_engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        yield SimpleNamespace(db=db, client=env.client)


def item(db, conv_id, *, quantity=1, notes=None, price=40, cost=5):
    db.add(Conversation(id=conv_id, title=f"Item {conv_id}", status="active", notes=json.dumps(notes or {"vendooStatus": "active"})))
    db.commit()
    listing_persistence.save_revision(db, conv_id, {"title": "Coat", "price": price, "quantity": quantity, "cost": cost}, "user")


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
    listing_persistence.save_revision(env.db, "tees", {
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
    listing_persistence.save_revision(env.db, "tees", {"title": "Coat", "price": 40, "quantity": 1}, "user")
    assert status(env.db, "tees").status == "active"
    line(env.db, "tees", quantity=2, order="b")
    assert status(env.db, "tees").status == "sold"


def test_quantity_correction_before_a_direct_sale_removes_fictitious_stock(env):
    item(env.db, "corrected", quantity=10, notes={"vendooStatus": "draft"})
    MarketplaceListingRepo(env.db).save("corrected", "ebay", {"state": "published", "listing_url": EBAY_URL})
    listing_persistence.save_revision(env.db, "corrected", {"title": "Coat", "quantity": 1}, "user")
    assert status(env.db, "corrected").quantity == 1
    line(env.db, "corrected", when=datetime.now(UTC) - timedelta(days=2))
    result = status(env.db, "corrected")
    assert (result.status, result.quantity, result.units_sold) == ("sold", 1, 1)


def test_stock_increases_after_a_partial_sale_do_not_restore_an_old_mistake(env):
    item(env.db, "restocked", quantity=10)
    listing_persistence.save_revision(env.db, "restocked", {"quantity": 3}, "user")
    line(env.db, "restocked", order="first")
    listing_persistence.save_revision(env.db, "restocked", {"quantity": 5}, "user")
    listing_persistence.save_revision(env.db, "restocked", {"quantity": 1}, "user")
    assert (status(env.db, "restocked").quantity, status(env.db, "restocked").units_sold) == (5, 1)
    line(env.db, "restocked", quantity=4, order="last")
    assert status(env.db, "restocked").status == "sold"


def test_cancelled_sales_do_not_freeze_an_incorrect_stock_baseline(env):
    item(env.db, "cancelled", quantity=10)
    sale = line(env.db, "cancelled")
    sale.order_status = "cancelled"
    env.db.commit()
    listing_persistence.save_revision(env.db, "cancelled", {"quantity": 1}, "user")
    assert status(env.db, "cancelled").quantity == 1


def test_legacy_sold_mark_keeps_the_corrected_pre_sale_baseline(env):
    item(env.db, "legacy", quantity=10)
    listing_persistence.save_revision(env.db, "legacy", {"quantity": 3}, "user")
    conv = env.db.get(Conversation, "legacy")
    conv.notes = json.dumps({"marketplaceMarks": {"ebay": {"state": "sold", "at": datetime.now(UTC).isoformat()}},
                             "vendooStatus": "active"})
    env.db.commit()
    listing_persistence.save_revision(env.db, "legacy", {"quantity": 1}, "user")
    result = status(env.db, "legacy")
    assert (result.status, result.quantity, result.units_sold) == ("active", 3, 1)


def test_inventory_quantities_do_not_decode_historical_listing_bodies(env):
    from sqlalchemy import event
    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.services.item_status import _quantities

    item(env.db, "tees", quantity=3)
    listing_persistence.save_revision(env.db, "tees", {"quantity": 1, "description": "x" * 100000}, "user")
    item(env.db, "coat", quantity=2)

    # Reject a full-body SELECT even when SQLAlchemy cached its JSON decoder.
    def scalar_only(execution):
        assert "json_extract" not in str(execution.statement.compile(dialect=env.db.bind.dialect)).lower()
        for column in execution.statement.selected_columns:
            assert not (getattr(column, "table", None) is ListingRevision.__table__
                        and column.key == "listing_json"), "Inventory loaded a complete historical listing"

    event.listen(env.db, "do_orm_execute", scalar_only)
    try:
        assert _quantities(env.db, {"tees", "coat"}) == {"tees": 1, "coat": 2}
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


def test_an_imported_marketplace_anteroom_cannot_delist_does_not_keep_a_delisted_item_active(env):
    # Vendoo's import also names Sellwild, which Anteroom neither shows nor delists.
    item(env.db, "tank", notes={"vendooStatus": "active", "vendooMarketplaces": ["poshmark", "sellwild"],
                                "vendooListingUrls": {"poshmark": POSH_URL},
                                "marketplaceDelists": {"poshmark": {"state": "deleted", "listing_id": "65a1b2c3d4e5f6a7b8c9d0e1"}}})
    assert status(env.db, "tank").status == "draft"


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


def test_analytics_missing_sales_data_lists_what_sales_records_flags(env):
    from datetime import date

    from vendoo_studio.services.sales_ledger import sale_records

    item(env.db, "coat")
    line(env.db, "coat", when=datetime(2024, 3, 1))
    item(env.db, "boots")
    line(env.db, "boots", order="o2", financials={"item_revenue": "40", "fees": "5", "shipping_cost": "3",
                                                  "shipping_credit": "2", "refunds": "0", "item_cost": "5"})
    flagged = sale_records(env.db, date(1970, 1, 1), datetime.now(UTC).date(), attention="missing")
    listed = inventory_analytics(env.db, range_id="30d")["incomplete_sales"]
    assert [row["id"] for row in listed] == [row["id"] for row in flagged]
    assert listed == [{"id": flagged[0]["id"], "conversation_id": "coat", "title": "Coat",
                       "missing": ["sale price", "shipping the buyer paid", "fees", "shipping you paid"]}]


def test_the_same_order_in_vendoo_and_the_ledger_counts_once(env):
    when = datetime.now(UTC) - timedelta(days=2)
    item(env.db, "coat", notes={"vendooStatus": "sold", "vendooSale": {"price": 38, "marketplace": "ebay", "fees": 4,
                                                                         "soldAt": when.isoformat()},
                                "vendooDates": {"sold": when.isoformat()}})
    # The ledger has no amounts yet: Vendoo's fill in.
    line(env.db, "coat", when=(when + timedelta(minutes=5)).replace(tzinfo=None))
    sold = [row for row in load_rows(env.db) if row.status == "sold"]
    assert len(sold) == 1 and sold[0].sold_price == 38 and sold[0].fees == 4
    from vendoo_studio.services.sales_records import records

    assert "shipping you paid" in records(env.db)[0]["missing_sale_data"]
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


def test_a_sold_out_item_refuses_another_sale(env):
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    request_id = uuid4().hex
    assert mark_sold(env, "coat", marketplace="ebay", request_id=request_id, item_revenue="9").status_code == 200
    for marketplace in ("ebay", "other"):
        response = mark_sold(env, "coat", marketplace=marketplace, item_revenue="9")
        assert response.status_code == 409, response.text
        assert "already sold" in response.json()["detail"]
    # Resending the same form is still the one sale, not a refusal.
    assert mark_sold(env, "coat", marketplace="ebay", request_id=request_id).status_code == 200
    assert env.db.query(MarketplaceSale).count() == 1


def test_a_multi_unit_item_takes_sales_until_its_units_run_out(env):
    item(env.db, "tees", quantity=3)
    assert mark_sold(env, "tees", quantity=1).status_code == 200
    assert mark_sold(env, "tees", quantity=3).status_code == 409
    assert mark_sold(env, "tees", quantity=2).status_code == 200
    assert mark_sold(env, "tees", quantity=1).status_code == 409
    assert env.db.query(MarketplaceSale).count() == 2


def remove(env, sale_id, revision=None):
    if revision is None:
        revision = env.client.get(f"/api/sales-ledger/{sale_id}").json()["financial_revision"]
    return env.client.delete(f"/api/sales-ledger/{sale_id}", params={"expected_revision": revision})


def test_deleting_a_recorded_sale_puts_the_item_back(env):
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    sale_id = mark_sold(env, "coat", marketplace="ebay", item_revenue="45").json()["sale"]["id"]
    assert status(env.db, "coat").status == "sold"
    response = remove(env, sale_id)
    assert response.status_code == 200, response.text
    assert env.db.query(MarketplaceSale).count() == 0
    # The sold mark Mark as sold made goes with it, so nothing still counts the sale.
    assert status(env.db, "coat").status == "active"
    assert marketplace_delist.listings(env.db, "coat")["ebay"]["sold_here"] is False
    assert env.client.get(f"/api/sales-ledger/{sale_id}").status_code == 404


@pytest.mark.parametrize("notes,restored", [(None, "draft"), ({"vendooStatus": "active"}, "active")])
def test_deleting_the_sale_reopens_the_persisted_inventory_item(undo_env, notes, restored):
    env = undo_env
    env.db.add(Conversation(id="local", title="Local sale undo", status=restored,
                            notes=json.dumps(notes) if notes else None))
    env.db.commit()
    listing_persistence.save_revision(env.db, "local", {"title": "Local sale undo", "price": 45}, "user")
    sale = mark_sold(env, "local", item_revenue="45").json()["sale"]
    # The inventory refresh persists the sale's Sold projection and settlement.
    assert env.client.get("/api/conversations").status_code == 200
    before = env.client.get("/api/conversations/local").json()
    assert before["status"] == "sold" and before["settled_at"] is not None

    response = remove(env, sale["id"], sale["financial_revision"])
    assert response.status_code == 200, response.text
    assert env.client.get(f"/api/sales-ledger/{sale['id']}").status_code == 404
    assert env.db.query(MarketplaceSale).count() == 0
    # A new read must agree with the ledger, not preserve the cached Sold label.
    after = env.client.get("/api/conversations/local").json()
    assert after["status"] == restored
    assert after["settled_at"] is None
    # Undo is not an explicit Keep open instruction for all future sales.
    assert after["unsettled_at"] is None
    sidebar = {row["id"]: row for row in env.client.get("/api/conversations").json()}
    assert sidebar["local"]["status"] == restored
    assert sidebar["local"]["settled_at"] is None
    assert mark_sold(env, "local", item_revenue="45").status_code == 200
    env.client.get("/api/conversations")
    sold_again = env.client.get("/api/conversations/local").json()
    assert sold_again["status"] == "sold" and sold_again["settled_at"]


def test_failed_sale_status_reconciliation_rolls_back_deletion_and_its_sold_mark(undo_env, monkeypatch):
    from vendoo_studio.services import item_status, sales_ledger

    env = undo_env
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    sale = mark_sold(env, "coat", marketplace="ebay", item_revenue="45").json()["sale"]
    env.client.get("/api/conversations")
    before = env.client.get("/api/conversations/coat").json()

    def fail(*_args):
        raise RuntimeError("fixture status read failed")

    monkeypatch.setattr(item_status, "status_for", fail)
    with pytest.raises(RuntimeError, match="fixture status read failed"):
        sales_ledger.delete_seller_sale(env.db, sale["id"], sale["financial_revision"])
    env.db.rollback()
    assert env.db.get(MarketplaceSale, sale["id"]) is not None
    env.db.expire_all()
    conv = env.db.get(Conversation, "coat")
    assert conv.status == "sold" and conv.settled_at.isoformat() == before["settled_at"]
    assert marketplace_delist.listings(env.db, "coat")["ebay"]["sold_here"] is True


@pytest.mark.parametrize("remaining", ["sale", "manual_mark", "imported_sold"])
def test_sale_deletion_preserves_remaining_sold_evidence_and_settlement(undo_env, remaining):
    env = undo_env
    item(env.db, "coat")
    sale = mark_sold(env, "coat", item_revenue="45").json()["sale"]
    if remaining == "sale":
        line(env.db, "coat", order="remaining-paid-order")
    else:
        conv = env.db.get(Conversation, "coat")
        conv.notes = json.dumps({"vendooStatus": "sold"} if remaining == "imported_sold" else {
            "marketplaceMarks": {"poshmark": {"state": "sold", "at": datetime.now(UTC).isoformat()}}})
        env.db.commit()
    env.client.get("/api/conversations")
    before = env.client.get("/api/conversations/coat").json()
    assert before["status"] == "sold" and before["settled_at"]

    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    after = env.client.get("/api/conversations/coat").json()
    assert after["status"] == "sold"
    assert after["settled_at"] == before["settled_at"]
    assert env.db.query(MarketplaceSale).count() == (1 if remaining == "sale" else 0)


def test_deleting_one_unit_reopens_a_still_live_multi_unit_item(undo_env):
    env = undo_env
    item(env.db, "tees", quantity=2, notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    first = mark_sold(env, "tees", quantity=1).json()["sale"]
    mark_sold(env, "tees", quantity=1)
    env.client.get("/api/conversations")
    assert env.client.get("/api/conversations/tees").json()["status"] == "sold"
    assert remove(env, first["id"], first["financial_revision"]).status_code == 200
    after = env.client.get("/api/conversations/tees").json()
    assert after["status"] == "active" and after["settled_at"] is None
    assert status(env.db, "tees").units_sold == 1
    assert marketplace_delist.listings(env.db, "tees")["ebay"]["state"] == "live"


def test_deleting_a_partial_sale_preserves_an_unrelated_manually_finished_draft(undo_env):
    env = undo_env
    env.db.add(Conversation(id="local", title="Finished draft", status="draft"))
    env.db.commit()
    listing_persistence.save_revision(env.db, "local", {"title": "Finished draft", "quantity": 2}, "user")
    sale = mark_sold(env, "local").json()["sale"]
    ConversationRepo(env.db).settle("local")
    before = env.client.get("/api/conversations/local").json()
    assert before["status"] == "draft" and before["settled_at"]
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    after = env.client.get("/api/conversations/local").json()
    assert after["status"] == "draft" and after["settled_at"] == before["settled_at"]


@pytest.mark.parametrize("boundary", ["before_full_sale", "after_full_sale", "between_units"])
def test_sale_undo_preserves_explicit_finish_through_sold_status(undo_env, boundary):
    env = undo_env
    item(env.db, "finished", quantity=2 if boundary == "between_units" else 1)
    if boundary == "between_units":
        assert mark_sold(env, "finished", quantity=1).status_code == 200
    if boundary == "after_full_sale":
        sale = mark_sold(env, "finished").json()["sale"]
        env.client.get("/api/conversations")
        assert env.client.get("/api/conversations/finished").json()["status"] == "sold"

    finished = env.client.post("/api/conversations/finished/settle")
    assert finished.status_code == 200, finished.text
    manual_time = finished.json()["settled_at"]
    assert manual_time is not None
    if boundary != "after_full_sale":
        sale = mark_sold(env, "finished", quantity=1).json()["sale"]
    env.client.get("/api/conversations")
    sold = env.client.get("/api/conversations/finished").json()
    assert sold["status"] == "sold" and sold["settled_at"] == manual_time

    removed = remove(env, sale["id"], sale["financial_revision"])
    assert removed.status_code == 200, removed.text
    after = env.client.get("/api/conversations/finished").json()
    assert after["status"] == "active"
    assert after["settled_at"] == manual_time
    assert env.db.query(MarketplaceSale).count() == (1 if boundary == "between_units" else 0)


def test_keep_open_then_undo_and_resell_preserves_the_explicit_choice(undo_env):
    env = undo_env
    item(env.db, "open")
    sale = mark_sold(env, "open").json()["sale"]
    env.client.get("/api/conversations")
    opened = env.client.post("/api/conversations/open/unsettle")
    assert opened.status_code == 200, opened.text
    kept_open_at = opened.json()["unsettled_at"]
    assert kept_open_at and opened.json()["settled_at"] is None
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    after = env.client.get("/api/conversations/open").json()
    assert after["status"] == "active" and after["settled_at"] is None and after["unsettled_at"] == kept_open_at
    sale = mark_sold(env, "open").json()["sale"]
    env.client.get("/api/conversations")
    sold = env.client.get("/api/conversations/open").json()
    assert sold["status"] == "sold" and sold["settled_at"] is None and sold["unsettled_at"] == kept_open_at
    finished = env.client.post("/api/conversations/open/settle").json()
    assert finished["settled_at"] and finished["unsettled_at"] is None
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    assert env.client.get("/api/conversations/open").json()["settled_at"] == finished["settled_at"]


def test_notes_updates_do_not_erase_explicit_finish_ownership(undo_env):
    env = undo_env
    item(env.db, "notes")
    manual = env.client.post("/api/conversations/notes/settle").json()["settled_at"]
    notes = {"vendooStatus": "active", "sellerNote": "Keep my Finish decision"}
    changed = env.client.patch("/api/conversations/notes", json={"notes": json.dumps(notes)})
    assert changed.status_code == 200, changed.text
    sale = mark_sold(env, "notes").json()["sale"]
    env.client.get("/api/conversations")
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    after = env.client.get("/api/conversations/notes").json()
    assert after["status"] == "active" and after["settled_at"] == manual
    assert parse_notes(after["notes"])["sellerNote"] == notes["sellerNote"]


def test_sale_undo_reloads_another_contexts_explicit_finish(undo_env):
    env = undo_env
    item(env.db, "finished-elsewhere")
    sale = mark_sold(env, "finished-elsewhere").json()["sale"]
    env.client.get("/api/conversations")
    cached = env.db.get(Conversation, "finished-elsewhere")
    assert cached.settled_at and not cached.settled_by_seller
    with Session(bind=env.db.get_bind()) as other:
        finished = ConversationRepo(other).settle(cached.id)
        manual_time = finished.settled_at
        assert finished.settled_by_seller
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    env.db.refresh(cached)
    assert cached.status == "active" and cached.settled_at == manual_time and cached.settled_by_seller


@pytest.mark.sqlite_only("reproduce a write between the SQLite Finish read and write")
def test_explicit_finish_survives_sale_undo_just_before_its_write(undo_env):
    from vendoo_studio.services.sales_ledger import delete_seller_sale

    env = undo_env
    item(env.db, "finish-race")
    sale = mark_sold(env, "finish-race").json()["sale"]
    env.client.get("/api/conversations")
    bind = env.db.get_bind()
    fired = False

    def undo_before_finish_write(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal fired
        if fired or not statement.startswith("UPDATE conversations") or "settled_by_seller" not in statement:
            return
        fired = True
        with Session(bind=bind) as other:
            delete_seller_sale(other, sale["id"], sale["financial_revision"])

    with Session(bind=bind) as manual:
        event.listen(bind, "before_cursor_execute", undo_before_finish_write)
        try:
            finished = ConversationRepo(manual).settle("finish-race")
        finally:
            event.remove(bind, "before_cursor_execute", undo_before_finish_write)
        assert fired
        assert finished.status == "active" and finished.settled_at is not None and finished.settled_by_seller
        assert manual.get(MarketplaceSale, sale["id"]) is None


def test_postgres_sale_undo_waits_for_explicit_finish_ownership(undo_env, monkeypatch):
    from vendoo_studio.services.sales_ledger import delete_seller_sale

    env = undo_env
    if env.db.get_bind().dialect.name != "postgresql":
        pytest.skip("row-lock contention requires Postgres")
    item(env.db, "concurrent-finish")
    sale = mark_sold(env, "concurrent-finish").json()["sale"]
    env.client.get("/api/conversations")
    with Session(bind=env.db.get_bind()) as other:
        # Keep the real Finish update uncommitted while the other process tries
        # to undo the sale, so the parent-row lock is actually contended.
        with monkeypatch.context() as held_transaction:
            held_transaction.setattr(other, "commit", other.flush)
            held = ConversationRepo(other).settle("concurrent-finish")
        assert held.settled_by_seller
        manual_time = held.settled_at
        env.db.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            delete_seller_sale(env.db, sale["id"], sale["financial_revision"])
        env.db.rollback()
        assert env.db.get(MarketplaceSale, sale["id"]) is not None
        other.commit()
    delete_seller_sale(env.db, sale["id"], sale["financial_revision"])
    env.db.expire_all()
    item_after = env.db.get(Conversation, "concurrent-finish")
    assert item_after.status == "active" and item_after.settled_at == manual_time and item_after.settled_by_seller


RACE_DELETE = {"listing_id": "123456789012", "url": EBAY_URL, "at": "2026-11-17T06:00:00+00:00"}
# Delist notes writes: the delete record as a delete starts, and a failure record.
DELIST_WRITES = {
    "record_delete": lambda db: marketplace_delist._record_delete(db, "race", "ebay", {**RACE_DELETE, "state": "deleting"}),
    "store": lambda db: marketplace_delist._record(db, "race", "ebay", {**RACE_DELETE, "state": "failed", "error": "Not deleted."}),
}


def sold_on_mercari_while_listed_on_ebay(env):
    """A seller-recorded Mercari sale and its sold mark, with the eBay listing still there to delist."""
    item(env.db, "race", notes={"vendooStatus": "active", "sellerNote": "keep me",
                                "vendooListingUrls": {"ebay": EBAY_URL, "poshmark": POSH_URL}})
    sale = mark_sold(env, "race", marketplace="mercari").json()["sale"]
    # The mark Mark as sold leaves when the sale alone does not prove the listing ended.
    conv = env.db.get(Conversation, "race")
    conv.notes = json.dumps({**parse_notes(conv.notes), "marketplaceMarks": {
        "mercari": {"state": "sold", "at": datetime.now(UTC).isoformat(), "url": None, "listing_id": None}}})
    env.db.commit()
    env.client.get("/api/conversations")
    env.db.expire_all()
    assert parse_notes(env.db.get(Conversation, "race").notes)["marketplaceMarks"]["mercari"]["state"] == "sold"
    return sale


def assert_undo_kept_by_delist_write(bind, writer, sale):
    with Session(bind=bind) as fresh:
        notes = parse_notes(fresh.get(Conversation, "race").notes)
        # The undone sale's mark stays gone, so the item does not read Sold again.
        assert "mercari" not in notes.get("marketplaceMarks", {})
        assert fresh.get(MarketplaceSale, sale["id"]) is None
        assert status_for(fresh, "race", notes).status != "sold"
        assert statuses(fresh, [("race", notes)])["race"].status != "sold"
        # Unrelated notes and the delete record (the retry target) survive.
        assert notes["sellerNote"] == "keep me"
        assert notes["vendooListingUrls"] == {"ebay": EBAY_URL, "poshmark": POSH_URL}
        record = notes["marketplaceDelists"]["ebay"]
        assert (record["listing_id"], record["state"]) == ("123456789012", "deleting" if writer == "record_delete" else "failed")
        assert marketplace_delist.listings(fresh, "race")["ebay"]["listing_id"] == "123456789012"


@pytest.mark.sqlite_only("reproduce a sale undo between the SQLite delist notes read and write")
@pytest.mark.parametrize("writer", DELIST_WRITES)
def test_a_delist_notes_write_keeps_a_sale_undo_made_just_before_it(undo_env, writer):
    from vendoo_studio.services.sales_ledger import delete_seller_sale

    env = undo_env
    sale = sold_on_mercari_while_listed_on_ebay(env)
    bind = env.db.get_bind()
    fired = False

    def undo_before_notes_write(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal fired
        if fired or not statement.startswith("UPDATE conversations"):
            return
        fired = True
        with Session(bind=bind) as other:
            delete_seller_sale(other, sale["id"], sale["financial_revision"])

    with Session(bind=bind) as delist:
        event.listen(bind, "before_cursor_execute", undo_before_notes_write)
        try:
            DELIST_WRITES[writer](delist)
        finally:
            event.remove(bind, "before_cursor_execute", undo_before_notes_write)
    assert fired
    assert_undo_kept_by_delist_write(bind, writer, sale)


@pytest.mark.parametrize("writer", DELIST_WRITES)
def test_postgres_delist_notes_write_waits_for_a_sale_undo_and_keeps_it(undo_env, monkeypatch, writer):
    import threading
    import time

    from vendoo_studio.services.sales_ledger import delete_seller_sale

    env = undo_env
    if env.db.get_bind().dialect.name != "postgresql":
        pytest.skip("row-lock contention requires Postgres")
    sale = sold_on_mercari_while_listed_on_ebay(env)
    bind = env.db.get_bind()
    failures: list[BaseException] = []

    def write():
        try:
            with Session(bind=bind) as delist:
                DELIST_WRITES[writer](delist)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            failures.append(exc)

    with Session(bind=bind) as undo:
        # Hold the undo uncommitted, so it keeps the item's row lock while the delist writes.
        with monkeypatch.context() as held_transaction:
            held_transaction.setattr(undo, "commit", undo.flush)
            delete_seller_sale(undo, sale["id"], sale["financial_revision"])
        writer_thread = threading.Thread(target=write)
        writer_thread.start()
        deadline = time.monotonic() + 10
        waiting = 0
        with bind.connect() as watch:
            while not waiting and time.monotonic() < deadline:
                waiting = watch.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                            "AND wait_event_type = 'Lock'"))
                time.sleep(0.05)
        assert waiting and writer_thread.is_alive(), "the delist write must wait on the undo's row lock"
        undo.commit()
    writer_thread.join(10)
    assert not writer_thread.is_alive() and not failures, failures
    assert_undo_kept_by_delist_write(bind, writer, sale)


def test_stale_sale_delete_keeps_the_sale_and_sold_settlement(undo_env):
    env = undo_env
    item(env.db, "coat")
    sale = mark_sold(env, "coat", item_revenue="45").json()["sale"]
    env.client.get("/api/conversations")
    before = env.client.get("/api/conversations/coat").json()
    assert sale["financial_revision"] == 1
    assert remove(env, sale["id"], revision=0).status_code == 409
    after = env.client.get("/api/conversations/coat").json()
    assert after["status"] == "sold" and after["settled_at"] == before["settled_at"]
    assert env.db.query(MarketplaceSale).count() == 1


@pytest.mark.parametrize("busy", ["in_progress", "listing"])
def test_sale_delete_reloads_another_contexts_busy_status(undo_env, busy):
    env = undo_env
    item(env.db, "coat")
    sale = mark_sold(env, "coat", item_revenue="45").json()["sale"]
    env.client.get("/api/conversations")
    cached = env.db.get(Conversation, "coat")
    assert cached.status == "sold"
    env.db.commit()
    env.db.refresh(cached)
    with Session(bind=env.db.get_bind()) as other:
        other.execute(update(Conversation).where(Conversation.id == "coat").values(
            status=busy, settled_at=None, unsettled_at=datetime.now(UTC)))
        other.commit()
    assert cached.status == "sold"  # This editor's loaded identity is stale.
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    with Session(bind=env.db.get_bind()) as fresh:
        conv = fresh.get(Conversation, "coat")
        assert conv.status == busy and conv.settled_at is None


def test_postgres_sale_delete_fences_a_concurrent_busy_write(undo_env, monkeypatch):
    from vendoo_studio.services import item_status

    env = undo_env
    engine = env.db.get_bind()
    if engine.dialect.name != "postgresql":
        pytest.skip("Postgres row-lock comparison; SQLite serializes writers")
    item(env.db, "coat")
    sale = mark_sold(env, "coat", item_revenue="45").json()["sale"]
    env.client.get("/api/conversations")
    original = item_status.status_for
    checked = []

    def verify_fence(db, conv_id, notes):
        # A separate process's write cannot slip between derivation and commit.
        with Session(bind=engine) as other:
            other.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError) as failure:
                other.execute(update(Conversation).where(Conversation.id == conv_id).values(status="in_progress"))
            assert failure.value.orig.sqlstate == "55P03"
            checked.append(conv_id)
        return original(db, conv_id, notes)

    monkeypatch.setattr(item_status, "status_for", verify_fence)
    assert remove(env, sale["id"], sale["financial_revision"]).status_code == 200
    assert checked == ["coat"]
    # The lock ends with deletion's commit: subsequent work remains possible.
    with Session(bind=engine) as other:
        other.execute(update(Conversation).where(Conversation.id == "coat").values(status="in_progress"))
        other.commit()
        assert other.get(Conversation, "coat").status == "in_progress"


def test_deleting_a_duplicate_keeps_the_first_sale(env):
    item(env.db, "coat", notes={"vendooStatus": "active", "vendooListingUrls": {"ebay": EBAY_URL}})
    first = mark_sold(env, "coat", marketplace="ebay").json()["sale"]["id"]
    # A second record made before duplicates were refused.
    env.db.add(MarketplaceSale(marketplace="ebay", order_id="seller-" + "b" * 32, line_id="1", quantity=1, order_status="paid",
                               conversation_id="coat", source="seller", state="dismissed"))
    env.db.commit()
    second = env.db.query(MarketplaceSale).filter(MarketplaceSale.id != first).one().id
    assert remove(env, second).status_code == 200
    assert [sale.id for sale in env.db.query(MarketplaceSale)] == [first]
    assert status(env.db, "coat").status == "sold"
    assert marketplace_delist.listings(env.db, "coat")["ebay"]["sold_here"] is True


def test_only_sales_the_seller_recorded_can_be_deleted(env):
    item(env.db, "coat")
    sale = line(env.db, "coat")
    response = remove(env, sale.id)
    assert response.status_code == 409
    assert "marketplace reported" in response.json()["detail"]
    assert env.db.query(MarketplaceSale).count() == 1
    assert remove(env, "missing", 0).status_code == 404


def test_a_sale_edited_elsewhere_is_not_deleted_from_an_old_view(env):
    item(env.db, "coat")
    sale_id = mark_sold(env, "coat", item_revenue="45").json()["sale"]["id"]
    assert remove(env, sale_id, revision=0).status_code == 409
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


@pytest.mark.parametrize("has_sale", [False, True])
def test_undated_revision_still_establishes_quantity(env, has_sale):
    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.services.item_status import _quantities

    item(env.db, "undated", quantity=3)
    env.db.query(ListingRevision).filter_by(conversation_id="undated").update({"created_at": None})
    env.db.commit()
    first_sales = {"undated": datetime.now(UTC)} if has_sale else {}
    assert _quantities(env.db, {"undated"}, first_sales) == {"undated": 3}


def test_listing_rows_carry_the_latest_sale_anteroom_recorded(env):
    item(env.db, "sold-on-ebay", notes={"vendooStatus": "active", "vendooDates": {"sold": ""}})
    line(env.db, "sold-on-ebay", when=datetime(2026, 11, 2, 3, 51, 17))
    item(env.db, "unsold")

    rows = {row["id"]: row for row in env.client.get("/api/conversations").json()}

    assert rows["sold-on-ebay"]["status"] == "sold"
    assert rows["sold-on-ebay"]["sold_at"] == "2026-11-02T03:51:17+00:00"
    assert rows["unsold"]["sold_at"] is None
    assert env.client.get("/api/conversations/sold-on-ebay").json()["sold_at"] == "2026-11-02T03:51:17+00:00"


def _analytics_sale_dates(db, conv_id):
    return sorted(row.sold_at for row in load_rows(db) if row.conversation_id == conv_id and row.sold_at)


def test_a_sold_listing_carries_the_sale_date_analytics_shows(env):
    """Clearing the other marketplaces later is not a newer sale: the Sold tab sorts by the real order."""
    item(env.db, "coat")
    line(env.db, "coat", "ebay", order="o1", order_line="l1", when=datetime(2026, 10, 20, 15, 0),
         financials={"item_revenue": "40"})
    # A check that read the Depop listing sold today, after the eBay sale.
    env.db.add(MarketplaceSale(marketplace="depop", order_id="shop-tee-136d", line_id="shop-tee-136d",
                               listing_id="shop-tee-136d", conversation_id="coat",
                               received_at=datetime(2026, 11, 6, 1, 0)))
    env.db.commit()

    row = next(row for row in env.client.get("/api/conversations").json() if row["id"] == "coat")

    assert row["status"] == "sold"
    assert row["sold_at"] == "2026-10-20T15:00:00+00:00"
    assert [moment.isoformat() for moment in _analytics_sale_dates(env.db, "coat")] == [row["sold_at"]]


def test_a_sale_whose_date_the_seller_cleared_has_no_sale_date(env):
    item(env.db, "coat")
    sale = line(env.db, "coat", "ebay", order="o1", order_line="l1", financials={"item_revenue": "40"})
    sale.sold_at = None
    sale.financials = {**sale.financials, "sold_on": None}
    env.db.commit()

    row = next(row for row in env.client.get("/api/conversations").json() if row["id"] == "coat")

    assert row["status"] == "sold"
    assert row["sold_at"] is None
    assert _analytics_sale_dates(env.db, "coat") == []


def test_sales_records_and_analytics_name_the_same_missing_amounts(env):
    """Both pages say which amounts are missing, counted per sale, from one source."""
    from datetime import date

    from vendoo_studio.services.sales_ledger import list_sales

    item(env.db, "coat")
    # Price, fees and cost recorded; both shipping amounts left blank.
    line(env.db, "coat", financials={"item_revenue": "40", "fees": "5", "item_cost": "5", "refunds": "0"})
    item(env.db, "boots")
    line(env.db, "boots", order="o2", financials={"item_revenue": "40", "fees": "5", "shipping_cost": "3",
                                                  "shipping_credit": "2", "refunds": "0", "item_cost": "5"})
    summary = list_sales(env.db, date(1970, 1, 1), datetime.now(UTC).date())["summary"]
    expected = [{"label": "shipping the buyer paid", "count": 1}, {"label": "shipping you paid", "count": 1}]
    assert summary["missing_records"] == 1
    assert summary["missing_counts"] == expected
    assert inventory_analytics(env.db, range_id="30d")["missing_counts"] == expected


SUPERSEDED_LINKS = [
    ("ebay", "https://www.ebay.com/itm/123456789012", "https://www.ebay.com/itm/987654321098"),
    ("etsy", "https://www.etsy.com/listing/1234567890/fixture", "https://www.etsy.com/listing/9876543210/fixture"),
    ("poshmark", "https://poshmark.com/listing/aaaaaaaaaaaaaaaaaaaaaaaa", "https://poshmark.com/listing/bbbbbbbbbbbbbbbbbbbbbbbb"),
    ("mercari", "https://www.mercari.com/us/item/m12345678901/", "https://www.mercari.com/us/item/m98765432109/"),
    ("depop", "https://www.depop.com/products/fixture-old-123/", "https://www.depop.com/products/fixture-new-456/"),
]


def superseded_item(env, marketplace, old, new):
    """The seller's link to an old listing Anteroom deleted, and a newer listing Anteroom published since."""
    old_id = marketplace_delist.listing_id(marketplace, old)
    item(env.db, "coat", notes={"marketplaceMarks": {marketplace: {"state": "listed", "url": old, "listing_id": old_id,
                                                                   "at": "2026-10-01T10:00:00+00:00"}},
                                "marketplaceDelists": {marketplace: {"state": "deleted", "url": old, "listing_id": old_id,
                                                                     "at": "2026-10-02T10:00:00+00:00"}}})
    MarketplaceListingRepo(env.db).save("coat", marketplace, {"state": "published", "listing_url": new,
                                                              "listing_id": marketplace_delist.listing_id(marketplace, new)})


@pytest.mark.parametrize("marketplace,old,new", SUPERSEDED_LINKS)
def test_deleting_the_newer_listing_retires_the_superseded_seller_link(env, marketplace, old, new):
    superseded_item(env, marketplace, old, new)
    new_id = marketplace_delist.listing_id(marketplace, new)
    assert marketplace_delist.listings(env.db, "coat")[marketplace]["listing_id"] == new_id
    marketplace_delist._deleted(env.db, "coat", marketplace, {"listing_id": new_id, "url": new, "at": "2026-11-01T10:00:00+00:00"})
    env.db.expire_all()
    entry = marketplace_delist.listings(env.db, "coat")[marketplace]
    assert (entry["state"], entry["listing_id"]) == ("deleted", new_id)
    notes = parse_notes(env.db.get(Conversation, "coat").notes)
    assert marketplace not in notes["marketplaceMarks"]
    assert marketplace_delist.removed_marketplaces(notes, {}) == [marketplace]


@pytest.mark.parametrize("marketplace,old,new", SUPERSEDED_LINKS)
def test_a_running_or_failed_delete_of_the_newer_listing_keeps_naming_it(env, marketplace, old, new):
    superseded_item(env, marketplace, old, new)
    new_id = marketplace_delist.listing_id(marketplace, new)
    base = {"listing_id": new_id, "url": new, "at": "2026-11-01T10:00:00+00:00"}
    marketplace_delist._record_delete(env.db, "coat", marketplace, {**base, "state": "deleting"})
    env.db.expire_all()
    assert marketplace not in parse_notes(env.db.get(Conversation, "coat").notes)["marketplaceMarks"]
    entry = marketplace_delist.listings(env.db, "coat")[marketplace]
    assert (entry["state"], entry["listing_id"], entry["url"]) == ("deleting", new_id, new)
    marketplace_delist._record(env.db, "coat", marketplace, {**base, "state": "failed", "error": "Not deleted."})
    env.db.expire_all()
    entry = marketplace_delist.listings(env.db, "coat")[marketplace]
    assert (entry["state"], entry["listing_id"], entry["error"]) == ("failed", new_id, "Not deleted.")
    assert marketplace_delist.removed_marketplaces(parse_notes(env.db.get(Conversation, "coat").notes), {marketplace: new}) == []


@pytest.mark.parametrize("marketplace,old,new", SUPERSEDED_LINKS)
def test_deleting_the_marked_listing_keeps_the_seller_link(env, marketplace, old, new):
    # Retiring a same-ID mark would let an older imported link read as live again.
    item(env.db, "coat", notes={"marketplaceMarks": {marketplace: {"state": "listed", "url": old}},
                                "vendooListingUrls": {marketplace: new}})
    old_id = marketplace_delist.listing_id(marketplace, old)
    marketplace_delist._deleted(env.db, "coat", marketplace, {"listing_id": old_id, "url": old, "at": "2026-11-01T10:00:00+00:00"})
    env.db.expire_all()
    notes = parse_notes(env.db.get(Conversation, "coat").notes)
    assert notes["marketplaceMarks"][marketplace]["url"] == old
    entry = marketplace_delist.listings(env.db, "coat")[marketplace]
    assert (entry["state"], entry["listing_id"]) == ("deleted", old_id)


@pytest.mark.parametrize("marketplace,old,new", SUPERSEDED_LINKS)
def test_a_sale_of_the_newer_listing_counts_despite_the_superseded_seller_link(env, marketplace, old, new):
    superseded_item(env, marketplace, old, new)
    line(env.db, "coat", marketplace, order="new-sale", listing_id=marketplace_delist.listing_id(marketplace, new))
    entry = marketplace_delist.listings(env.db, "coat")[marketplace]
    assert entry["sold_here"] is True and entry["listing_id"] == marketplace_delist.listing_id(marketplace, new)
