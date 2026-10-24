"""Draw the Anteroom icon: rooms receding toward a lit doorway.

One drawing, three targets. macOS masks and lights the app icon itself, so the
art is flat colour on Apple's 824-px tile with 100-px margins; anything larger
lands in the grey "squircle jail" on macOS 26. Chrome wants its 128-px icon on
96 px with 16 px of padding and a faint light edge for dark toolbars. Below
64 px the bands blur together, so 32 and 16 px are drawn separately with fewer
arches on whole pixels instead of being scaled down.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

DESKTOP = Path(__file__).resolve().parent
REPO_ROOT = DESKTOP.parent.parent
EXTENSION_ICONS = REPO_ROOT / "vendoo-extension" / "icons"
EXTENSION_ICON_SIZES = (16, 32, 48, 128)
STUDIO_FAVICON = DESKTOP.parent / "public" / "favicon.png"
APP_ICON_PNG = DESKTOP / "AppIcon.png"

SUPERSAMPLE = 8
CANVAS = 1024
TILE_BOX = (100, 100, 924, 924)
TILE_RADIUS = 185.4
TILE = (9, 9, 9, 255)
# One OKLCH hue (~265) with even lightness steps, so no band sinks into the
# tile or its neighbour; #346bf1 is the app's accent.
ARCH_COLOURS = ((21, 41, 105, 255), (36, 74, 180, 255), (52, 107, 241, 255), (144, 179, 254, 255))
DOOR = (255, 255, 255, 255)
# Arches share one floor and one centre for their tops (1024 canvas units).
FLOOR = 768
ARCH_CENTRE_Y = 488
ARCH_SPANS = ((192, 832), (256, 768), (320, 704), (384, 640))
DOOR_SPAN = (448, 576)

# Below 64 px: (tile offset, tile size, corner radius, floor, arch spans, door
# span, door height, colours), in whole pixels of that size.
SMALL_MASTERS = {
    32: (3, 26, 6, 25, ((5, 27), (8, 24), (11, 21)), (14, 18), 11, ARCH_COLOURS[1:]),
    16: (1, 14, 3, 13, ((3, 13), (5, 11)), (7, 9), 5, ARCH_COLOURS[2:]),
}

# Apple's continuous corner: the curve starts 1.528 radii from the corner
# instead of 1, easing into the straight edge. Each corner is a superellipse
# quadrant over that span, with the exponent set so the curve's midpoint sits
# where a circular corner of the nominal radius would.
_CORNER_SPAN = 1.52866
_CORNER_EXPONENT = 3.26
_CORNER_STEPS = 48


def continuous_rect(box: tuple[float, float, float, float], radius: float) -> list[tuple[float, float]]:
    left, top, right, bottom = box
    span = min(radius * _CORNER_SPAN, (right - left) / 2, (bottom - top) / 2)
    quadrant = []
    for i in range(_CORNER_STEPS + 1):
        t = math.pi / 2 * i / _CORNER_STEPS
        quadrant.append((math.cos(t) ** (2 / _CORNER_EXPONENT), math.sin(t) ** (2 / _CORNER_EXPONENT)))
    points: list[tuple[float, float]] = []
    # Clockwise from the top edge: each corner's centre and the signs that
    # point its quadrant outward.
    for cx, cy, sx, sy, order in (
        (right - span, top + span, 1, -1, reversed(quadrant)),
        (right - span, bottom - span, 1, 1, iter(quadrant)),
        (left + span, bottom - span, -1, 1, reversed(quadrant)),
        (left + span, top + span, -1, -1, iter(quadrant)),
    ):
        points.extend((cx + sx * span * u, cy + sy * span * v) for u, v in order)
    return points


def _arch(draw: ImageDraw.ImageDraw, left: float, right: float, centre_y: float, floor: float, fill) -> None:
    # PIL fills right and bottom edges inclusively; stop one sample short.
    radius = (right - left) / 2
    draw.pieslice([left, centre_y - radius, right - 1, centre_y + radius], 180, 360, fill=fill)
    draw.rectangle([left, centre_y, right - 1, floor - 1], fill=fill)


def _tile(box: tuple[float, float, float, float], radius: float, k: int) -> list[tuple[float, float]]:
    """The tile outline in samples, its right and bottom edges exclusive."""
    left, top, right, bottom = (v * k for v in box)
    return continuous_rect((left, top, right - 1, bottom - 1), radius * k)


def _supersampled(size: int, paint) -> Image.Image:
    image = Image.new("RGBA", (size * SUPERSAMPLE, size * SUPERSAMPLE), (0, 0, 0, 0))
    paint(ImageDraw.Draw(image), SUPERSAMPLE)
    # Box filtering averages coverage exactly; Lanczos rings past the tile edge.
    return image.resize((size, size), Image.Resampling.BOX)


def _render_master(size: int, tile_box: tuple[float, float, float, float]) -> Image.Image:
    """The four-arch drawing with its tile at ``tile_box`` (pixels of ``size``)."""
    tile_size = tile_box[2] - tile_box[0]
    unit = tile_size / (TILE_BOX[2] - TILE_BOX[0])

    def place(x: float, y: float, k: float) -> tuple[float, float]:
        return ((tile_box[0] + (x - TILE_BOX[0]) * unit) * k, (tile_box[1] + (y - TILE_BOX[1]) * unit) * k)

    def paint(draw: ImageDraw.ImageDraw, k: int) -> None:
        draw.polygon(_tile(tile_box, TILE_RADIUS * unit, k), fill=TILE)
        _, centre_y = place(0, ARCH_CENTRE_Y, k)
        _, floor = place(0, FLOOR, k)
        for (left, right), colour in zip((*ARCH_SPANS, DOOR_SPAN), (*ARCH_COLOURS, DOOR), strict=True):
            _arch(draw, place(left, 0, k)[0], place(right, 0, k)[0], centre_y, floor, colour)

    return _supersampled(size, paint)


def _render_small(size: int) -> Image.Image:
    offset, tile, radius, floor, spans, door, door_height, colours = SMALL_MASTERS[size]
    centre_y = floor - door_height + (door[1] - door[0]) / 2

    def paint(draw: ImageDraw.ImageDraw, k: int) -> None:
        box = (offset, offset, offset + tile, offset + tile)
        draw.polygon(_tile(box, radius, k), fill=TILE)
        for (left, right), colour in zip((*spans, door), (*colours, DOOR), strict=True):
            _arch(draw, left * k, right * k, centre_y * k, floor * k, colour)

    return _supersampled(size, paint)


def render_icon(size: int) -> Image.Image:
    """The macOS icon at ``size`` pixels: Apple's tile geometry, flat colour."""
    if size in SMALL_MASTERS:
        return _render_small(size)
    scale = size / CANVAS
    box = tuple(v * scale for v in TILE_BOX)
    if size == 64:
        # 64-unit bands land on whole pixels here; put the tile on them too.
        box = (6, 6, 58, 58)
    return _render_master(size, box)


def render_extension_icon(size: int) -> Image.Image:
    """Chrome's sizes: the store icon is 96 px of art in 16 px of padding."""
    if size in SMALL_MASTERS:
        return _render_small(size)
    padding = {48: 6, 128: 16}[size]
    image = _render_master(size, (padding, padding, size - padding, size - padding))
    if size == 128:
        # A faint light edge keeps the dark tile visible on dark toolbars.
        halo = image.getchannel("A").filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.5))
        glow = Image.new("RGBA", image.size, (255, 255, 255, 0))
        glow.putalpha(halo.point(lambda a: round(a * 0.22)))
        image = Image.alpha_composite(glow, image)
    return image


def write_icns(destination: Path) -> Path:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        raise FileNotFoundError("iconutil is required to build a macOS .icns file")
    with tempfile.TemporaryDirectory() as raw:
        iconset = Path(raw) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            render_icon(size).save(iconset / f"icon_{size}x{size}.png")
            render_icon(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run([iconutil, "-c", "icns", "-o", str(destination), str(iconset)], check=True)
    return destination


def write_app_icon_png(destination: Path = APP_ICON_PNG) -> Path:
    render_icon(CANVAS).save(destination)
    return destination


def write_favicon(destination: Path = STUDIO_FAVICON) -> Path:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    render_icon(32).save(destination)
    return destination


def write_extension_icons(directory: Path = EXTENSION_ICONS) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for size in EXTENSION_ICON_SIZES:
        path = directory / f"icon{size}.png"
        render_extension_icon(size).save(path)
        written.append(path)
    return written


if __name__ == "__main__":
    written: list[Path] = []
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else DESKTOP / "AppIcon.icns"
    if shutil.which("iconutil"):
        written.append(write_icns(destination))
    elif destination.suffix.lower() == ".icns":
        print(f"skip icns (iconutil not found): {destination}")
    written.append(write_app_icon_png())
    written.append(write_favicon())
    written.extend(write_extension_icons())
    for path in written:
        print(path)
