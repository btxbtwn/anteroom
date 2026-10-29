from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.models.conversation import Conversation, Message
from vendoo_studio.models.ownership import LOCAL_USER_ID
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_database import SellerDatabaseSession, get_seller_db
from vendoo_studio.services.seller_sessions import SellerIdentity

ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())


@pytest.fixture
def seller_engine(migration_engine):  # noqa: F811 — imported pytest fixture
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enforces hosted row security")
    schema_migrations.ensure_schema(migration_engine)
    with Session(migration_engine) as db:
        db.add_all([
            Conversation(id="alice", title="Alice coat", user_id=ALICE),
            Conversation(id="bob", title="Bob coat", user_id=BOB),
            Conversation(id="local", title="Local coat", user_id=LOCAL_USER_ID),
        ])
        db.commit()
        db.add_all([
            Message(id="alice-message", conversation_id="alice", role="user", text="Alice secret", user_id=ALICE),
            Message(id="bob-message", conversation_id="bob", role="user", text="Bob secret", user_id=BOB),
        ])
        db.commit()
    return migration_engine


def test_hosted_session_rejects_local_owner_and_sqlite():
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="account owner"):
            SellerDatabaseSession(user_id=LOCAL_USER_ID, bind=engine)
        with pytest.raises(ValueError, match="PostgreSQL"):
            SellerDatabaseSession(user_id=ALICE, bind=engine)
        with pytest.raises(ValueError):
            SellerDatabaseSession(user_id="invalid", bind=engine)
    finally:
        engine.dispose()


def test_request_dependency_requires_verified_identity():
    for state in [{}, {"seller": {"user_id": ALICE}}]:
        with pytest.raises(HTTPException) as error:
            next(get_seller_db(Request({"type": "http", "state": state})))
        assert error.value.status_code == 401


def test_request_dependency_uses_authenticated_account(seller_engine, monkeypatch):
    monkeypatch.setattr("vendoo_studio.database.engine", seller_engine)
    request = Request({"type": "http", "state": {"seller": SellerIdentity(user_id=BOB)}})
    dependency = get_seller_db(request)
    try:
        assert next(dependency).scalars(select(Conversation.id)).all() == ["bob"]
    finally:
        dependency.close()


def test_orm_raw_sql_and_joins_only_see_the_seller(seller_engine):
    for user, item, secret in [(ALICE, "alice", "Alice secret"), (BOB, "bob", "Bob secret")]:
        with SellerDatabaseSession(bind=seller_engine, user_id=user) as db:
            assert db.execute(text("SELECT current_user")).scalar_one() == "anteroom_seller"
            assert db.execute(text("SELECT marketplace FROM category_trees")).all() == []
            assert db.scalars(select(Conversation.id)).all() == [item]
            assert db.execute(text("SELECT id FROM conversations")).scalars().all() == [item]
            assert db.get(Conversation, "bob" if item == "alice" else "alice") is None
            assert db.scalars(select(Message.text).join(Conversation)).all() == [secret]
            assert [m.text for m in db.get(Conversation, item).messages] == [secret]


def test_updates_and_deletes_without_filters_cannot_touch_other_sellers(seller_engine):
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        assert db.execute(text("UPDATE conversations SET title = 'Edited'")).rowcount == 1
        db.commit()
        assert db.execute(text("DELETE FROM messages")).rowcount == 1
        db.commit()
        assert db.execute(text("DELETE FROM conversations WHERE id = 'bob'")).rowcount == 0
        db.commit()
    with Session(seller_engine) as db:
        assert db.get(Conversation, "bob").title == "Bob coat"
        assert db.get(Message, "bob-message").text == "Bob secret"
        assert db.get(Conversation, "local").title == "Local coat"


def test_new_records_get_verified_owner_and_other_owner_is_rejected(seller_engine):
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        draft = Conversation(id="new", title="New draft")
        db.add(draft)
        db.commit()
        assert draft.user_id == ALICE
        draft.user_id = BOB
        with pytest.raises(ValueError, match="another owner"):
            db.commit()
        db.rollback()
        assert draft.user_id == ALICE


@pytest.mark.parametrize("statement", [
    "INSERT INTO conversations (id, user_id) VALUES ('spoof', :bob)",
    "INSERT INTO conversations (id) VALUES ('local-default')",
    "UPDATE conversations SET user_id = :bob WHERE id = 'alice'",
    "TRUNCATE conversations CASCADE",
    "ALTER TABLE conversations DISABLE ROW LEVEL SECURITY",
    "INSERT INTO category_trees (marketplace) VALUES ('unapproved')",
])
def test_database_rejects_spoofing_and_unsafe_permissions(seller_engine, statement):
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"bob": BOB})
        db.rollback()
        assert db.scalars(select(Conversation.id)).all() == ["alice"]


def test_transaction_and_pool_reuse_do_not_carry_a_seller(seller_engine):
    # A pool of one forces alternating accounts onto the exact same connection.
    engine = create_engine(seller_engine.url, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            original_role = connection.execute(text("SELECT current_user")).scalar_one()
        for user, item in [(ALICE, "alice"), (BOB, "bob"), (ALICE, "alice")]:
            with SellerDatabaseSession(bind=engine, user_id=user) as db:
                assert db.scalars(select(Conversation.id)).all() == [item]
                db.commit()
                assert db.scalars(select(Conversation.id)).all() == [item]
                db.rollback()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT current_user")).scalar_one() == original_role
                assert connection.execute(text("SELECT nullif(current_setting('request.jwt.claims', true), '')")).scalar_one() is None
    finally:
        engine.dispose()


def test_failed_savepoint_keeps_the_seller_scope(seller_engine):
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(text("INSERT INTO conversations (id, user_id) VALUES ('spoof', :bob)"), {"bob": BOB})
        assert db.scalars(select(Conversation.id)).all() == ["alice"]
        db.commit()
        assert db.scalars(select(Conversation.id)).all() == ["alice"]


def test_role_that_owns_tables_is_refused(seller_engine):
    with seller_engine.begin() as connection:
        connection.execute(text("ALTER TABLE conversations OWNER TO anteroom_seller"))
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        with pytest.raises(RuntimeError, match="bypass seller isolation"):
            db.scalars(select(Conversation.id)).all()


def test_missing_table_protection_is_refused(seller_engine):
    with seller_engine.begin() as connection:
        connection.execute(text("ALTER TABLE messages DISABLE ROW LEVEL SECURITY"))
    with SellerDatabaseSession(bind=seller_engine, user_id=ALICE) as db:
        with pytest.raises(RuntimeError, match="not fully installed"):
            db.scalars(select(Conversation.id)).all()


def test_upgrade_of_existing_owned_records_keeps_owners(migration_engine):  # noqa: F811
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enforces hosted row security")
    from alembic import command
    from test_seller_ownership import migration_config

    command.upgrade(migration_config(migration_engine), "d8b6f2a19c40")
    with Session(migration_engine) as db:
        db.add_all([Conversation(id="before-alice", user_id=ALICE), Conversation(id="before-bob", user_id=BOB)])
        db.commit()
    schema_migrations.ensure_schema(migration_engine)
    for user, item in [(ALICE, "before-alice"), (BOB, "before-bob")]:
        with SellerDatabaseSession(bind=migration_engine, user_id=user) as db:
            assert db.scalars(select(Conversation.id)).all() == [item]


def test_every_seller_table_has_restrictive_row_security(seller_engine):
    from vendoo_studio.database import Base, load_models

    load_models()
    tables = {table.name for table in Base.metadata.tables.values() if "user_id" in table.c}
    with seller_engine.connect() as connection:
        enabled = set(connection.execute(text("SELECT relname FROM pg_class WHERE relrowsecurity")).scalars())
        restrictive = set(connection.execute(text("SELECT tablename FROM pg_policies WHERE policyname = 'seller_owner' AND permissive = 'RESTRICTIVE'")).scalars())
    assert tables <= enabled
    assert tables <= restrictive


def test_missing_claims_deny_all_rows(seller_engine):
    with seller_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE anteroom_seller"))
        assert connection.execute(text("SELECT id FROM conversations")).all() == []
        with pytest.raises(DBAPIError):
            connection.execute(text("INSERT INTO conversations (id, user_id) VALUES ('unscoped', :owner)"), {"owner": ALICE})
