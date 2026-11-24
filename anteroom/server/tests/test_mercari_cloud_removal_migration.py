"""Mercari is no longer a cloud marketplace: its saved cloud sign-ins are removed on upgrade."""
from datetime import UTC, datetime

from alembic import command
from sqlalchemy import MetaData, Table, select, text

from test_seller_ownership import migration_config, migration_engine  # noqa: F401

SELLER = "11111111-1111-4111-8111-111111111111"
MERCARI_SECRET = "22222222-2222-4222-8222-222222222222"
POSHMARK_SECRET = "33333333-3333-4333-8333-333333333333"


def test_upgrade_removes_mercari_cloud_sessions_and_their_secrets(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "e4f6c0a40d63-1")
    postgres = engine.dialect.name == "postgresql"
    previous = MetaData()
    sessions = Table("cloud_marketplace_sessions", previous, autoload_with=engine)
    journals = Table("cloud_operation_journals", previous, autoload_with=engine)
    references = Table("user_secret_references", previous, autoload_with=engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        if postgres:
            # Supabase Vault stands in as the table the revision reads.
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS vault"))
            connection.execute(text("CREATE TABLE IF NOT EXISTS vault.secrets (id uuid PRIMARY KEY, name text)"))
            connection.execute(text("INSERT INTO vault.secrets (id, name) VALUES (:mercari, :mercari_name), (:poshmark, :poshmark_name)"), {
                "mercari": MERCARI_SECRET, "mercari_name": f"anteroom:{SELLER}:cloud-session-mercari",
                "poshmark": POSHMARK_SECRET, "poshmark_name": f"anteroom:{SELLER}:cloud-session-poshmark"})
        for market in ("mercari", "poshmark"):
            connection.execute(sessions.insert(), {"id": f"session-{market}", "user_id": SELLER, "marketplace": market,
                                                   "state": "connected", "updated_at": now})
            connection.execute(journals.insert(), {"id": f"journal-{market}", "user_id": SELLER, "marketplace": market,
                                                   "journal_key": f"promotion_{market}", "payload": {"finished": True},
                                                   "updated_at": now})
        connection.execute(references.insert(), [
            {"id": "ref-mercari", "user_id": SELLER, "account": "cloud-session-mercari", "secret_id": MERCARI_SECRET},
            {"id": "ref-poshmark", "user_id": SELLER, "account": "cloud-session-poshmark", "secret_id": POSHMARK_SECRET},
        ])

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalars(select(sessions.c.marketplace)).all() == ["poshmark"]
        assert connection.scalars(select(journals.c.marketplace)).all() == ["poshmark"]
        assert connection.scalars(select(references.c.account)).all() == ["cloud-session-poshmark"]
        if postgres:
            assert connection.scalars(text("SELECT name FROM vault.secrets")).all() == [
                f"anteroom:{SELLER}:cloud-session-poshmark"]
