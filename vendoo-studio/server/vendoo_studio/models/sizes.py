"""Letter sizes written different ways, read as one size.

Marketplaces spell the same size differently: Depop offers XXL, eBay offers
2XL, and a plus tag reads 2X. ``size_key`` gives all of them one spelling, so a
size can be compared, or matched against a marketplace's own choices.
"""

from __future__ import annotations

import re
from typing import Any


def size_key(size: Any) -> str:
    """A size as one spelling, so a tag reading ``Large (12/14)`` or ``2X`` matches ``L`` or ``2XL``."""
    text = re.sub(r"[\s.\-]+", "", _TAG_RANGE.sub("", str(size or "")).upper())
    text = _SIZE_WORDS.get(text, text)
    text = _X_WORD.sub(lambda match: match.group(1) + match.group(2)[0], text)
    if counted := _COUNTED_SIZE.match(text):
        return f"{counted.group(1)}X{counted.group(2) or 'L'}"
    if repeated := _REPEATED_SIZE.match(text):
        xs, letter = len(repeated.group(1)), repeated.group(2)
        return f"{xs}X{letter}" if xs > 1 else "X" * xs + letter
    return text


def letter_size(size: Any) -> str | None:
    """The size in counted letters (``2XL``, ``XS``, ``M``) when it is a plain letter size, else None.

    A size with a range, like a kids ``XL (18/20)``, is a different choice from ``XL`` and is left alone.
    """
    if "(" in str(size or ""):
        return None
    key = size_key(size)
    return key if _LETTER_SIZE.fullmatch(key) else None


# The range a tag gives after the size, like ``M (8-10)``, is not the size.
_TAG_RANGE = re.compile(r"(?<=\S)\s*\([^)]*\)\s*$")
_SIZE_WORDS = {
    "EXTRASMALL": "XS", "XSMALL": "XS", "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
    "EXTRALARGE": "XL", "XLARGE": "XL", "XXLARGE": "XXL", "XXXLARGE": "XXXL",
}
# 3X-Large and XX-Small, as 3XL and XXS.
_X_WORD = re.compile(r"^([2-9]?X+)(LARGE|SMALL)$")
# 2XL, 3XS, and plus sizing's 2X, which is 2XL.
_COUNTED_SIZE = re.compile(r"^([2-9])X([SL]?)$")
# XS, XXL, XXXL.
_REPEATED_SIZE = re.compile(r"^(X*)([SL])$")
_LETTER_SIZE = re.compile(r"[2-9]X[SL]|X?[SL]|M")
