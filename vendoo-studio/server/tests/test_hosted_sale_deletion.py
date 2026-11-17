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
