"""A seller's sale undo updates only that seller's inventory projection."""
from datetime import UTC, datetime
from uuid import uuid4

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401


def test_sale_deletion_reopens_only_its_owners_item(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    records = []
    for auth, title in [(alice, "Alice sale undo"), (bob, "Bob still sold")]:
        created = client.post("/api/conversations", headers=auth, json={"title": title})
        assert created.status_code == 200, created.text
        identity = created.json()["id"]
        saved = client.put(f"/api/conversations/{identity}/listing", headers=auth, json={
            "listing": {"title": title, "price": 45}, "expected_revision_id": ""})
        assert saved.status_code == 200, saved.text
        recorded = client.post(f"/api/conversations/{identity}/marketplace-listings/sold", headers=auth, json={
            "marketplace": "other", "sold_on": datetime.now(UTC).date().isoformat(), "currency": "USD",
            "item_revenue": "45", "request_id": uuid4().hex})
        assert recorded.status_code == 200, recorded.text
        records.append((identity, recorded.json()["sale"]))
        rows = client.get("/api/conversations", headers=auth).json()
        assert len(rows) == 1 and rows[0]["status"] == "sold" and rows[0]["settled_at"]

    identity, sale = records[0]
    path = f"/api/sales-ledger/{sale['id']}"
    params = {"expected_revision": sale["financial_revision"]}
    assert client.delete(path, headers=bob, params=params).status_code == 404
    assert client.get(path, headers=alice).status_code == 200
    assert client.delete(path, headers=alice, params=params).status_code == 200
    assert client.get(path, headers=alice).status_code == 404
    rows = client.get("/api/conversations", headers=alice).json()
    assert len(rows) == 1 and rows[0]["id"] == identity
    assert rows[0]["status"] == "draft" and rows[0]["settled_at"] is None
    bob_rows = client.get("/api/conversations", headers=bob).json()
    assert len(bob_rows) == 1 and bob_rows[0]["id"] == records[1][0]
    assert bob_rows[0]["status"] == "sold" and bob_rows[0]["settled_at"]
    assert client.get(f"/api/sales-ledger/{records[1][1]['id']}", headers=bob).status_code == 200


EBAY_ALICE = "https://www.ebay.com/itm/123456789012"
EBAY_BOB = "https://www.ebay.com/itm/987654321098"


def hosted_item(client, auth, title):
    created = client.post("/api/conversations", headers=auth, json={"title": title})
    assert created.status_code == 200, created.text
    identity = created.json()["id"]
    saved = client.put(f"/api/conversations/{identity}/listing", headers=auth, json={
        "listing": {"title": title, "price": 45}, "expected_revision_id": ""})
    assert saved.status_code == 200, saved.text
    return identity


def stored_marks(engine, identity):
    from sqlalchemy.orm import Session

    from anteroom.models.conversation import Conversation
    from anteroom.services.vendoo_import import parse_notes

    with Session(engine) as db:
        return parse_notes(db.get(Conversation, identity).notes).get("marketplaceMarks", {})


def test_marketplace_marks_lock_and_write_only_their_owners_item(cloud, signing_keys):  # noqa: F811
    import pytest

    from test_seller_sessions import USER_ID
    from anteroom.services import marketplace_delist
    from anteroom.services.hosted_database import SellerDatabaseSession

    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    alice_item, bob_item = hosted_item(client, alice, "Alice coat"), hosted_item(client, bob, "Bob coat")

    def mark(auth, identity, marketplace, state, url=None):
        return client.post(f"/api/conversations/{identity}/marketplace-listings/{marketplace}/mark", headers=auth,
                           json={"state": state, "url": url})

    # Each mark runs the delist notes write, which locks the item row under the seller's row security.
    assert mark(alice, alice_item, "ebay", "listed", EBAY_ALICE).status_code == 200
    assert mark(bob, alice_item, "ebay", "listed", EBAY_BOB).status_code == 404
    assert mark(bob, bob_item, "ebay", "listed", EBAY_BOB).status_code == 200
    assert mark(alice, alice_item, "mercari", "sold").status_code == 200
    alice_listings = client.get(f"/api/conversations/{alice_item}/marketplace-listings", headers=alice).json()
    bob_listings = client.get(f"/api/conversations/{bob_item}/marketplace-listings", headers=bob).json()
    assert alice_listings["ebay"]["listing_id"] == "123456789012" and alice_listings["mercari"]["sold_here"]
    assert bob_listings["ebay"]["listing_id"] == "987654321098" and "mercari" not in bob_listings
    assert set(stored_marks(engine, alice_item)) == {"ebay", "mercari"}
    assert set(stored_marks(engine, bob_item)) == {"ebay"}

    # Another seller's item is invisible to the lock, so nothing is written to it.
    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
        with pytest.raises(AssertionError):
            marketplace_delist._record_delete(db, bob_item, "ebay", {
                "listing_id": "987654321098", "url": EBAY_BOB, "state": "deleting"})
        db.rollback()
    assert set(stored_marks(engine, bob_item)) == {"ebay"}
    assert client.get(f"/api/conversations/{bob_item}/marketplace-listings", headers=bob).json()["ebay"]["state"] == "live"


def test_hosted_delist_notes_write_waits_for_the_sellers_sale_undo(cloud, signing_keys, monkeypatch):  # noqa: F811
    import threading
    import time

    from sqlalchemy import text

    from test_seller_sessions import USER_ID
    from anteroom.services import marketplace_delist
    from anteroom.services.hosted_database import SellerDatabaseSession
    from anteroom.services.sales_ledger import delete_seller_sale

    client, engine = cloud
    alice = headers(signing_keys)
    identity = hosted_item(client, alice, "Alice race coat")
    recorded = client.post(f"/api/conversations/{identity}/marketplace-listings/sold", headers=alice, json={
        "marketplace": "mercari", "sold_on": datetime.now(UTC).date().isoformat(), "currency": "USD",
        "item_revenue": "45", "request_id": uuid4().hex})
    assert recorded.status_code == 200, recorded.text
    sale = recorded.json()["sale"]
    # The seller's own Sold mark for that sale, which the undo removes.
    marked = client.post(f"/api/conversations/{identity}/marketplace-listings/mercari/mark", headers=alice,
                         json={"state": "sold"})
    assert marked.status_code == 200, marked.text
    assert stored_marks(engine, identity)["mercari"]["state"] == "sold"
    failures: list[BaseException] = []

    def delist_write():
        try:
            with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
                marketplace_delist._record_delete(db, identity, "ebay", {
                    "listing_id": "123456789012", "url": EBAY_ALICE, "state": "deleting"})
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            failures.append(exc)

    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as undo:
        with monkeypatch.context() as held_transaction:
            held_transaction.setattr(undo, "commit", undo.flush)
            delete_seller_sale(undo, sale["id"], sale["financial_revision"])
        # Postgres keeps one pg_stat_activity snapshot per transaction, so the
        # watch autocommits: each poll then sees the writer as it is now.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watch:
            writer = threading.Thread(target=delist_write)
            writer.start()
            deadline, waiting = time.monotonic() + 10, 0
            while not waiting and time.monotonic() < deadline:
                waiting = watch.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                            "AND wait_event_type = 'Lock' AND query LIKE 'UPDATE conversations %'"))
                time.sleep(0.05)
        assert writer.is_alive(), failures
        assert waiting, "the seller's delist write must wait on the undo's row lock"
        undo.commit()
    writer.join(10)
    assert not writer.is_alive() and not failures, failures
    marks = stored_marks(engine, identity)
    assert "mercari" not in marks
    rows = client.get("/api/conversations", headers=alice).json()
    assert rows[0]["id"] == identity and rows[0]["status"] != "sold"
    assert client.get(f"/api/conversations/{identity}/marketplace-listings", headers=alice).json()["ebay"]["state"] == "deleting"
