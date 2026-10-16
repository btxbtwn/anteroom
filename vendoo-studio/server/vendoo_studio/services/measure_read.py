"""Read garment measurements from photos with yardsticks laid on the item.

The reading method is the measure-this skill's: its script finds each yardstick and
cuts enlarged tiles along it, the photo model reads the tiles twice without seeing the
other read, and only measurements whose two reads agree within a quarter inch come
back. The rest come back empty with the reason, for the seller to measure by hand.
Nothing here writes to a listing; the seller confirms what the bulk upload shows.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from PIL import Image, ImageOps

from vendoo_studio.config import skills_dir

log = logging.getLogger(__name__)

MAX_RULER_PHOTOS = 3  # pants take two; a third covers a sleeve shot
WOOD_SHARE = 0.04  # a photo with less yardstick colour than this has no yardstick worth finding
TILES_PER_MEASURE = 2  # the locate step picks at most this many
MAX_TILES_SHOWN = 4  # the read step also sees their neighbours, as a reader scanning along the stick would

# Skill measurement keys -> the bulk upload's field keys (garmentMeasurements.ts).
FIELD_KEYS = {"pit_to_pit": "pitToPit", "leg_opening": "legOpening"}


@lru_cache(maxsize=1)
def ruler() -> ModuleType:
    path = skills_dir() / "measure-this" / "scripts" / "ruler.py"
    spec = importlib.util.spec_from_file_location("measure_this_ruler", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise RuntimeError(f"The measure-this skill is missing at {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        sys.modules.pop(spec.name, None)
        raise RuntimeError(f"Reading measurements is not available in this build: {exc}") from exc
    return module


@lru_cache(maxsize=1)
def reading_rules() -> str:
    """The skill's photo protocol and reading steps, so Studio reads the way the skill says."""
    text = (skills_dir() / "measure-this" / "SKILL.md").read_text(encoding="utf-8")
    start = text.index("## Photo protocol")
    end = text.index("### 4.")
    rules = text[start:end]
    # Step 1 is the script, which Studio has already run.
    return re.sub(r"### 1\. Cut crops[\s\S]*?(?=### 2\.)", "", rules).strip()


def _has_wood(path: Path) -> bool:
    try:
        image = Image.open(path)
        image.draft("RGB", (160, 160))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((160, 160))
    except Exception:
        return False
    return float(ruler().wood_mask(image).mean()) > WOOD_SHARE


def find_ruler_photos(paths: list[Path], out: Path) -> list[dict]:
    """Crops for each photo with two yardsticks on it, in upload order."""
    module = ruler()
    found: list[dict] = []
    for path in paths:
        if len(found) == MAX_RULER_PHOTOS:
            break
        if not _has_wood(path):
            continue
        try:
            sticks = module.find_sticks(module.load(path))
        except Exception:
            log.exception("could not search %s for yardsticks", path.name)
            continue
        if len(sticks) < 2:
            continue
        number = len(found) + 1
        lines = [",".join(str(round(v)) for v in (s.x1, s.y1, s.x2, s.y2, s.width)) for s in sticks]
        report = module.crop(path, out / str(number), lines, set())
        report["number"] = number
        found.append(report)
    return found


def _image(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


def _json_object(text: str) -> dict | None:
    candidates = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text or "", re.I)
    if match := re.search(r"\{[\s\S]*\}", text or ""):
        candidates.append(match.group())
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _ask(provider, system: str, parts: list[dict]) -> dict:
    from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item

    messages = [{"role": "system", "content": system}, {"role": "user", "content": parts}]
    text = []
    async for item in provider.vision_chat(messages):
        kind, piece = unpack_stream_item(item)
        if kind == "content" and piece:
            text.append(piece)
    return _json_object("".join(text)) or {}


async def _read_once(provider, photos: list[dict], garment: str, measures: list[str]) -> dict:
    """One independent read: find where each measurement ends, then read those tiles."""
    from vendoo_studio.providers.xiaomi_mimo import encode_images

    rules = reading_rules()
    wanted = ", ".join(measures)
    locate_parts: list[dict] = [{"type": "text", "text": f"Garment: {garment}. Measurements to read: {wanted}."}]
    for photo in photos:
        overview = await encode_images([photo["overview"]], 1200)
        locate_parts += [{"type": "text", "text": f"Photo {photo['number']}: overview, each stick's letter on it."}, _image(overview[0])]
        for stick in photo["sticks"]:
            strip = await encode_images([stick["strip"]], 2000)
            locate_parts += [
                {"type": "text", "text": f"Photo {photo['number']}, stick {stick['stick']}: the straightened stick, tile numbers above it."},
                _image(strip[0]),
            ]
    located = await _ask(
        provider,
        "You read garment measurements from photos where the seller laid yardsticks on the item.\n\n"
        f"{rules}\n\n"
        "Decide which photo and stick carries each measurement and which tiles show the garment edge "
        f"where it ends (at most {TILES_PER_MEASURE}). Some photos are not measurement shots. "
        'Return only JSON: {"<measurement>": {"photo": 1, "stick": "A", "tiles": [3]} or "cannot read"}.',
        locate_parts,
    )

    read_parts: list[dict] = [{"type": "text", "text": f"Garment: {garment}."}]
    asked: list[str] = []
    by_number = {photo["number"]: photo for photo in photos}
    for measure in measures:
        spot = located.get(measure)
        if not isinstance(spot, dict):
            continue
        photo = by_number.get(spot.get("photo"))
        stick = next((s for s in (photo or {}).get("sticks", []) if s["stick"] == spot.get("stick")), None)
        if stick is None:
            continue
        picked = [n for n in spot.get("tiles") or [] if isinstance(n, int) and 1 <= n <= len(stick["tiles"])][:TILES_PER_MEASURE]
        if not picked:
            continue
        tiles = sorted({n + d for n in picked for d in (-1, 0, 1) if 1 <= n + d <= len(stick["tiles"])})
        if len(tiles) > MAX_TILES_SHOWN:
            centre = sum(picked) / len(picked)
            tiles = sorted(sorted(tiles, key=lambda n: abs(n - centre))[:MAX_TILES_SHOWN])
        urls = await encode_images([stick["strip"], *(stick["tiles"][n - 1] for n in tiles)], 1600)
        read_parts += [{"type": "text", "text": f"{measure}: photo {photo['number']}, stick {stick['stick']} (whole stick, then tiles {tiles})."}]
        read_parts += [_image(url) for url in urls]
        asked.append(measure)
    if not asked:
        return {}
    return await _ask(
        provider,
        "You read garment measurements from enlarged crops of a yardstick laid on the item.\n\n"
        f"{rules}\n\n"
        "Read each measurement where its ending garment edge meets the tick edge, to the nearest 1/8 inch. "
        'Return only JSON: {"<measurement>": <inches as a number> or "cannot read"}.',
        read_parts,
    )


async def read_measurements(provider, paths: list[Path], garment: str) -> dict:
    """Suggested measurements for one item, keyed like the bulk upload's fields."""
    module = ruler()
    measures = [key for key, _ in module.MEASURES[garment]]
    with tempfile.TemporaryDirectory(prefix="measure-") as workdir:
        photos = await asyncio.to_thread(find_ruler_photos, paths, Path(workdir))
        if not photos:
            return {"photos_read": 0, "measurements": {}}
        first, second = await asyncio.gather(
            _read_once(provider, photos, garment, measures),
            _read_once(provider, photos, garment, measures),
        )
        # When one read found a number and the other did not, a third independent read
        # settles it: a value still needs two independent reads within a quarter inch.
        split = [m for m in measures if (module.parse_inches(first.get(m)) is None) != (module.parse_inches(second.get(m)) is None)]
        third = await _read_once(provider, photos, garment, split) if split else {}
    measurements = {}
    for measure in measures:
        reads = [first.get(measure, "cannot read"), second.get(measure, "cannot read")]
        if measure in split:
            found = next(r for r in reads if module.parse_inches(r) is not None)
            reads = [found, third.get(measure, "cannot read")]
        measurements[FIELD_KEYS.get(measure, measure)] = module.settle_one(*reads)
    return {"photos_read": len(photos), "measurements": measurements}
