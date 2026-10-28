"""Non-destructive photo edits. Every image here is drawn by the test; no photos are committed."""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import photo_edits
from vendoo_studio.services.photo_edits import CropBox, PhotoEdits
from vendoo_studio.services.photo_store import LocalPhotoStore


def _item_photo(width: int = 400, height: int = 300) -> bytes:
    """A blue box on a mid-grey backdrop, the shape of a product shot."""
    img = Image.new("RGB", (width, height), (128, 128, 128))
    ImageDraw.Draw(img).rectangle((width // 4, height // 4, 3 * width // 4, 3 * height // 4), fill=(20, 40, 200))
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def _box_mask(img: Image.Image) -> Image.Image:
    width, height = img.size
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rectangle((width // 4, height // 4, 3 * width // 4, 3 * height // 4), fill=255)
    return mask


@pytest.fixture
def env(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr("vendoo_studio.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    repo = ConversationRepo(db)
    conv = repo.create(title="Denim jacket")
    contents = _item_photo()
    (tmp_path / "front.jpg").write_bytes(contents)
    photo = repo.add_photo(conv.id, "front.jpg", "front.jpg", "image/jpeg", len(contents),
                           checksum="a" * 64, width=400, height=300)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    base = f"/api/conversations/{conv.id}/photos/{photo.id}"
    yield type("Env", (), {"db": db, "repo": repo, "conv": conv, "photo": photo, "dir": tmp_path,
                           "client": client, "base": base, "original": contents})
    app.dependency_overrides.clear()
    db.close()


def _stored(env) -> Image.Image:
    env.db.refresh(env.photo)
    with Image.open(env.dir / env.photo.stored_filename) as img:
        return img.convert("RGB")


def test_square_crop_keeps_the_original_and_serves_the_edit(env):
    response = env.client.put(env.base + "/edits", json={"crop": {"x": 0.125, "y": 0, "width": 0.75, "height": 1}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["width"], body["height"]) == (300, 300)
    assert body["edits"]["crop"]["width"] == 0.75
    env.db.refresh(env.photo)
    assert env.photo.source_filename == "front.jpg"
    assert (env.dir / "front.jpg").read_bytes() == env.original
    assert env.photo.stored_filename != "front.jpg"
    assert body["url"].endswith(env.photo.checksum[:12])
    # The served file, the one every send reads, is the edit.
    served = env.client.get(f"/api/photos/{env.photo.id}")
    assert Image.open(BytesIO(served.content)).size == (300, 300)


def test_edits_render_from_the_original_each_time(env):
    crop = {"x": 0, "y": 0, "width": 0.5, "height": 0.5}
    env.client.put(env.base + "/edits", json={"crop": crop})
    env.db.refresh(env.photo)
    first = env.photo.stored_filename
    response = env.client.put(env.base + "/edits", json={"crop": crop, "exposure": 0.3})
    assert (response.json()["width"], response.json()["height"]) == (200, 150)
    # The previous edit's file goes; the original never does.
    assert not (env.dir / first).exists()
    assert sorted(p.name for p in env.dir.glob("*.jpg")) == sorted(["front.jpg", env.photo.stored_filename])


def test_exposure_brightness_and_contrast_change_the_tone(env):
    env.client.put(env.base + "/edits", json={"exposure": 1})
    assert _stored(env).getpixel((5, 5))[0] > 230
    env.client.put(env.base + "/edits", json={"brightness": -60})
    assert _stored(env).getpixel((5, 5))[0] < 100
    env.client.put(env.base + "/edits", json={"contrast": -100})
    flat = _stored(env)
    assert abs(flat.getpixel((5, 5))[0] - flat.getpixel((200, 150))[0]) < 6


def test_straightening_zooms_in_so_no_corner_is_empty(env):
    response = env.client.put(env.base + "/edits", json={"straighten": 10})
    body = response.json()
    assert body["width"] < 400 and body["height"] < 300
    assert abs(body["width"] / body["height"] - 4 / 3) < 0.02
    img = _stored(env)
    for corner in ((0, 0), (img.width - 1, 0), (0, img.height - 1), (img.width - 1, img.height - 1)):
        assert all(abs(value - 128) < 12 for value in img.getpixel(corner))


def test_turning_a_cropped_photo_turns_the_crop_with_it():
    edits = PhotoEdits(crop=CropBox(x=0, y=0, width=0.5, height=0.25))
    turned = edits.turned(90)
    assert turned.rotate == 90
    assert turned.crop == CropBox(x=0.75, y=0, width=0.25, height=0.5)
    assert edits.turned(90).turned(-90) == edits
    assert edits.turned(180).turned(180) == edits


def test_background_is_replaced_with_the_chosen_colour(env, monkeypatch):
    seen = []

    def mask(img):
        seen.append(img.size)
        return _box_mask(img)

    monkeypatch.setattr(photo_edits, "_vision_mask", mask)
    response = env.client.put(env.base + "/edits", json={"background": "#ffffff"})
    assert response.status_code == 200, response.text
    img = _stored(env)
    assert all(value > 245 for value in img.getpixel((5, 5)))
    assert img.getpixel((200, 150))[2] > 180
    # The cut-out is found once per original and reused by later edits.
    env.client.put(env.base + "/edits", json={"background": "#ff0000", "rotate": 90})
    img = _stored(env)
    assert img.getpixel((5, 5))[0] > 240 and img.getpixel((5, 5))[1] < 20
    assert seen == [(400, 300)]
    assert photo_edits.mask_path("front.jpg").exists()


def test_background_removal_failure_leaves_the_photo_alone(env, monkeypatch):
    def fail(_img):
        raise photo_edits.BackgroundRemovalError("Anteroom could not find an item to cut out in this photo.")

    monkeypatch.setattr(photo_edits, "_vision_mask", fail)
    response = env.client.put(env.base + "/edits", json={"background": "#ffffff"})
    assert response.status_code == 400
    assert "could not find an item" in response.json()["detail"]
    env.db.refresh(env.photo)
    assert (env.photo.stored_filename, env.photo.source_filename, env.photo.edits) == ("front.jpg", None, None)


def test_reset_serves_the_original_again(env):
    env.client.put(env.base + "/edits", json={"crop": {"x": 0, "y": 0, "width": 0.5, "height": 0.5}})
    env.db.refresh(env.photo)
    edited = env.photo.stored_filename
    response = env.client.delete(env.base + "/edits")
    assert response.status_code == 200
    body = response.json()
    assert body["edits"] is None and (body["width"], body["height"]) == (400, 300)
    env.db.refresh(env.photo)
    assert (env.photo.stored_filename, env.photo.source_filename) == ("front.jpg", None)
    assert env.photo.checksum != "a" * 64  # recomputed from the file
    assert not (env.dir / edited).exists()
    assert (env.dir / "front.jpg").read_bytes() == env.original


def test_saving_no_change_is_a_reset(env):
    env.client.put(env.base + "/edits", json={"exposure": 0.5})
    response = env.client.put(env.base + "/edits", json={"crop": {"x": 0, "y": 0, "width": 1, "height": 1}})
    assert response.json()["edits"] is None
    env.db.refresh(env.photo)
    assert env.photo.stored_filename == "front.jpg"


def test_invalid_edits_are_refused(env):
    for body in ({"straighten": 60}, {"background": "white"}, {"crop": {"x": 0.6, "y": 0, "width": 0.6, "height": 1}},
                 {"rotate": 45}, {"sharpen": 3}):
        assert env.client.put(env.base + "/edits", json=body).status_code == 422, body
    env.db.refresh(env.photo)
    assert env.photo.source_filename is None


def test_preview_is_small_and_can_leave_the_frame_whole(env):
    crop = {"x": 0, "y": 0, "width": 0.5, "height": 1}
    whole = env.client.post(env.base + "/edits/preview?crop=false", json={"crop": crop, "rotate": 90})
    assert whole.status_code == 200 and whole.headers["content-type"] == "image/jpeg"
    assert Image.open(BytesIO(whole.content)).size == (300, 400)
    cropped = env.client.post(env.base + "/edits/preview", json={"crop": crop, "rotate": 90})
    assert Image.open(BytesIO(cropped.content)).size == (150, 400)
    env.db.refresh(env.photo)
    assert env.photo.source_filename is None  # previews never save


def test_deleting_an_edited_photo_removes_every_file(env, monkeypatch):
    monkeypatch.setattr(photo_edits, "_vision_mask", _box_mask)
    env.client.put(env.base + "/edits", json={"background": "#ffffff"})
    env.client.get(f"/api/photos/{env.photo.id}/thumb?size=96")
    response = env.client.delete(f"/api/conversations/{env.conv.id}/photos/{env.photo.id}")
    assert response.status_code == 200
    assert [p for p in env.dir.rglob("*") if p.is_file()] == []


def test_a_copied_listing_keeps_the_original_under_the_edits(env):
    from vendoo_studio.services import listing_copy

    env.client.put(env.base + "/edits", json={"rotate": 90})
    env.db.refresh(env.photo)
    ListingRepo(env.db).save_revision(env.conv.id, {"title": "Denim jacket", "price": 30}, "user")
    preview = listing_copy.preview(env.db, env.conv.id)
    response = env.client.post(f"/api/conversations/{env.conv.id}/copy", json={
        "request_id": "a" * 32, "expected_basis": preview["expected_basis"], "title": "Copy", "sku": "COPY-1",
        "include_photos": True, "include_cost": False})
    assert response.status_code == 200, response.text
    copy_id = response.json()["id"]
    [copied] = env.repo.get_photos(copy_id)
    assert copied.edits == {**PhotoEdits(rotate=90).model_dump()}
    assert copied.source_filename not in (None, "front.jpg", env.photo.source_filename)
    assert (env.dir / copied.source_filename).read_bytes() == env.original
    reset = env.client.delete(f"/api/conversations/{copy_id}/photos/{copied.id}/edits").json()
    assert (reset["width"], reset["height"]) == (400, 300)


def test_an_edit_invalidates_a_reviewed_vendoo_send(env):
    from vendoo_studio.services.send_review import review_inputs

    ListingRepo(env.db).save_revision(env.conv.id, {"title": "Denim jacket", "price": 30}, "user")
    before = review_inputs(env.db, env.conv.id)[1]
    env.client.put(env.base + "/edits", json={"contrast": 20})
    assert review_inputs(env.db, env.conv.id)[1] != before


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision runs on macOS")
def test_vision_cuts_out_a_drawn_item():
    pytest.importorskip("Vision")
    img = Image.open(BytesIO(_item_photo(800, 600))).convert("RGB")
    try:
        mask = photo_edits._vision_mask(img)
    except photo_edits.BackgroundRemovalError as exc:
        pytest.skip(str(exc))
    assert mask.size == img.size
    assert mask.getpixel((400, 300)) > 200
    assert mask.getpixel((10, 10)) < 50


def test_packaged_app_bundles_vision():
    spec = (Path(__file__).resolve().parents[2] / "desktop" / "Anteroom.spec").read_text()
    assert '"Vision"' in spec
