"""Point ``database.in_worker_session`` at a test's own database.

Async services run each database step in a short session of their own on a
worker thread (``database.in_worker_session``), opened by
``database.SessionLocal``. A test with its own engine patches that factory, and
uses one shared connection so every thread sees the same in-memory database.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def memory_engine():
    """An in-memory SQLite database every thread shares."""
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


@contextmanager
def worker_sessions(engine):
    with patch("vendoo_studio.database.SessionLocal", sessionmaker(bind=engine)):
        yield
