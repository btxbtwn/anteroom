"""``read_size_tag`` and ``read_measurements`` over MCP: Item Details' read buttons for AI assistants."""
from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_mcp_photo_tools import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.mcp_token import McpAccessToken
from anteroom.services import listing_provider, measure_read, mcp_http, mcp_reading_tools, mcp_tokens
from anteroom.services.vendoo_import import parse_notes

READING_TOOLS = {"read_size_tag", "read_measurements"}


@pytest.fixture(autouse=True)
def _clean():
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot

    with SessionLocal() as db:
        for model in (McpAccessToken, BackgroundTask, ListingCorrection, SaleSnapshot, Message, Job,
                      Photo, Listing, ListingRevision, Conversation):
            db.query(model).delete()
        db.commit()
    mcp_tokens.clear_auth_failures()
    yield
    mcp_tokens.clear_auth_failures()


class FakeProvider:
    """A photo model that gives each independent read the answer it is handed, in order."""

    name, vision_model = "openrouter", "google/gemini-flash-latest"

    def __init__(self, answers: list[dict]):
        self.answers = list(answers)
        self.calls = 0

    async def vision_chat(self, messages):
        self.calls += 1
        yield "```json\n" + json.dumps(self.answers.pop(0)) + "\n```"


@pytest.fixture
def model(monkeypatch):
    """Install a photo model; returns a setter for the answers it gives."""
    def use(provider):
        monkeypatch.setattr(listing_provider, "get_photo_provider", lambda: provider)
        monkeypatch.setattr(listing_provider, "managed_task_provider", lambda _task: None)
        return provider
    return use


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


def _jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (400, 400), (40, 40, 45)).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def _draft(client, title="Pacsun camp shirt", photos=2, extra_headers=None) -> str:
    made, error = _call(client, "create_draft_listing", {"title": title}, extra_headers)
    assert not error, made
    conv_id = made["conversation_id"]
    if photos:
        added, error = _call(client, "add_listing_photos", {
            "conversation_id": conv_id, "photos": [{"data": _jpeg()} for _ in range(photos)],
        }, extra_headers)
        assert not error, added
    return conv_id


def _notes(conv_id, engine=None) -> dict:
    if engine is None:
        with SessionLocal() as db:
            return parse_notes(db.get(Conversation, conv_id).notes)
    with Session(engine) as db:
        return parse_notes(db.get(Conversation, conv_id).notes)


def _set_notes(conv_id, notes: dict) -> None:
    with SessionLocal() as db:
        db.get(Conversation, conv_id).notes = json.dumps(notes)
        db.commit()


def _found(*sizes):
    return {"tag": "found", "sizes": list(sizes)}


# ---------------------------------------------------------------------------
# Catalog and permissions
# ---------------------------------------------------------------------------


def test_reading_tools_are_listed_and_need_listings_write(model):
    names = {tool["name"] for tool in mcp_http.tool_definitions()}
    assert READING_TOOLS <= names
    for name in READING_TOOLS:
        assert mcp_http.TOOL_PERMISSIONS[name] == ("listings", "write")

    client = TestClient(app)
    conv_id = _draft(client)
    provider = model(FakeProvider([]))
    made = client.post("/api/settings/mcp-tokens", json={"name": "Reader", "permissions": {"listings": "read"}})
    reader = {"Authorization": f"Bearer {made.json()['token']}"}
    listed = {t["name"] for t in _rpc(client, "tools/list", extra_headers=reader).json()["result"]["tools"]}
    assert not READING_TOOLS & listed
    for name in READING_TOOLS:
        refused, error = _call(client, name, {"conversation_id": conv_id}, reader)
        assert error and f"not allowed to use {name}" in refused["error"]
    assert provider.calls == 0


def test_readings_get_a_longer_call_deadline():
    def call(name):
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}

    assert mcp_reading_tools.call_timeout(call("read_size_tag"), 30.0) == mcp_reading_tools.CALL_DEADLINE_SECONDS
    assert mcp_reading_tools.call_timeout([call("get_listing"), call("read_measurements")], 30.0) > 30.0
    assert mcp_reading_tools.call_timeout(call("get_listing"), 30.0) == 30.0
    assert mcp_reading_tools.call_timeout(None, 30.0) == 30.0


# ---------------------------------------------------------------------------
# read_size_tag
# ---------------------------------------------------------------------------


def test_read_size_tag_fills_a_blank_size_in_item_details(model):
    client = TestClient(app)
    conv_id = _draft(client)
    provider = model(FakeProvider([_found("M"), _found("m")]))

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert not error, read
    assert (read["status"], read["size"], read["saved"], read["photos_read"]) == ("accepted", "M", True, 2)
    assert read["item_details_size"] == "M" and read["pushed_to_marketplaces"] is False
    assert provider.calls == 2
    # Item Details Size is what generation keeps as the listing's Size.
    assert _notes(conv_id)["size"] == "M"
    # The answer never names the provider or its model.
    assert "gemini" not in json.dumps(read).casefold()


def test_read_size_tag_keeps_a_size_already_set(model):
    client = TestClient(app)
    conv_id = _draft(client)
    _set_notes(conv_id, {"size": "L", "sku": "A4-1"})
    model(FakeProvider([_found("M"), _found("M")]))

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert not error, read
    assert read["saved"] is False and read["item_details_size"] == "L"
    assert "tag reads M" in read["note"]
    assert _notes(conv_id) == {"size": "L", "sku": "A4-1"}


def test_read_size_tag_keeps_a_size_set_on_the_listing(model):
    client = TestClient(app)
    conv_id = _draft(client)
    edited, error = _call(client, "edit_listing", {"conversation_id": conv_id, "fields": {"size": "XL"}})
    assert not error, edited
    model(FakeProvider([_found("M"), _found("M")]))

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert not error, read
    assert read["saved"] is False and read["item_details_size"] == "XL"
    assert not _notes(conv_id).get("size")


@pytest.mark.parametrize(("answers", "status"), [
    ([_found("8"), _found("6")], "conflict"),
    ([{"tag": "none"}, {"tag": "none"}], "not_found"),
])
def test_read_size_tag_never_guesses(model, answers, status):
    client = TestClient(app)
    conv_id = _draft(client)
    model(FakeProvider(answers))

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert not error, read
    assert (read["status"], read["size"], read["saved"]) == (status, None, False)
    assert "Nothing was saved" in read["note"]
    assert not _notes(conv_id).get("size")


def test_read_size_tag_without_save_only_reports(model):
    client = TestClient(app)
    conv_id = _draft(client)
    model(FakeProvider([_found("S"), _found("S")]))

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id, "save": False})
    assert not error, read
    assert (read["size"], read["saved"], read["item_details_size"]) == ("S", False, None)
    assert not _notes(conv_id).get("size")


def test_read_size_tag_refusals(model):
    client = TestClient(app)
    bare = _draft(client, photos=0)
    model(FakeProvider([]))
    refused, error = _call(client, "read_size_tag", {"conversation_id": bare})
    assert error and "no photos" in refused["error"]
    refused, error = _call(client, "read_size_tag", {"conversation_id": "missing"})
    assert error and "not found" in refused["error"]

    model(None)
    conv_id = _draft(client)
    refused, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert error and "Connect a model" in refused["error"]


def test_a_stalled_model_is_named_and_nothing_is_saved(model):
    class Stalled:
        name = "openrouter"

        async def vision_chat(self, messages):
            raise RuntimeError("OpenRouter stopped answering while looking at the photos.")
            yield ""  # pragma: no cover

    client = TestClient(app)
    conv_id = _draft(client)
    model(Stalled())
    refused, error = _call(client, "read_size_tag", {"conversation_id": conv_id})
    assert error and "OpenRouter stopped answering" in refused["error"] and "Nothing was saved" in refused["error"]
    assert not _notes(conv_id).get("size")


# ---------------------------------------------------------------------------
# read_measurements
# ---------------------------------------------------------------------------


def _measured(monkeypatch, measurements: dict, photos_read=1):
    seen = {}

    async def read(provider, paths, garment):
        seen.update(garment=garment, photos=len(paths))
        return {"photos_read": photos_read, "measurements": measurements}

    monkeypatch.setattr(measure_read, "read_measurements", read)
    return seen


def test_read_measurements_fills_blank_fields_and_names_the_rest(model, monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    _set_notes(conv_id, {"sku": "A4-1", "measurements": {"top": {"sleeve": "9"}}})
    model(FakeProvider([]))
    seen = _measured(monkeypatch, {
        "pitToPit": {"status": "accepted", "value": 21.5, "reads": [21.5, 21.5]},
        "length": {"status": "disagree", "reads": [27, 29]},
        "sleeve": {"status": "accepted", "value": 10.0, "reads": [10, 10]},
    })

    read, error = _call(client, "read_measurements", {"conversation_id": conv_id})
    assert not error, read
    # The garment Item Details shows, read from every saved photo in order.
    assert seen == {"garment": "top", "photos": 2}
    assert read["saved"] == {"pitToPit": "21.5"}
    assert read["item_details_measurements"] == {"pitToPit": "21.5", "sleeve": "9"}
    assert read["measure_by_hand"] == ["Length"]
    notes = _notes(conv_id)
    # A measurement already there stays; only the blank one took the reading.
    assert notes["measurements"]["top"] == {"pitToPit": "21.5", "sleeve": "9"}
    assert notes["garment"] == "top" and notes["sku"] == "A4-1"


def test_read_measurements_for_a_named_garment(model, monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client, title="Levi's 505 jeans")
    model(FakeProvider([]))
    seen = _measured(monkeypatch, {
        "waist": {"status": "accepted", "value": 16.0},
        "inseam": {"status": "accepted", "value": 30.5},
    })

    read, error = _call(client, "read_measurements", {"conversation_id": conv_id, "garment": "pants"})
    assert not error, read
    assert seen["garment"] == "pants"
    assert read["saved"] == {"waist": "16", "inseam": "30.5"}
    assert read["measure_by_hand"] == ["Rise", "Leg Opening"]
    assert _notes(conv_id)["measurements"]["pants"] == {"waist": "16", "inseam": "30.5"}

    refused, error = _call(client, "read_measurements", {"conversation_id": conv_id, "garment": "hat"})
    assert error and "garment must be one of" in refused["error"]


def test_read_measurements_without_a_yardstick_saves_nothing(model, monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    model(FakeProvider([]))
    _measured(monkeypatch, {}, photos_read=0)

    read, error = _call(client, "read_measurements", {"conversation_id": conv_id})
    assert not error, read
    assert read["saved"] == {} and "No yardstick photo" in read["note"]
    assert "measurements" not in _notes(conv_id)


def test_read_measurements_without_save_only_reports(model, monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    model(FakeProvider([]))
    _measured(monkeypatch, {"pitToPit": {"status": "accepted", "value": 20.0}})

    read, error = _call(client, "read_measurements", {"conversation_id": conv_id, "save": False})
    assert not error, read
    assert read["read"] == {"pitToPit": "20"} and read["saved"] == {}
    assert "measurements" not in _notes(conv_id)


def test_both_readings_in_one_batch_keep_each_others_fields(model, monkeypatch):
    client = TestClient(app)
    conv_id = _draft(client)
    _set_notes(conv_id, {"sku": "A4-1"})
    model(FakeProvider([_found("M"), _found("M")]))
    _measured(monkeypatch, {"pitToPit": {"status": "accepted", "value": 21.0}})

    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "read_size_tag", "arguments": {"conversation_id": conv_id}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "read_measurements", "arguments": {"conversation_id": conv_id, "garment": "top"}}},
    ]
    resp = client.post("/api/mcp", json=batch)
    assert resp.status_code == 200, resp.text
    assert [r["result"]["isError"] for r in resp.json()] == [False, False], resp.text
    notes = _notes(conv_id)
    assert notes["size"] == "M" and notes["measurements"]["top"] == {"pitToPit": "21"} and notes["sku"] == "A4-1"


def test_readings_settled_side_by_side_keep_each_others_fields():
    import threading
    from time import monotonic

    client = TestClient(app)
    conv_id = _draft(client)
    _set_notes(conv_id, {"sku": "A4-1"})
    # Each settlement waits for the other after reading, so both read before either writes,
    # unless the database makes one wait for the other's lock (then the barrier gives up).
    barrier = threading.Barrier(2, timeout=1.5)
    errors: list[BaseException] = []

    def settle(fold):
        first = {"read": True}

        def once(db, conv):
            data = fold(db, conv)
            if first.pop("read", False):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
            return data

        try:
            with SessionLocal() as db:
                mcp_reading_tools._settle(db, conv_id, once, monotonic() + 30)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    size = {"status": "accepted", "size": "M", "sizes": ["M"]}
    measured = {"photos_read": 1, "measurements": {"pitToPit": {"status": "accepted", "value": 21.0}}}
    threads = [
        threading.Thread(target=settle, args=(lambda db, conv: mcp_reading_tools._fold_size(db, conv, size, True),)),
        threading.Thread(target=settle, args=(
            lambda db, conv: mcp_reading_tools._fold_measurements(db, conv, "top", measured, True),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    notes = _notes(conv_id)
    assert notes["size"] == "M" and notes["measurements"]["top"] == {"pitToPit": "21"} and notes["sku"] == "A4-1"


# ---------------------------------------------------------------------------
# Hosted
# ---------------------------------------------------------------------------


def _hosted_token(client, keys, seller):
    made = client.post("/api/settings/mcp-tokens", headers=headers(keys, seller),
                       json={"name": "Reader", "permissions": {"listings": "write"}})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def test_hosted_reads_the_sellers_own_photos_and_saves_to_their_item_details(  # noqa: F811
    cloud, signing_keys, storage, model,  # noqa: F811
):
    client, engine = cloud
    _objects, calls = storage
    alice = _hosted_token(client, signing_keys, USER_ID)
    bob = _hosted_token(client, signing_keys, BOB)
    conv_id = _draft(client, extra_headers=alice)
    provider = model(FakeProvider([_found("M"), _found("M")]))

    refused, error = _call(client, "read_size_tag", {"conversation_id": conv_id}, bob)
    assert error and "not found" in refused["error"].lower()
    assert provider.calls == 0

    read, error = _call(client, "read_size_tag", {"conversation_id": conv_id}, alice)
    assert not error, read
    assert (read["size"], read["saved"], read["photos_read"]) == ("M", True, 2)
    assert _notes(conv_id, engine)["size"] == "M"
    # An MCP token never reaches Storage: photos are read on the server key, scoped to this seller.
    assert [method for method, _url, _auth in calls].count("GET") == 2
    assert all(auth is None for _method, _url, auth in calls)


# ---------------------------------------------------------------------------
# Saving into Item Details
# ---------------------------------------------------------------------------


def _settle_size_now(conv_id, reading, *, deadline=None, interfere=None):
    """Run the save step for a size reading, optionally changing the notes once mid-save."""
    from time import monotonic

    calls = {"n": 0}

    def fold(db, conv):
        calls["n"] += 1
        data, notes = mcp_reading_tools._fold_size(db, conv, reading, True)
        if interfere is not None and calls["n"] == 1:
            # Another save lands between this one's read and its write.
            with SessionLocal() as other:
                other.get(Conversation, conv_id).notes = json.dumps(interfere)
                other.commit()
        return data, notes

    with SessionLocal() as db:
        data = mcp_reading_tools._settle(db, conv_id, fold, deadline if deadline is not None else monotonic() + 60)
    return data, calls["n"]


@pytest.mark.sqlite_only("Postgres locks the row while saving, so no other save can land mid-way")
def test_a_save_that_raced_another_is_folded_into_the_newer_notes():
    client = TestClient(app)
    conv_id = _draft(client, photos=0)
    reading = {"photos_read": 1, "status": "accepted", "size": "M", "sizes": ["M"]}
    measured = {"garment": "top", "measurements": {"top": {"pitToPit": "21"}}}

    data, attempts = _settle_size_now(conv_id, reading, interfere=measured)
    assert attempts == 2 and data["saved"] is True
    # Neither the measurement saved meanwhile nor this size was lost.
    assert _notes(conv_id) == {**measured, "size": "M"}


def test_nothing_is_saved_once_the_call_has_given_up():
    from time import monotonic

    client = TestClient(app)
    conv_id = _draft(client, photos=0)
    reading = {"photos_read": 1, "status": "accepted", "size": "M", "sizes": ["M"]}
    with pytest.raises(TimeoutError):
        _settle_size_now(conv_id, reading, deadline=monotonic() - 1)
    assert not _notes(conv_id).get("size")
