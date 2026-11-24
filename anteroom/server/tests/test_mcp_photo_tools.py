"""MCP tools that put photos on a listing: inline, by a single-use upload link, and the cover."""
from __future__ import annotations

import base64
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.mcp_photo_upload import McpPhotoUpload
from anteroom.models.mcp_token import McpAccessToken
from anteroom.services import mcp_http, mcp_photo_tools, mcp_tokens
from anteroom.services.photo_store import photo_store

PHOTO_TOOLS = {"add_listing_photos", "create_photo_upload_url", "set_cover_photo"}


@pytest.fixture(autouse=True)
def _clean():
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot
    from anteroom.models.marketplace_listing import MarketplaceListing
    from anteroom.models.marketplace_sale import MarketplaceSale
    from anteroom.models.sourcing import SourceBox

    with SessionLocal() as db:
        for model in (McpPhotoUpload, McpAccessToken, BackgroundTask, ListingCorrection, SaleSnapshot, Message, Job,
                      MarketplaceSale, MarketplaceListing, Photo, Listing, ListingRevision, Conversation, SourceBox):
            db.query(model).delete()
        db.commit()
    mcp_tokens.clear_auth_failures()
    yield
    mcp_tokens.clear_auth_failures()


def _rpc(client, method, params=None, extra_headers=None):
    return client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                       headers=extra_headers or {})


def _call(client, name, arguments, extra_headers=None):
    resp = _rpc(client, "tools/call", {"name": name, "arguments": arguments}, extra_headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert [item["type"] for item in result["content"]] == ["text"], result
    text = result["content"][0]["text"]
    if result["isError"]:
        return {"error": text}, True
    return json.loads(text), False


def _jpeg(width=640, height=480, color="navy", orientation=None) -> bytes:
    out = io.BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    Image.new("RGB", (width, height), color).save(out, "JPEG", **kwargs)
    return out.getvalue()


def _png(width=300, height=200) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 128)).save(out, "PNG")
    return out.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _draft(client, title="Wool coat", extra_headers=None) -> str:
    made, error = _call(client, "create_draft_listing", {"title": title}, extra_headers)
    assert not error, made
    return made["conversation_id"]


def _listing_photos(conv_id):
    with SessionLocal() as db:
        return db.query(Photo).filter(Photo.conversation_id == conv_id).order_by(Photo.display_order).all()


# ---------------------------------------------------------------------------
# Catalog and permissions
# ---------------------------------------------------------------------------


def test_photo_tools_are_listed_and_need_listings_write():
    names = {tool["name"] for tool in mcp_http.tool_definitions()}
    assert PHOTO_TOOLS <= names
    for name in PHOTO_TOOLS:
        assert mcp_http.TOOL_PERMISSIONS[name] == ("listings", "write")
    create = next(t for t in mcp_http.tool_definitions() if t["name"] == "create_draft_listing")
    assert "add_listing_photos" in create["description"]
    assert "create_photo_upload_url" in create["description"]

    client = TestClient(app)
    conv_id = _draft(client)
    made = client.post("/api/settings/mcp-tokens", json={"name": "Reader", "permissions": {"listings": "read"}})
    reader = {"Authorization": f"Bearer {made.json()['token']}"}
    listed = {t["name"] for t in _rpc(client, "tools/list", extra_headers=reader).json()["result"]["tools"]}
    assert not PHOTO_TOOLS & listed
    for name, args in [
        ("add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]}),
        ("create_photo_upload_url", {"conversation_id": conv_id}),
        ("set_cover_photo", {"conversation_id": conv_id, "photo_id": "x"}),
    ]:
        refused, error = _call(client, name, args, reader)
        assert error and f"not allowed to use {name}" in refused["error"]
    assert _listing_photos(conv_id) == []
    with SessionLocal() as db:
        assert db.query(McpPhotoUpload).count() == 0


# ---------------------------------------------------------------------------
# add_listing_photos
# ---------------------------------------------------------------------------


def test_add_listing_photos_saves_like_an_app_upload():
    client = TestClient(app)
    conv_id = _draft(client)
    portrait = _jpeg(800, 400, orientation=6)  # A phone's portrait shot: stored sideways, EXIF says turn.
    added, error = _call(client, "add_listing_photos", {
        "conversation_id": conv_id,
        "photos": [
            {"data": _b64(portrait), "filename": "front.jpg"},
            # A declared type is not trusted: this is a PNG, and it is stored as one.
            {"data": "data:image/jpeg;base64," + _b64(_png()), "filename": "../../etc/tag.jpg", "mime_type": "image/jpeg"},
        ],
    })
    assert not error, added
    assert added["published"] is False and added["pushed_to_marketplaces"] is False
    rows = _listing_photos(conv_id)
    assert [p.id for p in rows] == added["added_photo_ids"]
    assert added["photo_count"] == 2
    assert [p.mime_type for p in rows] == ["image/jpeg", "image/png"]
    assert rows[1].stored_filename.endswith(".png")
    assert "/" not in rows[1].original_filename and ".." not in rows[1].original_filename
    assert photo_store().read(rows[0].stored_filename) == portrait
    # The app's own thumbnail and the agent's view both stand the photo upright.
    thumb = client.get(f"/api/photos/{rows[0].id}/thumb?size=192")
    assert thumb.status_code == 200
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.height > image.width

    more, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]})
    assert not error, more
    assert [p.display_order for p in _listing_photos(conv_id)] == [0, 1, 2]


@pytest.mark.parametrize(("photo", "message"), [
    ({"data": "not base64!!"}, "base64"),
    ({"data": _b64(b"hello, I am text pretending to be a photo"), "mime_type": "image/jpeg"}, "JPEG, PNG, WebP or HEIC"),
    ({"data": _b64(b"GIF89a" + b"\x00" * 64)}, "JPEG, PNG, WebP or HEIC"),
    ({"data": ""}, "data"),
    ("just a string", "object"),
])
def test_add_listing_photos_refuses_what_is_not_a_photo(photo, message):
    client = TestClient(app)
    conv_id = _draft(client)
    refused, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [photo]})
    assert error and message in refused["error"], refused
    assert _listing_photos(conv_id) == []


def test_add_listing_photos_names_a_photo_that_does_not_decode_and_keeps_the_others():
    client = TestClient(app)
    conv_id = _draft(client)
    broken = _jpeg()[:200]  # A JPEG header with the picture cut off.
    added, error = _call(client, "add_listing_photos", {
        "conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}, {"data": _b64(broken)}],
    })
    assert not error, added
    assert len(added["added_photo_ids"]) == 1
    assert added["failed"] and "Photo 2" in added["failed"][0]
    assert len(_listing_photos(conv_id)) == 1


def test_add_listing_photos_respects_limits(monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    too_many = [{"data": _b64(_jpeg())}] * (mcp_photo_tools.MAX_INLINE_PHOTOS + 1)
    refused, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": too_many})
    assert error and "create_photo_upload_url" in refused["error"]

    monkeypatch.setattr(mcp_photo_tools, "MAX_PHOTO_COUNT", 2)
    _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]})
    refused, error = _call(client, "add_listing_photos", {
        "conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}, {"data": _b64(_jpeg())}],
    })
    assert error and "at most 2 photos" in refused["error"]
    assert len(_listing_photos(conv_id)) == 1

    missing, error = _call(client, "add_listing_photos", {"conversation_id": "nope", "photos": [{"data": _b64(_jpeg())}]})
    assert error and "not found" in missing["error"].lower()

    with SessionLocal() as db:
        db.add(Job(conversation_id=conv_id, status="queued", approved_revision_id="r", listing_snapshot={}))
        db.commit()
    busy, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]})
    assert error and "active listing job" in busy["error"]


def test_add_listing_photos_stores_after_the_database_session_closes(monkeypatch):
    """Normalizing and storing run with no session open; the rows are saved in a second, short one."""
    from anteroom.database import in_worker_session as real_in_worker_session
    from anteroom.routes import mcp as mcp_route
    from anteroom.services import photos as photo_pipeline

    client = TestClient(app)
    conv_id = _draft(client)
    events: list[str] = []
    open_sessions = 0

    async def tracking_in_worker(work, *args):
        def wrapped(db, *inner):
            nonlocal open_sessions
            open_sessions += 1
            events.append("session")
            try:
                return work(db, *inner)
            finally:
                open_sessions -= 1
        return await real_in_worker_session(wrapped, *args)

    real_process = photo_pipeline.process_bytes

    def tracking_process(*args, **kwargs):
        assert open_sessions == 0, "photo stored while a database session was open"
        events.append("store")
        return real_process(*args, **kwargs)

    monkeypatch.setattr(mcp_route, "in_worker_session", tracking_in_worker)
    monkeypatch.setattr(mcp_photo_tools, "in_worker_session", tracking_in_worker)
    monkeypatch.setattr(photo_pipeline, "process_bytes", tracking_process)

    added, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]})
    assert not error, added
    assert events == ["session", "store", "session"]


def test_add_listing_photos_after_the_deadline_saves_nothing_and_removes_the_file(monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    kept: list[str] = []
    real_save = mcp_photo_tools.save_photos

    def late_save(db, conv, metas, expired):
        kept.extend(meta["stored_filename"] for meta in metas)
        return real_save(db, conv, metas, lambda: True)

    monkeypatch.setattr(mcp_photo_tools, "save_photos", late_save)
    refused, error = _call(client, "add_listing_photos", {"conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}]})
    assert error and "not saved" in refused["error"]
    assert _listing_photos(conv_id) == []
    assert kept and not photo_store().exists(kept[0])


# ---------------------------------------------------------------------------
# create_photo_upload_url
# ---------------------------------------------------------------------------


def _upload_links(client, conv_id, count=1, extra_headers=None):
    made, error = _call(client, "create_photo_upload_url", {"conversation_id": conv_id, "count": count}, extra_headers)
    assert not error, made
    return made


def _upload(client, link, data, token=None):
    path = httpx.URL(link["upload_url"]).path
    return client.post(path, content=data, headers={"X-Upload-Token": token or link["upload_token"]})


def test_upload_link_adds_one_photo_once():
    client = TestClient(app)
    conv_id = _draft(client)
    made = _upload_links(client, conv_id, count=2)
    assert made["expires_in_seconds"] == 15 * 60
    links = made["uploads"]
    assert len(links) == 2 and links[0]["upload_url"] != links[1]["upload_url"]
    for link in links:
        # It points back at the server the assistant reached.
        assert re.fullmatch(re.escape(str(client.base_url).rstrip("/")) + r"/api/mcp/photo-uploads/[0-9a-f]{32}",
                            link["upload_url"])
        # The secret travels in a header, never in the URL, and the command is ready to run.
        assert link["upload_token"] not in link["upload_url"]
        assert link["upload_token"] in link["curl"] and link["upload_url"] in link["curl"]
        assert "--data-binary @" in link["curl"]
    with SessionLocal() as db:
        rows = db.query(McpPhotoUpload).all()
        assert {r.conversation_id for r in rows} == {conv_id}
        for row in rows:
            assert all(link["upload_token"] not in (row.id, row.token_hash) for link in links)
            assert len(row.token_hash) == 64

    sent = _upload(client, links[0], _jpeg(1200, 900))
    assert sent.status_code == 201, sent.text
    assert sent.headers["cache-control"] == "no-store"
    body = sent.json()
    assert body["conversation_id"] == conv_id and body["photo_count"] == 1
    assert [p.id for p in _listing_photos(conv_id)] == [body["photo_id"]]

    again = _upload(client, links[0], _jpeg())
    assert again.status_code == 401
    assert len(_listing_photos(conv_id)) == 1


def test_upload_link_refuses_wrong_expired_and_non_photos(monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    links = _upload_links(client, conv_id, count=4)["uploads"]

    assert _upload(client, links[0], _jpeg(), token="aru_" + "x" * 43).status_code == 401
    assert client.post(httpx.URL(links[0]["upload_url"]).path, content=_jpeg()).status_code == 401
    assert client.post("/api/mcp/photo-uploads/not-an-id", content=_jpeg(),
                       headers={"X-Upload-Token": links[0]["upload_token"]}).status_code in (401, 404)
    # A token is bound to its own link.
    assert _upload(client, links[1], _jpeg(), token=links[0]["upload_token"]).status_code == 401

    with SessionLocal() as db:
        row = db.get(McpPhotoUpload, httpx.URL(links[1]["upload_url"]).path.rsplit("/", 1)[1])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert _upload(client, links[1], _jpeg()).status_code == 401

    text = _upload(client, links[2], b"#!/bin/sh\necho not a photo\n")
    assert text.status_code == 422 and "JPEG, PNG, WebP or HEIC" in text.json()["detail"]
    # A failed upload spends the link.
    assert _upload(client, links[2], _jpeg()).status_code == 401

    monkeypatch.setattr(mcp_photo_tools, "MAX_UPLOAD_BYTES", 1000)
    big = _upload(client, links[3], _jpeg(1600, 1600, "white") + b"\x00" * 2000)
    assert big.status_code == 413
    assert _listing_photos(conv_id) == []


def test_upload_link_failures_are_rate_limited(monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    link = _upload_links(client, conv_id)["uploads"][0]
    monkeypatch.setattr(mcp_tokens, "AUTH_FAIL_MAX", 3)
    for _ in range(3):
        assert _upload(client, link, _jpeg(), token="aru_" + "y" * 43).status_code == 401
    limited = _upload(client, link, _jpeg())
    assert limited.status_code == 429
    assert _listing_photos(conv_id) == []


def test_upload_links_are_bounded():
    client = TestClient(app)
    conv_id = _draft(client)
    refused, error = _call(client, "create_photo_upload_url", {"conversation_id": conv_id, "count": 21})
    assert error and "at most" in refused["error"]
    missing, error = _call(client, "create_photo_upload_url", {"conversation_id": "nope"})
    assert error and "not found" in missing["error"].lower()
    with SessionLocal() as db:
        now = datetime.now(UTC)
        for _ in range(mcp_photo_tools.MAX_ACTIVE_UPLOADS):
            db.add(McpPhotoUpload(id=uuid.uuid4().hex, conversation_id=conv_id, token_hash="0" * 64,
                                  created_at=now, expires_at=now + timedelta(minutes=10)))
        # Spent and expired links do not count, and are cleared away.
        db.add(McpPhotoUpload(id=uuid.uuid4().hex, conversation_id=conv_id, token_hash="1" * 64,
                              created_at=now, expires_at=now - timedelta(minutes=1)))
        db.commit()
    refused, error = _call(client, "create_photo_upload_url", {"conversation_id": conv_id})
    assert error and "upload links" in refused["error"]
    with SessionLocal() as db:
        assert db.query(McpPhotoUpload).filter(McpPhotoUpload.token_hash == "1" * 64).count() == 0


def test_upload_link_for_a_deleted_listing_saves_nothing():
    client = TestClient(app)
    conv_id = _draft(client)
    link = _upload_links(client, conv_id)["uploads"][0]
    deleted, error = _call(client, "delete_draft_listing", {"conversation_id": conv_id, "confirm": True})
    assert not error, deleted
    gone = _upload(client, link, _jpeg())
    assert gone.status_code == 404
    with SessionLocal() as db:
        assert db.query(Photo).count() == 0


# ---------------------------------------------------------------------------
# set_cover_photo
# ---------------------------------------------------------------------------


def test_set_cover_photo_moves_the_photo_first():
    client = TestClient(app)
    conv_id = _draft(client)
    added, _ = _call(client, "add_listing_photos", {
        "conversation_id": conv_id,
        "photos": [{"data": _b64(_jpeg(color=c))} for c in ("red", "green", "blue")],
    })
    first, second, third = added["added_photo_ids"]
    with SessionLocal() as db:
        db.add(Photo(id=f"{conv_id}-depop", conversation_id=conv_id, original_filename="d.jpg",
                     stored_filename="d.jpg", mime_type="image/jpeg", size_bytes=1, display_order=0,
                     marketplace="depop"))
        db.commit()

    cover, error = _call(client, "set_cover_photo", {"conversation_id": conv_id, "photo_id": third})
    assert not error, cover
    assert cover["ordered_photo_ids"] == [third, first, second]
    assert cover["pushed_to_marketplaces"] is False
    assert [p.id for p in _listing_photos(conv_id) if p.marketplace is None] == [third, first, second]

    refused, error = _call(client, "set_cover_photo", {"conversation_id": conv_id, "photo_id": f"{conv_id}-depop"})
    assert error and "not found" in refused["error"].lower()
    refused, error = _call(client, "set_cover_photo", {"conversation_id": conv_id, "photo_id": "nope"})
    assert error and "not found" in refused["error"].lower()


# ---------------------------------------------------------------------------
# Hosted: private Storage, seller isolation, and the upload link's narrow reach
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(monkeypatch):
    from anteroom.services import hosted_photos

    objects: dict[str, bytes] = {}
    calls: list[tuple[str, str, str | None]] = []

    def request(method, url, **kwargs):
        headers = kwargs.get("headers") or {}
        calls.append((method, url, headers.get("Authorization")))
        if method == "DELETE":
            for key in kwargs["json"]["prefixes"]:
                objects.pop(key, None)
            return httpx.Response(200)
        key = url.split("/anteroom-photos/", 1)[1]
        if method == "POST":
            objects[key] = kwargs["content"]
            return httpx.Response(200)
        return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)

    monkeypatch.setenv("SUPABASE_URL", "https://storage.example.test")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "fixture-public")
    monkeypatch.setenv("SUPABASE_SERVER_SECRET_KEY", "fixture-only")
    monkeypatch.setattr(hosted_photos._storage, "request", request)
    return objects, calls


def _hosted_token(client, keys, seller, level="write"):
    made = client.post("/api/settings/mcp-tokens", headers=headers(keys, seller),
                       json={"name": "Photos", "permissions": {"listings": level}})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def test_hosted_inline_photos_go_to_the_sellers_private_storage(cloud, signing_keys, storage):  # noqa: F811
    client, engine = cloud
    objects, calls = storage
    alice = _hosted_token(client, signing_keys, USER_ID)
    bob = _hosted_token(client, signing_keys, BOB)
    conv_id = _draft(client, extra_headers=alice)

    added, error = _call(client, "add_listing_photos", {
        "conversation_id": conv_id, "photos": [{"data": _b64(_jpeg(3000, 2000, orientation=6))}],
    }, alice)
    assert not error, added
    with Session(engine) as db:
        rows = db.query(Photo).filter(Photo.conversation_id == conv_id).all()
    assert len(rows) == 1 and rows[0].user_id == USER_ID
    key = rows[0].stored_filename
    assert key.startswith(f"{USER_ID}/{conv_id}/") and key in objects
    # Hosted photos are stored upright, bounded and without EXIF, like the web app's uploads.
    with Image.open(io.BytesIO(objects[key])) as image:
        assert image.format == "JPEG" and max(image.size) <= 2400 and image.height > image.width
        assert not image.getexif()
    # An MCP token never reaches Storage: the server key writes, scoped to this seller.
    assert all(auth is None for _method, _url, auth in calls)

    refused, error = _call(client, "add_listing_photos", {
        "conversation_id": conv_id, "photos": [{"data": _b64(_jpeg())}],
    }, bob)
    assert error and "not found" in refused["error"].lower()
    refused, error = _call(client, "create_photo_upload_url", {"conversation_id": conv_id}, bob)
    assert error and "not found" in refused["error"].lower()
    refused, error = _call(client, "set_cover_photo", {"conversation_id": conv_id, "photo_id": rows[0].id}, bob)
    assert error and "not found" in refused["error"].lower()


def test_hosted_upload_link_reaches_one_listing_and_nothing_else(cloud, signing_keys, storage):  # noqa: F811
    client, engine = cloud
    objects, _calls = storage
    alice = _hosted_token(client, signing_keys, USER_ID)
    conv_id = _draft(client, extra_headers=alice)
    bob_conv = _draft(client, title="Bob's boots", extra_headers=_hosted_token(client, signing_keys, BOB))
    link = _upload_links(client, conv_id, extra_headers=alice)["uploads"][0]
    upload_path = httpx.URL(link["upload_url"]).path
    secret = link["upload_token"]

    # The link's secret is not a session: no other route takes it, in any header.
    for auth in ({"X-Upload-Token": secret}, {"Authorization": f"Bearer {secret}"}):
        assert client.get("/api/drafts", headers=auth).status_code == 401
        assert client.get(f"/api/drafts/{conv_id}", headers=auth).status_code == 401
        assert client.post(f"/api/drafts/{conv_id}/photos", content=_jpeg(), headers=auth).status_code == 401
        assert _rpc(client, "tools/list", extra_headers=auth).status_code == 401
        assert client.get(upload_path, headers=auth).status_code == 401
        assert client.post(upload_path + "/extra", content=_jpeg(), headers=auth).status_code == 401
        assert client.post(f"/api/mcp/photo-uploads/{uuid.uuid4().hex}", content=_jpeg(), headers=auth).status_code == 401

    sent = client.post(upload_path, content=_jpeg(2000, 1500), headers={"X-Upload-Token": secret})
    assert sent.status_code == 201, sent.text
    with Session(engine) as db:
        rows = db.query(Photo).filter(Photo.conversation_id == conv_id).all()
        assert db.query(Photo).filter(Photo.conversation_id == bob_conv).count() == 0
        assert db.get(McpPhotoUpload, upload_path.rsplit("/", 1)[1]).used_at is not None
    assert [r.id for r in rows] == [sent.json()["photo_id"]]
    assert rows[0].user_id == USER_ID and rows[0].stored_filename in objects
    assert rows[0].stored_filename.startswith(f"{USER_ID}/{conv_id}/")
    assert client.post(upload_path, content=_jpeg(), headers={"X-Upload-Token": secret}).status_code == 401

    # Alice's app sees it; the cover can be set through MCP.
    got, error = _call(client, "set_cover_photo", {"conversation_id": conv_id, "photo_id": rows[0].id}, alice)
    assert not error, got
