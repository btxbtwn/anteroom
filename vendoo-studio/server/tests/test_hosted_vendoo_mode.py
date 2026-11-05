from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401


def test_vendoo_draft_routes_require_authentication_and_owned_inventory(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post("/api/drafts", headers=alice, json={"title": "Private draft"}).json()["id"]
    path = f"/api/conversations/{identity}/vendoo-draft"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bob).status_code == 404
    assert client.put("/api/settings/ui", headers=bob, json={"listing_mode": "vendoo"}).status_code == 200
    assert client.post(path, headers=bob, json={"revision_id": "irrelevant"}).status_code == 404
