import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.services import schema_migrations


def test_migration_owner_can_advance_but_runtime_cannot_read_metadata(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('Postgres protects hosted metadata')
    schema_migrations.ensure_schema(engine)
    assert schema_migrations.recorded_revision(engine) == schema_migrations.head_revision()
    with engine.begin() as connection:
        assert connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='public.alembic_version'::regclass")).scalar_one()
        # Match Supabase defaults where client roles may have table privileges:
        # RLS must still deny reads and writes without a policy.
        connection.execute(text('GRANT SELECT, UPDATE ON alembic_version TO anteroom_seller'))
        connection.execute(text('SET LOCAL ROLE anteroom_seller'))
        assert connection.execute(text('SELECT version_num FROM alembic_version')).all() == []
        assert connection.execute(text("UPDATE alembic_version SET version_num='untrusted' ")).rowcount == 0
    assert schema_migrations.recorded_revision(engine) == schema_migrations.head_revision()
    with engine.begin() as connection:
        connection.execute(text('SET LOCAL ROLE anteroom_seller'))
        with pytest.raises(DBAPIError):
            connection.execute(text('ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY'))
