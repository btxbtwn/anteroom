"""AI Studio (Photoroom) and the free manual photo tools. Every image is drawn by the test;
Photoroom is never reached: its HTTP answers are mocked or the sandbox stand-in is used."""
from __future__ import annotations

import asyncio
import json
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom import database
from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import listing_persistence, photoroom
from anteroom.services.photo_edits import Annotation, PhotoEdits, Redaction
from anteroom.services.photo_store import LocalPhotoStore
from anteroom.services.photoroom import PhotoroomError, StudioOptions, VirtualModel

KEY = "fake" * 6  # A stand-in; never a real Photoroom key.


def _photo(width: int = 400, height: int = 300) -> bytes:
    img = Image.new("RGB", (width, height), (128, 128, 128))
    ImageDraw.Draw(img).rectangle((width // 4, height // 4, 3 * width // 4, 3 * height // 4), fill=(20, 40, 200))
    # A left/right marker, to see a flip.
    ImageDraw.Draw(img).rectangle((0, 0, 20, height), fill=(250, 10, 10))
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def _png(color: str, size: tuple[int, int] = (320, 320)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    monkeypatch.setattr("anteroom.services.photo_store._store", LocalPhotoStore(str(tmp_path)))
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    monkeypatch.delenv(photoroom.MANAGED_KEY_ENV, raising=False)
    repo = ConversationRepo(db)
    conv = repo.create(title="Denim jacket")
    photos = []
    for name in ("front.jpg", "back.jpg"):
        contents = _photo()
        (tmp_path / name).write_bytes(contents)
        photos.append(listing_persistence.add_photo(repo.db, conv.id, name, name, "image/jpeg", len(contents),
                                                    checksum="a" * 64, width=400, height=300))
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield type("Env", (), {"db": db, "conv": conv, "photos": photos, "dir": tmp_path, "client": client,
                           "studio": f"/api/conversations/{conv.id}/photos/studio",
                           "base": f"/api/conversations/{conv.id}/photos/{photos[0].id}"})
    app.dependency_overrides.clear()
    db.close()


def _stored(env, photo) -> Image.Image:
    env.db.refresh(photo)
    with Image.open(env.dir / photo.stored_filename) as img:
        return img.convert("RGB")


# --- Valid combinations -------------------------------------------------------------------


def test_studio_options_enforce_valid_combinations():
    with pytest.raises(ValidationError, match="Ghost mannequin or Flat lay"):
        StudioOptions(ghost_mannequin=True, flat_lay=True)
    with pytest.raises(ValidationError, match="virtual model makes its own scene"):
        StudioOptions(virtual_model=VirtualModel(), shadow=True)
    with pytest.raises(ValidationError, match="virtual model makes its own scene"):
        StudioOptions(virtual_model=VirtualModel(), remove_background=True)
    with pytest.raises(ValidationError, match="Choose an AI Studio edit"):
        StudioOptions()
    with pytest.raises(ValidationError):
        StudioOptions(remove_background=True, background="white")
    # Background removal comes with Ghost mannequin, Flat lay and Shadow.
    for choice in ("ghost_mannequin", "flat_lay", "shadow"):
        assert StudioOptions(**{choice: True}).remove_background is True
    assert StudioOptions(remove_background=True, background="#00ff00").background == "#00FF00"
    with pytest.raises(ValidationError):
        VirtualModel(pose="cartwheel")


def test_studio_options_become_photoroom_form_fields():
    assert StudioOptions(remove_background=True, background="#F5F5DC").form() == {
        "removeBackground": "true", "background.color": "F5F5DC", "export.format": "png", "outputSize": "originalImage"}
    ghost = StudioOptions(ghost_mannequin=True, shadow=True).form()
    assert ghost["ghostMannequin.mode"] == "ai.auto" and ghost["shadow.mode"] == "ai.soft"
    assert ghost["background.color"] == "FFFFFF" and "outputSize" not in ghost
    assert StudioOptions(flat_lay=True).form()["flatLay.mode"] == "ai.auto"
    auto = StudioOptions(virtual_model=VirtualModel()).form()
    assert auto == {"removeBackground": "false", "referenceBox": "originalImage", "virtualModel.mode": "ai.auto",
                    "virtualModel.scene.preset.name": "random",
                    "virtualModel.pose": "random", "export.format": "png"}
    chosen = StudioOptions(virtual_model=VirtualModel(model="maya", scene="beach", pose="seated")).form()
    assert chosen["virtualModel.model.preset.name"] == "maya"
    assert chosen["virtualModel.scene.preset.name"] == "beach" and chosen["virtualModel.pose"] == "seated"
    # Text-guided segmentation: optional keep/leave-out words Photoroom's preview API accepts.
    guided = StudioOptions(remove_background=True, keep="shirt", exclude="ruler, stand").form()
    assert guided["segmentation.prompt"] == "shirt"
    assert guided["segmentation.negativePrompt"] == "ruler, stand"
    blank = StudioOptions(remove_background=True, keep="  ", exclude="").form()
    assert "segmentation.prompt" not in blank and "segmentation.negativePrompt" not in blank
    with pytest.raises(ValidationError, match="virtual model makes its own scene"):
        StudioOptions(virtual_model=VirtualModel(), keep="shirt")


def test_studio_route_refuses_invalid_combinations(env):
    response = env.client.post(env.studio, json={"photo_ids": [env.photos[0].id],
                                                 "options": {"ghost_mannequin": True, "flat_lay": True}})
    assert response.status_code == 422
    response = env.client.post(env.studio, json={"photo_ids": [], "options": {"remove_background": True}})
    assert response.status_code == 422


# --- The Photoroom client -----------------------------------------------------------------


def _mock_photoroom(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(photoroom, "_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                                                                  timeout=timeout))
    return seen


def test_edit_sends_the_photo_and_options_with_the_sellers_key(monkeypatch):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    seen = _mock_photoroom(monkeypatch, lambda _request: httpx.Response(200, content=_png("white"),
                                                                         headers={"content-type": "image/png"}))
    result = asyncio.run(photoroom.edit(_photo(), StudioOptions(remove_background=True, background="#112233")))
    assert result.startswith(b"\x89PNG")
    request = seen[0]
    assert request.url == httpx.URL(photoroom.ENDPOINT)
    assert request.headers["x-api-key"] == KEY
    body = request.content
    assert b'name="background.color"\r\n\r\n112233' in body
    assert b'name="imageFile"; filename="photo.jpg"' in body
    assert b"Content-Type: image/jpeg" in body

    # Transparent PNG input preserves alpha by uploading as PNG.
    alpha = BytesIO()
    img = Image.new("RGBA", (32, 32), (10, 20, 30, 0))
    img.putpixel((0, 0), (10, 20, 30, 255))
    img.save(alpha, "PNG")
    asyncio.run(photoroom.edit(alpha.getvalue(), StudioOptions(remove_background=True)))
    assert b'name="imageFile"; filename="photo.png"' in seen[1].content
    assert b"Content-Type: image/png" in seen[1].content


def test_palette_png_keeps_transparent_pixels():
    palette = Image.new("P", (2, 1))
    palette.putpalette([0, 0, 0, 255, 255, 255])
    palette.putpixel((1, 0), 1)
    data = BytesIO()
    palette.save(data, "PNG", transparency=0)

    contents, filename, content_type = photoroom._prepare_upload(data.getvalue())
    assert (filename, content_type) == ("photo.png", "image/png")
    with Image.open(BytesIO(contents)) as result:
        assert result.getchannel("A").getextrema() == (0, 255)


def test_managed_key_takes_over_and_ignores_seller_key(monkeypatch):
    from anteroom.services.keychain import delete_slot, write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    assert photoroom.configured_key() == (KEY, "seller")
    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "managed-key")
    assert photoroom.managed_photoroom_enabled() is True
    assert photoroom.configured_key() == ("managed-key", "server")
    delete_slot(photoroom.KEY_ACCOUNT)
    assert photoroom.configured_key() == ("managed-key", "server")
    monkeypatch.delenv(photoroom.MANAGED_KEY_ENV)
    assert photoroom.configured_key() == (None, None)
    assert photoroom.managed_photoroom_enabled() is False


@pytest.mark.parametrize(("status", "body", "words"), [
    (401, {"detail": "bad"}, "refused the API key"),
    (402, {"error": {"detail": "no credits"}}, "no credits left"),
    (429, {}, "Photoroom is busy"),
    (400, {"error": {"message": "Image too small"}}, "Image too small"),
    (500, {"error": {"message": "boom"}}, "boom"),
])
def test_photoroom_refusals_are_explained_without_the_key(monkeypatch, status, body, words):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    _mock_photoroom(monkeypatch, lambda _request: httpx.Response(status, json=body))
    with pytest.raises(PhotoroomError) as caught:
        asyncio.run(photoroom.edit(_photo(), StudioOptions(remove_background=True)))
    assert words in str(caught.value)
    assert KEY not in str(caught.value)


def test_a_stalled_photoroom_call_times_out(monkeypatch):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)

    def stall(request):
        raise httpx.ReadTimeout("stalled", request=request)

    _mock_photoroom(monkeypatch, stall)
    with pytest.raises(PhotoroomError, match="did not finish this photo in time") as caught:
        asyncio.run(photoroom.edit(_photo(), StudioOptions(remove_background=True)))
    assert caught.value.status == 504
    monkeypatch.setattr(photoroom, "CALL_DEADLINE_SEC", 0.05)

    async def slow(_request):
        await asyncio.sleep(1)
        return httpx.Response(200, content=_png("white"))

    monkeypatch.setattr(photoroom, "_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(PhotoroomError, match="in time"):
        asyncio.run(photoroom.edit(_photo(), StudioOptions(remove_background=True)))


def test_without_a_key_ai_studio_asks_for_one(env):
    response = env.client.post(env.studio, json={"photo_ids": [env.photos[0].id], "options": {"remove_background": True}})
    assert response.status_code == 400
    assert "Photoroom API key" in response.json()["detail"]


def test_sandbox_never_calls_photoroom(monkeypatch):
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")

    def forbidden(_timeout):
        raise AssertionError("the sandbox reached Photoroom")

    monkeypatch.setattr(photoroom, "_client", forbidden)
    result = asyncio.run(photoroom.edit(_photo(), StudioOptions(remove_background=True, background="#00FF00")))
    with Image.open(BytesIO(result)) as img:
        assert img.convert("RGB").getpixel((1, 1)) == (0, 255, 0)


# --- AI Studio on listing photos ----------------------------------------------------------


def test_batch_ai_studio_keeps_each_original_and_reset_returns_to_it(env, monkeypatch):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    seen = _mock_photoroom(monkeypatch, lambda _request: httpx.Response(200, content=_png("#FFFFFF", (500, 500))))
    # The test database is one shared in-memory connection; real sessions come from a pool.
    monkeypatch.setattr(photoroom, "CONCURRENCY", 1)
    originals = [(env.dir / photo.stored_filename).read_bytes() for photo in env.photos]
    response = env.client.post(env.studio, json={"photo_ids": [photo.id for photo in env.photos],
                                                 "options": {"ghost_mannequin": True}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] == []
    assert len(seen) == 2
    assert [photo["id"] for photo in body["photos"]] == [photo.id for photo in env.photos]
    for saved, photo, original in zip(body["photos"], env.photos, originals, strict=True):
        assert saved["edits"]["studio"]["options"]["ghost_mannequin"] is True
        assert saved["edits"]["studio"]["options"]["remove_background"] is True
        assert (saved["width"], saved["height"]) == (500, 500)
        env.db.refresh(photo)
        assert photo.source_filename in ("front.jpg", "back.jpg")
        assert (env.dir / photo.source_filename).read_bytes() == original
        assert (env.dir / saved["edits"]["studio"]["key"]).exists()
    studio_key = body["photos"][0]["edits"]["studio"]["key"]

    # A manual edit renders on top of the AI result and keeps it.
    kept = env.client.put(env.base + "/edits", json={**body["photos"][0]["edits"], "border": {"width": 0.1, "color": "#000000"}})
    assert kept.status_code == 200, kept.text
    assert kept.json()["edits"]["studio"]["key"] == studio_key
    assert (kept.json()["width"], kept.json()["height"]) == (600, 600)

    reset = env.client.delete(env.base + "/edits")
    assert reset.status_code == 200
    assert reset.json()["edits"] is None
    assert (reset.json()["width"], reset.json()["height"]) == (400, 300)
    assert not (env.dir / studio_key).exists()
    env.db.refresh(env.photos[0])
    assert env.photos[0].stored_filename == "front.jpg"


def test_ai_studio_sends_what_the_seller_sees_so_redactions_never_leave(env, monkeypatch):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    env.client.put(env.base + "/edits", json={"redactions": [{"x": 0, "y": 0, "width": 1, "height": 1, "style": "pixelate"}]})
    redacted = _stored(env, env.photos[0])
    assert redacted.tobytes() != Image.open(env.dir / "front.jpg").convert("RGB").tobytes()
    sent: list[bytes] = []

    def answer(request):
        sent.append(request.content)
        return httpx.Response(200, content=_png("white"))

    _mock_photoroom(monkeypatch, answer)
    response = env.client.post(env.studio, json={"photo_ids": [env.photos[0].id], "options": {"remove_background": True}})
    assert response.status_code == 200, response.text
    # The redaction was applied before sending, then starts over on the result.
    assert response.json()["photos"][0]["edits"]["redactions"] == []
    start = sent[0].index(b"\xff\xd8\xff")
    end = sent[0].rindex(b"\xff\xd9") + 2
    with Image.open(BytesIO(sent[0][start:end])) as img:
        uploaded = img.convert("RGB")
        assert uploaded.size == redacted.size
        original = Image.open(env.dir / "front.jpg").convert("RGB")
        from PIL import ImageChops, ImageStat
        diff_redacted = sum(ImageStat.Stat(ImageChops.difference(uploaded, redacted)).mean)
        diff_original = sum(ImageStat.Stat(ImageChops.difference(uploaded, original)).mean)
        # Uploaded image matches the redacted photo the seller sees, never the unredacted upload.
        assert diff_redacted < 5.0
        assert diff_redacted < diff_original


def test_one_failed_photo_does_not_stop_the_others(env, monkeypatch):
    from anteroom.services.keychain import write_slot

    write_slot(photoroom.KEY_ACCOUNT, KEY)
    calls = {"count": 0}

    def answer(_request):
        calls["count"] += 1
        return httpx.Response(200, content=_png("white")) if calls["count"] == 1 else httpx.Response(402, json={})

    _mock_photoroom(monkeypatch, answer)
    monkeypatch.setattr(photoroom, "CONCURRENCY", 1)
    response = env.client.post(env.studio, json={"photo_ids": ["missing"], "options": {"remove_background": True}})
    assert response.json() == {"photos": [], "errors": [{"photo_id": "missing", "detail": "Photo not found"}]}
    assert calls["count"] == 0
    response = env.client.post(env.studio, json={"photo_ids": [photo.id for photo in env.photos],
                                                 "options": {"remove_background": True}})
    body = response.json()
    assert len(body["photos"]) == 1
    assert body["errors"] == [{"photo_id": env.photos[1].id, "detail": "Your Photoroom plan has no credits left for this edit."}]
    env.db.refresh(env.photos[1])
    assert env.photos[1].edits is None
    # Only the saved photo's result and the two uploads are on disk.
    assert len(list(env.dir.glob("*.jpg"))) == 4  # two uploads, one AI base, one rendered edit


def test_edits_cannot_point_at_another_file(env):
    response = env.client.put(env.base + "/edits", json={"studio": {"key": "back.jpg", "options": {"remove_background": True}}})
    assert response.status_code == 400
    assert "Use AI Studio" in response.json()["detail"]
    preview = env.client.post(env.base + "/edits/preview",
                              json={"studio": {"key": "back.jpg", "options": {"remove_background": True}}})
    assert preview.status_code == 400


def test_duplicate_and_oversized_batches_are_refused(env, monkeypatch):
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    same = env.photos[0].id
    response = env.client.post(env.studio, json={"photo_ids": [same, same], "options": {"remove_background": True}})
    assert response.status_code == 400
    response = env.client.post(env.studio, json={"photo_ids": [same] * 21, "options": {"remove_background": True}})
    assert response.status_code == 422


def test_sandbox_runs_ai_studio_end_to_end_offline(env, monkeypatch):
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    response = env.client.post(env.studio, json={"photo_ids": [env.photos[0].id],
                                                 "options": {"virtual_model": {"model": "auto", "scene": "beach", "pose": "auto"}}})
    assert response.status_code == 200, response.text
    assert response.json()["photos"][0]["edits"]["studio"]["options"]["virtual_model"]["scene"] == "beach"


def test_photoroom_key_settings_never_return_the_key(env):
    assert env.client.get("/api/settings/photoroom").json() == {
        "configured": False, "masked_key": None, "source": None, "available": False}
    saved = env.client.put("/api/settings/photoroom", json={"api_key": f"  {KEY}  "})
    assert saved.status_code == 200
    assert KEY not in saved.text
    assert saved.json()["source"] == "seller" and saved.json()["available"] is True
    assert env.client.put("/api/settings/photoroom", json={"api_key": "two words"}).status_code == 400
    refused = env.client.put("/api/settings/photoroom", json={"api_key": "x" * 501})
    assert refused.status_code == 400 and "x" * 501 not in refused.text
    removed = env.client.delete("/api/settings/photoroom")
    assert removed.json()["configured"] is False


def test_managed_photoroom_settings_lock_down_seller_keys(env, monkeypatch):
    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "managed-secret")
    status = env.client.get("/api/settings/photoroom")
    assert status.status_code == 200
    assert status.json() == {"configured": False, "masked_key": None, "source": "server", "available": True}
    assert "managed-secret" not in status.text
    assert env.client.put("/api/settings/photoroom", json={"api_key": KEY}).status_code == 403
    assert env.client.delete("/api/settings/photoroom").status_code == 403


def test_a_key_removal_the_keychain_cannot_save_is_reported(env, monkeypatch):
    from anteroom.services import keychain

    env.client.put("/api/settings/photoroom", json={"api_key": KEY})
    monkeypatch.setattr(keychain, "_save_store", lambda: False)
    assert env.client.delete("/api/settings/photoroom").status_code == 503
    assert photoroom.KEY_ACCOUNT in keychain._deleted


# --- Free manual tools --------------------------------------------------------------------


def test_flip_mirrors_the_photo(env):
    env.client.put(env.base + "/edits", json={"flip_horizontal": True})
    img = _stored(env, env.photos[0])
    assert img.getpixel((395, 150))[0] > 200 and img.getpixel((5, 150))[0] < 200
    env.client.put(env.base + "/edits", json={"flip_vertical": True})
    img = _stored(env, env.photos[0])
    assert img.getpixel((5, 150))[0] > 200


@pytest.mark.parametrize("name", ["vivid", "crisp", "warm", "cool", "bw", "studio"])
def test_each_filter_changes_the_photo(env, name):
    before = _stored(env, env.photos[0])
    response = env.client.put(env.base + "/edits", json={"filter": name})
    assert response.status_code == 200, response.text
    after = _stored(env, env.photos[0])
    assert after.size == before.size
    assert after.tobytes() != before.tobytes()
    if name == "bw":
        red, green, blue = after.getpixel((200, 150))
        assert abs(red - green) <= 2 and abs(green - blue) <= 2


def test_border_frames_the_finished_photo(env):
    response = env.client.put(env.base + "/edits", json={"border": {"width": 0.1, "color": "#000000"}})
    assert (response.json()["width"], response.json()["height"]) == (460, 360)
    img = _stored(env, env.photos[0])
    assert max(img.getpixel((2, 2))) < 20
    with pytest.raises(ValidationError):
        PhotoEdits(border={"width": 0.5})


def test_redaction_hides_a_region_only(env):
    before = _stored(env, env.photos[0])
    response = env.client.put(env.base + "/edits", json={"redactions": [
        {"x": 0.2, "y": 0.2, "width": 0.2, "height": 0.2, "style": "blur"},
        {"x": 0.6, "y": 0.6, "width": 0.3, "height": 0.3}]})
    assert response.status_code == 200, response.text
    assert response.json()["edits"]["redactions"][1]["style"] == "pixelate"
    after = _stored(env, env.photos[0])
    # The box edge inside the blurred region is softened; the far corner is untouched.
    assert after.getpixel((100, 100)) != before.getpixel((100, 100)) or after.getpixel((99, 76)) != before.getpixel((99, 76))
    assert after.getpixel((380, 20)) == pytest.approx(before.getpixel((380, 20)), abs=3)


def test_thin_redaction_at_far_edge_renders(env):
    response = env.client.put(env.base + "/edits", json={"redactions": [
        {"x": 0.9999, "y": 0.0, "width": 0.0001, "height": 1.0, "style": "pixelate"},
    ]})
    assert response.status_code == 200, response.text


def test_annotations_draw_arrows_shapes_and_labels(env):
    response = env.client.put(env.base + "/edits", json={"annotations": [
        {"kind": "arrow", "x1": 0.1, "y1": 0.9, "x2": 0.4, "y2": 0.6, "color": "#00FF00"},
        {"kind": "rectangle", "x1": 0.6, "y1": 0.1, "x2": 0.9, "y2": 0.3, "color": "#00FF00"},
        {"kind": "ellipse", "x1": 0.6, "y1": 0.5, "x2": 0.9, "y2": 0.9},
        {"kind": "text", "x1": 0.1, "y1": 0.05, "x2": 0.1, "y2": 0.05, "text": "Small stain", "color": "#FFFF00"}]})
    assert response.status_code == 200, response.text
    img = _stored(env, env.photos[0])
    pixels = img.load()
    greens = [1 for x in range(img.width) for y in range(img.height)
              if pixels[x, y][1] > 200 and pixels[x, y][0] < 80 and pixels[x, y][2] < 80]
    assert len(greens) > 100
    with pytest.raises(ValidationError, match="needs some text"):
        Annotation(kind="text", x1=0, y1=0, x2=0, y2=0, text="  ")
    with pytest.raises(ValidationError, match="Only text labels"):
        Annotation(kind="arrow", x1=0, y1=0, x2=1, y2=1, text="no")
    with pytest.raises(ValidationError):
        PhotoEdits(annotations=[{"kind": "arrow", "x1": 0, "y1": 0, "x2": 1, "y2": 1}] * 31)


def test_turning_carries_marks_with_the_frame():
    edits = PhotoEdits(redactions=[Redaction(x=0, y=0, width=0.5, height=0.25)],
                       annotations=[Annotation(kind="arrow", x1=0, y1=0, x2=1, y2=0.5)])
    turned = edits.turned(90)
    box = turned.redactions[0]
    assert (box.x, box.y, box.width, box.height) == (0.75, 0, 0.25, 0.5)
    arrow = turned.annotations[0]
    assert (arrow.x1, arrow.y1, arrow.x2, arrow.y2) == (1, 0, 0.5, 1)
    assert edits.turned(90).turned(270).redactions == edits.redactions


def test_turning_a_mirrored_photo_turns_the_picture_the_way_it_looks(env):
    """Rotation renders before the flip, so a quarter turn swaps the flip's axis."""
    env.client.put(env.base + "/edits", json={"flip_horizontal": True})
    shown = _stored(env, env.photos[0])
    turned = env.client.post(env.base + "/rotate", json={"degrees": 90}).json()
    assert turned["edits"]["flip_horizontal"] is False and turned["edits"]["flip_vertical"] is True
    import numpy as np

    after = np.asarray(_stored(env, env.photos[0]), dtype=np.int16)
    expected = np.asarray(shown.rotate(-90, expand=True), dtype=np.int16)
    # Only JPEG rounding differs; the other direction would move the red edge to the far side.
    assert after.shape == expected.shape and np.abs(after - expected).mean() < 2
    assert PhotoEdits(flip_horizontal=True).turned(180).flip_horizontal is True


def test_every_new_tool_resets_to_the_original(env):
    response = env.client.put(env.base + "/edits", json={
        "flip_horizontal": True, "filter": "warm", "border": {"width": 0.05},
        "redactions": [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}],
        "annotations": [{"kind": "rectangle", "x1": 0.1, "y1": 0.1, "x2": 0.3, "y2": 0.3}]})
    assert response.status_code == 200
    original = (env.dir / "front.jpg").read_bytes()
    reset = env.client.delete(env.base + "/edits")
    assert reset.json()["edits"] is None
    env.db.refresh(env.photos[0])
    assert env.photos[0].stored_filename == "front.jpg"
    assert (env.dir / "front.jpg").read_bytes() == original


def test_studio_and_vision_background_are_not_combined():
    with pytest.raises(ValidationError, match="AI Studio already set"):
        PhotoEdits(background="#FFFFFF", studio={"key": "x.jpg", "options": {"remove_background": True}})
    assert json.loads(PhotoEdits().model_dump_json())["annotations"] == []


def test_studio_commit_with_lost_acknowledgement_keeps_both_files(env, monkeypatch):
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    replace = ConversationRepo.replace_photo_file

    def lost_ack(*args, **kwargs):
        replace(*args, **kwargs)
        raise RuntimeError("lost commit acknowledgement")

    monkeypatch.setattr(ConversationRepo, "replace_photo_file", lost_ack)
    photo = env.photos[0]
    response = env.client.post(env.studio, json={"photo_ids": [photo.id], "options": {"flat_lay": True}})
    assert response.json()["errors"]
    env.db.refresh(photo)
    assert (env.dir / photo.stored_filename).is_file()
    assert (env.dir / photo.edits["studio"]["key"]).is_file()
