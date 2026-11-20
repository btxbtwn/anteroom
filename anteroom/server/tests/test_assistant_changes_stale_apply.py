"""Applying an assistant proposal must not overwrite an edit the seller made after it."""
from anteroom.repositories.queries import ListingRepo
from anteroom.services import listing_persistence

from test_business_assistant import changes_reply, propose, seed, workspace  # noqa: F401


def test_apply_refuses_a_listing_edited_after_the_proposal(workspace, monkeypatch):  # noqa: F811
    db, client = workspace
    _box, _jeans, coat = (row.id for row in seed(db))
    _response, saved = propose(client, monkeypatch, changes_reply({"listing": coat, "set": {"title": "Assistant title"}}))
    assert saved["changes"]["items"][0]["fields"][0]["old"] == "Carhartt | Chore Coat"

    # The seller retitles the coat in the editor after reading the proposal.
    current = ListingRepo(db).get_revisions(coat)[0].listing_json
    listing_persistence.save_revision(db, coat, {**current, "title": "Seller's newer title"}, source="user_form")
    db.commit()

    applied = client.post(f"/api/assistant/messages/{saved['id']}/changes/apply").json()
    db.expire_all()
    # The proposal showed old="Carhartt | Chore Coat"; the seller's newer title must not be silently replaced.
    assert ListingRepo(db).get_revisions(coat)[0].listing_json["title"] == "Seller's newer title"
    assert applied["changes"]["items"][0]["skipped"] == "It changed since this was proposed. Ask again."


def test_interrupted_apply_resumes_without_reapplying_completed_items(workspace, monkeypatch):  # noqa: F811
    import pytest
    from anteroom.models.assistant import AssistantMessage
    from anteroom.routes import assistant

    db, client = workspace
    _box, jeans, coat = (row.id for row in seed(db))
    _, saved = propose(client, monkeypatch, changes_reply(
        {"listing": jeans, "set": {"asking": 40}},
        {"listing": coat, "set": {"asking": 100}},
    ))
    original = assistant._apply_to_listing
    calls = []

    def fail_second(item, session):
        calls.append(item["listing_id"])
        result = original(item, session)
        if len(calls) == 2:
            raise RuntimeError("database failed after the second edit flushed")
        return result

    monkeypatch.setattr(assistant, "_apply_to_listing", fail_second)
    with pytest.raises(RuntimeError, match="database failed"):
        client.post(f"/api/assistant/messages/{saved['id']}/changes/apply")
    db.expire_all()
    assert db.get(AssistantMessage, saved['id']).changes['completed'] == 1
    first_revision = ListingRepo(db).get_revisions(jeans)[0].id
    assert ListingRepo(db).get_revisions(jeans)[0].listing_json['price'] == 40
    assert ListingRepo(db).get_revisions(coat)[0].listing_json['price'] == 120
    monkeypatch.setattr(assistant, "_apply_to_listing", original)
    retried = client.post(f"/api/assistant/messages/{saved['id']}/changes/apply")
    assert retried.status_code == 200, retried.text
    assert retried.json()['changes']['status'] == 'applied'
    assert ListingRepo(db).get_revisions(jeans)[0].id == first_revision
    assert ListingRepo(db).get_revisions(coat)[0].listing_json['price'] == 100
