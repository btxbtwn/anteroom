"""Applying an assistant proposal must not overwrite an edit the seller made after it."""
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import listing_persistence

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
