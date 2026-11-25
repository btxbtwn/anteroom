"""Import look-alikes wait in Anteroom for review, so every device and the seller's assistant see one queue."""
import json

from test_chrome_import import POST, chrome, post  # noqa: F401

from anteroom.models.conversation import Conversation
from anteroom.models.import_review import ImportReview
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import listing_persistence
from anteroom.services.marketplace_delist import listings


def _look_alike(chrome, title="Levi's Denim Jacket", price=45):  # noqa: F811
    item = ConversationRepo(chrome.db).create(title=title)
    listing_persistence.save_revision(chrome.db, item.id, {"title": title, "price": price}, "user")
    chrome.db.commit()
    return item


def _held(chrome):  # noqa: F811
    body = post(chrome, "poshmark", POST, unless_matched=True).json()
    assert body["conversation_id"] is None, body
    return body


def _queue(chrome, **params):  # noqa: F811
    response = chrome.client.get("/api/import-reviews", params=params)
    assert response.status_code == 200, response.text
    return response.json()["reviews"]


def test_a_look_alike_import_all_holds_waits_in_the_queue(chrome):  # noqa: F811
    item = _look_alike(chrome, price=80)
    _held(chrome)
    reviews = _queue(chrome)
    assert len(reviews) == 1
    review = reviews[0]
    assert (review["marketplace"], review["listing_id"], review["title"], review["price"]) == (
        "poshmark", POST, "Levi's denim jacket", "45.00")
    assert review["url"] == f"https://poshmark.com/listing/{POST}"
    match = review["match"]
    assert (match["conversation_id"], match["title"], match["price"], match["photo_count"]) == (
        item.id, "Levi's Denim Jacket", 80.0, 0)
    assert match["price_differs"] is True and "alike" in review["reason"] and "$80.00 in Anteroom" in review["reason"]
    assert _queue(chrome, marketplace="poshmark") == reviews and _queue(chrome, marketplace="ebay") == []
    # Holding the same listing again keeps one row.
    _held(chrome)
    assert chrome.db.query(ImportReview).count() == 1


def test_linking_the_listing_clears_its_review(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    assert post(chrome, "poshmark", POST, link_conversation_id=item.id).status_code == 200
    assert _queue(chrome) == [] and chrome.db.query(ImportReview).count() == 0
    assert listings(chrome.db, item.id)["poshmark"]["listing_id"] == POST


def test_importing_the_listing_as_new_clears_its_review(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    created = post(chrome, "poshmark", POST).json()["conversation_id"]
    assert created not in (None, item.id)
    assert _queue(chrome) == [] and chrome.db.query(ImportReview).count() == 0


def test_not_the_same_item_stops_suggesting_it_and_import_all_brings_the_listing_in(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    response = chrome.client.post(f"/api/import-reviews/{review_id}/not-a-match")
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "not_match"
    assert _queue(chrome) == []
    assert chrome.client.post(f"/api/import-reviews/{review_id}/not-a-match").status_code == 404
    # The page preview no longer suggests that item for the listing.
    chrome.remote.page = {"ok": True, "next_cursor": None, "rows": [{"id": POST, "title": "Levi's denim jacket", "price": 45}]}
    rows = chrome.client.get("/api/chrome-import/poshmark/active").json()["rows"]
    assert rows[0]["match"] is None
    # Import all now brings it in as its own item.
    created = post(chrome, "poshmark", POST, unless_matched=True).json()["conversation_id"]
    assert created not in (None, item.id)
    assert chrome.db.query(ImportReview).count() == 0


def test_a_listing_linked_some_other_way_leaves_the_queue(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    # The suggested item went live on Poshmark through another listing.
    from anteroom.services.marketplace_delist import mark

    mark(chrome.db, item.id, "poshmark", "listed", "https://poshmark.com/listing/" + "e" * 24)
    assert _queue(chrome) == []


# --- MCP ----------------------------------------------------------------------------------------------


def _call(chrome, name, arguments=None, headers=None):  # noqa: F811
    response = chrome.client.post("/api/mcp", headers=headers or {}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def test_mcp_lists_the_same_queue_with_the_suggested_item(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    body, error = _call(chrome, "get_import_matches", {"marketplace": "poshmark"})
    assert not error, body
    assert body["count"] == 1 and body["pushed_to_marketplaces"] is False
    assert body["reviews"] == _queue(chrome)
    assert body["reviews"][0]["match"]["conversation_id"] == item.id
    _, error = _call(chrome, "get_import_matches", {"marketplace": "amazon"})
    assert error


def test_mcp_confirm_links_like_the_app_and_keeps_the_items_edits(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    calls = len(chrome.remote.calls)
    body, error = _call(chrome, "confirm_import_match", {"review_id": review_id})
    assert not error, body
    assert (body["linked"], body["conversation_id"], body["listing_id"], body["pushed_to_marketplaces"]) == (
        True, item.id, POST, False)
    # Checked with the marketplace first, by reading only.
    assert [call[0] for call in chrome.remote.calls[calls:]] == ["marketplace.import.listing"]
    assert listings(chrome.db, item.id)["poshmark"]["listing_id"] == POST
    assert ListingRepo(chrome.db).get_revisions(item.id)[0].listing_json["title"] == "Levi's Denim Jacket"
    assert chrome.db.query(Conversation).count() == 1 and chrome.remote.downloads == []
    assert _queue(chrome) == []
    # Nothing left to confirm.
    assert _call(chrome, "confirm_import_match", {"review_id": review_id})[1]


def test_mcp_confirm_links_nothing_when_the_listing_is_no_longer_for_sale(chrome):  # noqa: F811
    item = _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    chrome.remote.reply = {"ok": False, "error": "This Poshmark listing is sold.", "error_code": "rejected"}
    message, error = _call(chrome, "confirm_import_match", {"review_id": review_id})
    assert error and message.startswith("Nothing was linked") and "sold" in message
    assert "poshmark" not in listings(chrome.db, item.id)
    assert [review["id"] for review in _queue(chrome)] == [review_id]


def test_mcp_confirm_that_runs_out_of_time_links_nothing(chrome, monkeypatch):  # noqa: F811
    import asyncio

    from anteroom.services import chrome_import

    item = _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]

    async def stall(*_args, **_kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(chrome_import, "_read", stall)
    monkeypatch.setattr("anteroom.routes.mcp._MCP_RPC_TIMEOUT_SECONDS", 2.5)
    message, error = _call(chrome, "confirm_import_match", {"review_id": review_id})
    assert error and "did not answer in time" in message
    assert "poshmark" not in listings(chrome.db, item.id)


def test_mcp_confirm_whose_link_step_ends_after_the_deadline_reports_what_was_saved(chrome, monkeypatch):  # noqa: F811
    import time

    from anteroom.services import inventory_import

    item = _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    real_link = inventory_import._link

    def late_link(*args):
        # The marketplace check passed; saving the link ends after the tool's deadline (0.5 s here).
        time.sleep(1.0)
        return real_link(*args)

    monkeypatch.setattr(inventory_import, "_link", late_link)
    # The link step's deadline is 0.5 s; the call as a whole has room to spare on a busy CI worker.
    monkeypatch.setattr("anteroom.routes.mcp._MCP_RPC_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr("anteroom.routes.mcp._MCP_REPLY_MARGIN_SECONDS", 9.5)
    body, error = _call(chrome, "confirm_import_match", {"review_id": review_id})
    # What the assistant is told matches the database.
    assert not error, body
    assert body["linked"] is True and body["conversation_id"] == item.id
    chrome.db.expire_all()
    assert listings(chrome.db, item.id)["poshmark"]["listing_id"] == POST
    assert _queue(chrome) == []


def test_holding_the_same_listing_from_two_sessions_at_once_keeps_one_row(chrome):  # noqa: F811
    from sqlalchemy import event

    from anteroom.database import SessionLocal
    from anteroom.services import import_reviews

    item = _look_alike(chrome)
    match = {"conversation_id": item.id, "score": 0.9}
    with SessionLocal() as first, SessionLocal() as second:
        held = []

        def other_device_holds_first(*_args):
            # The second hold has read no row; the first one lands before it inserts.
            if not held:
                held.append(True)
                import_reviews.hold(first, "poshmark", POST, "Levi's denim jacket", 45, match)

        event.listen(second, "before_flush", other_device_holds_first)
        import_reviews.hold(second, "poshmark", POST, "Levi's denim jacket", 50, match)
    chrome.db.expire_all()
    rows = chrome.db.query(ImportReview).all()
    assert len(rows) == 1 and rows[0].decision == "pending"


def test_mcp_reject_marks_it_not_the_same_item(chrome):  # noqa: F811
    _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    body, error = _call(chrome, "reject_import_match", {"review_id": review_id})
    assert not error, body
    assert body["decision"] == "not_match" and body["pushed_to_marketplaces"] is False
    assert _queue(chrome) == []
    assert _call(chrome, "reject_import_match", {"review_id": review_id})[1]


def test_a_read_only_token_cannot_confirm_or_reject(chrome):  # noqa: F811
    _look_alike(chrome)
    _held(chrome)
    review_id = _queue(chrome)[0]["id"]
    created = chrome.client.post("/api/settings/mcp-tokens", json={"name": "Reader"})
    assert created.status_code == 201, created.text
    bearer = {"Authorization": f"Bearer {created.json()['token']}"}
    assert not _call(chrome, "get_import_matches", headers=bearer)[1]
    for tool in ("confirm_import_match", "reject_import_match"):
        message, error = _call(chrome, tool, {"review_id": review_id}, headers=bearer)
        assert error and "not allowed" in message
    assert len(_queue(chrome)) == 1
