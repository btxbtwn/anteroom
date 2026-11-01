"""The hosted change feed other devices follow through Supabase Realtime."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.models.conversation import Conversation, Message
from vendoo_studio.models.seller_change import TOPIC_BY_TABLE, SellerChange
from vendoo_studio.models.user_preference import UserPreference
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_database import SellerDatabaseSession

ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())


def test_every_seller_table_names_its_topic():
    from vendoo_studio.database import Base, load_models

    load_models()
    seller_tables = {table.name for table in Base.metadata.tables.values() if "user_id" in table.c}
    assert seller_tables == set(TOPIC_BY_TABLE)


@pytest.fixture
def feed_engine(migration_engine):  # noqa: F811 — imported pytest fixture
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("The change feed is written by hosted PostgreSQL sessions")
    # Supabase's client role exists before Anteroom's migrations run.
    with migration_engine.begin() as connection:
        for role in ("authenticated", "anon"):
            connection.execute(text(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; "
                                    "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$"))
    schema_migrations.ensure_schema(migration_engine)
    return migration_engine


def feed(engine):
    with Session(engine) as db:
        return {(row.user_id, row.topic) for row in db.scalars(select(SellerChange))}


def test_a_sellers_write_announces_only_its_topics_for_that_seller(feed_engine):
    with SellerDatabaseSession(bind=feed_engine, user_id=ALICE) as db:
        db.add(Conversation(id="coat", title="Coat"))
        db.commit()
    assert feed(feed_engine) == {(ALICE, "listings")}

    with SellerDatabaseSession(bind=feed_engine, user_id=BOB) as db:
        db.add(UserPreference(key="settings", value={"theme": "dark"}))
        db.add(Conversation(id="bob-coat", title="Coat"))
        db.flush()
        db.add(Message(id="bob-message", conversation_id="bob-coat", role="user", text="hi"))
        db.commit()
    assert feed(feed_engine) == {(ALICE, "listings"), (BOB, "listings"), (BOB, "settings")}


def test_changes_announce_at_commit_and_not_after_rollback(feed_engine):
    with SellerDatabaseSession(bind=feed_engine, user_id=ALICE) as db:
        db.add(Conversation(id="coat", title="Coat"))
        db.commit()
        first = db.scalar(select(SellerChange.changed_at))
        db.get(Conversation, "coat").title = "Wool coat"
        db.flush()
        db.rollback()
        db.get(Conversation, "coat")  # a read alone announces nothing
        db.commit()
        assert db.scalar(select(SellerChange.changed_at)) == first
        # Statements outside the unit of work announce too.
        db.execute(update(Conversation).where(Conversation.id == "coat").values(title="Edited"))
        db.commit()
        assert db.scalar(select(SellerChange.changed_at)) > first
    assert feed(feed_engine) == {(ALICE, "listings")}


def test_background_workers_announce_through_the_seller_context(feed_engine, monkeypatch):
    from vendoo_studio.services import hosted_context

    monkeypatch.setattr("vendoo_studio.database.engine", feed_engine)
    context = hosted_context.for_seller(ALICE)
    context.settings.write({"theme": "dark"})
    assert feed(feed_engine) == {(ALICE, "settings")}


def subscriber_rows(engine, user, statement):
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE authenticated"))
        connection.execute(text("SELECT set_config('request.jwt.claims', :claims, true)"),
                           {"claims": json.dumps({"sub": user, "role": "authenticated"})})
        return connection.execute(text(statement)).all()


def test_a_subscriber_reads_only_its_own_feed_and_no_seller_data(feed_engine):
    for user in (ALICE, BOB):
        with SellerDatabaseSession(bind=feed_engine, user_id=user) as db:
            db.add(Conversation(id=f"coat-{user}", title="Private title"))
            db.commit()
    assert subscriber_rows(feed_engine, ALICE, "SELECT user_id, topic FROM seller_changes") == [(ALICE, "listings")]
    assert subscriber_rows(feed_engine, BOB, "SELECT user_id, topic FROM seller_changes") == [(BOB, "listings")]
    # Supabase grants its client roles every new public table by default;
    # row security alone must keep a subscriber out of seller data.
    from vendoo_studio.database import Base

    seller_tables = [table.name for table in Base.metadata.tables.values()
                     if "user_id" in table.c and table.name != "seller_changes"]
    with feed_engine.begin() as connection:
        for table in seller_tables:
            connection.execute(text(f'GRANT SELECT ON "{table}" TO authenticated'))
    for table in seller_tables:
        assert subscriber_rows(feed_engine, ALICE, f'SELECT * FROM "{table}"') == [], table
    for statement in ("INSERT INTO seller_changes (user_id, topic, changed_at) VALUES (:me, 'listings', now())",
                      "UPDATE seller_changes SET changed_at = now()",
                      "DELETE FROM seller_changes"):
        with pytest.raises(DBAPIError), feed_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE authenticated"))
            connection.execute(text("SELECT set_config('request.jwt.claims', :claims, true)"),
                               {"claims": json.dumps({"sub": ALICE})})
            connection.execute(text(statement), {"me": ALICE})


def test_the_feed_is_published_to_realtime(feed_engine):
    with feed_engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM pg_publication_tables WHERE pubname = 'supabase_realtime' "
            "AND tablename = 'seller_changes'")) == 1


def test_previous_release_upgrades_to_the_feed(migration_engine):  # noqa: F811
    from alembic import command

    config = migration_config(migration_engine)
    command.upgrade(config, "3b9d6f2a1c47")
    with migration_engine.begin() as connection:
        connection.execute(text("INSERT INTO conversations (id, title) VALUES ('before-feed', 'Coat')"))
    command.upgrade(config, "head")
    with migration_engine.connect() as connection:
        assert connection.scalar(text("SELECT title FROM conversations WHERE id = 'before-feed'")) == "Coat"
        assert connection.scalar(text("SELECT count(*) FROM seller_changes")) == 0
