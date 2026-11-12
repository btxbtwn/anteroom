import asyncio
import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.models.catalog import CategorySchema
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services.chat_field_context import chat_field_context
from vendoo_studio.services.chat_prompts import build_chat_messages
from worker_sessions import memory_engine, worker_sessions


@pytest.fixture
def context_db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with worker_sessions(engine), sessionmaker(bind=engine)() as db:
        conv = ConversationRepo(db).create(notes=json.dumps({"vendooItemId": "draft-1"}))
        revision = listing_persistence.save_revision(db, conv.id, {
            "title": "Shirt", "category_path": "Clothing > Tops",
            "ebay_specifics": {"pattern": "Solid"},
        }, source="user_form")
        db.add(CategorySchema(
            general_path="Clothing > Tops", marketplace="ebay", category_path="Tops",
            fields=[{"label": "Material", "required": True, "options": ["Cotton", "Silk"]}],
        ))
        db.commit()
        job = JobRepo(db).create(conv.id, revision.id, revision.listing_json,
                                  status="completed")
        yield db, conv.id, job
    engine.dispose()


def rows(prompt):
    return json.loads(prompt.split("\n")[-1])


def test_empty_required_fields_have_options_without_a_draft_readback(context_db):
    db, conv_id, _ = context_db
    prompt = chat_field_context(db, conv_id)
    material = next(row for row in rows(prompt) if row["field"] == "Material")
    assert material["required"] is True
    assert material["options"] == ["Cotton", "Silk"]
    assert material["action"] == "generate_value"








def test_hidden_and_unselected_fields_are_excluded(context_db):
    db, conv_id, job = context_db
    JobRepo(db).add_event(job.id, "completion_review", payload={"schema": {
        "etsy": {"fields": [{"label": "Holiday", "value": ""}]},
        "ebay": {"fields": [{"label": "Return Policy", "value": ""}]},
    }})
    with patch("vendoo_studio.services.chat_field_context.hidden_fields", return_value={
        "always": [{"marketplace": "ebay", "field": "material"}], "listing": [],
    }):
        assert rows(chat_field_context(db, conv_id)) == []


def test_old_draft_observations_are_not_reused(context_db):
    db, conv_id, job = context_db
    job.vendoo_item_id = "old-draft"
    db.commit()
    JobRepo(db).add_event(job.id, "completion_review", payload={"schema": {
        "ebay": {"fields": [{"label": "Pattern", "value": ""}]},
    }})
    assert all(row["field"] != "Pattern" for row in rows(chat_field_context(db, conv_id)))


def test_every_chat_turn_gets_field_context_even_without_skill_rules(context_db):
    db, conv_id, _ = context_db
    with patch("vendoo_studio.services.chat_prompts.load_skill_rules", return_value=""):
        messages = asyncio.run(build_chat_messages(conv_id, "Can you finish the fields?"))
    assert "Listing field gaps" in messages[0]["content"]
    assert "Material" in messages[0]["content"]
    assert "Never publish" in messages[0]["content"]


def test_not_applicable_optional_field_is_excluded_from_readback(context_db):
    db, conv_id, job = context_db
    listing_persistence.save_revision(db, conv_id, {
        "title": "Shirt", "category_path": "Clothing > Tops",
        "ebay_specifics": {"holiday": "Does Not Apply"},
    }, source="model")
    JobRepo(db).add_event(job.id, "completion_review", payload={"schema": {
        "ebay": {"fields": [{"label": "Holiday", "value": ""}]},
    }})
    assert all(row["field"] != "Holiday" for row in rows(chat_field_context(db, conv_id)))




def test_chat_clear_without_status_is_remembered(context_db):
    from vendoo_studio.services.chat_listing import apply_listing_payload

    db, conv_id, _ = context_db
    db.add(CategorySchema(
        general_path="Clothing > Tops", marketplace="general", category_path="Tops",
        fields=[{"label": "Secondary Color", "required": False}],
    ))
    db.commit()
    assert any(row["field"] == "Secondary Color" for row in rows(chat_field_context(db, conv_id)))
    apply_listing_payload(db, conv_id,
        '{"missing_fields":[{"marketplace":"general","field":"Secondary Color","value":""}]}')
    listing = ListingRepo(db).get_revisions(conv_id)[0].listing_json
    assert listing["reviewed_empty_fields"] == [{"marketplace": "general", "field": "Secondary Color"}]
    assert all(row["field"] != "Secondary Color" for row in rows(chat_field_context(db, conv_id)))
    apply_listing_payload(db, conv_id,
        '{"missing_fields":[{"marketplace":"general","field":"Secondary Color","value":"White"}]}')
    listing = ListingRepo(db).get_revisions(conv_id)[0].listing_json
    assert "reviewed_empty_fields" not in listing
    assert listing["secondaryColor"] == "White"


def test_chat_clear_is_recorded_when_saving_without_a_write_on_read(context_db):
    from vendoo_studio.routes.listings import get_listing

    db, conv_id, _ = context_db
    parent = ListingRepo(db).get_revisions(conv_id)[0]
    ConversationRepo(db).add_message(conv_id, "assistant",
        'I left Secondary Color blank because there is no verified second color.\n'
        '{"missing_fields":[{"marketplace":"general","field":"Secondary Color","value":""}]}')
    listing_persistence.save_revision(db, conv_id, parent.listing_json, source="model_refinement",
                                  parent_revision_id=parent.id)
    assert ListingRepo(db).latest_revision(conv_id).listing_json["reviewed_empty_fields"] == [
        {"marketplace": "general", "field": "Secondary Color"},
    ]
    count = len(ListingRepo(db).get_revisions(conv_id))
    response = get_listing(conv_id, db)
    assert response.listing["reviewed_empty_fields"] == [
        {"marketplace": "general", "field": "Secondary Color"},
    ]
    get_listing(conv_id, db)
    assert len(ListingRepo(db).get_revisions(conv_id)) == count




def test_saved_chat_clear_recovery_does_not_override_a_later_seller_value(context_db):
    from vendoo_studio.routes.listings import get_listing

    db, conv_id, _ = context_db
    parent = ListingRepo(db).get_revisions(conv_id)[0]
    ConversationRepo(db).add_message(conv_id, "assistant",
        '{"missing_fields":[{"marketplace":"general","field":"Secondary Color","value":""}]}')
    cleared = listing_persistence.save_revision(db, conv_id, parent.listing_json,
        source="model_refinement", parent_revision_id=parent.id)
    listing_persistence.save_revision(db, conv_id, {**parent.listing_json, "secondaryColor": "White"},
        source="user_form", parent_revision_id=cleared.id)
    response = get_listing(conv_id, db)
    assert response.listing["secondaryColor"] == "White"
    assert "reviewed_empty_fields" not in response.listing
