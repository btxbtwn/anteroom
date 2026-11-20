from alembic import command
from sqlalchemy import MetaData, Table, event, select
from sqlalchemy.orm import Session

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from anteroom.models.conversation import Conversation
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.routes.hosted_workspace import inventory_response
from anteroom.services import schema_migrations
from anteroom.services.account_import import _row
from anteroom.services.inventory_analytics import load_rows
from anteroom.services.suggestions import _listing_rows


def test_inventory_reads_do_not_extract_large_marketplace_forms(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat"))
        db.flush()
        db.add(ListingRevision(id="revision", conversation_id="item", source="test", listing_json={
            "sku": "coat-1", "price": 42, "quantity": 2, "cost": 5,
            "brand": "Example", "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
            "ebay_specifics": {"season": ["Winter"], "material": ["Wool"]},
            "marketplace_forms": {"unused": "large form " * 10000},
        }))
        db.flush()
        db.add(Listing(conversation_id="item", current_revision_id="revision"))
        db.add(MarketplaceListing(conversation_id="item", marketplace="ebay", state="published",
                                  listing_url="https://www.ebay.com/itm/1"))
        db.add(MarketplaceListing(conversation_id="item", marketplace="mercari", state="not_saved",
                                  error="Rejected", error_fields=[{"field": "size", "message": "Required"}]))
        db.commit()
        reads = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT") and "listing_revisions" in statement:
                reads.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            suggestion = _listing_rows(db)[0]
            analytics = load_rows(db)[0]
            from anteroom.services import hosted_read_snapshots as snapshots

            phases = {}
            token = snapshots._build_phases.set(phases)
            try:
                summary = inventory_response(db)[0]
            finally:
                snapshots._build_phases.reset(token)
            assert set(phases) == {f'step_{step}_{metric}' for step in snapshots.INVENTORY_STEPS
                                   for metric in ('ms', 'orm_calls')}
            assert phases['step_conversations_orm_calls'] == 1
            assert phases['step_covers_orm_calls'] == 1
            assert phases['step_assemble_orm_calls'] == 0
            assert all(isinstance(value, int) and value >= 0 for value in phases.values())
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert suggestion[3:] == ("Example", ["Men", "Coats"], ["Winter"], "Wool coat", "Coat", ["Wool"])
        assert analytics.price == 42 and analytics.cost == 5
        assert summary["sku"] == "coat-1" and summary["price"] == 42 and summary["quantity"] == 2
        assert summary["direct_marketplaces"] == ["ebay"]
        assert summary["direct_records"] == {
            "ebay": {"state": "published", "listing_url": "https://www.ebay.com/itm/1", "failed": False, "fields": 0, "price": None, "listed_at": None},
            "mercari": {"state": "not_saved", "listing_url": None, "failed": True, "fields": 1, "price": None, "listed_at": None},
        }
        assert reads
        assert not any("listing_json" in query for query in reads)


def test_upgrade_computes_existing_fields_and_sql_writes_keep_them_current(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "b6e2c4a81f09")
    payload = {"sku": "coat-1", "price": 42.5, "quantity": 2, "cost": 0, "brand": "Example",
               "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
               "ebay_specifics": {"season": ["Winter"], "material": ["Wool"]},
               "marketplace_forms": {"unused": "form " * 10000}}
    expected = {"sku": "coat-1", "price": 42.5, "quantity": 2, "cost": 0, "brand": "Example",
                "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
                "season": ["Winter"], "material": ["Wool"]}
    with engine.begin() as connection:
        conversations = Table("conversations", MetaData(), autoload_with=connection)
        revisions = Table("listing_revisions", MetaData(), autoload_with=connection)
        connection.execute(conversations.insert().values(id="item", title="Coat"))
        connection.execute(revisions.insert().values(id="revision", conversation_id="item", source="test", listing_json=payload))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(select(ListingRevision.inventory_fields)) == expected
        assert connection.scalar(select(ListingRevision.listing_json)) == payload
        # Direct SQL must update the projection too, independently of ORM hooks.
        connection.execute(ListingRevision.__table__.update().values(listing_json={
            "price": None, "quantity": 0, "title": "Changed", "brand": True,
            "category_path": '["Men", "Coats"]', "ebay_specifics": None,
        }))
        updated = connection.scalar(select(ListingRevision.inventory_fields))
        assert updated == {"sku": None, "price": None, "quantity": 0, "cost": None, "brand": True,
                           "category_path": '["Men", "Coats"]', "title": "Changed", "type": None,
                           "season": None, "material": None}
    # A Mac export includes generated columns; the server recomputes rather
    # than trying to insert the exported generated value.
    raw = {"id": "other", "conversation_id": "item", "source": "test", "listing_json": payload,
           "inventory_fields": {"price": 999}}
    prepared = _row(ListingRevision.__table__, raw, "11111111-1111-1111-1111-111111111111")
    assert "inventory_fields" not in prepared
    assert prepared["listing_json"] == payload


def test_hosted_inventory_reports_where_imported_listings_are_live(migration_engine):  # noqa: F811
    """The web table and sidebar read the same import fields the Mac list sends."""
    import json

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    notes = {
        "vendooMarketplaces": ["ebay", "poshmark", "mercari"],
        "vendooListingUrls": {"ebay": "https://www.ebay.com/itm/123456789012",
                              "poshmark": "https://poshmark.com/listing/Tee-66f0aabbccddeeff00112233",
                              "mercari": "https://www.mercari.com/us/item/m12345678901/"},
        "vendooLabels": "Tees, Summer",
        "vendooDates": {"listed": "2026-10-01T12:00:00Z", "soldByMarketplace": {"mercari": "2026-11-01T12:00:00Z"}},
        "marketplaceDelists": {"poshmark": {"state": "deleted", "listing_id": "66f0aabbccddeeff00112233"}},
    }
    with Session(engine) as db:
        db.add(Conversation(id="tee", title="Tee", notes=json.dumps(notes)))
        db.commit()
        summary = inventory_response(db)[0]
    assert "notes" not in summary
    assert summary["vendoo_marketplaces"] == ["ebay", "poshmark", "mercari"]
    assert summary["vendoo_listing_urls"]["ebay"] == "https://www.ebay.com/itm/123456789012"
    assert summary["removed_marketplaces"] == ["poshmark"]
    assert summary["vendoo_sold_dates"] == {"mercari": "2026-11-01T12:00:00Z"}
    assert summary["vendoo_listed_at"] == "2026-10-01T12:00:00Z"
    assert summary["vendoo_labels"] == ["Tees", "Summer"]


def test_hosted_inventory_carries_the_sale_date_the_sold_tab_sorts_by(migration_engine):  # noqa: F811
    """Date Sold (Newest) on the web sorts by each row's sale date, as the Mac list sends it."""
    from datetime import datetime

    from anteroom.models.marketplace_sale import MarketplaceSale

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add_all([Conversation(id="coat", title="Coat"), Conversation(id="tee", title="Tee")])
        db.flush()
        db.add(MarketplaceSale(marketplace="ebay", order_id="o1", line_id="l1", listing_id="1", conversation_id="coat",
                               order_status="paid", sold_at=datetime(2026, 11, 5, 21, 4, 48),
                               received_at=datetime(2026, 11, 5, 21, 6)))
        db.commit()
        rows = {row["id"]: row for row in inventory_response(db)}
    assert rows["coat"]["status"] == "sold"
    assert rows["coat"]["sold_at"].startswith("2026-11-05T21:04:48")
    assert rows["coat"]["units_sold"] == 1
    assert rows["tee"]["sold_at"] is None
    assert rows["tee"]["units_sold"] == 0


def test_hosted_inventory_names_the_marketplace_a_sold_item_sold_on(migration_engine):  # noqa: F811
    """The sidebar must not say Listed on a marketplace the item sold on (detail says Sold here)."""
    from datetime import datetime

    from anteroom.models.marketplace_sale import MarketplaceSale
    from anteroom.services import listing_persistence

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add_all([Conversation(id="top", title="Crop top"), Conversation(id="tees", title="Two tees")])
        db.commit()
        listing_persistence.save_revision(db, "tees", {"title": "Two tees", "quantity": 2}, "user")
        for conv_id in ("top", "tees"):
            # The listing record stays published after a sale: a sale never changes it.
            db.add(MarketplaceListing(conversation_id=conv_id, marketplace="poshmark", state="published",
                                      listing_url=f"https://poshmark.com/listing/{conv_id}"))
            db.add(MarketplaceListing(conversation_id=conv_id, marketplace="mercari", state="published",
                                      listing_url=f"https://www.mercari.com/us/item/m{len(conv_id)}2345678/"))
            db.add(MarketplaceSale(marketplace="poshmark", order_id=f"o-{conv_id}", line_id=f"l-{conv_id}", listing_id=conv_id,
                                   conversation_id=conv_id, order_status="paid", quantity=1,
                                   sold_at=datetime(2026, 11, 17, 14, 32), received_at=datetime(2026, 11, 17, 14, 33)))
        db.commit()
        rows = {row["id"]: row for row in inventory_response(db)}
    assert rows["top"]["status"] == "sold"
    assert rows["top"]["sold_marketplaces"] == ["poshmark"]
    # The other live listing still reads listed: it may need delisting.
    assert sorted(rows["top"]["direct_marketplaces"]) == ["mercari", "poshmark"]
    # One of two units sold: the Poshmark listing still has stock, so nothing is named sold.
    assert rows["tees"]["status"] == "active"
    assert rows["tees"]["sold_marketplaces"] == []


def test_unsent_edits_uses_stored_normalized_hash_without_reading_listing_json(migration_engine):  # noqa: F811
    from anteroom.services import listing_persistence, unsent_edits as unsent_module

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat"))
        db.commit()
        rev1 = listing_persistence.save_revision(
            db, "item", {
                "title": "Wool coat", "price": 42,
                "poshmark_specifics": {"originalPrice": 50},
                "marketplace_forms": {"unused": "x" * 1000},
            }, "user_form",
        )
        rev2 = listing_persistence.save_revision(
            db, "item", {
                "title": "Wool coat", "price": 42,
                "poshmark_specifics": {"originalPrice": 0},
                "marketplace_forms": {"unused": "x" * 1000},
            }, "user_form",
        )
        rev1_id = rev1.id
        rev2_id = rev2.id
        db.add(MarketplaceListing(
            conversation_id="item", marketplace="ebay", state="published",
            listing_url="https://www.ebay.com/itm/1", revision_id=rev1_id,
        ))
        db.commit()

        assert db.get(ListingRevision, rev1_id).normalized_hash is not None
        assert db.get(ListingRevision, rev2_id).normalized_hash == db.get(ListingRevision, rev1_id).normalized_hash

        unsent_module._REVISIONS_MATCH.clear()
        reads: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT") and "listing_revisions" in statement:
                reads.append(statement)

        convs = db.query(Conversation).all()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert unsent_module.unsent_edits(db, convs, {"item": rev2_id}) == set()
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert reads
        assert not any("listing_json" in sql for sql in reads)

        # A real content edit is still flagged without reading listing_json.
        rev3 = listing_persistence.save_revision(
            db, "item", {
                "title": "Wool coat", "price": 55,
                "poshmark_specifics": {"originalPrice": 0},
                "marketplace_forms": {"unused": "x" * 1000},
            }, "user_form",
        )
        rev3_id = rev3.id
        db.commit()
        convs = db.query(Conversation).all()
        unsent_module._REVISIONS_MATCH.clear()
        reads.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert unsent_module.unsent_edits(db, convs, {"item": rev3_id}) == {"item"}
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert reads
        assert not any("listing_json" in sql for sql in reads)

        # Legacy pre-migration rows with NULL normalized_hash backfill on first read,
        # so the next cold start never reads listing_json for them again.
        db.query(ListingRevision).update({"normalized_hash": None})
        db.commit()
        convs = db.query(Conversation).all()
        unsent_module._REVISIONS_MATCH.clear()
        assert unsent_module.unsent_edits(db, convs, {"item": rev3_id}) == {"item"}
        db.expire_all()
        assert db.get(ListingRevision, rev1_id).normalized_hash is not None
        assert db.get(ListingRevision, rev3_id).normalized_hash is not None

        convs = db.query(Conversation).all()
        unsent_module._REVISIONS_MATCH.clear()
        reads.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert unsent_module.unsent_edits(db, convs, {"item": rev3_id}) == {"item"}
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert reads
        assert not any("listing_json" in sql for sql in reads)


def test_upgrade_adds_normalized_hash_to_existing_revisions(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "26c67b32452c")
    with engine.begin() as connection:
        conversations = Table("conversations", MetaData(), autoload_with=connection)
        revisions = Table("listing_revisions", MetaData(), autoload_with=connection)
        connection.execute(conversations.insert().values(id="item", title="Coat"))
        connection.execute(revisions.insert().values(
            id="legacy-rev", conversation_id="item", source="test", listing_json={"title": "Coat", "price": 40},
        ))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(select(ListingRevision.normalized_hash).where(ListingRevision.id == "legacy-rev")) is None
        assert connection.scalar(select(ListingRevision.listing_json).where(ListingRevision.id == "legacy-rev")) == {
            "title": "Coat", "price": 40,
        }


def test_analytics_reads_paid_lines_once(migration_engine, monkeypatch):  # noqa: F811
    from anteroom.services import inventory_analytics, item_status

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat"))
        db.commit()
        calls = 0
        original = item_status.paid_lines

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(item_status, "paid_lines", counted)
        inventory_analytics._read_analytics_inputs(db, None)
        assert calls == 1


def test_suggestions_reads_compact_columns_without_listing_json(migration_engine):  # noqa: F811
    from anteroom.services.suggestions import list_suggestions

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat", status="draft"))
        db.flush()
        db.add(ListingRevision(id="rev", conversation_id="item", source="test", listing_json={
            "title": "Wool coat", "brand": "Example",
        }))
        db.flush()
        db.add(Listing(conversation_id="item", current_revision_id="rev", validation_status="error"))
        db.commit()

        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            cards = list_suggestions(db)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert [card["kind"] for card in cards] == ["fix_validation"]
        assert not any("listing_json" in sql for sql in statements)
        conv_queries = [sql for sql in statements if "conversations" in sql]
        assert conv_queries
        assert not any("updated_at" in sql or "box_id" in sql for sql in conv_queries)


def test_remember_direct_categories_refreshes_normalized_hash_and_clears_match_cache(migration_engine):  # noqa: F811
    from anteroom.models.marketplace_listing import MarketplaceListing
    from anteroom.services import chrome_catalog, listing_persistence, unsent_edits as unsent_module

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat"))
        db.commit()
        payload = {"title": "Wool coat", "price": 45}
        rev1 = listing_persistence.save_revision(db, "item", payload, "user_form")
        db.add(MarketplaceListing(
            conversation_id="item", marketplace="poshmark", state="published",
            listing_id="pm-1", revision_id=rev1.id,
        ))
        db.commit()

        # Rewriting direct_category_ids in place must refresh normalized_hash and
        # invalidate any cached pair comparisons involving that revision.
        old_hash = rev1.normalized_hash
        chrome_catalog.remember_direct_categories(db, rev1, {"poshmark": "cat-42"})
        assert rev1.normalized_hash != old_hash
        assert rev1.normalized_hash == unsent_module.normalized_listing_hash(rev1.listing_json)

        # Saving an identical copy of the updated payload must not show unsent edits.
        rev2 = listing_persistence.save_revision(db, "item", dict(rev1.listing_json), "user_form")
        db.commit()
        convs = db.query(Conversation).all()
        unsent_module._REVISIONS_MATCH.clear()
        assert unsent_module.unsent_edits(db, convs, {"item": rev2.id}) == set()

        # Rewriting rev1 again invalidates the cached match immediately.
        chrome_catalog.remember_direct_categories(db, rev1, {"depop": "dep-9"})
        assert unsent_module.unsent_edits(db, convs, {"item": rev2.id}) == {"item"}


def test_legacy_hash_backfill_batches_updates_into_one_statement(migration_engine):  # noqa: F811
    from anteroom.models.marketplace_listing import MarketplaceListing
    from anteroom.services import unsent_edits as unsent_module

    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        revisions: dict[str, str] = {}
        for idx in range(10):
            conv_id = f"item-{idx}"
            sent_id = f"sent-{idx}"
            curr_id = f"curr-{idx}"
            db.add(Conversation(id=conv_id, title=f"Coat {idx}"))
            db.flush()
            db.add_all([
                ListingRevision(id=sent_id, conversation_id=conv_id, source="test",
                                listing_json={"title": f"Coat {idx}", "price": 40}, normalized_hash=None),
                ListingRevision(id=curr_id, conversation_id=conv_id, source="test",
                                listing_json={"title": f"Coat {idx}", "price": 45}, normalized_hash=None),
            ])
            db.flush()
            db.add(Listing(conversation_id=conv_id, current_revision_id=curr_id))
            db.add(MarketplaceListing(
                conversation_id=conv_id, marketplace="ebay", state="published",
                listing_id=f"eb-{idx}", revision_id=sent_id,
            ))
            revisions[conv_id] = curr_id
        db.commit()

        convs = db.query(Conversation).all()
        unsent_module._REVISIONS_MATCH.clear()
        updates: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("UPDATE"):
                updates.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            changed = unsent_module.unsent_edits(db, convs, revisions)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert changed == set(revisions)
        # 20 legacy revisions backfilled in a single batched CASE UPDATE, not 20 round trips.
        assert len(updates) == 1
        assert "quiet_changes" not in db.info
        db.expire_all()
        stored = [
            row[0] for row in db.query(ListingRevision.normalized_hash).all()
        ]
        assert len(stored) == 20
        assert all(digest is not None for digest in stored)


