"""Mode isolation, immutable draft sends, and recovery without replaying writes."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.job import Job
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services import listing_mode, listing_persistence, user_settings, vendoo_drafts
from vendoo_studio.services.photo_store import LocalPhotoStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(user_settings, "settings_path", lambda: tmp_path / "settings.json")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    monkeypatch.setattr(vendoo_drafts, "SessionLocal", sessions)
    monkeypatch.setattr("vendoo_studio.services.extension_connection.current_extension",
        lambda: SimpleNamespace(connected=True, capabilities={"vendoo.drafts"}))
    photos = LocalPhotoStore(tmp_path / "photos")
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", photos)
    photos.put("test.jpg", b"approved photograph")
    conv = ConversationRepo(db).create(title="Private Vendoo draft")
    rev = listing_persistence.save_revision(db, conv.id,
        {"title": "Manual draft", "description": "Manually entered", "price": 20, "quantity": 1}, "user")
    listing_persistence.add_photo(db, conv.id, "test.jpg", "test.jpg", "image/jpeg", 1)
    app.dependency_overrides[get_db] = lambda: db
    tasks = []
    def launch(coro):
        assert asyncio.get_running_loop().is_running(), "Background sends must start on the server's event loop."
        tasks.append(coro)
    monkeypatch.setattr(vendoo_drafts, "spawn", launch)
    yield db, TestClient(app), conv.id, rev.id, tasks
    for task in tasks:
        task.close()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def enable(client):
    response = client.put("/api/settings/ui", json={"listing_mode": "vendoo"})
    assert response.status_code == 200, response.text
    assert response.json()["listing_mode"] == "vendoo"


def test_default_is_direct_and_disabled_vendoo_touches_no_browser(workspace, monkeypatch):
    _db, client, conv, revision, tasks = workspace
    bridge = AsyncMock()
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", bridge)
    assert client.get("/api/settings/ui").json()["listing_mode"] == "direct"
    assert client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision}).status_code == 409
    bridge.assert_not_called()
    assert not tasks


@pytest.mark.parametrize("market", ["ebay", "etsy", "poshmark", "mercari", "depop", "vinted", "sellwild"])
def test_vendoo_mode_refuses_direct_list_on_every_marketplace(workspace, monkeypatch, market):
    _db, client, conv, revision, tasks = workspace
    enable(client)
    bridge = AsyncMock()
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", bridge)
    response = client.post(f"/api/conversations/{conv}/native-marketplaces/{market}/list",
                           json={"revision_id": revision, "confirm": True})
    assert response.status_code == 409, response.text
    assert "listing mode" in response.json()["detail"]
    bridge.assert_not_called()
    assert not tasks


def test_mode_cannot_change_while_a_send_runs(workspace):
    db, client, conv, revision, tasks = workspace
    enable(client)
    started = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    assert started.status_code == 200, started.text
    assert len(tasks) == 1
    assert client.put("/api/settings/ui", json={"listing_mode": "direct"}).status_code == 409
    JobRepo(db).update_status(started.json()["job_id"], "failed", error="Interrupted before dispatch")
    assert client.put("/api/settings/ui", json={"listing_mode": "direct"}).status_code == 200


def test_draft_send_roundtrip_releases_connection_and_never_publishes(workspace, monkeypatch):
    db, client, conv, revision, tasks = workspace
    enable(client)
    response = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    job_id = response.json()["job_id"]
    tasks.pop().close()
    db.commit()
    active = set()
    def checkout(connection, *_args):
        active.add(connection)
    def checkin(connection, *_args):
        active.discard(connection)
    engine = db.get_bind()
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    sent = {}
    calls = []
    async def bridge(job, kind, payload, **kwargs):
        assert kind == "job.vendoo_api" and payload["listing_mode"] == "vendoo"
        assert not active
        results = []
        for op in payload["ops"]:
            calls.append(op["op"])
            value = {"session": {"uid": "seller"}, "new_item_id": {"item_id": "a" * 20},
                "subscription": {"version": "3"}, "upload_photo": {"image": {"version": 3, "id": "image-id"}}}.get(op["op"], {})
            if op["op"] == "create_item":
                sent.update(copy.deepcopy(op["item"]))
            if op["op"] == "get_item":
                value = {"item": copy.deepcopy(sent)}
            results.append({"op": op["op"], "ok": True, **value})
        return {"ok": True, "results": results}
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", bridge)
    try:
        asyncio.run(vendoo_drafts.run(job_id))
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
    result = client.get(f"/api/conversations/{conv}/vendoo-draft").json()
    assert result["status"] == "completed", result
    assert sent["generalDetails"]["title"] == "Manual draft"
    assert sent["generalDetails"]["images"][0]["id"] == "image-id"
    assert calls.count("create_item") == 1
    assert not ({"list_item", "delist_item", "delete_item"} & set(calls))


def test_cancelling_after_uploads_stops_before_the_draft_write(workspace, monkeypatch):
    db, client, conv, revision, tasks = workspace
    enable(client)
    response = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    job_id = response.json()["job_id"]
    tasks.pop().close()
    db.commit()
    calls = []
    async def bridge(_job, _kind, payload, **_kwargs):
        results = []
        for op in payload["ops"]:
            calls.append(op["op"])
            value = {"session": {"uid": "seller"}, "new_item_id": {"item_id": "a" * 20},
                "subscription": {"version": "3"}, "upload_photo": {"image": {"version": 3, "id": "image-id"}}}.get(op["op"], {})
            if op["op"] == "upload_photo":
                db.execute(update(Job).where(Job.id == job_id).values(status="cancelled"))
                db.commit()
            results.append({"op": op["op"], "ok": True, **value})
        return {"ok": True, "results": results}
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", bridge)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(vendoo_drafts.run(job_id))
    assert "create_item" not in calls
    db.expire_all()
    assert JobRepo(db).get(job_id).status == "cancelled"


def test_failed_reserved_draft_is_not_created_again(workspace):
    db, client, conv, revision, _tasks = workspace
    enable(client)
    job = JobRepo(db).create(conv, revision, {}, status="failed", current_step="vendoo_check_required")
    job.vendoo_item_id = "a" * 20
    db.commit()
    response = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    assert response.status_code == 409
    assert "Check the existing" in response.json()["detail"]


def test_an_old_bridge_is_refused_immediately_without_starting_a_job(workspace, monkeypatch):
    _db, client, conv, revision, tasks = workspace
    enable(client)
    monkeypatch.setattr("vendoo_studio.services.extension_connection.current_extension",
        lambda: SimpleNamespace(connected=True, capabilities={"marketplace.actions"}))
    response = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    assert response.status_code == 409
    assert "0.2.55" in response.json()["detail"]
    assert not tasks


def test_owned_updates_preserve_remote_listing_states_and_allow_clearing_fields():
    before = {"generalDetails": {"title": "Before", "brand": "Nike"}, "labels": [],
              "listings": {"poshmark": {"status": {"listed": True}, "overrides": {"title": "Before"}}}}
    after = {"generalDetails": {"title": "After"}, "labels": [],
             "listings": {"poshmark": {"status": {"listed": False}, "overrides": {"title": "After"}}}}
    updates = vendoo_drafts._updates(vendoo_drafts._owned(before), vendoo_drafts._owned(after))
    assert updates == {"generalDetails.title": "After", "generalDetails.brand": None,
                       "listings.poshmark.overrides.title": "After"}


def test_checking_a_missing_reserved_draft_allows_a_new_send(workspace, monkeypatch):
    db, client, conv, revision, tasks = workspace
    enable(client)
    job = JobRepo(db).create(conv, revision, {"_vendoo_uid": "seller"},
        status="failed", current_step="vendoo_check_required")
    job.vendoo_item_id = "a" * 20
    db.commit()
    bridge = AsyncMock(return_value={"ok": True, "results": [
        {"op": "session", "ok": True, "uid": "seller"}, {"op": "get_item", "ok": True, "item": None}]})
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", bridge)
    checked = client.post(f"/api/conversations/{conv}/vendoo-draft/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["url"] is None
    assert "no draft" in checked.json()["error"]
    assert [op["op"] for op in bridge.call_args.args[2]["ops"]] == ["session", "get_item"]
    response = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    assert response.status_code == 200, response.text
    assert len(tasks) == 1


def test_readback_refuses_dropped_fields_and_updates_preserve_independent_edits():
    item = {"userID": "seller", "itemID": "a" * 20,
            "generalDetails": {"title": "Expected", "brand": "Nike"}, "listings": {}, "labels": []}
    remote = copy.deepcopy(item)
    remote["generalDetails"]["title"] = "Dropped title"
    with pytest.raises(ValueError, match="different draft fields"):
        vendoo_drafts._verify(item, remote)
    remote["generalDetails"]["title"] = "Expected"
    remote["generalDetails"]["brand"] = "Seller's independent Vendoo edit"
    vendoo_drafts._verify(item, remote, {"generalDetails.title": "Expected"})


def test_checking_different_fields_allows_an_explicit_update_of_the_same_draft(workspace, monkeypatch):
    db, client, conv, revision, tasks = workspace
    enable(client)
    expected = {"userID": "seller", "itemID": "a" * 20, "generalDetails": {"title": "Expected"}}
    remote = {**expected, "generalDetails": {"title": "Older draft", "brand": "Manual Vendoo brand"}}
    job = JobRepo(db).create(conv, revision, {"_vendoo_uid": "seller", "_vendoo_sent": expected},
        status="failed", current_step="vendoo_check_required")
    job.vendoo_item_id = expected["itemID"]
    db.commit()
    monkeypatch.setattr(vendoo_drafts.browser_bridge, "request", AsyncMock(return_value={"ok": True, "results": [
        {"op": "session", "ok": True, "uid": "seller"}, {"op": "get_item", "ok": True, "item": remote}]}))
    checked = client.post(f"/api/conversations/{conv}/vendoo-draft/check")
    assert checked.status_code == 200
    assert checked.json()["status"] == "failed"
    assert "Save changes" in checked.json()["error"]
    sent = client.post(f"/api/conversations/{conv}/vendoo-draft", json={"revision_id": revision})
    assert sent.status_code == 200, sent.text
    assert len(tasks) == 1
    new_job = JobRepo(db).get(sent.json()["job_id"])
    assert new_job.listing_snapshot["_vendoo_previous"]["item_id"] == expected["itemID"]
    assert new_job.listing_snapshot["_vendoo_previous"]["sent"]["generalDetails"] == expected["generalDetails"]
    assert new_job.listing_snapshot["_vendoo_previous"]["retry"] == {"generalDetails.title": "Expected"}


def test_mode_checks_do_not_accept_the_old_enabled_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(user_settings, "settings_path", lambda: tmp_path / "prefs.json")
    user_settings.update_settings(lambda payload: payload.update({"ui": {"vendoo_enabled": True}}))
    assert listing_mode.read() == "direct"
    with pytest.raises(HTTPException):
        listing_mode.require("vendoo")
