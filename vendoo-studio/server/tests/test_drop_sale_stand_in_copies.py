"""The upgrade that removes Vendoo and Mark as sold copies of sales a marketplace order already records."""
from datetime import datetime

from alembic import command
from sqlalchemy import MetaData, Table, select

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.models.ownership import LOCAL_USER_ID
from vendoo_studio.services.sales_ledger import order_basis

REVISION = "a7d3e5c1b9f4"
OTHER = "11111111-1111-1111-1111-111111111111"


def _sale(row_id, item, marketplace, source, sold_at, user=LOCAL_USER_ID, financials=None, order_id=None):
    return {"id": row_id, "user_id": user, "conversation_id": item, "marketplace": marketplace, "source": source,
            "order_id": order_id or row_id, "line_id": "1", "listing_id": None if source != "marketplace" else "42",
            "quantity": 1, "order_status": "paid", "state": "dismissed", "sold_at": sold_at,
            "received_at": datetime(2026, 1, 1), "financials": financials, "financial_revision": 1 if financials else 0}


def test_upgrade_keeps_the_marketplace_record_with_the_sellers_amounts(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "cd33c540e489")
    vendoo = {"currency": "USD", "item_revenue": "30", "fees": "3.5", "item_cost": "4", "order_basis": "x", "source": "vendoo"}
    typed = {**vendoo, "source": "seller", "item_revenue": "28", "sold_on": "2026-03-01"}
    etsy = {"currency": "USD", "item_revenue": "32", "shipping_credit": "6", "refunds": "0", "fees": None,
            "shipping_cost": None, "item_cost": None, "order_basis": "kept", "source": "marketplace"}
    with engine.begin() as connection:
        conversations, sales = (Table(name, MetaData(), autoload_with=connection) for name in ("conversations", "marketplace_sales"))
        for item in ("blouse", "scarf", "coat", "boots"):
            connection.execute(conversations.insert().values(id=item, user_id=LOCAL_USER_ID, title=item))
        connection.execute(conversations.insert().values(id="other-blouse", user_id=OTHER, title="blouse"))
        connection.execute(sales.insert(), [
            # A Vendoo copy of an Etsy sale: Etsy's record stays, with Vendoo's cost and fees.
            _sale("etsy-blouse", "blouse", "etsy", "marketplace", datetime(2026, 3, 2, 15), financials=etsy),
            _sale("vendoo-blouse", "blouse", "etsy", "vendoo", datetime(2026, 3, 2), financials=vendoo),
            # Amounts and a date the seller typed on the Vendoo copy are kept whole.
            _sale("ebay-scarf", "scarf", "ebay", "marketplace", datetime(2026, 4, 2, 9)),
            _sale("vendoo-scarf", "scarf", "ebay", "vendoo", datetime(2026, 4, 1, 12), financials=typed),
            # Another marketplace's sale is not a copy.
            _sale("etsy-coat", "coat", "etsy", "marketplace", datetime(2026, 5, 1)),
            _sale("vendoo-coat", "coat", "poshmark", "vendoo", datetime(2026, 5, 1), financials=vendoo),
            # One copy, two Etsy orders: the nearer order replaces it; both orders stay.
            _sale("etsy-boots-1", "boots", "etsy", "marketplace", datetime(2025, 6, 1)),
            _sale("etsy-boots-2", "boots", "etsy", "marketplace", datetime(2026, 6, 1)),
            _sale("mark-boots", "boots", "etsy", "seller", datetime(2026, 6, 2),
                  financials={"currency": "USD", "item_cost": "9", "source": "seller"}),
            # Another seller's copy has no order of its own and stays.
            _sale("vendoo-other-blouse", "other-blouse", "etsy", "vendoo", datetime(2026, 3, 2), user=OTHER, financials=vendoo),
        ])
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        sales = Table("marketplace_sales", MetaData(), autoload_with=connection)
        rows = {(row.user_id, row.id): row for row in connection.execute(select(sales))}
    mine = {row_id for user, row_id in rows if user == LOCAL_USER_ID}
    assert mine == {"etsy-blouse", "ebay-scarf", "etsy-coat", "vendoo-coat", "etsy-boots-1", "etsy-boots-2"}
    assert {row_id for user, row_id in rows if user == OTHER} == {"vendoo-other-blouse"}
    blouse = rows[(LOCAL_USER_ID, "etsy-blouse")]
    assert blouse.financials == {**etsy, "item_cost": "4", "fees": "3.5"}
    assert blouse.financial_revision == 2
    assert blouse.sold_at == datetime(2026, 3, 2, 15)
    scarf = rows[(LOCAL_USER_ID, "ebay-scarf")]
    assert {key: value for key, value in scarf.financials.items() if key != "order_basis"} == \
        {key: value for key, value in typed.items() if key != "order_basis"}
    assert scarf.financials["order_basis"] == order_basis(scarf)
    assert scarf.sold_at == datetime(2026, 4, 1, 12)
    assert rows[(LOCAL_USER_ID, "etsy-boots-2")].financials["item_cost"] == "9"
    assert rows[(LOCAL_USER_ID, "etsy-boots-1")].financials is None
