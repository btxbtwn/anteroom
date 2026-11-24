"""Retiring the Sourcing tab drops scout_checks, sourcing preferences, and the scheduler helper while keeping source_boxes."""
from datetime import UTC, datetime

from alembic import command
from sqlalchemy import MetaData, Table, inspect, select, text

from test_seller_ownership import migration_config, migration_engine  # noqa: F401

SELLER = "11111111-1111-4111-8111-111111111111"


def test_upgrade_retires_sourcing_artifacts_and_preserves_bought_boxes(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "c7a3e9f41b28-1")
    postgres = engine.dialect.name == "postgresql"

    previous = MetaData()
    scout_checks = Table("scout_checks", previous, autoload_with=engine)
    preferences = Table("user_preferences", previous, autoload_with=engine)
    changes = Table("seller_changes", previous, autoload_with=engine)
    boxes = Table("source_boxes", previous, autoload_with=engine)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.execute(scout_checks.insert(), {
            "id": "scout-1", "user_id": SELLER, "status": "done", "photos": [], "created_at": now,
        })
        connection.execute(preferences.insert(), [
            {"id": "pref-1", "user_id": SELLER, "key": "sourcing", "value": {"prefs": {"zip": "60601"}}, "updated_at": now},
            {"id": "pref-2", "user_id": SELLER, "key": "settings", "value": {"theme": "dark"}, "updated_at": now},
        ])
        connection.execute(changes.insert(), [
            {"user_id": SELLER, "topic": "sourcing", "changed_at": now},
            {"user_id": SELLER, "topic": "sales", "changed_at": now},
        ])
        connection.execute(boxes.insert(), {
            "id": "box-1", "user_id": SELLER, "store": "Wholesale Co", "title": "Designer Lot",
            "price": 250.0, "shipping": 35.0, "pieces": 10, "bought_at": now, "created_at": now,
        })

    command.upgrade(config, "head")

    assert not inspect(engine).has_table("scout_checks")
    assert inspect(engine).has_table("source_boxes")
    with engine.connect() as connection:
        assert connection.scalars(select(preferences.c.key)).all() == ["settings"]
        assert connection.scalars(select(changes.c.topic)).all() == ["sales"]
        assert connection.scalars(select(boxes.c.id)).all() == ["box-1"]
        if postgres:
            fn_exists = connection.scalar(text(
                "SELECT to_regprocedure('anteroom_private.due_sourcing_owners(integer, text[], text, text)')"
            ))
            assert fn_exists is None

    command.downgrade(config, "c7a3e9f41b28-1")
    assert inspect(engine).has_table("scout_checks")
    if postgres:
        with engine.connect() as connection:
            fn_exists = connection.scalar(text(
                "SELECT to_regprocedure('anteroom_private.due_sourcing_owners(integer, text[], text, text)')"
            ))
            assert fn_exists is not None
    command.upgrade(config, "head")
    assert not inspect(engine).has_table("scout_checks")
