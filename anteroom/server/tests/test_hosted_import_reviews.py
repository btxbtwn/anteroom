"""Hosted import look-alike reviews belong to one seller, on the web routes and through MCP."""
import json

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401

from anteroom.models.conversation import Conversation
from anteroom.models.import_review import ImportReview


def _seed(engine, user_id, conv_id, review_id):
    with Session(engine) as db:
        db.add(Conversation(id=conv_id, user_id=user_id, title="Levi's Denim Jacket"))
        db.flush()
        db.add(ImportReview(id=review_id, user_id=user_id, marketplace="poshmark", listing_id=review_id.ljust(24, "0"),
                            title="Levi's denim jacket", conversation_id=conv_id, score=0.9))
        db.commit()


def _call(client, name, arguments, auth):
    response = client.post("/api/mcp", headers=auth, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def test_each_seller_sees_and_settles_only_their_own_import_reviews(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys, USER_ID), headers(signing_keys, BOB)
    _seed(engine, USER_ID, "alice-item", "aaaaaaaa")
    _seed(engine, BOB, "bob-item", "bbbbbbbb")

    listed = client.get("/api/import-reviews", headers=alice)
    assert listed.status_code == 200, listed.text
    assert [review["id"] for review in listed.json()["reviews"]] == ["aaaaaaaa"]
    assert client.post("/api/import-reviews/aaaaaaaa/not-a-match", headers=bob).status_code == 404

    token = client.post("/api/settings/mcp-tokens", headers=bob,
                        json={"name": "Bob", "permissions": {"listings": "write"}}).json()
    bearer = {"Authorization": f"Bearer {token['token']}"}
    body, error = _call(client, "get_import_matches", {}, bearer)
    assert not error and [review["id"] for review in body["reviews"]] == ["bbbbbbbb"]
    for tool in ("reject_import_match", "confirm_import_match"):
        assert _call(client, tool, {"review_id": "aaaaaaaa"}, bearer)[1]
    body, error = _call(client, "reject_import_match", {"review_id": "bbbbbbbb"}, bearer)
    assert not error and body["decision"] == "not_match"
    with Session(engine) as db:
        assert {row.id: row.decision for row in db.query(ImportReview)} == {"aaaaaaaa": "pending", "bbbbbbbb": "not_match"}
