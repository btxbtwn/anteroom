from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_sale import MarketplaceSale, SaleSyncEvent
from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.services import native_field_fill, native_marketplaces  # noqa: F401 - register session hooks before commits
from anteroom.services import sale_quantity_sync
from anteroom.services.hosted_database import SellerDatabaseSession
from test_seller_ownership import migration_config, migration_engine  # noqa: F401

PREVIOUS_HEAD = "c7a3e9f41b28"


def test_two_sessions_record_one_delist_outcome(migration_engine):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), "head")
    ready_to_insert = Barrier(2, timeout=10)
    hosted = engine.dialect.name == "postgresql"
    owner = "a3fdd683-638b-4e6e-8a05-8358ac2f2bc4" if hosted else LOCAL_USER_ID

    @event.listens_for(engine, "before_cursor_execute")
    def align_inserts(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO sale_sync_events"):
            ready_to_insert.wait()

    def record(_):
        session = SellerDatabaseSession(bind=engine, user_id=owner) if hosted else Session(engine)
        with session as db:
            row = sale_quantity_sync.record_sync_event(
                db, sale_id="sale", conversation_id=None, sku=None, sold_marketplace="ebay",
                target_marketplace="etsy", action="delist", status="succeeded", target_listing_id="target",
            )
            return row.id

    with ThreadPoolExecutor(max_workers=2) as threads:
        ids = list(threads.map(record, range(2)))
    with Session(engine) as db:
        rows = db.scalars(select(SaleSyncEvent)).all()
        assert [row.id for row in rows] == [ids[0]]
        assert ids[0] == ids[1]
        assert rows[0].status == "succeeded"
        assert rows[0].user_id == owner


def test_two_initial_quantity_plans_dispatch_once(migration_engine, monkeypatch):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), "head")
    with Session(engine) as db:
        db.add(Conversation(id="draft", title="In stock"))
        db.flush()
        db.add(MarketplaceSale(id="sale", conversation_id="draft", marketplace="poshmark",
                               order_id="order", line_id="line"))
        db.commit()

    ready_to_insert = Barrier(2, timeout=10)

    @event.listens_for(engine, "before_cursor_execute")
    def align_inserts(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO sale_sync_events"):
            ready_to_insert.wait()

    plan = {"conversation_id": "draft", "revision_id": "revision", "previous_quantity": 3,
            "sold_marketplace": "poshmark", "sku": None, "targets": [
                {"marketplace": "ebay", "action": "adjust_quantity", "listing_id": "target", "target_quantity": 2},
            ]}
    monkeypatch.setattr(sale_quantity_sync, "_load_adjustment_plan", lambda *_: plan)
    monkeypatch.setattr(sale_quantity_sync, "_auto_adjust_active", lambda **_: True)
    dispatched = []

    async def updater(*args):
        dispatched.append(args)
        return {"ok": True}

    async def race():
        await asyncio.gather(*(
            sale_quantity_sync.adjust_quantities_for_sale(
                "sale", 2, session_factory=lambda: Session(engine), updater=updater,
            ) for _ in range(2)
        ))

    asyncio.run(race())
    assert dispatched == [("draft", "ebay", "revision", 2)]
    with Session(engine) as db:
        assert db.scalars(select(SaleSyncEvent)).one().status == "succeeded"


def test_upgrade_keeps_confirmed_then_oldest_tracker_row(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, PREVIOUS_HEAD)
    events = Table("sale_sync_events", MetaData(), autoload_with=engine)
    early = datetime(2026, 11, 1)
    late = early + timedelta(minutes=1)
    base = {"sale_id": "sale", "sold_marketplace": "poshmark", "target_marketplace": "ebay",
            "action": "adjust_quantity", "status": "succeeded", "attempts": 1,
            "created_at": late, "updated_at": late, "new_listing_id": None,
            "user_id": "a3fdd683-638b-4e6e-8a05-8358ac2f2bc4"}
    with engine.begin() as connection:
        connection.execute(events.insert(), [
            {**base, "id": "failed", "status": "failed", "attempts": 7, "created_at": early},
            {**base, "id": "later-success"},
            {**base, "id": "b-first-time", "created_at": early},
            {**base, "id": "a-first-time", "created_at": early, "new_listing_id": "confirmed"},
            {**base, "id": "other-owner", "user_id": "faf89636-74b4-44e5-a54c-abfd051a15a4"},
            {**base, "id": "other-action", "action": "delist"},
            {**base, "id": "other-marketplace", "target_marketplace": "etsy"},
        ])

    if engine.dialect.name == "postgresql":
        # A legacy process can still create a duplicate between cleanup and
        # the concurrent index build. Force that race on the first build.
        builds = 0
        with engine.connect() as migration:
            @event.listens_for(migration, "before_cursor_execute")
            def insert_while_building(connection, cursor, statement, parameters, context, executemany):
                nonlocal builds
                if statement.startswith("CREATE UNIQUE INDEX CONCURRENTLY uq_sale_sync_events_seller_sale_target_action"):
                    builds += 1
                    if builds == 1:
                        with engine.begin() as legacy:
                            legacy.execute(events.insert(), {**base, "id": "late-duplicate", "status": "pending"})

            config.attributes["connection"] = migration
            command.upgrade(config, "head")
        assert builds == 2
    else:
        command.upgrade(config, "head")
    with engine.begin() as connection:
        kept = {row.id: row.new_listing_id
                for row in connection.execute(select(events.c.id, events.c.new_listing_id))}
        assert kept == {"a-first-time": "confirmed", "other-owner": None,
                        "other-action": None, "other-marketplace": None}
        # An ordinary insert and update with the old schema still work during rollout.
        connection.execute(events.insert(), {**base, "id": "old-writer", "sale_id": "new-sale"})
        connection.execute(events.update().where(events.c.id == "old-writer").values(attempts=2))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(events.insert(), {**base, "id": "duplicate"})


def test_upgrade_waits_out_a_busy_tracker_table(migration_engine, monkeypatch):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != "postgresql":
        pytest.skip("Table locks and lock timeouts are Postgres behavior")
    config = migration_config(engine)
    command.upgrade(config, PREVIOUS_HEAD)
    waits = []
    with engine.connect() as writer, engine.connect() as migration:
        # A live writer holds a lock that conflicts with the cleanup's fence.
        writer.exec_driver_sql("LOCK TABLE sale_sync_events IN ROW EXCLUSIVE MODE")

        def sleep(delay):
            waits.append(delay)
            writer.rollback()

        monkeypatch.setattr(time, "sleep", sleep)
        migration.exec_driver_sql("SET lock_timeout = '200ms'")
        migration.commit()
        config.attributes["connection"] = migration
        command.upgrade(config, "head")
    assert waits == [10]
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
        ), {"name": "uq_sale_sync_events_seller_sale_target_action"}) is True
