from __future__ import annotations

from datetime import datetime, UTC
import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine, migration_config, read_rows, seed_previous_release  # noqa: F401
from vendoo_studio.database import Base, load_models
from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_database import SellerDatabaseSession

ALICE, BOB = str(uuid.uuid4()), str(uuid.uuid4())
PREVIOUS_REVISION = "a4a9eee52599"
PARENTS = ("source_boxes", "conversations", "automations", "promotion_automations", "jobs", "diagnostic_runs", "ebay_offer_rules")


def references():
    load_models()
    return [(table, next(iter(key.columns)).name, key.referred_table)
            for table in Base.metadata.tables.values()
            for key in table.foreign_key_constraints if len(key.columns) == 1]


def values_for(table, seller, **overrides):
    """Minimal valid DB rows; schema types supply irrelevant required payloads."""
    result = {"user_id": seller}
    for column in table.c:
        if column.name in result or column.nullable or column.default is not None or column.server_default is not None:
            continue
        if isinstance(column.type, sa.JSON):
            result[column.name] = {}
        elif isinstance(column.type, sa.DateTime):
            result[column.name] = datetime.now(UTC)
        elif isinstance(column.type, (sa.Integer, sa.Float)):
            result[column.name] = 1
        else:
            result[column.name] = uuid.uuid4().hex
    if "id" in table.c:
        result["id"] = uuid.uuid4().hex
    for key in table.foreign_key_constraints:
        if len(key.columns) == 1:
            result[next(iter(key.columns)).name] = f"{key.referred_table.name}-{seller}"
    return result | overrides


def seed_parents(engine):
    for parent in PARENTS:
        table = Base.metadata.tables[parent]
        for seller in (ALICE, BOB):
            with engine.begin() as connection:
                connection.execute(table.insert().values(**values_for(table, seller, id=f"{parent}-{seller}")))


def test_every_existing_reference_requires_a_matching_owner(migration_engine, subtests):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    seed_parents(engine)
    for table, column, parent in references():
        with subtests.test(table=table.name, reference=column):
            good = values_for(table, ALICE)
            bad = good | {column: f"{parent.name}-{BOB}"}
            # Plain SQL proves the database itself enforces the rule, even for
            # an administrative session which bypasses hosted row policies.
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(table.insert().values(**bad))
            with engine.begin() as connection:
                connection.execute(table.insert().values(**good))
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(table.update().where(table.c.user_id == ALICE).values({column: f"{parent.name}-{BOB}"}))
            with engine.begin() as connection:
                connection.execute(table.delete().where(sa.and_(
                    *(key == good[key.name] for key in table.primary_key.columns))))


def test_parent_owner_change_cannot_strand_children(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO conversations (id, user_id) VALUES ('item', :owner)"), {"owner": ALICE})
        connection.execute(sa.text("INSERT INTO messages (id, conversation_id, user_id, role, text) "
                                   "VALUES ('message', 'item', :owner, 'user', 'Keep')"), {"owner": ALICE})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("UPDATE conversations SET user_id = :owner WHERE id = 'item'"), {"owner": BOB})
    # Explicitly moving the whole graph in one transaction remains possible
    # for a future seller-approved import; constraints check the final state.
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE conversations SET user_id = :owner WHERE id = 'item'"), {"owner": BOB})
        connection.execute(sa.text("UPDATE messages SET user_id = :owner WHERE id = 'message'"), {"owner": BOB})


def test_optional_links_clear_and_cascades_keep_their_original_behavior(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    seed_parents(engine)
    sales = Base.metadata.tables["marketplace_sales"]
    copies = Base.metadata.tables["listing_copy_receipts"]
    baselines = Base.metadata.tables["item_price_baselines"]
    item = f"conversations-{ALICE}"
    with engine.begin() as connection:
        connection.execute(sales.insert().values(**values_for(sales, ALICE, id="sale", conversation_id=item)))
        connection.execute(copies.insert().values(**values_for(copies, ALICE, request_id="copy", conversation_id=item)))
        connection.execute(baselines.insert().values(**values_for(baselines, ALICE, conversation_id=item)))
        # Remove seeded jobs first: their original FK deliberately forbids
        # deleting an item which still has job history.
        connection.execute(sa.text("DELETE FROM diagnostic_runs"))
        connection.execute(sa.text("DELETE FROM jobs"))
        connection.execute(sa.text("DELETE FROM conversations WHERE id = :item"), {"item": item})
    with engine.connect() as connection:
        assert connection.execute(sa.select(sales.c.user_id, sales.c.conversation_id)).one() == (ALICE, None)
        assert connection.execute(sa.select(copies.c.user_id, copies.c.conversation_id)).one() == (ALICE, None)
        assert connection.scalar(sa.select(sa.func.count()).select_from(baselines)) == 0


def test_relationship_loads_and_deletes_do_not_reassign_owners(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        item = Conversation(id="item", user_id=ALICE)
        item.messages.append(Message(id="message", user_id=ALICE, role="user", text="Keep"))
        item.photos.append(Photo(id="photo", user_id=ALICE, original_filename="coat.jpg", stored_filename="coat.jpg",
                                 mime_type="image/jpeg", size_bytes=1))
        db.add(item)
        db.commit()
        db.expire_all()
        assert db.get(Conversation, "item").messages[0].user_id == ALICE
        assert db.get(Conversation, "item").photos[0].conversation.id == "item"
        foreign = Message(id="foreign-message", user_id=BOB, role="user", text="Wrong owner")
        item.messages.append(foreign)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert foreign.user_id == BOB
        db.delete(db.get(Conversation, "item"))
        db.commit()
        assert db.query(Message).count() == 0
        assert db.query(Photo).count() == 0


def test_populated_previous_release_upgrade_preserves_records_and_old_foreign_keys(migration_engine):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), PREVIOUS_REVISION)
    names = seed_previous_release(engine)
    before = read_rows(engine, names)
    old = {table: sa.inspect(engine).get_foreign_keys(table) for table in sa.inspect(engine).get_table_names()}
    schema_migrations.ensure_schema(engine)
    for row in before.get("listing_revisions", []):
        row["inventory_quantity"] = None
    assert read_rows(engine, names) == before
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    for table, keys in old.items():
        assert all(key in sa.inspect(engine).get_foreign_keys(table) for key in keys)


def test_mismatched_old_records_refuse_upgrade_without_rewriting_them(migration_engine):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), PREVIOUS_REVISION)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO conversations (id, user_id) VALUES ('item', :owner)"), {"owner": BOB})
        connection.execute(sa.text("INSERT INTO messages (id, conversation_id, user_id, role, text) "
                                   "VALUES ('message', 'item', :owner, 'user', 'Keep')"), {"owner": ALICE})
    before = read_rows(engine, ["conversations", "messages"])
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        schema_migrations.ensure_schema(engine)
    assert schema_migrations.recorded_revision(engine) == PREVIOUS_REVISION
    assert read_rows(engine, before) == before


def test_hosted_raw_writes_cannot_link_to_another_seller(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != "postgresql":
        pytest.skip("Hosted sessions require PostgreSQL")
    schema_migrations.ensure_schema(engine)
    seed_parents(engine)
    for table, column, parent in references():
        bad = values_for(table, ALICE, **{column: f"{parent.name}-{BOB}"})
        with SellerDatabaseSession(bind=engine, user_id=ALICE) as db:
            with pytest.raises(IntegrityError):
                db.execute(table.insert().values(**bad))
                db.commit()
            db.rollback()
            assert db.scalar(sa.select(Conversation.id)) == f"conversations-{ALICE}"
