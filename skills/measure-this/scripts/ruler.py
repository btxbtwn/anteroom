#!/usr/bin/env python3
"""Cut a measurement photo into crops a model can read a ruler from, and settle two reads.

A whole-garment photo leaves each 1/8 inch tick a few pixels wide, which is why a ruler
read from the full frame is unreliable. `crop` finds each wooden yardstick in the photo,
straightens it so the numerals sit upright, and writes overlapping enlarged tiles along
it with a strip of garment on either side. The reader opens only the tile where the
garment edge meets the scale.

`settle` takes two independent reads of each measurement and accepts a value only when
they agree within a quarter inch. Anything else goes back to the seller.

    python3 ruler.py crop photo.jpg --out crops/
    python3 ruler.py crop photo.jpg --out crops/ --line 120,900,2300,310   # stick not found
    python3 ruler.py crop photo.jpg --out crops/ --flip A                  # numerals upside down
    python3 ruler.py settle reads.json

Needs Pillow and numpy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

AGREE_WITHIN = 0.25  # inches two reads may differ by and still be accepted
DETECT_SIDE = 400  # the photo is searched for sticks at this size
MIN_STICK_SHARE = 0.3  # a stick must run at least this share of the photo's side
TILE_INCHES_ACROSS = 4.5  # tile width, in stick widths (a yardstick is about 1.1 in wide)
TILE_OUT_WIDTH = 1560
BACKDROP = (238, 238, 238)

# Garment type -> the measurements Studio's listing format carries, in its order.
# The line itself is specified in skills/list-this/SKILL.md (the `measurements` parameter).
MEASURES = {
    "top": [("pit_to_pit", "Pit to pit"), ("length", "Length"), ("sleeve", "Sleeve")],
    "pants": [("waist", "Waist"), ("rise", "Rise"), ("inseam", "Inseam"), ("leg_opening", "Leg opening")],
    "skirt": [("waist", "Waist"), ("hip", "Hip"), ("length", "Length")],
}


@dataclass
class Stick:
    """One yardstick: its centre line in photo pixels, and its width."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


def load(path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def wood_mask(image: Image.Image) -> np.ndarray:
    """Pixels the colour of unfinished wood, from tan in shade to near white in full sun.

    Sunlit wood keeps only a trace of warmth, so the saturation floor is low. That lets
    cream and white garments through as well; `find_sticks` tells them apart by shape.
    """
    hue, sat, val = (np.asarray(band, dtype=np.float32) for band in image.convert("HSV").split())
    return (hue > 8) & (hue < 42) & (sat > 7) & (sat < 150) & (val > 150)


def box(profile: np.ndarray, width: int) -> np.ndarray:
    return np.convolve(profile, np.ones(width) / width, mode="same")


def find_sticks(image: Image.Image, limit: int = 2) -> list[Stick]:
    """Find up to `limit` sticks as long, narrow, straight bands of wood colour.

    For each angle the mask is rotated and summed along rows. A stick lying along that
    angle shows as a tall narrow peak: many wood pixels on its own rows and few on the
    rows either side. A cream or tan garment is wood coloured too, but it is a blob, so
    its rows and their neighbours are equally full and it scores nothing.
    """
    scale = DETECT_SIDE / max(image.size)
    small = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
    mask = wood_mask(small)
    side = int(math.hypot(*small.size)) + 2
    canvas = Image.new("L", (side, side), 0)
    offset = ((side - small.width) // 2, (side - small.height) // 2)
    canvas.paste(Image.fromarray((mask * 255).astype(np.uint8)), offset)

    sticks: list[Stick] = []
    for _ in range(limit):
        best = None
        for angle in np.arange(0, 180, 1.0):
            rows = np.asarray(canvas.rotate(angle, resample=Image.BILINEAR), dtype=np.float32) / 255
            profile = rows.sum(axis=1)
            for width in (10, 14, 18, 24, 32, 44):
                core = box(profile, max(width // 2, 3))
                flank = np.maximum(np.roll(core, width), np.roll(core, -width))
                score = core - flank
                row = int(score.argmax())
                if best is None or score[row] > best[0]:
                    best = (float(score[row]), angle, row, width, rows)
        score, angle, row, width, rows = best
        if score < MIN_STICK_SHARE * DETECT_SIDE:
            break
        # Refine the width and the ends from the rotated mask around the winning row.
        profile = rows.sum(axis=1)
        level = profile[row] * 0.5
        top = row
        while top > 0 and profile[top - 1] > level:
            top -= 1
        bottom = row
        while bottom < side - 1 and profile[bottom + 1] > level:
            bottom += 1
        columns = np.nonzero(rows[top : bottom + 1].mean(axis=0) > 0.5)[0]
        if len(columns) < 2:
            break
        centre = (top + bottom) / 2
        ends = []
        for column in (columns[0], columns[-1]):
            # Undo the rotation (PIL rotates counter-clockwise about the canvas centre).
            dx, dy = column - side / 2, centre - side / 2
            rad = math.radians(angle)
            x = dx * math.cos(rad) - dy * math.sin(rad) + side / 2 - offset[0]
            y = dx * math.sin(rad) + dy * math.cos(rad) + side / 2 - offset[1]
            ends.append((x / scale, y / scale))
        sticks.append(refine(image, to_edges(image, Stick(*ends[0], *ends[1], width=(bottom - top + 1) / scale))))
        # Erase this stick so the next pass finds the other one.
        draw = ImageDraw.Draw(canvas)
        (ax, ay), (bx, by) = [(x * scale + offset[0], y * scale + offset[1]) for x, y in ends]
        ux, uy = (bx - ax) / max(math.hypot(bx - ax, by - ay), 1), (by - ay) / max(math.hypot(bx - ax, by - ay), 1)
        draw.line(
            (ax - ux * side, ay - uy * side, bx + ux * side, by + uy * side),
            fill=0,
            width=int((bottom - top + 1) * 1.6) + 2,
        )
    # Both are the same kind of stick, so a width far off the other's is a misread: a pale
    # garment taken for wood reads too wide, a bleached stick too narrow. A yardstick
    # spans 3-12% of a photo's side from a whole-garment shot to a close-up.
    if len(sticks) == 2 and max(s.width for s in sticks) > 1.6 * min(s.width for s in sticks):
        side = max(image.size)
        plausible = [s.width for s in sticks if 0.03 * side <= s.width <= 0.12 * side]
        if len(plausible) == 1:
            for stick in sticks:
                stick.width = plausible[0]
    # Name them in a stable order: the stick lying flatter across the photo is A.
    sticks.sort(key=lambda s: abs(s.y2 - s.y1) / max(abs(s.x2 - s.x1), 1e-6))
    return sticks


def to_edges(image: Image.Image, stick: Stick) -> Stick:
    """Run the stick's line out to the photo's edges.

    Where the other stick crosses it, or the sun bleaches it, the wood colour stops and
    the found stick ends early, short of the reading. Crops of bare garment cost nothing.
    """
    ux, uy = (stick.x2 - stick.x1) / stick.length, (stick.y2 - stick.y1) / stick.length
    reach = []
    for sign in (-1, 1):
        x, y = (stick.x1, stick.y1) if sign < 0 else (stick.x2, stick.y2)
        steps = [
            (limit - at) / (sign * part)
            for at, part, limits in ((x, ux, (0, image.width - 1)), (y, uy, (0, image.height - 1)))
            for limit in limits
            if sign * part * (limit - at) > 0
        ]
        distance = max(min(steps, default=0), 0)
        reach.append((x + sign * ux * distance, y + sign * uy * distance))
    return Stick(*reach[0], *reach[1], width=stick.width)


def refine(image: Image.Image, stick: Stick) -> Stick:
    """Tighten a coarse stick to the full-size photo: its true centre line and width.

    The search works in whole degrees on a small copy, which over a yardstick's length
    drifts by more than a tick. Here the stick is cut into short sections, each section's
    wood band is measured across, and a line is fitted through the section centres.
    """
    ux, uy = (stick.x2 - stick.x1) / stick.length, (stick.y2 - stick.y1) / stick.length
    nx, ny = -uy, ux
    half = max(image.size) * 0.09
    quad = []
    for along, across in ((0, -half), (0, half), (stick.length, half), (stick.length, -half)):
        quad += [stick.x1 + ux * along + nx * across, stick.y1 + uy * along + ny * across]
    band = image.transform((round(stick.length), round(2 * half)), Image.QUAD, quad, resample=Image.BILINEAR)
    mask = wood_mask(band).astype(np.float32)
    centres, widths = [], []
    for section in np.array_split(np.arange(mask.shape[1]), 40):
        profile = box(mask[:, section].mean(axis=1), 9)
        peak = int(profile.argmax())
        if profile[peak] < 0.5:
            continue
        top = bottom = peak
        while top > 0 and profile[top - 1] > profile[peak] * 0.5:
            top -= 1
        while bottom < len(profile) - 1 and profile[bottom + 1] > profile[peak] * 0.5:
            bottom += 1
        centres.append((section.mean(), (top + bottom) / 2 - half))
        widths.append(bottom - top + 1)
    if len(centres) < 4:
        return stick
    # Sun bleaches stretches of the stick out of the mask, so most sections read narrow.
    # The widest sections are the ones seen whole; fit the line through those.
    width = float(np.percentile(widths, 85))
    kept = [(x, y) for (x, y), w in zip(centres, widths, strict=True) if abs(w - width) < width * 0.2]
    if len(kept) < 3:
        return stick
    xs, ys = np.array(kept).T
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs))]
    slope = float(np.median(slopes))
    shift = float(np.median(ys - slope * xs))

    def point(along: float) -> tuple[float, float]:
        across = shift + slope * along
        return stick.x1 + ux * along + nx * across, stick.y1 + uy * along + ny * across

    return Stick(*point(0), *point(stick.length), width=width)


def straighten(image: Image.Image, stick: Stick, flip: bool) -> tuple[Image.Image, float]:
    """The stick as a level strip with garment either side. Returns it and the stick width in it."""
    ux, uy = (stick.x2 - stick.x1) / stick.length, (stick.y2 - stick.y1) / stick.length
    if ux < 0:
        ux, uy = -ux, -uy
        stick = Stick(stick.x2, stick.y2, stick.x1, stick.y1, stick.width)
    nx, ny = -uy, ux
    half = stick.width * 1.6
    pad = stick.width * 0.5

    def corner(along: float, across: float) -> tuple[float, float]:
        return (stick.x1 + ux * along + nx * across, stick.y1 + uy * along + ny * across)

    quad = (*corner(-pad, -half), *corner(-pad, half), *corner(stick.length + pad, half), *corner(stick.length + pad, -half))
    size = (round(stick.length + 2 * pad), round(2 * half))
    strip = image.transform(size, Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=BACKDROP)
    if ticks_on_bottom(strip, stick.width) != flip:
        strip = strip.rotate(180)
    return strip, stick.width


def ticks_on_bottom(strip: Image.Image, width: float) -> bool:
    """Rulers print their ticks along one edge with the numerals upright beneath them.

    A row of ticks is the busiest thing on the stick: light and dark alternate all the
    way along it. So the tick edge is the half of the stick with the busiest row.
    """
    grey = np.asarray(strip.convert("L"), dtype=np.float32)
    busy = box(np.abs(np.diff(grey, axis=1)).mean(axis=1), max(int(width * 0.08), 3))
    mid = len(busy) // 2
    reach = int(width * 0.6)
    return busy[mid : mid + reach].max() > busy[mid - reach : mid].max()


def crop(photo: Path, out: Path, lines: list[str], flips: set[str]) -> dict:
    image = load(photo)
    if lines:
        sticks = []
        for line in lines:
            x1, y1, x2, y2, *rest = (float(part) for part in line.split(","))
            sticks.append(Stick(x1, y1, x2, y2, width=rest[0] if rest else max(image.size) * 0.05))
    else:
        sticks = find_sticks(image)
    out.mkdir(parents=True, exist_ok=True)

    overview = image.copy()
    overview.thumbnail((1200, 1200))
    ratio = overview.width / image.width
    draw = ImageDraw.Draw(overview)
    report = {"photo": str(photo), "size": list(image.size), "overview": str(out / "overview.jpg"), "sticks": []}

    for index, stick in enumerate(sticks):
        name = "AB"[index] if index < 2 else str(index)
        strip, width = straighten(image, stick, name in flips)
        tile_w = round(width * TILE_INCHES_ACROSS)
        step = tile_w // 2
        starts = list(range(0, max(strip.width - tile_w, 0) + 1, step)) or [0]
        if starts[-1] + tile_w < strip.width:
            starts.append(strip.width - tile_w)
        tiles = []
        for number, start in enumerate(starts, 1):
            tile = strip.crop((start, 0, min(start + tile_w, strip.width), strip.height))
            tile = tile.resize((TILE_OUT_WIDTH, round(tile.height * TILE_OUT_WIDTH / tile.width)), Image.LANCZOS)
            path = out / f"{name}-{number:02d}.jpg"
            tile.save(path, quality=92)
            tiles.append(str(path))

        # The whole strip, small, with tile numbers above it so the reader can pick one.
        shrink = min(2000 / strip.width, 1.0)
        small = strip.resize((round(strip.width * shrink), round(strip.height * shrink)), Image.LANCZOS)
        header = 26
        sheet = Image.new("RGB", (small.width, small.height + header), "white")
        sheet.paste(small, (0, header))
        pen = ImageDraw.Draw(sheet)
        for number, start in enumerate(starts, 1):
            x = (start + tile_w / 2) * shrink
            pen.line((x, header - 8, x, header), fill="black", width=2)
            pen.text((x - 6, 4), f"{number:02d}", fill="black")
        strip_path = out / f"{name}-strip.jpg"
        sheet.save(strip_path, quality=90)

        mx, my = (stick.x1 + stick.x2) / 2 * ratio, (stick.y1 + stick.y2) / 2 * ratio
        draw.ellipse((mx - 20, my - 20, mx + 20, my + 20), fill="black")
        draw.text((mx - 5, my - 7), name, fill="white")
        report["sticks"].append(
            {
                "stick": name,
                "line": [round(v) for v in (stick.x1, stick.y1, stick.x2, stick.y2)],
                "width": round(stick.width),
                "strip": str(strip_path),
                "tiles": tiles,
            }
        )
    overview.save(out / "overview.jpg", quality=88)
    return report


def parse_inches(value) -> float | None:
    """A read as inches, or None for a refusal ("cannot read", null, blank)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip().rstrip('"').replace(" in", ""))
    except ValueError:
        return None


def settle_one(first, second) -> dict:
    """Accept two reads that agree within a quarter inch; flag anything else."""
    a, b = parse_inches(first), parse_inches(second)
    if a is None or b is None:
        return {"status": "cannot_read", "reads": [first, second]}
    if abs(a - b) > AGREE_WITHIN + 1e-9:
        return {"status": "disagree", "reads": [a, b]}
    # Sellers list to the nearest half inch, so the agreed value is rounded the same way.
    return {"status": "accepted", "value": math.floor((a + b) / 2 * 2 + 0.5) / 2, "reads": [a, b]}


def format_inches(value: float) -> str:
    return f'{value:g}"'


def settle(reads: dict) -> dict:
    """`reads` is {"garment": "top", "reads": {"pit_to_pit": [22.5, 22.375], ...}}."""
    garment = reads["garment"]
    result = {"garment": garment, "measurements": {}, "needs_seller": []}
    parts = []
    for key, label in MEASURES[garment]:
        if key not in reads["reads"]:
            continue
        settled = settle_one(*reads["reads"][key])
        result["measurements"][key] = settled
        if settled["status"] == "accepted":
            parts.append(f"{label}: {format_inches(settled['value'])}")
        else:
            result["needs_seller"].append(label)
    result["line"] = f"- Measurements ({garment}): {'; '.join(parts)}" if parts else None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    cropper = commands.add_parser("crop", help="write readable crops of each ruler in a photo")
    cropper.add_argument("photo", type=Path)
    cropper.add_argument("--out", type=Path, required=True)
    cropper.add_argument("--line", action="append", default=[], metavar="X1,Y1,X2,Y2[,WIDTH]",
                         help="a stick's centre line in photo pixels, when it is not found")
    cropper.add_argument("--flip", action="append", default=[], metavar="STICK",
                         help="turn this stick's crops over when its numerals come out upside down")
    settler = commands.add_parser("settle", help="accept or flag two reads of each measurement")
    settler.add_argument("reads", type=Path)
    args = parser.parse_args()

    if args.command == "crop":
        print(json.dumps(crop(args.photo, args.out, args.line, set(args.flip)), indent=2))
    else:
        print(json.dumps(settle(json.loads(args.reads.read_text())), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
