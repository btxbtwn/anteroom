"""Test engines: which ones let worker threads query at the same time."""
from sqlalchemy.orm import sessionmaker

from worker_sessions import file_engine, memory_engine


def _connections(engine):
    sessions = sessionmaker(bind=engine)
    first, second = sessions(), sessions()
    try:
        # Both open at once, as a stream's worker thread and another request's query are.
        return first.connection().connection.dbapi_connection, second.connection().connection.dbapi_connection
    finally:
        first.close()
        second.close()


def test_file_engine_gives_each_open_session_its_own_connection(tmp_path):
    engine = file_engine(tmp_path)
    try:
        first, second = _connections(engine)
        assert first is not second
    finally:
        engine.dispose()


def test_memory_engine_shares_one_connection_so_sessions_must_take_turns():
    engine = memory_engine()
    try:
        first, second = _connections(engine)
        assert first is second
    finally:
        engine.dispose()
