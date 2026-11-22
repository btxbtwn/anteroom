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
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

AGREE_WITHIN = 0.25  # inches two reads may differ by and still be accepted
DETECT_SIDE = 320  # the photo is searched for sticks at this size
MAX_PIXELS = 50_000_000  # refuse decompression bombs and >50 MP images
MAX_SIDE = 4096  # caps decode + HSV/crop memory while keeping 1/8" ticks legible
MIN_STICK_SHARE = 0.20  # a stick must span at least this share of the photo's short side
REFINE_MAX_LEN = 1200  # cap temporary band width during line refinement and verification
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
    """One yardstick: its centre line in photo pixels, its width, and detection confidence."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    score: float = 0.0

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


def _load_with_scale(path: Path, *, max_side: int | None = MAX_SIDE) -> tuple[Image.Image, float, float]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError(f"Photo exceeds maximum pixel count ({MAX_PIXELS})")
            orig_w, orig_h = image.size
            if image.getexif().get(274) in (5, 6, 7, 8):
                orig_w, orig_h = orig_h, orig_w
            if max_side:
                image.draft("RGB", (max_side, max_side))
            upright = ImageOps.exif_transpose(image)
            if max_side and max(upright.size) > max_side:
                upright.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            rgb = upright.convert("RGB")
            return rgb, rgb.width / orig_w, rgb.height / orig_h


def load(path: Path, *, max_side: int | None = MAX_SIDE) -> Image.Image:
    return _load_with_scale(path, max_side=max_side)[0]


def _hsv_bands(image: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hue, sat, val = (np.asarray(band, dtype=np.float32) for band in image.convert("HSV").split())
    return hue, sat, val


def wood_mask(image: Image.Image) -> np.ndarray:
    """Pixels the colour of unfinished wood, from tan in shade to near white in full sun.

    Sunlit wood keeps only a trace of warmth, so the saturation floor is low. That lets
    cream and white garments through as well; `find_sticks` tells them apart by shape and
    periodic ruler tick marks.
    """
    hue, sat, val = _hsv_bands(image)
    return (hue > 8) & (hue < 42) & (sat > 7) & (sat < 150) & (val > 150)


def dark_wood_mask(image: Image.Image) -> np.ndarray:
    """Pixels of darker or weathered wooden rulers (e.g. vintage folding rulers on light fabric)."""
    hue, sat, val = _hsv_bands(image)
    return (hue > 8) & (hue < 44) & (sat > 36) & (sat < 200) & (val > 50) & (val < 225)


def any_wood_mask(image: Image.Image) -> np.ndarray:
    """Union of bright and darker wooden ruler masks in a single HSV pass."""
    hue, sat, val = _hsv_bands(image)
    light = (hue > 8) & (hue < 42) & (sat > 7) & (sat < 150) & (val > 150)
    dark = (hue > 8) & (hue < 44) & (sat > 36) & (sat < 200) & (val > 50) & (val < 225)
    return light | dark


def _local_extrema(arr: np.ndarray, radius: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Separable (2*radius+1)x(2*radius+1) local max and min in pure numpy."""
    h, w = arr.shape
    span = 2 * radius + 1
    pad_y = np.pad(arr, ((radius, radius), (0, 0)), mode="edge")
    max_y = np.maximum.reduce([pad_y[i : i + h, :] for i in range(span)])
    min_y = np.minimum.reduce([pad_y[i : i + h, :] for i in range(span)])
    pad_max = np.pad(max_y, ((0, 0), (radius, radius)), mode="edge")
    pad_min = np.pad(min_y, ((0, 0), (radius, radius)), mode="edge")
    return (
        np.maximum.reduce([pad_max[:, i : i + w] for i in range(span)]),
        np.minimum.reduce([pad_min[:, i : i + w] for i in range(span)]),
    )


def tick_edge_wood_mask(image: Image.Image, *, wood: np.ndarray | None = None) -> np.ndarray:
    """High-contrast printed ruler ticks adjacent to wood pixels.

    Separates a wooden yardstick lying on a similarly coloured plaid or tan garment where
    plain wood colour blends into the cloth.
    """
    grey = np.asarray(image.convert("L"), dtype=np.float32)
    loc_max, loc_min = _local_extrema(grey, radius=2)
    contrast = (loc_max - loc_min) > 42.0
    base_wood = any_wood_mask(image) if wood is None else wood
    wood_near = _local_extrema(base_wood.astype(np.uint8), radius=2)[0] > 0
    return contrast & wood_near


def box(profile: np.ndarray, width: int) -> np.ndarray:
    return np.convolve(profile, np.ones(width) / width, mode="same")


def _verify_stick(
    image: Image.Image,
    stick: Stick,
    mask_fn,
    *,
    is_tick_channel: bool = False,
) -> float | None:
    """Verify a candidate stick carries periodic ruler ticks concentrated along its edge.

    Uses a short sliding-window STFT on the horizontal first difference so perspective
    foreshortening on vertical sticks does not smear the tick frequency peak, and compares
    the peak tick row against interior rows (`edge_ratio`) to reject uniform stripes, knit
    ribbing, polka dots, woven tags, and plaid patterns. Returns a confidence score or None.
    """
    side_max = max(image.size)
    side_min = min(image.size)
    if stick.length < side_min * 0.25:
        return None
    min_w = side_max * (0.011 if is_tick_channel else 0.008)
    if stick.width < min_w or stick.width > side_max * 0.145:
        return None

    ux, uy = (stick.x2 - stick.x1) / stick.length, (stick.y2 - stick.y1) / stick.length
    if ux < 0:
        ux, uy = -ux, -uy
        stick = Stick(stick.x2, stick.y2, stick.x1, stick.y1, stick.width)
    nx, ny = -uy, ux

    # Cap verification strip length so 12 MP full-res photos stay fast while preserving ticks.
    v_scale = min(1.0, REFINE_MAX_LEN / max(stick.length, 1.0))
    w_img = stick.width
    w = w_img * v_scale
    half_img = w_img * 1.45
    out_w = max(int(round(stick.length * v_scale)), 64)
    out_h = max(int(round(2 * half_img * v_scale)), 24)
    quad = (
        stick.x1 - nx * half_img,
        stick.y1 - ny * half_img,
        stick.x1 + nx * half_img,
        stick.y1 + ny * half_img,
        stick.x2 + nx * half_img,
        stick.y2 + ny * half_img,
        stick.x2 - nx * half_img,
        stick.y2 - ny * half_img,
    )
    strip = image.transform((out_w, out_h), Image.Transform.QUAD, quad, resample=Image.Resampling.BILINEAR)

    # Check colour continuity and contrast on a 2x horizontally downsampled strip first.
    strip_sm = strip.resize((max(out_w // 2, 32), out_h), Image.Resampling.BILINEAR)
    hue, sat, val = _hsv_bands(strip_sm)
    light_sm = (hue > 8) & (hue < 42) & (sat > 7) & (sat < 150) & (val > 150)
    dark_sm = (hue > 8) & (hue < 44) & (sat > 36) & (sat < 200) & (val > 50) & (val < 225)
    wood_sm = light_sm | dark_sm
    if mask_fn is wood_mask:
        strip_mask_sm = light_sm
    elif mask_fn is dark_wood_mask:
        strip_mask_sm = dark_sm
    elif mask_fn is tick_edge_wood_mask:
        strip_mask_sm = tick_edge_wood_mask(strip_sm, wood=wood_sm)
    else:
        strip_mask_sm = mask_fn(strip_sm)

    mid = out_h // 2
    r_lo = max(0, int(mid - w * 0.52))
    r_hi = min(out_h, int(mid + w * 0.52) + 1)
    core_col_sm = strip_mask_sm[r_lo:r_hi].mean(axis=0)
    active_sm = np.nonzero(core_col_sm > 0.25)[0]
    min_active = max(32, int(side_min * v_scale * 0.11))
    if len(active_sm) < min_active:
        return None
    sx0, sx1 = int(active_sm[0]), int(active_sm[-1]) + 1

    # Ignore out-of-bounds black quad fill so border-hugging lines cannot fake wood contrast or edge_ratio.
    sm_w = strip_sm.width
    t_sm = ((np.arange(sx0, sx1, dtype=np.float32) + 0.5) / sm_w) * stick.length
    d_sm = (((np.arange(out_h, dtype=np.float32) + 0.5) / out_h) * 2.0 - 1.0) * half_img
    px_sm = stick.x1 + ux * t_sm[None, :] + nx * d_sm[:, None]
    py_sm = stick.y1 + uy * t_sm[None, :] + ny * d_sm[:, None]
    in_bounds_sm = (
        (px_sm >= 1.0) & (px_sm <= image.width - 2.0) & (py_sm >= 1.0) & (py_sm <= image.height - 2.0)
    )
    if float(in_bounds_sm[r_lo:r_hi].mean()) < 0.85:
        return None

    active_wood_density = float((wood_sm[r_lo:r_hi, sx0:sx1].mean(axis=0) > 0.22).mean())
    width_ratio = w_img / side_max
    min_wood_density = 0.90 if width_ratio > 0.055 else (0.80 if is_tick_channel else 0.45)
    if active_wood_density < min_wood_density:
        return None

    core_wood = float(core_col_sm[sx0:sx1].mean())
    ft_hi = max(1, int(mid - w * 0.78))
    fb_lo = min(out_h - 1, int(mid + w * 0.78))
    flanks: list[float] = []
    wood_flanks: list[float] = []
    for sl in (slice(0, ft_hi), slice(fb_lo, out_h)):
        valid_flank = in_bounds_sm[sl]
        if float(valid_flank.mean()) >= 0.25:
            flanks.append(float(strip_mask_sm[sl, sx0:sx1][valid_flank].mean()))
            if is_tick_channel:
                wood_flanks.append(float(wood_sm[sl, sx0:sx1][valid_flank].mean()))
    if not flanks:
        return None
    flank_wood = sum(flanks) / len(flanks)
    wood_contrast = core_wood - flank_wood
    if wood_contrast < 0.12:
        return None
    # In the tick channel the fitted band follows the print, not the whole stick.
    # Continuous wood on both sides puts that printed band inside the stick.
    if len(wood_flanks) == 2 and min(wood_flanks) >= 0.75:
        return None

    x0 = max(0, sx0 * 2)
    x1 = min(out_w, sx1 * 2)
    if x1 - x0 < 96:
        return None

    grey = np.asarray(strip.convert("L"), dtype=np.float32)[:, x0:x1]
    dx = np.diff(grey, axis=1)
    length_px = dx.shape[1]
    k_rows = max(min(int(round(w * 0.12)), 5), 2)
    in_lo = max(0, r_lo)
    in_end = min(dx.shape[0], max(in_lo + k_rows, r_hi))
    in_dx = dx[in_lo:in_end]
    if in_dx.shape[0] < k_rows:
        return None
    cs_r = np.cumsum(np.pad(in_dx, ((1, 0), (0, 0))), axis=0)
    bands = (cs_r[k_rows:] - cs_r[:-k_rows]) / k_rows
    n_in = bands.shape[0]
    if n_in < 1:
        return None

    min_period = max(3.2, w * 0.08)
    max_period = max(min_period * 1.8, min(36.0, w * 0.48))
    freq_lo, freq_hi = 1.0 / max_period, 1.0 / min_period
    nominal = max(128, min(192, int(round(w * 4.5))))
    # Wide rulers put their plausible periods in a narrow band of frequencies.
    # Only grow the window when the nominal one cannot resolve five bins.
    if math.floor(nominal * freq_hi) - math.ceil(nominal * freq_lo) + 1 < 5:
        nominal = math.ceil(5 / (freq_hi - freq_lo))
    win_len = min(length_px, nominal)
    step = max(32, win_len // 2)
    starts = list(range(0, max(1, length_px - win_len + 1), step))
    if starts[-1] + win_len < length_px:
        starts.append(length_px - win_len)
    win_starts = [s for s in starts if core_col_sm[sx0 + s // 2 : sx0 + (s + win_len) // 2].mean() >= 0.25]
    if len(win_starts) < 2:
        win_starts = starts

    hann = np.hanning(win_len).astype(np.float32)
    freqs = np.fft.rfftfreq(win_len, d=1.0)
    valid_bins = np.nonzero((freqs >= freq_lo) & (freqs <= freq_hi))[0]
    if len(valid_bins) < 5:
        return None
    n_bins = len(freqs)

    row_proms = np.zeros((n_in, len(win_starts)), dtype=np.float32)
    row_dxs = np.zeros((n_in, len(win_starts)), dtype=np.float32)
    row_pows = np.zeros((n_in, len(win_starts)), dtype=np.float32)

    for wi, start in enumerate(win_starts):
        segs = bands[:, start : start + win_len]
        in_sp = np.abs(np.fft.rfft((segs - segs.mean(axis=1, keepdims=True)) * hann[None, :], axis=1)) ** 2
        abs_segs = np.abs(segs)
        ks = valid_bins[np.argmax(in_sp[:, valid_bins], axis=1)]
        for ri in range(n_in):
            k = int(ks[ri])
            # Bin k describes k repeats, but can be a harmonic of the tick
            # spacing. Allow for that and for marks clipped by window edges.
            rank = max(3, k // 2)
            row_dxs[ri, wi] = np.partition(abs_segs[ri], -rank)[-rank]
            bg_lo = max(2, k - 12)
            bg_hi = min(n_bins, k + 13)
            bg = np.concatenate([in_sp[ri, bg_lo : max(bg_lo, k - 2)], in_sp[ri, min(bg_hi, k + 3) : bg_hi]])
            med = float(np.median(bg)) + 1e-6 if len(bg) >= 4 else 1.0
            pw = float(in_sp[ri, k])
            row_pows[ri, wi] = pw
            row_proms[ri, wi] = pw / med

    med_proms = np.median(row_proms, axis=1)
    best_ri = int(np.argmax(med_proms))
    if not is_tick_channel:
        band_rows = in_lo + np.arange(n_in) + (k_rows - 1) / 2.0
        edge_rows = np.flatnonzero(np.abs(band_rows - mid) >= w * 0.25)
        if not len(edge_rows):
            return None
        edge_ri = int(edge_rows[np.argmax(med_proms[edge_rows])])
        # Adjacent rows of the same printed tick can differ slightly after resampling.
        if med_proms[edge_ri] < med_proms[best_ri] * 0.90:
            return None
        best_ri = edge_ri
    mp = float(med_proms[best_ri])
    dx_med = float(np.median(row_dxs[best_ri]))
    row_med_pow = np.median(row_pows, axis=1)
    edge_ratio = (
        float(row_med_pow[best_ri] / (float(np.percentile(row_med_pow, 25)) + 1e-6)) if n_in >= 4 else 2.0
    )

    min_edge_ratio = 4.5 if is_tick_channel else 3.0
    # Close-up wide sticks have crisp dark tick marks; only distant narrow sticks may soften below dx=15.
    if width_ratio > 0.055:
        contrast_ok = dx_med >= 22.0
    elif width_ratio <= 0.045:
        contrast_ok = dx_med >= 15.0 or (dx_med >= 10.0 and wood_contrast >= 0.50)
    else:
        contrast_ok = dx_med >= 18.0
    if mp < 18.0 or edge_ratio < min_edge_ratio or not contrast_ok:
        return None
    return mp * min(edge_ratio, 25.0)


def _row_distance(row: int, other_row: int, angle_gap: float, side: int) -> int:
    """Compare rotated rows after aligning their perpendicular directions."""
    if angle_gap > 90.0:
        other_row = side - 1 - other_row
    return abs(row - other_row)


def _bands_overlap(first: Stick, second: Stick) -> bool:
    """Test the finite ruler rectangles on each of their separating axes."""
    delta = (
        (second.x1 + second.x2 - first.x1 - first.x2) / 2.0,
        (second.y1 + second.y2 - first.y1 - first.y2) / 2.0,
    )
    u, v = [
        ((s.x2 - s.x1) / s.length, (s.y2 - s.y1) / s.length) for s in (first, second)
    ]
    normals = (-u[1], u[0]), (-v[1], v[0])
    for x, y in (u, v, *normals):
        reaches = sum(
            (s.length * abs(axis[0] * x + axis[1] * y) + s.width * abs(normal[0] * x + normal[1] * y)) / 2.0
            for s, axis, normal in zip((first, second), (u, v), normals, strict=True)
        )
        if abs(delta[0] * x + delta[1] * y) >= reaches:
            return False
    return True


def find_sticks(image: Image.Image, limit: int = 2) -> list[Stick]:
    """Find up to `limit` wooden rulers as long, narrow straight bands with periodic ticks.

    Searches three complementary channels in a single RGB rotation pass:
      0. bright/unfinished wood (`wood_mask`)
      1. darker/weathered folding rulers (`dark_wood_mask`)
      2. high-contrast ruler tick edges near wood (`tick_edge_wood_mask`) for wood-on-plaid

    Candidates are picked across diverse orientations, refined to full image geometry, and
    verified for periodic tick marks concentrated along the ruler edge.
    """
    scale = DETECT_SIDE / max(image.size)
    small = image.resize(
        (max(round(image.width * scale), 1), max(round(image.height * scale), 1)),
        Image.Resampling.BILINEAR,
    )
    hue, sat, val = _hsv_bands(small)
    w_mask = (hue > 8) & (hue < 42) & (sat > 7) & (sat < 150) & (val > 150)
    d_mask = (hue > 8) & (hue < 44) & (sat > 36) & (sat < 200) & (val > 50) & (val < 225)
    t_mask = tick_edge_wood_mask(small, wood=(w_mask | d_mask))

    side = int(math.hypot(*small.size)) + 2
    offset = ((side - small.width) // 2, (side - small.height) // 2)
    arr = np.zeros((side, side, 3), dtype=np.uint8)
    arr[offset[1] : offset[1] + small.height, offset[0] : offset[0] + small.width, 0] = (w_mask * 255).astype(np.uint8)
    arr[offset[1] : offset[1] + small.height, offset[0] : offset[0] + small.width, 1] = (d_mask * 255).astype(np.uint8)
    arr[offset[1] : offset[1] + small.height, offset[0] : offset[0] + small.width, 2] = (t_mask * 255).astype(np.uint8)
    canvas = Image.fromarray(arr, mode="RGB")

    thresh = MIN_STICK_SHARE * min(small.size)
    widths = (5, 8, 12, 17, 24, 34)
    peaks_by_ch: dict[int, list[tuple[float, int, float, int, int]]] = {0: [], 1: [], 2: []}
    for angle in range(0, 180, 3):
        rot = np.asarray(canvas.rotate(angle, resample=Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
        for ch in (0, 1, 2):
            profile = rot[:, :, ch].sum(axis=1)
            best_score_row = np.zeros(side, dtype=np.float32)
            best_width_row = np.zeros(side, dtype=np.int32)
            for width in widths:
                core = box(profile, max(width // 2, 3))
                flank = np.maximum(np.roll(core, width), np.roll(core, -width))
                score = core - flank
                better = score > best_score_row
                best_score_row[better] = score[better]
                best_width_row[better] = width
            sc_copy = best_score_row.copy()
            for _ in range(5):
                row = int(sc_copy.argmax())
                s = float(sc_copy[row])
                if s < thresh:
                    break
                w = int(best_width_row[row])
                peaks_by_ch[ch].append((s, ch, float(angle), row, w))
                sup = max(w, 10)
                sc_copy[max(0, row - sup) : min(side, row + sup + 1)] = 0.0

    # Keep orientation-diverse peaks per channel while leaving room when a striped garment
    # shares the same angle as the ruler.
    raw_chosen: list[tuple[float, int, float, int, int]] = []
    for ch in (0, 1, 2):
        ch_chosen: list[tuple[float, int, float, int, int]] = []
        for s, ch_id, ang, row, w in sorted(peaks_by_ch[ch], reverse=True):
            dup = False
            same_ang_count = 0
            for _, _, c_ang, c_row, c_w in ch_chosen:
                gap = abs(ang - c_ang)
                da = min(gap, 180.0 - gap)
                if da < 10.0 and _row_distance(row, c_row, gap, side) < max(w, c_w, 12) * 1.4:
                    dup = True
                    break
                if da < 18.0:
                    same_ang_count += 1
            if not dup and same_ang_count < 5:
                ch_chosen.append((s, ch_id, ang, row, w))
                if len(ch_chosen) >= 7:
                    break
        raw_chosen.extend(ch_chosen)

    # Cross-channel deduplication by (angle, row) before fine search and verification.
    # Keep tick-edge candidates separate from full-wood candidates so a narrow tick-edge
    # peak never suppresses a full-wood peak on the same ruler (and vice versa).
    raw_chosen.sort(reverse=True)
    chosen: list[tuple[float, int, float, int, int]] = []
    for item in raw_chosen:
        _, ch, ang, row, w = item
        dup = False
        for _, c_ch, c_ang, c_row, c_w in chosen:
            if (ch == 2) != (c_ch == 2):
                continue
            gap = abs(ang - c_ang)
            da = min(gap, 180.0 - gap)
            if da < 7.0 and _row_distance(row, c_row, gap, side) < max(min(w, c_w), 8):
                dup = True
                break
        if not dup:
            chosen.append(item)
            if len(chosen) >= 14:
                break

    ch_canvas = [canvas.getchannel(0), canvas.getchannel(1), canvas.getchannel(2)]
    mask_fns = (wood_mask, dark_wood_mask, tick_edge_wood_mask)
    side_max = max(image.size)
    verified: list[Stick] = []
    for _, ch, base_ang, base_row, base_w in chosen:
        best = None
        r_lo = max(0, base_row - max(base_w, 12))
        r_hi = min(side, base_row + max(base_w, 12) + 1)
        for da in (-2.0, -1.0, 0.0, 1.0, 2.0):
            # Keep the trial rotation in the same row frame as the coarse peak.
            ang = base_ang + da
            rot = np.asarray(ch_canvas[ch].rotate(ang, resample=Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
            profile = rot.sum(axis=1)
            for width in widths:
                core = box(profile, max(width // 2, 3))
                flank = np.maximum(np.roll(core, width), np.roll(core, -width))
                score = core - flank
                row = r_lo + int(score[r_lo:r_hi].argmax())
                if best is None or score[row] > best[0]:
                    best = (float(score[row]), ang, row, width, rot)
        if best is None:
            continue
        _, ang, row, _, rows = best
        profile = rows.sum(axis=1)
        level = profile[row] * 0.5
        top = bottom = row
        while top > 0 and profile[top - 1] > level:
            top -= 1
        while bottom < side - 1 and profile[bottom + 1] > level:
            bottom += 1
        columns = np.nonzero(rows[top : bottom + 1].mean(axis=0) > 0.35)[0]
        if len(columns) < 2:
            continue
        centre = (top + bottom) / 2.0
        ends = []
        for column in (columns[0], columns[-1]):
            dx, dy = column - side / 2.0, centre - side / 2.0
            rad = math.radians(ang)
            x = dx * math.cos(rad) - dy * math.sin(rad) + side / 2.0 - offset[0]
            y = dx * math.sin(rad) + dy * math.cos(rad) + side / 2.0 - offset[1]
            ends.append((x / scale, y / scale))
        raw = to_edges(image, Stick(*ends[0], *ends[1], width=(bottom - top + 1) / scale))
        mask_fn = mask_fns[ch]
        refined = refine(image, raw, mask_fn=mask_fn)
        confidence = _verify_stick(image, refined, mask_fn, is_tick_channel=(ch == 2))
        if confidence is not None:
            # The tick-edge mask measures only the narrow printed tick strip; widen crop
            # width to a realistic ruler width so tiles carry full numerals and garment edge.
            width_out = refined.width
            if ch == 2 and width_out < side_max * 0.035:
                width_out = min(max(width_out * 2.2, side_max * 0.035), side_max * 0.065)
            verified.append(Stick(refined.x1, refined.y1, refined.x2, refined.y2, width=width_out, score=confidence))

    # Deduplicate parallel/overlapping detections of the same physical stick.
    verified.sort(key=lambda s: s.score, reverse=True)
    sticks: list[Stick] = []
    for st in verified:
        ang = math.degrees(math.atan2(st.y2 - st.y1, st.x2 - st.x1)) % 180.0
        dup = False
        for kept in sticks:
            k_ang = math.degrees(math.atan2(kept.y2 - kept.y1, kept.x2 - kept.x1)) % 180.0
            da = min(abs(ang - k_ang), 180.0 - abs(ang - k_ang))
            if da < 15.0 and _bands_overlap(st, kept):
                dup = True
                break
        if not dup:
            sticks.append(st)
            if len(sticks) >= limit:
                break

    # Both sticks are the same kind of yardstick, so a width far off the other's is a misread.
    if len(sticks) == 2 and max(s.width for s in sticks) > 1.6 * min(s.width for s in sticks):
        plausible = [s.width for s in sticks if 0.03 * side_max <= s.width <= 0.12 * side_max]
        if len(plausible) == 1:
            for stick in sticks:
                stick.width = plausible[0]
    # Name them in a stable order: the stick lying flatter across the photo is A.
    sticks.sort(key=lambda s: abs(s.y2 - s.y1) / max(abs(s.x2 - s.x1), 1e-6))
    return sticks


def to_edges(image: Image.Image, stick: Stick) -> Stick:
    """Intersect the stick's infinite line cleanly with the photo bounds (Liang-Barsky).

    Where the other stick crosses it, or lighting bleaches it, the wood colour stops and
    the found stick ends early, short of the reading. Exact rectangle clipping prevents
    near-corner endpoints from shooting thousands of pixels outside the frame.
    """
    length = stick.length
    if length < 1e-6:
        return stick
    ux, uy = (stick.x2 - stick.x1) / length, (stick.y2 - stick.y1) / length
    w, h = float(image.width - 1), float(image.height - 1)
    t_lo, t_hi = -1e12, 1e12
    for p, q in (
        (-ux, stick.x1),
        (ux, w - stick.x1),
        (-uy, stick.y1),
        (uy, h - stick.y1),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return stick
        else:
            t = q / p
            if p < 0:
                t_lo = max(t_lo, t)
            else:
                t_hi = min(t_hi, t)
    if t_lo >= t_hi:
        return stick
    return Stick(
        stick.x1 + ux * t_lo,
        stick.y1 + uy * t_lo,
        stick.x1 + ux * t_hi,
        stick.y1 + uy * t_hi,
        width=stick.width,
        score=stick.score,
    )


def refine(image: Image.Image, stick: Stick, *, mask_fn=wood_mask) -> Stick:
    """Tighten a coarse stick to the full-size photo: its true centre line and width.

    The search works in coarse degrees on a small copy, which over a yardstick's length
    drifts by more than a tick. Here the stick is cut into short sections, each section's
    mask band is measured across near the coarse centre, and a robust median line is fitted.
    """
    if stick.length < 1e-3:
        return stick
    ux, uy = (stick.x2 - stick.x1) / stick.length, (stick.y2 - stick.y1) / stick.length
    nx, ny = -uy, ux
    side_max = max(image.size)
    half = max(min(stick.width * 2.0, side_max * 0.09), side_max * 0.025)
    quad = []
    for along, across in ((0, -half), (0, half), (stick.length, half), (stick.length, -half)):
        quad += [stick.x1 + ux * along + nx * across, stick.y1 + uy * along + ny * across]
    r_scale = min(1.0, REFINE_MAX_LEN / max(stick.length, 1.0))
    band_w = max(round(stick.length * r_scale), 1)
    band_h = max(round(2 * half * r_scale), 1)
    band = image.transform((band_w, band_h), Image.Transform.QUAD, quad, resample=Image.Resampling.BILINEAR)
    mask = mask_fn(band).astype(np.float32)

    centres, widths = [], []
    w_band = stick.width * r_scale
    half_band = half * r_scale
    smooth_w = max(min(int(w_band * 0.22), 7), 3)
    max_w = min(w_band * 1.75, half_band * 1.35)
    search_r = max(int(half_band * 0.55), int(w_band * 0.9))
    for section in np.array_split(np.arange(mask.shape[1]), 40):
        if len(section) == 0:
            continue
        profile = box(mask[:, section].mean(axis=1), smooth_w)
        mid = len(profile) // 2
        lo = max(0, mid - search_r)
        hi = min(len(profile), mid + search_r + 1)
        peak = lo + int(profile[lo:hi].argmax())
        if profile[peak] < 0.40:
            continue
        top = bottom = peak
        while top > 0 and profile[top - 1] > profile[peak] * 0.5:
            top -= 1
        while bottom < len(profile) - 1 and profile[bottom + 1] > profile[peak] * 0.5:
            bottom += 1
        w_sec = bottom - top + 1
        if w_sec > max_w:
            continue
        centres.append((float(section.mean()) / r_scale, ((top + bottom) / 2.0 - half_band) / r_scale))
        widths.append(w_sec / r_scale)
    if len(centres) < 4:
        return stick
    width = float(np.percentile(widths, 60))
    kept = [(x, y) for (x, y), w in zip(centres, widths, strict=True) if abs(w - width) < max(width * 0.28, 4.0)]
    if len(kept) < 3:
        return stick
    xs, ys = np.array(kept).T
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs))
        for j in range(i + 1, len(xs))
        if abs(xs[j] - xs[i]) > 1.0
    ]
    if not slopes:
        return stick
    slope = float(np.median(slopes))
    shift = float(np.median(ys - slope * xs))

    def point(along: float) -> tuple[float, float]:
        across = shift + slope * along
        return stick.x1 + ux * along + nx * across, stick.y1 + uy * along + ny * across

    return to_edges(image, Stick(*point(0), *point(stick.length), width=width, score=stick.score))


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
    strip = image.transform(size, Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC, fillcolor=BACKDROP)
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


def crop(
    photo: Path,
    out: Path,
    lines: list[str],
    flips: set[str],
    *,
    image: Image.Image | None = None,
) -> dict:
    if image is not None:
        scale_x = scale_y = 1.0
    else:
        image, scale_x, scale_y = _load_with_scale(photo)
    if lines:
        scale = (scale_x + scale_y) / 2
        sticks = []
        for line in lines:
            x1, y1, x2, y2, *rest = (float(part) for part in line.split(","))
            sticks.append(
                Stick(
                    x1 * scale_x,
                    y1 * scale_y,
                    x2 * scale_x,
                    y2 * scale_y,
                    width=(rest[0] * scale) if rest else max(image.size) * 0.05,
                )
            )
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
            tile = tile.resize(
                (TILE_OUT_WIDTH, round(tile.height * TILE_OUT_WIDTH / tile.width)),
                Image.Resampling.LANCZOS,
            )
            path = out / f"{name}-{number:02d}.jpg"
            tile.save(path, quality=92)
            tiles.append(str(path))

        # The whole strip, small, with tile numbers above it so the reader can pick one.
        shrink = min(2000 / strip.width, 1.0)
        small = strip.resize((round(strip.width * shrink), round(strip.height * shrink)), Image.Resampling.LANCZOS)
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
