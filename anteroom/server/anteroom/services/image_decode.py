"""Decode untrusted photos with bounded memory, on the Mac and the hosted API alike."""
from __future__ import annotations

import io
import threading
import warnings

from PIL import Image, ImageOps

# Camera photos reach about 48 megapixels (6000×8000); larger is refused.
MAX_PIXELS = 50_000_000
FORMATS = frozenset({'JPEG', 'MPO', 'PNG', 'WEBP', 'HEIF', 'HEIC'})
# One full decode at a time bounds decoder memory while uploads, imports and
# thumbnails arrive concurrently.
_decode_slot = threading.Semaphore(1)


def decode(data: bytes, *, max_side: int | None = None) -> tuple[Image.Image, tuple[int, int]]:
    """An upright, loaded copy of ``data`` and the stored image's own size.

    With ``max_side`` the result is at most that many pixels on a side, and a
    JPEG decodes at a fraction of its size when that still covers it. Raises
    ``ValueError`` for anything that is not a JPEG, PNG, WebP or HEIC photo of
    at most ``MAX_PIXELS``.
    """
    with _decode_slot:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in FORMATS or image.width * image.height > MAX_PIXELS:
                        raise ValueError('unsupported or too large')
                    size = image.size
                    if max_side:
                        image.draft('RGB', (max_side, max_side))
                    upright = ImageOps.exif_transpose(image)
                    if max_side:
                        upright.thumbnail((max_side, max_side))
                    upright.load()
                    return upright, size
        except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('not a valid image') from exc


def normalized_jpeg(data: bytes, *, max_side: int) -> tuple[bytes, int, int]:
    """A JPEG at most ``max_side`` px on a side, flattened onto white, without EXIF or location data."""
    image, _size = decode(data, max_side=max_side)
    rgba = image.convert('RGBA')
    result = Image.new('RGB', rgba.size, 'white')
    result.paste(rgba, mask=rgba.getchannel('A'))
    output = io.BytesIO()
    result.save(output, format='JPEG', quality=90)
    return output.getvalue(), result.width, result.height


def jpeg(data: bytes, *, max_side: int | None = None, quality: int) -> bytes:
    """``data`` as an upright RGB JPEG, optionally shrunk to ``max_side``."""
    image, _size = decode(data, max_side=max_side)
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=quality, optimize=max_side is not None)
    return output.getvalue()
