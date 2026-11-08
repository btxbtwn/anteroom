"""An eBay or Etsy order line replaces the Vendoo copy or Mark as sold that stood in for it.

The marketplace's record is kept; the seller's cost of goods and any amounts
they typed carry over. Nothing on any marketplace changes.
"""
import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import order_amounts, order_recovery, relay_sales
from vendoo_studio.services.sales_ledger import financial_values, order_basis

LISTING = "1234567890"
ORDER = "4211998766"
SOLD = datetime(2026, 3, 2, 15, 30)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(Conversation(id="blouse", title="Casual Corner Paisley Blouse", notes=json.dumps({
            "vendooListingUrls": {"etsy": f"https://www.etsy.com/listing/{LISTING}/casual-corner-paisley-blouse"},
        })))
        session.commit()
        yield session
    engine.dispose()


def vendoo_copy(db, marketplace="etsy", sold_at=datetime(2026, 3, 2), **financials):
    sale = MarketplaceSale(id="vendoo-blouse", marketplace=marketplace, order_id="Vendoo import", line_id="blouse",
                           quantity=1, order_status="paid", source="vendoo", state="dismissed",
                           conversation_id="blouse", sold_at=sold_at, received_at=datetime(2026, 1, 1), financial_revision=1)
    db.add(sale)
    db.flush()
    sale.financials = {"currency": "USD", "item_revenue": "30", "fees": "3.5", "item_cost": "4", "shipping_cost": "5",
                       "shipping_credit": "0", "refunds": "0", "order_basis": order_basis(sale),
                       "updated_at": "2026-03-03T00:00:00+00:00", "source": "vendoo", **financials}
    db.commit()
    return sale


def etsy_line(db, order_id=ORDER, sold_at=SOLD, conversation_id=None):
    sale = MarketplaceSale(marketplace="etsy", order_id=order_id, line_id=f"t-{order_id}", listing_id=LISTING, quantity=1,
                           conversation_id=conversation_id, sold_at=sold_at)
    db.add(sale)
    db.commit()
    return sale


def only_sale(db) -> MarketplaceSale:
    db.expire_all()
    sale = db.query(MarketplaceSale).one()
    assert sale.source == "marketplace"
    assert sale.order_id == ORDER
    return sale


def test_etsy_webhook_sale_replaces_the_vendoo_copy_and_keeps_its_cost(db):
    vendoo_copy(db)
    sale = relay_sales.record_sale(db, marketplace="etsy", order_id=ORDER, line_id="t1", listing_id=LISTING,
                                   quantity=1, sold_at=SOLD)
    assert sale is not None
    kept = only_sale(db)
    assert kept.conversation_id == "blouse"
    assert kept.sold_at == SOLD
    # Until Etsy's amounts arrive, Vendoo's stand in; Etsy then overwrites what it reports.
    assert kept.financials["item_cost"] == "4"
    assert kept.financials["fees"] == "3.5"
    assert order_amounts.needs_amounts("etsy", [kept])
    money = order_amounts.OrderMoney("USD", {"item_revenue": Decimal("32"), "shipping_credit": Decimal("6"),
                                             "refunds": Decimal("0"), "fees": None, "shipping_cost": None},
                                     {"t1": Decimal("32")}, {"t1": Decimal("32")})
    assert order_amounts.store(db, "etsy", ORDER, money, datetime(2026, 3, 4)) == 1
    kept = only_sale(db)
    assert kept.financials["source"] == "marketplace"
    assert kept.financials["item_revenue"] == "32"
    assert kept.financials["item_cost"] == "4"
    assert kept.financials["fees"] == "3.5"
    assert financial_values(kept)[1] is False


def test_webhook_sale_keeps_amounts_and_sale_date_the_seller_typed(db):
    vendoo_copy(db, sold_at=datetime(2026, 3, 1, 12), source="seller", sold_on="2026-03-01", item_revenue="28")
    relay_sales.record_sale(db, marketplace="etsy", order_id=ORDER, line_id="t1", listing_id=LISTING,
                            quantity=1, sold_at=SOLD)
    kept = only_sale(db)
    assert kept.financials["source"] == "seller"
    assert kept.financials["item_revenue"] == "28"
    assert kept.financials["item_cost"] == "4"
    assert kept.sold_at == datetime(2026, 3, 1, 12)
    assert financial_values(kept)[1] is False
    assert kept.financial_revision == 1


def test_recovered_order_line_replaces_the_seller_mark_as_sold(db):
    db.add(MarketplaceSale(marketplace="etsy", order_id="seller-" + "a" * 32, line_id="1", quantity=1, order_status="paid",
                           conversation_id="blouse", source="seller", state="dismissed", sold_at=datetime(2026, 3, 2, 12),
                           financials={"currency": "USD", "item_revenue": "30", "item_cost": "4", "source": "seller"}))
    db.commit()
    line = order_recovery.OrderLine(ORDER, "t1", LISTING, 1, "paid", SOLD, SOLD)
    assert order_recovery._save(db, "etsy", [line], datetime(2026, 3, 4))["recorded"] == 1
    kept = only_sale(db)
    assert kept.financials["item_cost"] == "4"
    assert kept.financials["source"] == "seller"


def test_linking_an_earlier_etsy_sale_to_its_item_replaces_the_vendoo_copy(db):
    etsy_line(db)
    vendoo_copy(db)
    assert relay_sales.reconcile_unmatched(db, marketplace="etsy") == 1
    kept = only_sale(db)
    assert kept.conversation_id == "blouse"
    assert kept.financials["item_cost"] == "4"


def test_a_vendoo_copy_on_another_marketplace_is_kept(db):
    vendoo_copy(db, marketplace="poshmark")
    relay_sales.record_sale(db, marketplace="etsy", order_id=ORDER, line_id="t1", listing_id=LISTING,
                            quantity=1, sold_at=SOLD)
    assert sorted(row.source for row in db.query(MarketplaceSale).all()) == ["marketplace", "vendoo"]


def test_one_vendoo_copy_is_replaced_once_and_both_etsy_orders_stay(db):
    etsy_line(db, order_id="111", sold_at=datetime(2025, 6, 1))
    etsy_line(db, order_id=ORDER)
    vendoo_copy(db)
    assert relay_sales.reconcile_unmatched(db, marketplace="etsy") == 2
    db.expire_all()
    rows = {row.order_id: row for row in db.query(MarketplaceSale).all()}
    assert set(rows) == {"111", ORDER}
    assert rows[ORDER].financials["item_cost"] == "4"
    assert rows["111"].financials is None
