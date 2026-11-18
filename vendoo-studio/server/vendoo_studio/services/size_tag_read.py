"""Read the clothing size printed on an item's size tag, for the bulk upload.

Two independent reads of the photos, as the list-this skill asks of a tag size
(extractor and verifier must agree). Only a size both reads copy the same way
comes back as accepted. A missing tag, a tag neither read can make out, or two
different sizes come back without a size, for the seller to check by hand:
nothing is guessed. This is the garment's size, not its shipping package size.
Nothing here writes to a listing; the seller confirms what the bulk upload shows.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from vendoo_studio.services.measure_read import ask_vision_json, image_part, reader_name

log = logging.getLogger(__name__)

PHOTO_SIDE = 1280  # tag close-ups stay legible; whole-item shots cost less

SYSTEM = (
    "You read the clothing size printed on a garment's size tag in a seller's photos.\n"
    "- Only a size tag or size label counts. A brand tab, product code, RN number, care "
    "instructions or a hang tag price is not a size tag.\n"
    "- Copy the US size exactly as printed (M, XL, 0, 10, PS, 32W 30L, 2T). Leave out other "
    "countries' sizes and translations printed beside it (XL / TG is XL; M (EU 38) is M).\n"
    "- A size of 0 or 00 is a real size; copy it.\n"
    "- Never estimate a size from how the garment looks or from measurements.\n"
    "- If the tag is cut, blurred, covered or too small to read, say it cannot be read.\n"
    "- If the photos show more than one size tag with different sizes, list every size you see.\n"
    'Return only JSON: {"tag": "found" | "none" | "unreadable", "sizes": ["<size as printed>"]}.'
)

_SPACES = re.compile(r"\s+")


def _size_text(value) -> str:
    """A size as text; 0 is a size, so only None and blanks are empty.

    A list or object is not a size (a model nesting its answer); it reads as nothing,
    so the item goes to the seller instead of a size like "['S', 'XL']".
    """
    if value is None or isinstance(value, (bool, list, tuple, dict)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _SPACES.sub(" ", str(value)).strip()


def _same(size: str) -> str:
    """Spelling-insensitive key: 32 x 30, 32×30 and 32 X 30 are one size."""
    return re.sub(r"\s*[x×]\s*", "×", size.casefold())


def _one_read(answer: dict) -> tuple[str, list[str]]:
    """(tag status, distinct sizes) from one model answer."""
    tag = str(answer.get("tag") or "").strip().casefold()
    raw = answer.get("sizes")
    values = raw if isinstance(raw, list) else [raw]
    sizes: list[str] = []
    for value in values:
        size = _size_text(value)
        if size and size.casefold() not in {"unreadable", "unknown", "cannot read", "none"} \
                and all(_same(size) != _same(seen) for seen in sizes):
            sizes.append(size)
    if tag not in {"found", "none", "unreadable"}:
        tag = "found" if sizes else "unreadable"
    if tag == "found" and not sizes:
        tag = "unreadable"
    return tag, sizes if tag == "found" else []


def settle(first: dict, second: dict) -> dict:
    """Accept a size only when both reads found one tag and copied it the same way."""
    (tag_a, sizes_a), (tag_b, sizes_b) = _one_read(first), _one_read(second)
    seen: list[str] = []
    for size in sizes_a + sizes_b:
        if all(_same(size) != _same(other) for other in seen):
            seen.append(size)
    if tag_a == tag_b == "none":
        return {"status": "not_found", "size": None, "sizes": []}
    if tag_a == tag_b == "found" and len(sizes_a) == len(sizes_b) == 1 and len(seen) == 1:
        return {"status": "accepted", "size": sizes_a[0], "sizes": seen}
    if len(seen) > 1:
        return {"status": "conflict", "size": None, "sizes": seen}
    return {"status": "unreadable", "size": None, "sizes": seen}


async def _read_once(provider, urls: list[str]) -> dict:
    parts: list[dict] = [{"type": "text", "text": "Read the size tag in these photos of one item."}]
    for number, url in enumerate(urls, start=1):
        parts += [{"type": "text", "text": f"Photo {number}."}, image_part(url)]
    return await ask_vision_json(provider, SYSTEM, parts)


async def read_size_tag(provider, paths: list[Path]) -> dict:
    """The item's tag size, or why there is none, from its photos."""
    from vendoo_studio.providers.xiaomi_mimo import encode_images

    if not paths:
        return {"photos_read": 0, "status": "not_found", "size": None, "sizes": []}
    urls = await encode_images([str(path) for path in paths], PHOTO_SIDE)
    first, second = await asyncio.gather(_read_once(provider, urls), _read_once(provider, urls))
    result = settle(first, second)
    log.info("size tag read: %s from %d photos (%s)", result["status"], len(paths), reader_name(provider))
    return {"photos_read": len(paths), **result}
