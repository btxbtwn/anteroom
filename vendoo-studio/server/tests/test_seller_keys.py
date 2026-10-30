from __future__ import annotations

from datetime import datetime, UTC, timedelta
import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from test_seller_ownership import migration_engine, migration_config, read_rows  # noqa: F401
from vendoo_studio.database import Base, load_models
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing_copy import ListingCopyReceipt
from vendoo_studio.models.similar_item import PhotoFingerprint
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_database import SellerDatabaseSession
from vendoo_studio.services.listing_copy import copy_result, completed_copy

ALICE, BOB = str(uuid.uuid4()), str(uuid.uuid4())
PREVIOUS_REVISION = "9664651bec7c"

# User-chosen/external identities may be identical in independent shops.
CASES = [
    ("category_schemas", dict(general_path="Clothing", marketplace="ebay", category_path="Coats", fields=[])),
    ("category_field_schemas", dict(marketplace="ebay", category_id="123", fields=[])),
    ("category_nodes", dict(marketplace="ebay", path="Clothing/Coats", parent_path="Clothing", label="Coats")),
    ("diagnostic_runs", dict(observation_id="observation", job_id="job", step="fill", collector_version="1", mode="test")),
    ("ebay_offer_rules", dict(account_id="account", listing_id="123", discount_percent=10, minimum_price_cents=1000)),
    ("field_registry", dict(marketplace="ebay", category_path="Coats", normalized_label="brand")),
    ("marketplace_offer_floors", dict(marketplace="ebay", listing_id="123", floor_cents=1000)),
    ("marketplace_sales", dict(marketplace="ebay", order_id="order", line_id="line")),
    ("photo_fingerprints", dict(checksum="same-photo", dhash="a" * 16)),
    ("listing_copy_receipts", dict(request_id="same-request", fingerprint="basis", source_id="source")),
    ("ebay_actions", dict(account_id="account", kind="offer", resource_id="123", fingerprint="basis", state="sending",
                          expires_at=datetime.now(UTC) + timedelta(hours=1))),
]


def prepare(engine, revision="head"):
    load_models()
    command.upgrade(migration_config(engine), revision)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO conversations (id) VALUES ('item')"))
        connection.execute(sa.text("INSERT INTO jobs (id, conversation_id, approved_revision_id, listing_snapshot) "
                                   "VALUES ('job', 'item', 'revision', '{}')"))


@pytest.mark.parametrize("table_name,values", CASES, ids=[case[0] for case in CASES])
def test_independent_shops_can_reuse_keys_but_same_shop_cannot(migration_engine, table_name, values):  # noqa: F811
    engine = migration_engine
    prepare(engine)
    table = Base.metadata.tables[table_name]
    for seller in [ALICE, BOB]:
        with engine.begin() as connection:
            connection.execute(table.insert().values(**values, user_id=seller))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(table.insert().values(**values, user_id=ALICE))
    with engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(table)) == 2


def test_upgrade_keeps_all_keyed_records_and_matches_models(migration_engine):  # noqa: F811
    engine = migration_engine
    prepare(engine, PREVIOUS_REVISION)
    with engine.begin() as connection:
        for name, values in CASES:
            table = Base.metadata.tables[name]
            connection.execute(table.insert().values(**values, user_id=ALICE))
    names = [name for name, _ in CASES]
    before = read_rows(engine, names)
    schema_migrations.ensure_schema(engine)
    assert read_rows(engine, names) == before
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    for name, values in CASES:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(Base.metadata.tables[name].insert().values(**values, user_id=ALICE))
        with engine.begin() as connection:
            connection.execute(Base.metadata.tables[name].insert().values(**values, user_id=BOB))


def test_hosted_copy_receipts_and_photo_identity_are_separate_per_seller(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != "postgresql":
        pytest.skip("Hosted sessions require PostgreSQL")
    prepare(engine)
    for seller, item in [(ALICE, "alice-copy"), (BOB, "bob-copy")]:
        with SellerDatabaseSession(bind=engine, user_id=seller) as db:
            db.add(Conversation(id=item))
            db.commit()
            db.add_all([ListingCopyReceipt(request_id="same-request", fingerprint="basis", source_id="source", conversation_id=item),
                        PhotoFingerprint(checksum="same-photo", dhash=item)])
            db.commit()
    for seller, item in [(ALICE, "alice-copy"), (BOB, "bob-copy")]:
        with SellerDatabaseSession(bind=engine, user_id=seller) as db:
            assert copy_result(db, "source", "same-request") == item
            assert completed_copy(db, "listing", "source", "same-request", "basis") == item
            assert db.query(PhotoFingerprint).one().dhash == item
            assert db.get(PhotoFingerprint, {"user_id": seller, "checksum": "same-photo"}).dhash == item


def test_hosted_ebay_in_flight_lock_is_per_seller(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != "postgresql":
        pytest.skip("Hosted sessions require PostgreSQL")
    prepare(engine)
    table = Base.metadata.tables["ebay_actions"]
    values = dict(CASES[-1][1])
    for seller in [ALICE, BOB]:
        with SellerDatabaseSession(bind=engine, user_id=seller) as db:
            db.execute(table.insert().values(**values, user_id=seller))
            db.commit()
    with SellerDatabaseSession(bind=engine, user_id=ALICE) as db:
        with pytest.raises(IntegrityError):
            db.execute(table.insert().values(**values, user_id=ALICE))
        db.rollback()
        assert len(db.execute(sa.select(table)).all()) == 1


@pytest.mark.sqlite_only("only desktop SQLite databases predate Alembic")
def test_pre_alembic_key_upgrade_preserves_diagnostic_children(migration_engine):  # noqa: F811
    engine = migration_engine
    prepare(engine, PREVIOUS_REVISION)
    with engine.begin() as connection:
        run = Base.metadata.tables["diagnostic_runs"]
        observation = Base.metadata.tables["field_observations"]
        connection.execute(run.insert().values(id="run", **CASES[3][1]))
        connection.execute(observation.insert().values(id="field", diagnostic_run_id="run", observation_id="field-id"))
        connection.execute(sa.text("DROP TABLE alembic_version"))
    before = read_rows(engine, ["diagnostic_runs", "field_observations"])
    schema_migrations.ensure_schema(engine)
    assert read_rows(engine, before) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
