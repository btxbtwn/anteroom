import sqlite3

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError

from vendoo_studio.services import backups, schema_migrations
from vendoo_studio.services.schema_drift import inspect_database
from vendoo_studio.services.schema_migrations import (
    SchemaError,
    ensure_schema,
    head_revision,
    recorded_revision,
)


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    directory = tmp_path / "backups"
    monkeypatch.setattr(backups, "BACKUPS_DIR", str(directory))
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path / "userdata"))
    return directory


@pytest.fixture
def engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'studio.db'}")


def _legacy_database(engine):
    """A database as an older build left it: real tables, never stamped."""
    from vendoo_studio.database import Base, load_models

    load_models()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(
            text("INSERT INTO conversations (id, title) VALUES ('keep-me', 'vintage coat')")
        )
    return engine


def test_head_revision_is_resolvable():
    assert head_revision()
    assert schema_migrations.migrations_dir().is_dir()


def test_fresh_database_is_created_at_head(engine, snapshots):
    revision = ensure_schema(engine)

    assert revision == head_revision()
    assert recorded_revision(engine) == head_revision()
    assert inspect_database(engine.url.database).clean


def test_fresh_database_is_not_snapshotted(engine, snapshots):
    """There is nothing to lose yet, so do not litter the backups folder."""
    ensure_schema(engine)

    assert backups.list_snapshots(snapshots) == []


def test_legacy_database_is_adopted_without_losing_rows(engine, snapshots):
    _legacy_database(engine)
    assert recorded_revision(engine) is None

    revision = ensure_schema(engine)

    assert revision == head_revision()
    assert recorded_revision(engine) == head_revision()
    with sqlite3.connect(engine.url.database) as connection:
        rows = connection.execute("SELECT title FROM conversations").fetchall()
    assert rows == [("vintage coat",)]


def test_adopting_a_legacy_database_snapshots_first(engine, snapshots):
    _legacy_database(engine)

    ensure_schema(engine)

    assert [snapshot.reason for snapshot in backups.list_snapshots(snapshots)] == ["pre-adopt"]


def test_legacy_database_missing_a_column_is_repaired(engine, snapshots):
    """The gap an older build left behind is closed before the stamp goes on."""
    _legacy_database(engine)
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN settled_at")

    ensure_schema(engine)

    assert "settled_at" in {
        column["name"] for column in inspect(engine).get_columns("conversations")
    }


def test_unrepairable_legacy_database_refuses_the_stamp(engine, snapshots, monkeypatch):
    """A gap that survives healing must not be stamped as current."""
    _legacy_database(engine)
    from vendoo_studio.services.schema_drift import Drift

    monkeypatch.setattr(
        "vendoo_studio.services.schema_drift.inspect_database",
        lambda path=None: Drift(
            missing_tables=(),
            missing_columns=("listings.price",),
            unknown_tables=(),
        ),
    )

    with pytest.raises(SchemaError, match="listings.price"):
        ensure_schema(engine)

    assert recorded_revision(engine) is None


def test_a_database_from_a_newer_build_is_refused(engine, snapshots):
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'f00dcafe9999'")

    with pytest.raises(SchemaError, match="newer version"):
        ensure_schema(engine)


def test_an_up_to_date_database_is_left_alone(engine, snapshots):
    ensure_schema(engine)
    before = backups.list_snapshots(snapshots)

    assert ensure_schema(engine) == head_revision()
    assert backups.list_snapshots(snapshots) == before


def test_ensure_schema_is_idempotent(engine, snapshots):
    first = ensure_schema(engine)
    second = ensure_schema(engine)
    third = ensure_schema(engine)

    assert first == second == third
    assert inspect_database(engine.url.database).clean


def test_the_draft_cache_takes_over_from_the_old_draft_events(engine, snapshots):
    """A database from the previous release keeps its statuses and loses the bulk."""
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "c3a71f0b5d42")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute(
            "INSERT INTO conversations (id, title) VALUES ('c1', 'vintage coat')"
        )
        connection.execute(
            "INSERT INTO jobs (id, conversation_id, approved_revision_id, listing_snapshot) "
            "VALUES ('j1', 'c1', 'r1', '{}')"
        )
        for sequence, listed in enumerate([False, False, True]):
            payload = (
                '{"ok": true, "source": "vendoo_sync", "item_id": "itm1", '
                '"url": "https://web.vendoo.co/app/item/itm1", '
                '"item": {"generalDetails": {"description": "' + "x" * 2000 + '"}, '
                '"listings": {"ebay": {"status": {"listed": ' + str(listed).lower() + '}}}}}'
            )
            connection.execute(
                "INSERT INTO job_events (id, job_id, sequence, event_type, payload, created_at) "
                f"VALUES ('e{sequence}', 'j1', {sequence}, 'vendoo_draft', ?, "
                f"'2026-09-2{sequence} 00:00:00')",
                (payload,),
            )

    assert ensure_schema(engine) == head_revision()

    with sqlite3.connect(engine.url.database) as connection:
        cached = connection.execute(
            "SELECT item_id, source, payload FROM vendoo_draft_cache"
        ).fetchall()
        left = connection.execute(
            "SELECT COUNT(*) FROM job_events WHERE event_type = 'vendoo_draft'"
        ).fetchone()

    assert left == (0,)
    assert len(cached) == 1
    item_id, source, payload = cached[0]
    assert (item_id, source) == ("itm1", "vendoo_sync")
    assert payload == '{"item": {"listings": {"ebay": {"status": {"listed": true}}}}}'


REVISION_TEMPLATE = '''"""{message}"""
from alembic import op
import sqlalchemy as sa

revision = {revision!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    {upgrade}


def downgrade() -> None:
    pass
'''


@pytest.fixture
def scripted_migrations(tmp_path, monkeypatch):
    """A migrations folder this test owns, so revisions can be added to it."""
    directory = tmp_path / "migrations"
    (directory / "versions").mkdir(parents=True)
    real = schema_migrations.migrations_dir()
    (directory / "env.py").write_text((real / "env.py").read_text())
    (directory / "script.py.mako").write_text((real / "script.py.mako").read_text())
    monkeypatch.setattr(schema_migrations, "migrations_dir", lambda: directory)

    def add(revision, down, upgrade, message="test revision"):
        (directory / "versions" / f"{revision}.py").write_text(
            REVISION_TEMPLATE.format(
                message=message, revision=revision, down=down, upgrade=upgrade
            )
        )

    return add


def test_a_new_revision_is_applied_and_snapshotted_first(engine, snapshots, scripted_migrations):
    scripted_migrations(
        "0001",
        None,
        "op.create_table('probe', sa.Column('id', sa.String(), primary_key=True))",
    )
    assert ensure_schema(engine) == "0001"
    assert backups.list_snapshots(snapshots) == []

    scripted_migrations(
        "0002",
        "0001",
        "op.add_column('probe', sa.Column('note', sa.String(), nullable=True))",
    )

    assert ensure_schema(engine) == "0002"
    assert "note" in {column["name"] for column in inspect(engine).get_columns("probe")}
    assert [snapshot.reason for snapshot in backups.list_snapshots(snapshots)] == ["pre-migration"]


def test_a_failing_revision_leaves_the_snapshot_behind(engine, snapshots, scripted_migrations, tmp_path):
    """When a migration blows up, the copy taken beforehand is what saves you."""
    scripted_migrations(
        "0001",
        None,
        "op.create_table('probe', sa.Column('id', sa.String(), primary_key=True))",
    )
    ensure_schema(engine)

    scripted_migrations("0002", "0001", "op.drop_table('does_not_exist')")

    with pytest.raises(OperationalError):
        ensure_schema(engine)

    snapshot = backups.latest_snapshot(snapshots)
    assert snapshot is not None
    assert snapshot.reason == "pre-migration"

    unpacked = backups.decompress_snapshot(snapshot.path, tmp_path / "rolled-back.db")
    restored = sqlite3.connect(f"file:{unpacked}?mode=ro", uri=True)
    try:
        assert restored.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001",)
    finally:
        restored.close()


def test_the_models_match_the_migrations(engine, snapshots):
    """A model change without a revision must fail here, not on someone's machine.

    Everything the migrations build is compared against what the models
    declare. A column added to a model and not to a revision shows up as a
    difference, which is the failure earlier builds could not detect: startup
    would create nothing, and the gap would surface later as a query error on
    a database that had already been through an update.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from vendoo_studio.database import Base, load_models

    load_models()
    ensure_schema(engine)

    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == [], (
        "The models and the migrations have drifted apart. Generate a revision:\n"
        "  cd vendoo-studio && .venv/bin/alembic revision --autogenerate -m \"...\"\n"
        f"Differences: {differences}"
    )


def test_listing_evidence_migrates_previous_release_without_inventing_history(engine, snapshots):
    from alembic import command
    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "47a52dece27b")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title) VALUES ('seller-item', 'Original listing')")
        connection.execute("INSERT INTO listing_revisions (id, conversation_id, listing_json, source) VALUES ('seller-rev', 'seller-item', '{}', 'user_form')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT title FROM conversations WHERE id = 'seller-item'").fetchone() == ("Original listing",)
        assert connection.execute("SELECT source FROM listing_revisions WHERE id = 'seller-rev'").fetchone() == ("user_form",)
        for table in ("listing_corrections", "sale_snapshots"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'listing_evidence'").fetchone() is None
    assert [snapshot.reason for snapshot in backups.list_snapshots(snapshots)] == ["pre-migration"]


def test_linked_listings_get_the_sync_baseline_imports_now_record(engine, snapshots):
    """An import from before the baseline existed is level with Vendoo as imported."""
    import json

    from alembic import command
    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "9d4e7a2b1c60")
    bound = json.dumps({"vendooItemId": "itm"})
    with sqlite3.connect(engine.url.database) as connection:
        for conv_id, notes in [
            ("imported", bound),
            ("regenerated", bound),
            ("synced", json.dumps({"vendooItemId": "itm", "vendooSyncedRevision": "kept"})),
            ("unlinked", None),
        ]:
            connection.execute(
                "INSERT INTO conversations (id, title, notes) VALUES (?, 'x', ?)", (conv_id, notes),
            )
        for rev_id, conv_id, source, created in [
            ("imp-old", "imported", "vendoo_import", "2026-09-20 03:07:00"),
            ("imp-named", "imported", "vendoo_import", "2026-09-20 03:07:01"),
            ("norm", "imported", "dropdown_normalize", "2026-09-21 00:00:00"),
            ("blank", "regenerated", "reset", "2026-10-09 00:29:00"),
            ("gen", "regenerated", "model", "2026-10-09 00:31:00"),
            ("own", "synced", "vendoo_import", "2026-09-20 00:00:00"),
            ("draft", "unlinked", "model", "2026-09-20 00:00:00"),
        ]:
            connection.execute(
                "INSERT INTO listing_revisions (id, conversation_id, listing_json, source, created_at) "
                "VALUES (?, ?, '{}', ?, ?)",
                (rev_id, conv_id, source, created),
            )
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        notes = {
            conv_id: json.loads(raw) if raw else {}
            for conv_id, raw in connection.execute("SELECT id, notes FROM conversations")
        }
    assert notes["imported"]["vendooSyncedRevision"] == "imp-named"
    assert notes["imported"]["vendooSyncedAt"] == "1789873621000"
    assert notes["regenerated"]["vendooSyncedRevision"] == "blank"
    assert notes["synced"] == {"vendooItemId": "itm", "vendooSyncedRevision": "kept"}
    assert notes["unlinked"] == {}


def test_previous_release_keeps_listing_identity_when_stock_observations_are_added(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "292cc5d3b9a5")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title) VALUES ('stock-item', 'Multi-unit coat')")
        connection.execute("INSERT INTO marketplace_listings (id, conversation_id, marketplace, state, listing_id, listing_url, created_at, updated_at) "
                           "VALUES ('binding', 'stock-item', 'etsy', 'published', '555', 'https://www.etsy.com/listing/555', '2026-10-26', '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        row = connection.execute("SELECT listing_id, state, inventory_observation FROM marketplace_listings WHERE id='binding'").fetchone()
    assert row == ("555", "published", None)
    assert inspect_database(engine.url.database).clean


def test_existing_sale_alerts_keep_identity_and_dismissal_when_order_ledger_is_added(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "5f761d58b211")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO marketplace_sales (id, marketplace, order_id, line_id, listing_id, state, quantity, received_at) "
                           "VALUES ('keep-sale', 'etsy', '100', '200', '555', 'dismissed', 2, '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        row = connection.execute("SELECT order_id, line_id, state, quantity, order_status, order_observed_at FROM marketplace_sales WHERE id='keep-sale'").fetchone()
    assert row == ("100", "200", "dismissed", 2, "paid", None)
    assert inspect_database(engine.url.database).clean


def test_existing_orders_keep_identity_and_state_when_financial_records_are_added(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "6bc19d8ea678")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO marketplace_sales (id, marketplace, order_id, line_id, listing_id, state, quantity, order_status, received_at) "
                           "VALUES ('keep-financial-sale', 'etsy', '100', '200', '555', 'dismissed', 2, 'refunded', '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        row = connection.execute("SELECT order_id, line_id, listing_id, state, quantity, order_status, financials, financial_revision FROM marketplace_sales WHERE id='keep-financial-sale'").fetchone()
    assert row == ("100", "200", "555", "dismissed", 2, "refunded", None, 0)
    assert inspect_database(engine.url.database).clean


def test_copy_receipts_upgrade_keeps_existing_drafts_and_financial_records(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "9661374eb3a1")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status) VALUES ('copy-source', 'Existing coat', 'draft')")
        connection.execute("INSERT INTO marketplace_sales (id, marketplace, order_id, line_id, state, received_at, financials, financial_revision) "
                           "VALUES ('sale-copy-upgrade', 'etsy', 'order', 'line', 'dismissed', '2026-10-26', '{\"currency\":\"USD\",\"fees\":\"2.25\"}', 3)")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT title, status FROM conversations WHERE id='copy-source'").fetchone() == ("Existing coat", "draft")
        row = connection.execute("SELECT order_id, state, financials, financial_revision FROM marketplace_sales WHERE id='sale-copy-upgrade'").fetchone()
        assert row == ("order", "dismissed", '{"currency":"USD","fees":"2.25"}', 3)
        assert connection.execute("SELECT count(*) FROM listing_copy_receipts").fetchone() == (0,)
    assert inspect_database(engine.url.database).clean


def test_template_upgrade_preserves_existing_copy_receipts_and_marks_listing_sources(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "5909d3d92ccf")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status) VALUES ('old-copy', 'Existing copy', 'draft')")
        connection.execute("INSERT INTO listing_videos (id, conversation_id, original_filename, stored_filename, mime_type, checksum, size_bytes, duration_seconds, width, height) VALUES ('old-video', 'old-copy', 'coat.mp4', 'saved.mp4', 'video/mp4', 'checksum', 100, 10, 640, 480)")
        connection.execute("INSERT INTO listing_copy_receipts (request_id, fingerprint, source_id, conversation_id, created_at) VALUES (?, ?, 'old-source', 'old-copy', '2026-10-26')", ("a" * 32, "b" * 64))
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT source_kind, source_id, conversation_id, fingerprint FROM listing_copy_receipts").fetchone() == ("listing", "old-source", "old-copy", "b" * 64)
        assert connection.execute("SELECT title FROM conversations WHERE id='old-copy'").fetchone() == ("Existing copy",)
        assert connection.execute("SELECT stored_filename, checksum FROM listing_videos WHERE id='old-video'").fetchone() == ("saved.mp4", "checksum")
        assert connection.execute("SELECT count(*) FROM listing_templates").fetchone() == (0,)
    assert inspect_database(engine.url.database).clean


def test_marketplace_review_upgrade_preserves_tasks_but_expires_unpinned_pending_cards(engine, snapshots):
    from alembic import command
    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "c4d8e2b71a05")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id,title,status) VALUES ('approval-item','Existing coat','active')")
        connection.execute("INSERT INTO automations (id,name,status,timezone,schedule,filters,actions,marketplaces,require_approval,created_at,updated_at) VALUES ('task','Existing task','active','UTC','{}','{}','[]','[\"ebay\"]',0,'2026-10-26','2026-10-26')")
        connection.execute("INSERT INTO automation_approvals (id,automation_id,conversation_id,title,revision_id,marketplaces,status,created_at) VALUES ('card','task','approval-item','Existing coat','old-rev','[\"ebay\"]','pending','2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT name,status FROM automations WHERE id='task'").fetchone() == ("Existing task","active")
        assert connection.execute("SELECT title,revision_id,status,action,targets,review_basis FROM automation_approvals WHERE id='card'").fetchone() == ("Existing coat","old-rev","superseded","publish","{}","")
        assert "require_approval" not in {row[1] for row in connection.execute("PRAGMA table_info(automations)")}
    assert inspect_database(engine.url.database).clean


def test_ebay_variation_groups_upgrade_keeps_existing_ebay_listing_identity(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "151f7674a7c7")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title) VALUES ('ebay-item', 'Existing tee')")
        connection.execute("INSERT INTO marketplace_listings (id, conversation_id, marketplace, state, ebay_sku, ebay_offer_id, ebay_listing_id, "
                           "ebay_inventory_written, created_at, updated_at) VALUES ('binding', 'ebay-item', 'ebay', 'draft', 'TEE-1', 'offer-9', NULL, 1, "
                           "'2026-10-26', '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        row = connection.execute("SELECT state, ebay_sku, ebay_offer_id, ebay_inventory_written, ebay_group_key, ebay_variant_offers "
                                 "FROM marketplace_listings WHERE id='binding'").fetchone()
    assert row == ("draft", "TEE-1", "offer-9", 1, None, None)
    assert recorded_revision(engine) == head_revision()


def test_photo_edit_upgrade_keeps_every_photo_unedited(engine, snapshots):
    from alembic import command
    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "a7c3e91f5d20")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id,title,status) VALUES ('item','Denim jacket','draft')")
        connection.execute("INSERT INTO photos (id,conversation_id,original_filename,stored_filename,mime_type,size_bytes,display_order,checksum) VALUES ('photo','item','front.jpg','saved.jpg','image/jpeg',10,0,'checksum')")
        connection.execute("INSERT INTO marketplace_listings (id,conversation_id,marketplace,state,etsy_image_ids,created_at,updated_at) VALUES ('etsy','item','etsy','draft','{\"photo\": \"100\"}','2026-10-26','2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT stored_filename,checksum,source_filename,edits FROM photos").fetchone() == ("saved.jpg","checksum",None,None)
        assert connection.execute("SELECT etsy_image_ids,etsy_image_checksums FROM marketplace_listings").fetchone() == ('{"photo": "100"}',None)
    assert inspect_database(engine.url.database).clean


def test_seller_sales_upgrade_marks_every_existing_line_as_marketplace_reported(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "a7e3c1f09b42")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status) VALUES ('sold-coat', 'Sold coat', 'sold')")
        connection.execute("INSERT INTO marketplace_sales (id, marketplace, order_id, line_id, listing_id, state, quantity, order_status, conversation_id, received_at, financials, financial_revision) "
                           "VALUES ('keep-line', 'depop', 'order', 'line', 'abc', 'new', 1, 'paid', 'sold-coat', '2026-10-26', '{\"currency\":\"USD\",\"fees\":\"3\"}', 2)")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        row = connection.execute("SELECT order_id, state, conversation_id, financials, financial_revision, source FROM marketplace_sales WHERE id='keep-line'").fetchone()
    assert row == ("order", "new", "sold-coat", '{"currency":"USD","fees":"3"}', 2, "marketplace")
    assert inspect_database(engine.url.database).clean


def test_promotions_upgrade_keeps_previous_release_data_and_starts_empty(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "7d2e4c9a1b35")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status) VALUES ('posh-item', 'Existing coat', 'active')")
        connection.execute("INSERT INTO listing_templates (id, name, fields, revision, created_at, updated_at) "
                           "VALUES ('tpl', 'Coats', '{}', 1, '2026-10-26', '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT title, status FROM conversations WHERE id='posh-item'").fetchone() == ("Existing coat", "active")
        assert connection.execute("SELECT name FROM listing_templates WHERE id='tpl'").fetchone() == ("Coats",)
        assert connection.execute("SELECT count(*) FROM listing_promotions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM marketplace_offer_floors").fetchone() == (0,)
        connection.execute("INSERT INTO marketplace_offer_floors (id, marketplace, listing_id, conversation_id, floor_cents, updated_at) "
                           "VALUES ('f1', 'poshmark', 'abc', 'posh-item', 2500, '2026-10-26')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO marketplace_offer_floors (id, marketplace, listing_id, floor_cents, updated_at) "
                               "VALUES ('f2', 'poshmark', 'abc', 3000, '2026-10-26')")
    assert recorded_revision(engine) == head_revision()
    assert inspect_database(engine.url.database).clean


def test_promotion_automations_upgrade_keeps_seller_sends_and_starts_with_nothing_enabled(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "3ece513f8e0d")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status) VALUES ('posh-item', 'Existing coat', 'active')")
        connection.execute("INSERT INTO listing_promotions (id, batch_id, conversation_id, marketplace, action, listing_id, state, note, created_at, expires_at, sent_at) "
                           "VALUES ('p1', 'b1', 'posh-item', 'poshmark', 'offer', 'abc', 'unknown', 'Check it', '2026-10-26', '2026-10-26', '2026-10-26')")
        connection.execute("INSERT INTO marketplace_offer_floors (id, marketplace, listing_id, conversation_id, floor_cents, updated_at) "
                           "VALUES ('f1', 'poshmark', 'abc', 'posh-item', 2500, '2026-10-26')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT state, note, automation_id FROM listing_promotions WHERE id='p1'").fetchone() == ("unknown", "Check it", None)
        assert connection.execute("SELECT floor_cents FROM marketplace_offer_floors WHERE id='f1'").fetchone() == (2500,)
        assert connection.execute("SELECT count(*) FROM promotion_automations").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM promotion_runs").fetchone() == (0,)
    assert recorded_revision(engine) == head_revision()
    assert inspect_database(engine.url.database).clean


def test_similar_items_upgrade_keeps_previous_release_items_and_photos(engine, snapshots):
    from alembic import command

    config = schema_migrations.alembic_config(str(engine.url))
    command.upgrade(config, "ea3c87866204")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("INSERT INTO conversations (id, title, status, notes) VALUES ('coat', 'Wool coat', 'active', '{\"vendooLabels\": \"A19\"}')")
        connection.execute("INSERT INTO photos (id,conversation_id,original_filename,stored_filename,mime_type,size_bytes,display_order,checksum) "
                           "VALUES ('front','coat','front.jpg','saved.jpg','image/jpeg',10,0,'abc')")
    ensure_schema(engine)
    with sqlite3.connect(engine.url.database) as connection:
        assert connection.execute("SELECT title, notes FROM conversations").fetchone() == ("Wool coat", '{"vendooLabels": "A19"}')
        assert connection.execute("SELECT checksum FROM photos").fetchone() == ("abc",)
        assert connection.execute("SELECT count(*) FROM photo_fingerprints").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM similar_item_reviews").fetchone() == (0,)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO conversations (id, title) VALUES ('copy', 'Wool coat')")
        connection.execute("INSERT INTO similar_item_reviews (id, first_id, second_id, decision) VALUES ('r1', 'coat', 'copy', 'dismissed')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO similar_item_reviews (id, first_id, second_id, decision) VALUES ('r2', 'coat', 'copy', 'merged')")
        connection.execute("DELETE FROM conversations WHERE id='copy'")
        assert connection.execute("SELECT count(*) FROM similar_item_reviews").fetchone() == (0,)
    assert recorded_revision(engine) == head_revision()
    assert inspect_database(engine.url.database).clean
