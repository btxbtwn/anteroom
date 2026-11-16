"""Worker migrations must see every shared role that their grants depend on."""
import pytest
from sqlalchemy import text


def test_api_role_exists_before_worker_migration_grants_are_applied():
    from vendoo_studio.database import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("Postgres test-cluster bootstrap")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anteroom_api')"))
        assert connection.scalar(text("SELECT has_table_privilege('anteroom_api', 'error_email_state', 'SELECT')"))
        assert connection.scalar(text("SELECT has_table_privilege('anteroom_api', 'error_email_state', 'UPDATE')"))
