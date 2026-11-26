import json

import pytest
from sqlalchemy import select, text

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.listing_quality import ListingQualityAttempt
from anteroom.repositories.queries import RevisionConflict
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.listing_persistence import save_revision
from anteroom.services.listing_quality import _changes, _form_corrections

CONSENT = "/api/listing-quality/consent"
REPORT = "/api/operator/listing-quality"


def test_changes_keep_only_fixed_labels_and_counts():
    before = {"title": "Original", "price": 10, "ebay_specifics": {"private custom label": "secret"},
              "sku": "private SKU"}
    after = {"title": "Changed", "size": "S", "sku": "new SKU", "price": None,
             "ebay_specifics": {"private custom label": "done"}}
    assert _changes(before, after, {"title", "size", "price", "sku", "ebay_specifics.private custom label"}) == {
        "general.title": {"replaced": 1}, "general.size": {"filled": 1}, "general.price": {"cleared": 1},
        "ebay.specifics": {"replaced": 1},
    }


def test_marketplace_reconciliation_and_aliases_preserve_only_explicit_corrections():
    generated = {"title": "General title", "price": 18,
                 "poshmark_specifics": {"title": "Poshmark title", "poshmarkPrice": 28}}
    submitted = {**generated, "poshmark_specifics": {"title": "General title", "listingPrice": 18}}
    corrections = _form_corrections({}, generated, generated, submitted)
    assert corrections == {"poshmark.title": {"replaced": 1}, "poshmark.price": {"replaced": 1}}
    # Following General removes both independent marketplace fields from storage.
    stored = {"title": "General title", "price": 18}
    second = _form_corrections(corrections, generated, stored, {**stored, "size": "S"})
    assert second == {**corrections, "general.size": {"filled": 1}}
    cleared = _form_corrections(corrections, generated, stored, {
        **stored, "poshmark_specifics": {"title": ""},
    })
    assert cleared == {"poshmark.title": {"cleared": 1}, "poshmark.price": {"replaced": 1}}
    restored = _form_corrections(second, generated, {**stored, "size": "S"}, generated)
    assert restored == {}
    assert _form_corrections({}, generated, generated, {
        **generated, "poshmark_specifics": {"title": "", "listingPrice": "$28.00"},
    }) == {"poshmark.title": {"cleared": 1}}


def test_hosted_consent_and_attempt_report(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    monkeypatch.setenv("ANTEROOM_OPERATOR_USER_ID", BOB)
    assert client.get(CONSENT).status_code == 401
    assert client.get(CONSENT, headers=alice).json() == {"enabled": False}
    assert client.put(CONSENT, headers=alice, json={"enabled": "true"}).status_code == 422
    assert client.put(CONSENT, headers=alice, json={"enabled": True, "user_id": BOB}).status_code == 422
    assert client.get(REPORT, headers=alice).status_code == 403

    identity = client.post("/api/conversations", headers=alice, json={"title": "Coat"}).json()["id"]
    path = f"/api/conversations/{identity}/listing"

    def generate(source, payload, expected, *, owner=USER_ID, draft=identity):
        with SellerDatabaseSession(bind=engine, user_id=owner) as db:
            db.execute(text("SET LOCAL TIME ZONE 'Europe/Berlin'"))
            return save_revision(db, draft, payload, source=source, expected_revision_id=expected).id

    before_opt_in = generate("model", {"title": "Untracked"}, "")
    assert client.get(REPORT, headers=bob).json()["generated_attempts"] == 0
    assert client.put(CONSENT, headers=alice, json={"enabled": True}).json() == {"enabled": True}
    assert client.get(CONSENT, headers=bob).json() == {"enabled": False}
    assert client.get(REPORT, headers=bob).json()["generated_attempts"] == 0

    authored = client.put(path, headers=alice, json={
        "expected_revision_id": before_opt_in, "listing": {"title": "seller title", "price": 15},
    }).json()["revision_id"]
    base = generate("model", {"title": "generated title", "description": "private prose", "price": 15}, authored)
    with pytest.raises(RevisionConflict):
        generate("model", {"title": "stale attempt"}, authored)
    finalized_payload = {"title": "generated title", "description": "finalized private prose", "price": 15}
    finalized = generate("generation_finalize", finalized_payload, base)
    with SellerDatabaseSession(bind=engine, user_id=BOB) as db:
        assert db.scalars(select(ListingQualityAttempt)).all() == []
    initial_report = client.get(REPORT, headers=bob).json()
    assert initial_report["generated_attempts"] == 1
    assert initial_report["overwrite_compared_attempts"] == 1
    assert initial_report["reviewed_attempts"] == 0
    assert initial_report["overwrites"] == {"model": {"general.title": {"replaced": 1}}}
    assert initial_report["correction_rate"] is None

    edited = client.put(path, headers=alice, json={
        "expected_revision_id": finalized,
        "listing": {"title": "private corrected title", "description": "", "size": "M", "price": 15},
    })
    assert edited.status_code == 200, edited.text
    replay = client.put(path, headers=alice, json={
        "expected_revision_id": finalized, "listing": {"title": "stale seller edit"},
    })
    assert replay.status_code == 409
    rating = client.put(f"/api/conversations/{identity}/generation-feedback", headers=alice, json={
        "revision_id": finalized, "rating": "down", "issues": ["style"], "note": "private rating",
    })
    assert rating.status_code == 200, rating.text
    second = client.put(path, headers=alice, json={
        "expected_revision_id": edited.json()["revision_id"],
        "listing": {**edited.json()["listing"], "brand": "private brand", "primaryColor": "Blue"},
    })
    assert second.status_code == 200, second.text
    report = client.get(REPORT, headers=bob).json()
    assert report["reviewed_attempts"] == report["corrected_attempts"] == 1
    assert report["correction_rate"] == report["overwrite_rate"] == 1
    assert report["corrections"] == {
        "general.title": {"replaced": 1}, "general.description": {"cleared": 1},
        "general.size": {"filled": 1}, "general.brand": {"filled": 1}, "general.primaryColor": {"filled": 1},
    }
    assert report["field_rankings"] == [
        {"field": field, "corrected_attempts": 1, "affected_sellers": 1, "edit_rate": 1}
        for field in ("general.brand", "general.description", "general.primaryColor", "general.size", "general.title")
    ]
    assert "private" not in json.dumps(report)
    assert identity not in json.dumps(report)

    assert client.put(CONSENT, headers=bob, json={"enabled": True}).status_code == 200
    bob_draft = client.post("/api/conversations", headers=bob, json={"title": "Boots"}).json()["id"]
    bob_revision = generate("model", {"title": "Boots", "ebay_specifics": {"Shaft": "short", "Toe": "square"}},
                            "", owner=BOB, draft=bob_draft)
    bob_save = client.put(f"/api/conversations/{bob_draft}/listing", headers=bob, json={
        "expected_revision_id": bob_revision,
        "listing": {"title": "Tall boots", "ebay_specifics": {"Shaft": "tall", "Toe": "round"}},
    })
    assert bob_save.status_code == 200, bob_save.text
    both = client.get(REPORT, headers=bob).json()
    rankings = both["field_rankings"]
    assert both["corrections"]["ebay.specifics"] == {"replaced": 2}
    assert both["overwrite_compared_attempts"] == 1
    assert both["generated_attempts"] == 2
    assert both["overwrite_rate"] == 1
    assert rankings[0] == {"field": "general.title", "corrected_attempts": 2, "affected_sellers": 2, "edit_rate": 1}
    assert rankings[1] == {"field": "ebay.specifics", "corrected_attempts": 1, "affected_sellers": 1, "edit_rate": 0.5}

    assert client.put(CONSENT, headers=alice, json={"enabled": False}).json() == {"enabled": False}
    assert client.get(REPORT, headers=bob).json()["generated_attempts"] == 1
    assert client.get(path, headers=alice).json()["listing"]["title"] == "private corrected title"
    assert client.put(CONSENT, headers=alice, json={"enabled": True}).json() == {"enabled": True}
    assert client.get(REPORT, headers=bob).json()["generated_attempts"] == 1
    assert client.put(CONSENT, headers=bob, json={"enabled": False}).status_code == 200
    assert client.get(REPORT, headers=bob).json()["generated_attempts"] == 0


def test_non_form_revisions_keep_lineage_without_claiming_review(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    monkeypatch.setenv("ANTEROOM_OPERATOR_USER_ID", USER_ID)
    assert client.put(CONSENT, headers=alice, json={"enabled": True}).status_code == 200
    identity = client.post("/api/conversations", headers=alice, json={"title": "Coat"}).json()["id"]
    path = f"/api/conversations/{identity}"

    def save(source, listing, expected):
        with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
            return save_revision(db, identity, listing, source=source, expected_revision_id=expected).id

    draft = {"title": "AI title", "price": 18, "size": "S"}
    generated = save("model", draft, "")
    assert client.get(REPORT, headers=alice).json()["overwrite_rate"] is None
    revision = generated
    for source, fields in (
        ("model_refinement", {"title": "Chat title"}),
        ("user_form", {"title": "Assistant proposal"}),
        ("bulk_edit", {"brand": "Bulk brand"}),
        ("price_markdown", {"price": 15}),
    ):
        draft = {**draft, **fields}
        revision = save(source, draft, revision)
    manual_path = f"/api/drafts/{identity}"
    manual = client.get(manual_path, headers=alice).json()
    patched = client.patch(manual_path, headers=alice, json={
        "title": "Draft patch title", "expected_updated_at": manual["updated_at"], "fields": manual["fields"],
    })
    assert patched.status_code == 200, patched.text
    editor = client.get(path + "/listing", headers=alice).json()
    assert client.get(REPORT, headers=alice).json()["reviewed_attempts"] == 0
    form = client.put(path + "/listing", headers=alice, json={
        "listing": {**editor["listing"], "size": "M"}, "expected_revision_id": editor["current_revision_id"],
    })
    assert form.status_code == 200, form.text
    updated = save("generation_finalize", {**form.json()["listing"], "size": "L"}, form.json()["revision_id"])
    report = client.get(REPORT, headers=alice).json()
    assert report["corrections"] == {"general.size": {"replaced": 1}}
    assert report["overwrites"] == {"generation_finalize": {"general.size": {"replaced": 1}}}
    assert report["overwrite_compared_attempts"] == 1
    reverted = client.post(f"{path}/revisions/{generated}/restore", headers=alice, json={
        "expected_revision_id": updated,
    })
    assert reverted.status_code == 200, reverted.text
    report = client.get(REPORT, headers=alice).json()
    assert report["reviewed_attempts"] == 1 and report["corrections"] == {}
    resaved = client.put(path + "/listing", headers=alice, json={
        "listing": {"title": "Seller title", "price": 18, "size": "S"},
        "expected_revision_id": reverted.json()["revision_id"],
    })
    assert resaved.status_code == 200, resaved.text
    assert client.get(REPORT, headers=alice).json()["corrections"] == {"general.title": {"replaced": 1}}
