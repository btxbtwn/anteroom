"""The similar-item finder suggests pairs; only the seller merges or dismisses them."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from vendoo_studio.database import SessionLocal
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import similar_items


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def _every_pair_stays_visible(monkeypatch):
    """Earlier tests leave items behind, and the finder only returns the closest 200.

    A pair this file just created can sort after those and disappear. These tests
    check the match itself, so they read the full list.
    """
    monkeypatch.setattr(similar_items, "MAX_PAIRS", 1_000_000)


def _picture(seed: int, *, shade: int = 0) -> Image.Image:
    image = Image.new("RGB", (240, 320), (230 - shade, 225 - shade, 220 - shade))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40 + seed * 30, 60, 160 + seed * 10, 280 - seed * 20), fill=(30 + seed * 40, 60, 120))
    draw.ellipse((20, 20 + seed * 25, 90, 90 + seed * 25), fill=(200, 40 + seed * 30, 40))
    return image


def _item(db, title: str, *, brand: str = "", size: str = "", photos: list[tuple[Image.Image, str]] = (), labels: str = ""):
    from vendoo_studio.config import PHOTOS_DIR

    conv = ConversationRepo(db).create(title=title)
    conv.notes = json.dumps({"vendooLabels": labels}) if labels else None
    ListingRepo(db).save_revision(conv.id, {"title": title, "brand": brand, "size": size, "price": 40, "labels": labels.split(", ") if labels else []}, source="user_form")
    Path(PHOTOS_DIR).mkdir(parents=True, exist_ok=True)
    for order, (image, checksum) in enumerate(photos):
        name = f"{uuid.uuid4().hex}.jpg"
        image.save(Path(PHOTOS_DIR) / name, "JPEG", quality=92)
        db.add(Photo(conversation_id=conv.id, original_filename=name, stored_filename=name, mime_type="image/jpeg",
                     size_bytes=1, display_order=order, checksum=checksum))
    db.commit()
    return conv


def _pairs(db, *items) -> dict[frozenset[str], dict]:
    """Suggested pairs whose items are both in ``items``.

    Other tests leave rows in this database. A pair is kept only when both
    items belong to this test, so a leftover lookalike cannot count as one.
    """
    ids = {item.id for item in items}
    return {
        key: pair for pair in similar_items.find_similar(db)["pairs"]
        if (key := frozenset((pair["first"]["id"], pair["second"]["id"]))) <= ids
    }


def test_photos_skus_and_titles_suggest_pairs_but_a_different_size_does_not(db):
    nike = _item(db, "Nike Air Max 90 Sneakers White Mens", brand="Nike", size="10")
    reworded = _item(db, "White Nike Air Max 90 sneakers mens", brand="nike", size="10")
    other_size = _item(db, "Nike Air Max 90 Sneakers White Mens", brand="Nike", size="12")
    shared_a = _item(db, "Wool coat", photos=[(_picture(1), "same-file")])
    shared_b = _item(db, "Grey overcoat", photos=[(_picture(2), "front"), (_picture(1), "same-file")])
    reshot_a = _item(db, "Floral midi dress summer", photos=[(_picture(3), "dress-1")])
    reshot_b = _item(db, "Summer floral midi dress", photos=[(_picture(3, shade=8), "dress-2")])
    # Same backdrop, different garment: a lookalike cover alone is not a match.
    lookalike = _item(db, "Levi's 501 jeans", photos=[(_picture(3, shade=4), "jeans")])
    sku_a = _item(db, "Blue blouse")
    sku_b = _item(db, "Silk top")
    for conv in (sku_a, sku_b):
        conv.notes = json.dumps({"sku": "BIN-7"})
    db.commit()

    pairs = _pairs(db, nike, reworded, other_size, shared_a, shared_b, reshot_a, reshot_b, lookalike, sku_a, sku_b)
    assert pairs[frozenset((nike.id, reworded.id))]["reasons"] == ["similar_title"]
    assert frozenset((nike.id, other_size.id)) not in pairs
    assert frozenset((reworded.id, other_size.id)) not in pairs
    assert pairs[frozenset((shared_a.id, shared_b.id))]["reasons"] == ["same_photo"]
    assert pairs[frozenset((sku_a.id, sku_b.id))]["reasons"] == ["same_sku"]
    reshot = pairs[frozenset((reshot_a.id, reshot_b.id))]
    assert reshot["reasons"] == ["similar_title", "similar_photo"] and reshot["photo_distance"] <= similar_items.PHOTO_DISTANCE
    assert not any(lookalike.id in key for key in pairs)
    assert reshot["first"]["cover_url"].startswith("/api/photos/")
    # Fingerprints are kept, so the next scan reads no file.
    from vendoo_studio.models.similar_item import PhotoFingerprint

    assert db.query(PhotoFingerprint).filter(PhotoFingerprint.checksum.in_(["dress-1", "dress-2"])).count() == 2


def test_a_dismissed_pair_is_not_suggested_again(db):
    first = _item(db, "Patagonia Better Sweater fleece jacket", brand="Patagonia")
    second = _item(db, "Patagonia better sweater jacket fleece", brand="Patagonia")
    client = TestClient(app)

    def suggested() -> bool:
        return any({pair["first"]["id"], pair["second"]["id"]} == {first.id, second.id}
                   for pair in client.get("/api/similar-items").json()["pairs"])

    assert suggested()
    assert client.post("/api/similar-items/dismiss", json={"first_id": second.id, "second_id": first.id}).status_code == 200
    assert not suggested()
    assert db.get(Conversation, first.id) and db.get(Conversation, second.id)


def test_merge_moves_links_sales_and_labels_and_keeps_the_other_item(db):
    keep = _item(db, "Carhartt Detroit jacket brown", brand="Carhartt", labels="A19")
    other = _item(db, "Carhartt detroit jacket in brown", brand="Carhartt", labels="a19, Closet")
    db.add(MarketplaceListing(conversation_id=keep.id, marketplace="ebay", state="published", listing_id="e1"))
    db.add(MarketplaceListing(conversation_id=other.id, marketplace="poshmark", state="published", listing_id="p1"))
    db.add(MarketplaceSale(marketplace="poshmark", order_id="o1", line_id="l1", conversation_id=other.id))
    db.commit()

    response = TestClient(app).post("/api/similar-items/merge", json={"keep_id": keep.id, "merge_id": other.id})
    assert response.status_code == 200, response.text
    assert response.json() == {"kept_id": keep.id, "merged_id": other.id, "marketplaces": ["poshmark"], "sales": 1, "labels": ["Closet"]}

    db.expire_all()
    assert {row.marketplace for row in db.query(MarketplaceListing).filter_by(conversation_id=keep.id)} == {"ebay", "poshmark"}
    assert db.query(MarketplaceSale).filter_by(order_id="o1").one().conversation_id == keep.id
    assert json.loads(db.get(Conversation, keep.id).notes)["vendooLabels"] == "A19, Closet"
    assert ListingRepo(db).get_revisions(keep.id)[0].listing_json["labels"] == ["A19", "Closet"]
    merged = db.get(Conversation, other.id)
    assert merged is not None and merged.settled_at is not None
    assert json.loads(merged.notes)["mergedInto"] == keep.id
    assert _pairs(db, keep, other) == {}


def test_merge_refuses_two_listings_on_one_marketplace(db):
    keep = _item(db, "Coach tabby bag black leather", brand="Coach")
    other = _item(db, "Coach Tabby black leather bag", brand="Coach")
    for conv, listing_id in ((keep, "a"), (other, "b")):
        db.add(MarketplaceListing(conversation_id=conv.id, marketplace="depop", state="published", listing_id=listing_id))
    db.commit()

    response = TestClient(app).post("/api/similar-items/merge", json={"keep_id": keep.id, "merge_id": other.id})
    assert response.status_code == 409
    assert "Depop" in response.json()["detail"]
    db.expire_all()
    assert db.query(MarketplaceListing).filter_by(conversation_id=other.id).count() == 1
    assert db.get(Conversation, other.id).settled_at is None


def test_merge_waits_for_a_running_send(db):
    keep = _item(db, "Arcteryx Atom LT hoody")
    other = _item(db, "Arcteryx Atom LT hoodie")
    other.status = "listing"
    db.commit()
    with pytest.raises(similar_items.MergeError) as error:
        similar_items.merge(db, keep.id, other.id)
    assert error.value.status == 409
