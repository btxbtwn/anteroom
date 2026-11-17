"""Previous-release Finished decisions remain closed without guessing their origin."""
from datetime import datetime

from alembic import command
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.orm import Session

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services.sales_ledger import delete_seller_sale


def test_previous_release_finished_rows_default_to_seller_owned(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "fd7c3a5d05ea")
    assert "settled_by_seller" not in {column["name"] for column in inspect(engine).get_columns("conversations")}
    previous = MetaData()
    conversations = Table("conversations", previous, autoload_with=engine)
    sales = Table("marketplace_sales", previous, autoload_with=engine)
    when = datetime(2026, 10, 2, 10, 30)
    notes = '{"sellerNote":"Keep this decision"}'
    with engine.begin() as connection:
        connection.execute(conversations.insert(), [
            {"id": "legacy-finished", "title": "Finished before upgrade", "status": "sold",
             "settled_at": when, "unsettled_at": None, "notes": notes},
            {"id": "legacy-open", "title": "Kept open before upgrade", "status": "sold",
             "settled_at": None, "unsettled_at": when, "notes": notes},
        ])
        connection.execute(sales.insert(), {"id": "legacy-sale", "marketplace": "other", "order_id": "fixture-order",
            "line_id": "fixture-line", "conversation_id": "legacy-finished", "source": "seller",
            "state": "resolved", "received_at": when, "quantity": 1})

    command.upgrade(config, "head")
    columns = {column["name"]: column for column in inspect(engine).get_columns("conversations")}
    assert columns["settled_by_seller"]["nullable"] is False
    with Session(engine) as db:
        finished, opened = db.get(Conversation, "legacy-finished"), db.get(Conversation, "legacy-open")
        assert finished.settled_by_seller is True and finished.settled_at == when
        assert finished.notes == notes
        assert opened.settled_at is None and opened.unsettled_at == when and opened.notes == notes
        # The old release stored no provenance. Undo can correct Sold, but must
        # not reopen this historically ambiguous Finished item.
        delete_seller_sale(db, "legacy-sale", 0)
        db.refresh(finished)
        assert finished.status == "draft" and finished.settled_at == when and finished.settled_by_seller
        assert finished.notes == notes
    with engine.connect() as connection:
        assert connection.scalar(select(sales.c.id).where(sales.c.id == "legacy-sale")) is None
