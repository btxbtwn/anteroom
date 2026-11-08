"""Draw the Anteroom icon: rooms receding toward a lit doorway.

One drawing, four targets. macOS masks and lights the app icon itself, so the
art is flat colour on Apple's 824-px tile with 100-px margins; anything larger
lands in the grey "squircle jail" on macOS 26. iOS wants a full-bleed opaque
square and rounds the corners itself. Chrome wants its 128-px icon on
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

from PIL import Image, ImageChops, ImageDraw, ImageFilter

DESKTOP = Path(__file__).resolve().parent
REPO_ROOT = DESKTOP.parent.parent
EXTENSION_ICONS = REPO_ROOT / "vendoo-extension" / "icons"
EXTENSION_ICON_SIZES = (16, 32, 48, 128)
STUDIO_FAVICON = DESKTOP.parent / "public" / "favicon.png"
APP_ICON_PNG = DESKTOP / "AppIcon.png"
IOS_APP_ICON = REPO_ROOT / "anteroom-ios" / "Sources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon.png"

SUPERSAMPLE = 8
CANVAS = 1024
TILE_BOX = (100, 100, 924, 924)
TILE_RADIUS = 185.4
TILE = (9, 9, 9, 255)
# Dusk: OKLCH lightness 0.34, 0.50, 0.66, 0.82 in even steps, so each room is
# brighter than the one around it, while the hue turns about 45 degrees per
# room from violet through magenta and coral to amber. The doorway is a warm
# white. src/components/AnteroomLogo.tsx draws the same colours.
ARCH_COLOURS = ((67, 16, 132, 255), (158, 48, 129, 255), (232, 96, 91, 255), (254, 178, 81, 255))
DOOR = (254, 250, 240, 255)
# Arches share one floor and one centre for their tops (1024 canvas units).
FLOOR = 768
ARCH_CENTRE_Y = 488
ARCH_SPANS = ((192, 832), (256, 768), (320, 704), (384, 640))
DOOR_SPAN = (448, 576)
# The arches fade out from here to the floor instead of ending on a hard line,
# fully opaque at FADE_TOP, 70% at 45% of the way, clear at the floor.
FADE_TOP = 560
_FADE_FROM = (FADE_TOP - ARCH_CENTRE_Y) / (FLOOR - ARCH_CENTRE_Y)

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


def _fade_opacity(t: float) -> float:
    if t <= 0:
        return 1.0
    if t >= 1:
        return 0.0
    return 1 - 0.3 * t / 0.45 if t < 0.45 else 0.7 * (1 - t) / 0.55


def _fade(layer: Image.Image, top: float, floor: float) -> Image.Image:
    """Multiply ``layer``'s alpha by the floor fade between rows ``top`` and ``floor``."""
    ramp = Image.new("L", (1, layer.height))
    ramp.putdata([round(255 * _fade_opacity((y + 0.5 - top) / (floor - top))) for y in range(layer.height)])
    alpha = ImageChops.multiply(layer.getchannel("A"), ramp.resize(layer.size))
    layer.putalpha(alpha)
    return layer


def _supersampled(size: int, tile, arches, centre_y: float, floor: float) -> Image.Image:
    """Draw the tile, then the arches faded toward ``floor`` (pixels of ``size``)."""
    k = SUPERSAMPLE
    image = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    tile(ImageDraw.Draw(image), k)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    arches(ImageDraw.Draw(layer), k)
    top = centre_y + (floor - centre_y) * _FADE_FROM
    image = Image.alpha_composite(image, _fade(layer, top * k, floor * k))
    # Box filtering averages coverage exactly; Lanczos rings past the tile edge.
    return image.resize((size, size), Image.Resampling.BOX)


def _render_master(size: int, tile_box: tuple[float, float, float, float]) -> Image.Image:
    """The four-arch drawing with its tile at ``tile_box`` (pixels of ``size``)."""
    tile_size = tile_box[2] - tile_box[0]
    unit = tile_size / (TILE_BOX[2] - TILE_BOX[0])

    def place(x: float, y: float, k: float) -> tuple[float, float]:
        return ((tile_box[0] + (x - TILE_BOX[0]) * unit) * k, (tile_box[1] + (y - TILE_BOX[1]) * unit) * k)

    def tile(draw: ImageDraw.ImageDraw, k: int) -> None:
        draw.polygon(_tile(tile_box, TILE_RADIUS * unit, k), fill=TILE)

    def arches(draw: ImageDraw.ImageDraw, k: int) -> None:
        _, centre_y = place(0, ARCH_CENTRE_Y, k)
        _, floor = place(0, FLOOR, k)
        for (left, right), colour in zip((*ARCH_SPANS, DOOR_SPAN), (*ARCH_COLOURS, DOOR), strict=True):
            _arch(draw, place(left, 0, k)[0], place(right, 0, k)[0], centre_y, floor, colour)

    return _supersampled(size, tile, arches, place(0, ARCH_CENTRE_Y, 1)[1], place(0, FLOOR, 1)[1])


def _render_small(size: int) -> Image.Image:
    offset, tile, radius, floor, spans, door, door_height, colours = SMALL_MASTERS[size]
    centre_y = floor - door_height + (door[1] - door[0]) / 2

    def paint_tile(draw: ImageDraw.ImageDraw, k: int) -> None:
        box = (offset, offset, offset + tile, offset + tile)
        draw.polygon(_tile(box, radius, k), fill=TILE)

    def arches(draw: ImageDraw.ImageDraw, k: int) -> None:
        for (left, right), colour in zip((*spans, door), (*colours, DOOR), strict=True):
            _arch(draw, left * k, right * k, centre_y * k, floor * k, colour)

    return _supersampled(size, paint_tile, arches, centre_y, floor)


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


def render_ios_icon() -> Image.Image:
    """The iPhone icon: the tile edge to edge on an opaque square; iOS rounds it."""
    square = Image.new("RGBA", (CANVAS, CANVAS), TILE)
    return Image.alpha_composite(square, _render_master(CANVAS, (0, 0, CANVAS, CANVAS))).convert("RGB")


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


def write_ios_icon(destination: Path = IOS_APP_ICON) -> Path:
    render_ios_icon().save(destination)
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
    written.append(write_ios_icon())
    written.append(write_favicon())
    written.extend(write_extension_icons())
    for path in written:
        print(path)
