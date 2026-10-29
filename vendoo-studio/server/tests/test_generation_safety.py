"""Regressions for generation using stale photos or overwriting seller edits."""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vendoo_studio.database import Base, load_models
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo, RevisionConflict
from vendoo_studio.services import listing_field_gaps, listing_generate


@pytest.fixture
def draft():
    load_models()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conv = ConversationRepo(db).create(title="Original")
        yield db, conv.id
    engine.dispose()


@pytest.mark.parametrize("change", ["upload", "delete", "edit", "reorder"])
def test_changed_photos_do_not_reuse_old_analysis(draft, change):
    db, conv_id = draft
    repo = ConversationRepo(db)
    first = repo.add_photo(conv_id, "a.jpg", "a.jpg", "image/jpeg", 10, checksum="a")
    second = repo.add_photo(conv_id, "b.jpg", "b.jpg", "image/jpeg", 10, checksum="b")
    text = "Photo analysis:\n- brand: Original"
    repo.add_message(conv_id, "system", text,
                     photo_fingerprint=listing_generate.photo_fingerprint(repo.get_photos(conv_id)))
    assert listing_generate.current_photo_analysis(repo, conv_id) == text
    if change == "upload":
        repo.add_photo(conv_id, "c.jpg", "c.jpg", "image/jpeg", 10)
    elif change == "delete":
        repo.delete_photo(conv_id, first.id)
    elif change == "edit":
        first.checksum = "edited"
        db.commit()
    else:
        repo.reorder_photos(conv_id, [second.id, first.id])
    assert listing_generate.current_photo_analysis(repo, conv_id) is None


def test_marketplace_only_photos_do_not_invalidate_item_reading(draft):
    db, conv_id = draft
    repo = ConversationRepo(db)
    repo.add_photo(conv_id, "a.jpg", "a.jpg", "image/jpeg", 10)
    text = "Photo analysis:\n- brand: Original"
    repo.add_message(conv_id, "system", text,
                     photo_fingerprint=listing_generate.photo_fingerprint(repo.get_photos(conv_id)))
    repo.add_photo(conv_id, "b.jpg", "b.jpg", "image/jpeg", 10, marketplace="ebay")
    assert listing_generate.current_photo_analysis(repo, conv_id) == text


def test_gap_fill_cannot_overwrite_an_edit_made_during_model_call(draft, monkeypatch):
    db, conv_id = draft
    listings = ListingRepo(db)
    original = {"title": "Original", "price": 20}
    listings.save_revision(conv_id, original, source="model")
    seller = {"title": "Seller edit", "price": 99}

    async def reply(*args, **kwargs):
        listings.save_revision(conv_id, seller, source="user_form")
        return [{"marketplace": "etsy", "field": "Pattern", "value": "Solid"}]

    monkeypatch.setattr(listing_field_gaps.RegistryService, "merge_learned_fields", lambda *args: None)
    monkeypatch.setattr(listing_field_gaps, "collect_empty_discovered_fields",
                        lambda *args: [{"marketplace": "etsy", "field": "Pattern"}])
    monkeypatch.setattr(listing_field_gaps, "_request_missing_field_values", reply)
    with pytest.raises(RevisionConflict):
        asyncio.run(listing_field_gaps.fill_listing_field_gaps(
            db, conv_id, original, object(), evidence="Solid"))
    assert listings.get_revisions(conv_id)[0].listing_json == seller
    assert len(listings.get_revisions(conv_id)) == 2


def test_validation_repair_cannot_overwrite_a_newer_seller_edit(draft, monkeypatch):
    db, conv_id = draft
    listings = ListingRepo(db)
    seller = {"title": "Seller edit", "price": 99}

    async def repair(*args, **kwargs):
        listings.save_revision(conv_id, seller, source="user_form")
        return {"title": "Repaired", "price": 20}

    monkeypatch.setattr(listing_generate, "_validation_blockers", lambda *args: [{"field": "price"}])
    monkeypatch.setattr(listing_generate, "fill_validation_gaps", repair)
    with pytest.raises(RevisionConflict):
        asyncio.run(listing_generate.persist_generated_listing_with_repair(
            db, conv_id, '{"title":"Original","price":20}', object()))
    assert listings.get_revisions(conv_id)[0].listing_json == seller


def test_first_listing_cannot_overwrite_a_seller_draft_created_while_generating(draft):
    db, conv_id = draft
    listings = ListingRepo(db)
    seller = {"title": "Seller draft", "price": 99}
    listings.save_revision(conv_id, seller, source="user_form")
    with pytest.raises(RevisionConflict):
        listing_generate.persist_generated_listing(
            db, conv_id, '{"title":"Generated","price":20}', expected_revision_id="")
    assert listings.get_revisions(conv_id)[0].listing_json == seller


def test_native_fill_discards_evidence_if_photos_change_while_loading_forms(draft, monkeypatch):
    from vendoo_studio.services import native_field_fill

    db, conv_id = draft
    repo = ConversationRepo(db)
    repo.add_photo(conv_id, "a.jpg", "a.jpg", "image/jpeg", 10)
    listings = ListingRepo(db)
    original = {"title": "Original", "ebay_specifics": {}}
    listings.save_revision(conv_id, original, source="model")

    async def load_forms(*args, **kwargs):
        repo.add_photo(conv_id, "new.jpg", "new.jpg", "image/jpeg", 10)
        return {"ebay": {"category_id": "1", "fields": [{
            "path": "ebay_specifics.Closure", "label": "Closure", "options": ["Zip"],
            "input_type": "select", "category_specific": True,
        }]}}

    monkeypatch.setattr(native_field_fill, "load_forms", load_forms)
    monkeypatch.setattr(listing_generate, "collect_provider_text", AsyncMock(
        return_value='{"fields":{"ebay_specifics.Closure":"Zip"}}'))
    result = asyncio.run(native_field_fill._fill_native_fields(
        db, conv_id, object(), evidence="Zip", markets=["ebay"], categories=False))
    assert result is None
    assert listings.get_revisions(conv_id)[0].listing_json == original
    assert len(listings.get_revisions(conv_id)) == 1


def test_category_selection_cannot_overwrite_a_newer_seller_edit(draft, monkeypatch):
    from vendoo_studio.services import schema_probe

    db, conv_id = draft
    listings = ListingRepo(db)
    listings.save_revision(conv_id, {"title": "Original", "price": 20}, source="model")
    seller = {"title": "Seller edit", "price": 99}

    async def choose(*args, **kwargs):
        listings.save_revision(conv_id, seller, source="user_form")
        return {"general": "Clothing > Tops"}

    monkeypatch.setattr("vendoo_studio.services.category_selection.select_categories", choose)
    monkeypatch.setattr("vendoo_studio.services.marketplaces.selected_fillable_platforms", lambda: [])
    monkeypatch.setattr(schema_probe, "mapped_marketplace_paths", AsyncMock(return_value={}))
    monkeypatch.setattr("vendoo_studio.services.direct_categories.attach_direct_categories", AsyncMock())
    with pytest.raises(RevisionConflict):
        asyncio.run(schema_probe.prepare_generation_schema(db, conv_id, object(), "Cotton tee", ""))
    assert listings.get_revisions(conv_id)[0].listing_json == seller
    assert len(listings.get_revisions(conv_id)) == 2


@pytest.mark.parametrize("module,class_name", [
    ("chat_completions", "ChatCompletionsProvider"),
    ("chatgpt_codex", "ChatGPTCodexProvider"),
    ("xiaomi_mimo", "MiMoProvider"),
    ("anthropic_api", "AnthropicAPIProvider"),
    ("claude_agent", "ClaudeProvider"),
    ("cursor_agent", "CursorProvider"),
])
def test_every_provider_encodes_photos_after_the_tenth(monkeypatch, module, class_name):
    module = importlib.import_module(f"vendoo_studio.providers.{module}")
    provider_type = getattr(module, class_name)
    provider = SimpleNamespace(vision_model="fixture")
    paths = [f"photo-{i}.jpg" for i in range(20)]

    class Encoded(Exception):
        pass

    encode = AsyncMock(side_effect=Encoded)
    monkeypatch.setattr(module, "encode_images", encode)
    with pytest.raises(Encoded):
        asyncio.run(provider_type.analyze_photos(provider, paths))
    assert encode.await_args.args[0] == paths


def test_full_resolution_retry_includes_a_late_unreadable_tag(monkeypatch):
    unreadable = {"evidence": {"size": {"value": "unreadable", "source": "unreadable"}}}
    readable = {"evidence": {"size": {"value": "XL", "source": "tag"}}}
    provider = SimpleNamespace(analyze_photos=AsyncMock(side_effect=[unreadable, readable]))
    paths = [f"photo-{i}.jpg" for i in range(20)]
    monkeypatch.setattr("vendoo_studio.providers.xiaomi_mimo.image_exceeds",
                        lambda path, limit: path == paths[-1])
    result = asyncio.run(listing_generate.analyze_photos_with_tag_retry(provider, paths))
    assert result["evidence"]["size"]["value"] == "XL"
    assert provider.analyze_photos.await_args.args[0] == paths
    assert provider.analyze_photos.await_args.kwargs["max_side"] is None
