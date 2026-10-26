import json
from uuid import uuid4

from test_listing_copy import source as source

from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.listing_copy import ListingCopyReceipt
from vendoo_studio.models.listing_template import ListingTemplate
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import listing_copy, listing_templates


def capture(source, fields=None, **changes):
    data = source.client.get("/api/listing-templates/source/source").json()
    return source.client.post("/api/listing-templates", json={"name": "Coats", "selection": {"source_id": "source", "expected_basis": data["expected_basis"],
        "fields": fields or ["price", "cost", "labels"]}, **changes})


def target(source):
    source.db.add(Conversation(id="target", title="Target", notes='{"cog":"99","vendooLabels":"Keep","private":"keep"}'))
    source.db.commit()
    ListingRepo(source.db).save_revision("target", {"title": "Existing coat", "description": "Original text", "price": 20, "quantity": 3, "cost": 99, "labels": ["Keep"]}, "user_form")


def application(source, template, mode="replace"):
    body = {"conversation_id": "target", "expected_template_revision": template["revision"], "mode": mode}
    review = source.client.post(f"/api/listing-templates/{template['id']}/apply-preview", json=body)
    assert review.status_code == 200, review.text
    return {**body, **{key: value for key, value in review.json().items() if key.startswith("expected_")}}, review.json()


def test_template_captures_only_selected_reusable_fields_and_never_private_identity(source):
    fields = source.client.get("/api/listing-templates/source/source").json()["fields"]
    assert "sku" not in fields and "internal_notes" not in fields and "job_id" not in fields
    assert "listing_id" not in fields["ebay_specifics"]
    response = capture(source, ["description", "ebay_specifics", "direct_category_ids"])
    assert response.status_code == 200, response.text
    template = response.json()
    assert set(template["fields"]) == {"description", "ebay_specifics", "direct_category_ids"}
    assert template["fields"]["ebay_specifics"]["customAspect"] == "Keep"
    assert source.db.query(Conversation).count() == 1 and source.db.query(Photo).count() == 2
    assert source.db.query(ListingTemplate).count() == 1
    assert len(source.client.get("/api/listing-templates").json()["templates"]) == 1
    assert capture(source, ["sku"]).status_code == 422


def test_source_and_template_revisions_guard_capture_rename_replace_and_delete(source):
    selection = source.client.get("/api/listing-templates/source/source").json()
    source.db.get(Conversation, "source").notes = "changed"
    source.db.commit()
    response = source.client.post("/api/listing-templates", json={"name": "Coats", "selection": {"source_id":"source", "expected_basis":selection["expected_basis"], "fields":["price"]}})
    assert response.status_code == 409 and source.db.query(ListingTemplate).count() == 0
    template = capture(source).json()
    path = f"/api/listing-templates/{template['id']}"
    assert source.client.put(path, json={"name":"Renamed", "expected_revision":1}).json()["revision"] == 2
    assert source.client.put(path, json={"name":"Stale", "expected_revision":1}).status_code == 409
    assert source.client.delete(path, params={"expected_revision":1}).status_code == 409
    data = source.client.get("/api/listing-templates/source/source").json()
    updated = source.client.put(path, json={"name":"Replaced", "expected_revision":2, "selection":{"source_id":"source", "expected_basis":data["expected_basis"], "fields":["description"]}})
    assert updated.status_code == 200 and set(updated.json()["fields"]) == {"description"}
    assert source.client.delete(path, params={"expected_revision":3}).status_code == 200
    assert source.db.query(ListingTemplate).count() == 0


def test_template_creates_clean_partial_draft_once_and_results_survive_template_deletion(source):
    template = capture(source).json()
    body = {"request_id":uuid4().hex, "expected_revision":1, "title":"New coat draft", "sku":"NEW", "quantity":4}
    path = f"/api/listing-templates/{template['id']}"
    first = source.client.post(path + "/draft", json=body)
    assert first.status_code == 200, first.text
    key = first.json()["conversation_id"]
    conv = source.db.get(Conversation,key)
    assert conv.status == "draft" and conv.box_id is None
    fields = ListingRepo(source.db).get_revisions(key)[0].listing_json
    assert fields == {"price":40,"cost":9,"labels":["Winter"],"sku":"NEW","quantity":4}
    assert json.loads(conv.notes)["cog"] == "9"
    for model in (Photo, Job, Message, MarketplaceSale):
        assert source.db.query(model).filter_by(conversation_id=key).count() == 0
    assert not MarketplaceListingRepo(source.db).for_conversation(key)
    assert source.db.query(ListingCopyReceipt).one().source_kind == "template"
    assert source.client.delete(path,params={"expected_revision":1}).status_code == 200
    assert source.client.post(path + "/draft",json=body).json()["conversation_id"] == key
    assert source.client.get(path + f"/draft-result/{body['request_id']}").json()["conversation_id"] == key
    assert source.client.get(f"/api/conversations/{template['id']}/copy-result/{body['request_id']}").status_code == 404
    assert source.client.post(path + "/draft",json={**body,"quantity":5}).status_code == 409


def test_apply_requires_review_and_saves_the_reviewed_editor_result_with_item_details(source):
    template = capture(source).json()
    target(source)
    before = source.db.query(ListingRevision).count()
    approval, review = application(source,template)
    assert review["changes"] and source.db.query(ListingRevision).count() == before
    response = source.client.post(f"/api/listing-templates/{template['id']}/apply",json=approval)
    assert response.status_code == 200, response.text
    revision = ListingRepo(source.db).get_revisions("target")[0]
    assert revision.source == "template" and revision.parent_revision_id == approval["expected_revision_id"]
    from vendoo_studio.models.listing_evidence import ListingCorrection
    corrections = source.db.query(ListingCorrection).filter_by(revision_id=revision.id).all()
    assert corrections and all(row.source == "template" for row in corrections)
    assert revision.listing_json["price"] == 40 and revision.listing_json["cost"] == 9 and revision.listing_json["labels"] == ["Winter"]
    assert revision.listing_json["quantity"] == 3 and revision.listing_json["description"] == "Original text"
    notes = json.loads(source.db.get(Conversation,"target").notes)
    assert notes["cog"] == "9" and notes["vendooLabels"] == "Winter" and notes["private"] == "keep"
    assert source.client.post(f"/api/listing-templates/{template['id']}/apply",json=approval).status_code == 409


def test_fill_blanks_preserves_known_zero_and_nested_answers():
    result = listing_templates.apply_fields({"cost":0,"brand":"Known","ebay_specifics":{"Size":"L","color":""}},
                                           {"cost":9,"brand":"Other","ebay_specifics":{"Size":"M","color":"Blue","material":"Wool"}}, "fill_blank")
    assert result == {"cost":0,"brand":"Known","ebay_specifics":{"Size":"L","color":"Blue","material":"Wool"}}


def test_changed_item_template_or_mode_rejects_an_old_application(source, monkeypatch):
    template = capture(source).json()
    target(source)
    approval, _review = application(source,template)
    path = f"/api/listing-templates/{template['id']}"
    assert source.client.post(path+"/apply",json={**approval,"mode":"fill_blank"}).status_code == 409
    conv = source.db.get(Conversation,"target")
    conv.notes = '{"private":"changed"}'
    source.db.commit()
    assert source.client.post(path+"/apply",json=approval).status_code == 409
    approval, _review = application(source,template)
    monkeypatch.setattr(listing_copy,"running_marketplace",lambda db,key: "ebay" if key == "target" else None)
    assert source.client.post(path+"/apply",json=approval).status_code == 409
    monkeypatch.setattr(listing_copy,"running_marketplace",lambda db,key: None)
    assert source.client.put(path,json={"name":"Renamed","expected_revision":1}).status_code == 200
    assert source.client.post(path+"/apply",json=approval).status_code == 409
    assert ListingRepo(source.db).get_revisions("target")[0].listing_json["price"] == 20



def test_category_selections_keep_catalog_ids_but_strip_operational_identity(source):
    listing = ListingRepo(source.db).get_revisions("source")[0].listing_json
    ListingRepo(source.db).save_revision("source", {**listing, "marketplace_category_objects": {"ebay": {"id":"123", "path":["Clothing", "Coats"], "listing_id":"old-listing", "access_token":"do-not-copy"}}}, "user_form")
    response = capture(source, ["marketplace_category_objects"])
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["marketplace_category_objects"]["ebay"] == {"id":"123", "path":["Clothing", "Coats"]}


def test_marketplace_forms_cannot_reuse_private_notes_variant_skus_or_video_identity(source):
    original = ListingRepo(source.db).get_revisions("source")[0].listing_json
    ListingRepo(source.db).save_revision("source", {**original, "poshmark_specifics": {
        "poshmarkSku": "SOURCE-SKU", "sellerNotes": "Private source notes", "video_id": "source-video",
        "originalPrice": 60, "inventoryRows": [{"sizeId": "M", "sizeSetId": "regular", "quantity": 2, "sku": "SOURCE-M"}],
    }}, "user_form")
    result = capture(source, ["poshmark_specifics"])
    assert result.status_code == 200, result.text
    assert result.json()["fields"]["poshmark_specifics"] == {"originalPrice": 60, "inventoryRows": [{"sizeId": "M", "sizeSetId": "regular", "quantity": 2}]}
    draft = source.client.post(f"/api/listing-templates/{result.json()['id']}/draft", json={
        "request_id": uuid4().hex, "expected_revision": 1, "title": "New coat", "sku": "NEW-SKU", "quantity": 1})
    assert draft.status_code == 200, draft.text
    current = ListingRepo(source.db).get_revisions(draft.json()["conversation_id"])[0].listing_json
    assert current["sku"] == "NEW-SKU" and "SOURCE" not in json.dumps(current)


def test_review_matches_the_saved_editor_result_including_item_measurements(source):
    from vendoo_studio.services.bulk_edit import digest
    template = capture(source, ["price"]).json()
    target(source)
    conv = source.db.get(Conversation, "target")
    conv.notes = json.dumps({"garment": "top", "measurements": {"top": {"length": "25", "pitToPit": "20"}}})
    source.db.commit()
    approval, review = application(source, template)
    measurement_change = next(change for change in review["changes"] if change["field"] == "depop_specifics")
    assert measurement_change["new"]["measurements"]["values"][0]["value"] == "20"
    response = source.client.post(f"/api/listing-templates/{template['id']}/apply", json=approval)
    assert response.status_code == 200, response.text
    saved = ListingRepo(source.db).get_revision(response.json()["revision_id"]).listing_json
    assert digest(saved) == approval["expected_result"]
