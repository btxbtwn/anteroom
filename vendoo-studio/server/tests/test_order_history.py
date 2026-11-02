import asyncio
import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import order_history, user_settings
from vendoo_studio.services.order_history import (
    MAX_ORDERS,
    HistoryError,
    Line,
    Order,
    line_amounts,
    parse_orders,
)


def test_oversized_read_is_rejected_before_cleanup_can_run():
    with pytest.raises(HistoryError, match="too many orders"):
        parse_orders("depop", {"orders": [{}] * (MAX_ORDERS + 1)})


def test_bundle_amounts_preserve_totals_to_the_cent():
    order = Order("123", "paid", False, datetime(2026, 11, 1), "USD",
                  {field: Decimal("1.00") for field in
                   ("item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost")},
                  tuple(Line(str(i), str(i), (), Decimal("1"), None) for i in range(3)))
    amounts = line_amounts(order)
    for field in order.totals:
        assert sum(row[field] for row in amounts) == Decimal("1.00")
        assert all(row[field].as_tuple().exponent == -2 for row in amounts)


@pytest.fixture
def history_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = {}
    monkeypatch.setattr(user_settings, "read_settings", lambda: settings.copy())
    monkeypatch.setattr(user_settings, "update_settings", lambda mutate: mutate(settings))
    monkeypatch.setattr(order_history, "bridge_can_read", lambda: True)
    with factory() as db:
        db.add(Conversation(id="coat", title="Coat", notes=json.dumps({
            "vendooListingUrls": {"depop": "https://www.depop.com/products/shop-coat-136d/"},
        })))
        db.commit()
    yield factory
    engine.dispose()


def placeholder(db):
    sale = MarketplaceSale(marketplace="depop", order_id="shop-coat-136d", line_id="shop-coat-136d",
                           listing_id="shop-coat-136d", conversation_id="coat")
    db.add(sale)
    db.commit()
    return sale.id


@pytest.mark.parametrize("partial", [{"more": True}, {"unreadable": 1}])
def test_partial_history_preserves_old_listing_reports(history_db, monkeypatch, partial):
    with history_db() as db:
        sale_id = placeholder(db)
    monkeypatch.setattr("vendoo_studio.services.browser_bridge.request", AsyncMock(return_value={
        "ok": True, "orders": [], **partial,
    }))
    result = asyncio.run(order_history.sync("depop", session_factory=history_db))
    assert result["ok"] is True
    with history_db() as db:
        assert db.get(MarketplaceSale, sale_id) is not None


def test_history_freezes_vendoo_sale_before_clearing_listing_reports(history_db, monkeypatch):
    with history_db() as db:
        placeholder(db)
        conv = db.get(Conversation, "coat")
        notes = json.loads(conv.notes)
        notes.update(vendooStatus="sold", vendooSale={"marketplace": "depop", "price": 20, "fees": 2})
        conv.notes = json.dumps(notes)
        db.commit()
    monkeypatch.setattr("vendoo_studio.services.browser_bridge.request", AsyncMock(return_value={"ok": True, "orders": []}))
    assert asyncio.run(order_history.sync("depop", session_factory=history_db))["ok"] is True
    with history_db() as db:
        sale = db.query(MarketplaceSale).one()
        assert sale.source == "vendoo"
        assert sale.financials["item_revenue"] == "20"
        assert sale.financials["fees"] == "2"


def test_new_order_replaces_only_one_manual_sale_and_keeps_its_date(history_db):
    with history_db() as db:
        for index in (1, 2):
            db.add(MarketplaceSale(marketplace="depop", order_id=f"manual-{index}", line_id="1",
                                   conversation_id="coat", source="seller", sold_at=datetime(2026, 10, index),
                                   financials={"source": "seller", "sold_on": f"2026-10-0{index}", "item_cost": "5"}))
        db.commit()
        order = Order("receipt", "paid", False, datetime(2026, 10, 2, 12), "USD",
                      dict.fromkeys(order_history.TOTALS), (Line("line", "shop-coat-136d", (), None, None),))
        result = order_history.save_orders(db, "depop", [order], datetime(2026, 11, 1))
        assert result["replaced"] == 1
        assert db.query(MarketplaceSale).count() == 2
        assert db.query(MarketplaceSale).filter_by(order_id="manual-1").one()
        sale = db.query(MarketplaceSale).filter_by(order_id="receipt").one()
        assert sale.financials["sold_on"] == "2026-10-02"
        assert sale.sold_at == datetime(2026, 10, 2)
        repeated = order_history.save_orders(db, "depop", [order], datetime(2026, 11, 1))
        assert repeated["recorded"] == repeated["replaced"] == 0
        assert db.query(MarketplaceSale).count() == 2


def test_order_replaces_a_listing_report_without_losing_corrected_amounts(history_db):
    with history_db() as db:
        sale_id = placeholder(db)
        old = db.get(MarketplaceSale, sale_id)
        old.financials = {"source": "seller", "item_revenue": "23.50", "fees": "2.75"}
        db.commit()
        order = Order("receipt", "paid", False, datetime(2026, 10, 2), "USD",
                      dict.fromkeys(order_history.TOTALS), (Line("line", "shop-coat-136d", (), None, None),))
        result = order_history.save_orders(db, "depop", [order], datetime(2026, 11, 1))
        assert result["replaced"] == 1
        sale = db.query(MarketplaceSale).one()
        assert sale.order_id == "receipt"
        assert sale.financials["item_revenue"] == "23.50"
        assert sale.financials["fees"] == "2.75"
