"""Import coordination stays within one seller and spans database connections."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

from anteroom.services import inventory_import, service_context


@contextmanager
def seller(identity):
    with service_context.use(SimpleNamespace(seller_id=identity)):
        yield


def test_local_imports_serialize_only_the_same_seller():
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    entered = Event()
    release = Event()

    def hold():
        with seller("one"), inventory_import._import_guard(db):
            entered.set()
            assert release.wait(5)

    def acquire(identity):
        with seller(identity), inventory_import._import_guard(db):
            return True

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(hold)
        try:
            assert entered.wait(2)
            same = pool.submit(acquire, "one")
            other = pool.submit(acquire, "two")
            assert other.result(timeout=2)
            assert not same.done()
        finally:
            release.set()
        first.result()
        assert same.result(timeout=2)


def test_postgres_guard_holds_until_commit_and_separates_sellers(tmp_path):
    from pathlib import Path

    from pgserver.postgres_server import get_server
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    server = get_server(Path(tmp_path) / "postgres", cleanup_mode="delete")
    engine = create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://"))
    entered = Event()
    attempting = Event()
    release = Event()
    try:
        with engine.begin() as db:
            db.execute(text("CREATE TABLE import_guard_probe (id integer)"))

        def hold():
            with Session(engine) as db, seller("one"), inventory_import._import_guard(db):
                db.execute(text("INSERT INTO import_guard_probe VALUES (1)"))
                entered.set()
                assert release.wait(5)
                db.commit()

        def read(identity):
            with Session(engine) as db, seller(identity):
                attempting.set()
                with inventory_import._import_guard(db):
                    return db.scalar(text("SELECT count(*) FROM import_guard_probe"))

        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(hold)
            try:
                assert entered.wait(2)
                same = pool.submit(read, "one")
                assert attempting.wait(2)
                # Distinct connections share no Python lock on PostgreSQL.
                assert pool.submit(read, "two").result(timeout=2) == 0
                assert not same.done()
            finally:
                release.set()
            first.result()
            assert same.result(timeout=2) == 1
    finally:
        engine.dispose()
        server.cleanup()
