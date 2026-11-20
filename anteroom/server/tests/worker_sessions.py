"""Point ``database.in_worker_session`` at a test's own database.

Async services run each database step in a short session of their own on a
worker thread (``database.in_worker_session``), opened by
``database.SessionLocal``. A test with its own engine patches that factory.

``memory_engine`` shares ONE sqlite3 connection between every session and
thread. That is only safe while sessions take turns. When a test lets worker
threads query at the same time (a stream running while another request reads),
use ``file_engine``: each session gets its own connection, as in the app.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def file_engine(directory):
    """A SQLite database file in ``directory``, set up as the Mac app's engine.

    Each session checks out its own connection (WAL, busy timeout), so worker
    threads can query concurrently without sharing one sqlite3 connection.
    Dispose the engine before removing ``directory``.
    """
    from anteroom.database import make_engine

    return make_engine(f"sqlite:///{Path(directory) / 'test.db'}")


def memory_engine():
    """An in-memory SQLite database every thread shares."""
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


@contextmanager
def worker_sessions(engine):
    with patch("anteroom.database.SessionLocal", sessionmaker(bind=engine)):
        yield
