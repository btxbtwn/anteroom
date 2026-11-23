"""AI Studio: Photoroom's Image Editing API, run on a listing photo the seller picks.

Background removal on any solid colour, AI shadows, ghost mannequin, flat lay and
virtual models come from Photoroom's ``POST /v2/edit``
(https://docs.photoroom.com/getting-started/api-reference-openapi). Anteroom
sends one photo per call and keeps the result beside the untouched upload, so
Reset always goes back to the original (``services/photo_edits.py``).

The key is the seller's own: the Mac's Keychain, or the seller's Vault slot when
hosted. A server-wide ``PHOTOROOM_API_KEY`` is used only when the seller has none.
The key never reaches a log, the database in plain text, or a response.

Sandbox mode never calls Photoroom: ``sandbox_result`` draws a stand-in locally,
so the editor runs end to end offline.
"""
from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO
from typing import Literal, cast

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)

ENDPOINT = "https://image-api.photoroom.com/v2/edit"
KEY_ACCOUNT = "photoroom-api-key"
SERVER_KEY_ENV = "PHOTOROOM_API_KEY"
# Generative edits take tens of seconds; nothing Photoroom documents takes longer.
CALL_DEADLINE_SEC = 120.0
CONNECT_TIMEOUT_SEC = 10.0
# Photoroom accepts up to 30 MB and 5,000 px on the widest side.
MAX_INPUT_SIDE = 4096
# Photos one request may send; each is its own Photoroom call.
MAX_BATCH = 20
# Calls in flight for one request. Photoroom's default limit is 60 images a minute.
CONCURRENCY = 4

MODEL_PRESETS = ("avery", "sam", "taylor", "kendall", "jordan", "casey", "maya", "reece", "lena", "julia",
                 "jackson", "sophia", "emma", "ava", "zoe", "fiona")
SCENE_PRESETS = ("street", "bedroom", "sunset", "factory", "studio", "coloredstudio", "concretestudio", "beach",
                 "tropical", "library", "forest", "businessdistrict", "countryside", "flowers", "goldenlight",
                 "mountain", "pool", "latincity", "cafe", "asiancity", "nightlights", "desert")
POSE_PRESETS = ("standing", "34turn", "powerstance", "walkingforward", "handinpocket", "crossedarms", "back",
                "overtheshoulder", "seated", "adjustingclothing", "playfulspin")

ModelPreset = Literal["auto", "avery", "sam", "taylor", "kendall", "jordan", "casey", "maya", "reece", "lena",
                      "julia", "jackson", "sophia", "emma", "ava", "zoe", "fiona"]
ScenePreset = Literal["auto", "street", "bedroom", "sunset", "factory", "studio", "coloredstudio", "concretestudio",
                      "beach", "tropical", "library", "forest", "businessdistrict", "countryside", "flowers",
                      "goldenlight", "mountain", "pool", "latincity", "cafe", "asiancity", "nightlights", "desert"]
PosePreset = Literal["auto", "standing", "34turn", "powerstance", "walkingforward", "handinpocket", "crossedarms",
                     "back", "overtheshoulder", "seated", "adjustingclothing", "playfulspin"]


class VirtualModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelPreset = "auto"
    scene: ScenePreset = "auto"
    pose: PosePreset = "auto"


class StudioOptions(BaseModel):
    """One AI Studio edit. Photoroom does the work; these are the seller's choices."""

    model_config = ConfigDict(extra="forbid")

    remove_background: bool = False
    background: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    """The solid colour behind the cut-out item."""
    ghost_mannequin: bool = False
    flat_lay: bool = False
    shadow: bool = False
    virtual_model: VirtualModel | None = None

    @model_validator(mode="after")
    def _valid_combination(self) -> StudioOptions:
        if self.ghost_mannequin and self.flat_lay:
            raise ValueError("Choose Ghost mannequin or Flat lay, not both.")
        if self.virtual_model is not None and (self.remove_background or self.ghost_mannequin
                                               or self.flat_lay or self.shadow):
            raise ValueError("A virtual model makes its own scene, so it cannot be combined with other AI Studio edits.")
        if self.ghost_mannequin or self.flat_lay or self.shadow:
            # These all cut the item out first.
            self.remove_background = True
        if not (self.remove_background or self.virtual_model is not None):
            raise ValueError("Choose an AI Studio edit.")
        self.background = self.background.upper()
        return self

    def form(self) -> dict[str, str]:
        """Photoroom's form fields for these options."""
        if self.virtual_model is not None:
            # Both keep the generated scene, as Photoroom's Virtual Model example sends them.
            fields = {"removeBackground": "false", "referenceBox": "originalImage", "virtualModel.mode": "ai.auto"}
            if self.virtual_model.model != "auto":
                fields["virtualModel.model.preset.name"] = self.virtual_model.model
            # Photoroom's own "random" is what Auto means here.
            fields["virtualModel.scene.preset.name"] = (
                "random" if self.virtual_model.scene == "auto" else self.virtual_model.scene)
            fields["virtualModel.pose"] = "random" if self.virtual_model.pose == "auto" else self.virtual_model.pose
            fields["export.format"] = "png"
            return fields
        fields = {"removeBackground": "true", "background.color": self.background[1:], "export.format": "png"}
        if self.ghost_mannequin:
            fields["ghostMannequin.mode"] = "ai.auto"
        elif self.flat_lay:
            fields["flatLay.mode"] = "ai.auto"
        else:
            fields["outputSize"] = "originalImage"
        if self.shadow:
            fields["shadow.mode"] = "ai.soft"
        return fields

    def label(self) -> str:
        """What the seller chose, in their words."""
        if self.virtual_model is not None:
            return "Virtual model"
        parts = ["Ghost mannequin" if self.ghost_mannequin else "Flat lay" if self.flat_lay else "Background removed"]
        if self.shadow:
            parts.append("shadow")
        return ", ".join(parts)


class PhotoroomError(RuntimeError):
    """A Photoroom call failed; ``str()`` is safe to show the seller and never holds the key."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def configured_key() -> tuple[str | None, Literal["seller", "server"] | None]:
    """The key to call Photoroom with, and where it came from."""
    from anteroom.services.keychain import read_slot

    key = (read_slot(KEY_ACCOUNT) or "").strip()
    if key:
        return key, "seller"
    server = os.environ.get(SERVER_KEY_ENV, "").strip()
    return (server, "server") if server else (None, None)


def _sandboxed() -> bool:
    from anteroom import sandbox
    return sandbox.enabled()


def available() -> bool:
    return _sandboxed() or configured_key()[0] is not None


def _prepare_upload(data: bytes) -> tuple[bytes, str, str]:
    """Encode the photo within Photoroom's size limits: JPEG for opaque photos, PNG when transparent."""
    from anteroom.services import image_decode

    img, _size = image_decode.decode(data, max_side=MAX_INPUT_SIDE)
    buffer = BytesIO()
    if "A" in img.getbands() or "transparency" in img.info:
        rgba = img.convert("RGBA")
        # Pillow returns a single 8-bit channel in L mode.
        min_alpha, _ = cast("tuple[int, int]", rgba.getchannel("A").getextrema())
        if min_alpha < 255:
            rgba.save(buffer, "PNG")
            return buffer.getvalue(), "photo.png", "image/png"
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(buffer, "JPEG", quality=92, optimize=True)
    return buffer.getvalue(), "photo.jpg", "image/jpeg"


def _error_message(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("detail") or "")
    except ValueError:
        pass
    detail = " ".join(detail.split())[:200]
    if response.status_code in (401, 403):
        return "Photoroom refused the API key. Check it in Settings → Providers."
    if response.status_code == 402:
        return "Your Photoroom plan has no credits left for this edit."
    if response.status_code == 429:
        return "Photoroom is busy (too many photos at once). Try again in a minute."
    if detail:
        return f"Photoroom could not edit this photo: {detail}"
    return f"Photoroom could not edit this photo (HTTP {response.status_code})."


def _client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """One client per call; tests replace this with a mock transport."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


async def edit(image: bytes, options: StudioOptions) -> bytes:
    """Photoroom's edit of ``image`` (any format Pillow reads) as PNG bytes.

    Raises ``PhotoroomError`` naming Photoroom when it refuses, fails or
    passes ``CALL_DEADLINE_SEC``.
    """
    if _sandboxed():
        return await asyncio.to_thread(sandbox_result, image, options)
    # Hosted, the key is a Vault read: a blocking database query, kept off the event loop.
    key, _source = await asyncio.to_thread(configured_key)
    if not key:
        raise PhotoroomError("Add a Photoroom API key in Settings → Providers to use AI Studio.", 400)
    payload, filename, mime = await asyncio.to_thread(_prepare_upload, image)
    timeout = httpx.Timeout(CALL_DEADLINE_SEC, connect=CONNECT_TIMEOUT_SEC)
    try:
        async with asyncio.timeout(CALL_DEADLINE_SEC):
            async with _client(timeout) as client:
                response = await client.post(
                    ENDPOINT,
                    headers={"x-api-key": key, "Accept": "image/png, application/json"},
                    data=options.form(),
                    files={"imageFile": (filename, payload, mime)},
                )
    except TimeoutError:
        raise PhotoroomError("Photoroom did not finish this photo in time. Try again.", 504) from None
    except httpx.TimeoutException:
        raise PhotoroomError("Photoroom did not finish this photo in time. Try again.", 504) from None
    except httpx.HTTPError as exc:
        # The exception names the URL and never the key, but log only its type.
        log.warning("Photoroom request failed: %s", type(exc).__name__)
        raise PhotoroomError("Could not reach Photoroom. Check your connection and try again.", 502) from None
    if not response.is_success:
        log.info("Photoroom refused an edit with HTTP %s", response.status_code)
        raise PhotoroomError(_error_message(response), 400 if response.status_code in (401, 402, 403) else 502)
    if not response.content.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
        raise PhotoroomError("Photoroom answered without an image. Try again.")
    return response.content


def sandbox_result(image: bytes, options: StudioOptions) -> bytes:
    """A deterministic stand-in for Photoroom's answer, drawn offline for the sandbox.

    The photo sits on the chosen colour with a margin, so the editor's flow and
    Reset can be checked end to end without a key or the network.
    """
    from anteroom.services import image_decode

    img, _size = image_decode.decode(image, max_side=1600)
    img = img.convert("RGB")
    fill = "#E7E5E4" if options.virtual_model is not None else options.background
    margin = max(8, min(img.size) // 12)
    canvas = Image.new("RGB", (img.width + 2 * margin, img.height + 2 * margin), fill)
    if options.shadow:
        canvas.paste(Image.new("RGB", img.size, "#BDBDBD"), (margin + margin // 3, margin + margin // 3))
    canvas.paste(img, (margin, margin))
    buffer = BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()
