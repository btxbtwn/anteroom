"""Opening a listing asks for the marketplace preflight from several places at once.

The editor, the marketplace panel and the iPhone each ask, and a save or live
update asks again. In production (2026-11-25) five or six of these held pooled
connections at the same time, each loading the whole listing for 0.5–3.6 s.
Requests that arrive while a preflight is being built share that one build.
"""
import asyncio
import threading
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base
from anteroom.main import app
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import listing_persistence, native_marketplaces as native

LISTING = {"title": "Wool coat", "description": "Warm wool coat.", "price": 40, "brand": "Pendleton",
           "condition": "Good", "weight_oz": 30, "package_dimensions_in": "14x10x4"}
PANES = 5


@pytest.fixture
def listing(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        conv = ConversationRepo(db).create(title=LISTING["title"])
        revision = listing_persistence.save_revision(db, conv.id, LISTING, "model")
        ids = conv.id, revision.id
    checkouts = []
    event.listen(engine, "checkout", lambda *_: checkouts.append(1))
    monkeypatch.setattr("anteroom.database.SessionLocal", sessions)
    return ids, checkouts


def _open_listing(conv_id: str, revision_id: str | None, joined: threading.Event):
    """Every pane asks at once; the build finishes only after all of them have asked."""
    arrived = []
    share = native.shared_preflight

    async def counting(*args):
        arrived.append(1)
        if len(arrived) == PANES:
            joined.set()
        return await share(*args)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            query = f"?revision_id={revision_id}" if revision_id else ""
            url = f"/api/conversations/{conv_id}/native-marketplaces/preflight{query}"
            return await asyncio.gather(*(client.get(url) for _ in range(PANES)))

    with patch.object(native, "shared_preflight", counting):
        return asyncio.run(run())


@pytest.mark.parametrize("asks_for_revision", [True, False])
def test_opening_a_listing_builds_one_preflight_for_every_pane(listing, asks_for_revision):
    (conv_id, revision_id), checkouts = listing
    joined = threading.Event()
    build = native.preflight
    builds = []

    def slow_build(*args):
        builds.append(1)
        joined.wait(10)
        return build(*args)

    with patch.object(native, "preflight", slow_build):
        replies = _open_listing(conv_id, revision_id if asks_for_revision else None, joined)

    assert [reply.status_code for reply in replies] == [200] * PANES, [reply.text for reply in replies]
    answers = [reply.json() for reply in replies]
    assert all(answer == answers[0] for answer in answers)
    assert answers[0]["revision_id"] == revision_id
    assert set(answers[0]["marketplaces"]) >= {"ebay", "poshmark", "mercari", "depop", "etsy"}
    assert len(builds) == 1, f"{len(builds)} preflight builds for one listing"
    assert len(checkouts) <= 2, f"{len(checkouts)} connection checkouts for one listing"
    assert native._preflight_builds == {}


def test_a_newer_revision_does_not_wait_on_an_older_build(listing):
    (conv_id, revision_id), _ = listing
    started, release = threading.Event(), threading.Event()
    build = native.preflight
    seen = []

    def slow_build(*args):
        seen.append(1)
        started.set()
        release.wait(10)
        return build(*args)

    async def run():
        older = asyncio.ensure_future(native.shared_preflight(conv_id, "older-revision"))
        await asyncio.to_thread(started.wait, 10)
        newer = asyncio.ensure_future(native.shared_preflight(conv_id, revision_id))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(older, newer)

    with patch.object(native, "preflight", slow_build):
        older, newer = asyncio.run(run())
    assert len(seen) == 2
    assert newer["revision_id"] == revision_id


def test_a_failed_build_reaches_every_waiter_and_is_not_kept(listing):
    with pytest.raises(native.NativeMarketplaceError):
        asyncio.run(native.shared_preflight("missing-listing", None))
    assert native._preflight_builds == {}
