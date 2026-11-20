"""Sizes written different ways, read as one size.

Marketplaces spell the same size differently: Depop offers XXL, eBay offers
2XL, and a plus tag reads 2X. ``size_key`` gives all of them one spelling, so a
size can be compared, and ``size_choice`` finds it among a marketplace's own choices.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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


def size_choice(size: Any, choices: Iterable[Any]) -> str | None:
    """The one choice that is ``size`` as the marketplace spells it, or None.

    Tried in order, each only when the earlier ones find nothing: the same
    text; the same text ignoring case, spaces, dots and dashes (``one-size``);
    One Size under any of its names (``OS``); the same letter size
    (``XXL`` is ``2XL``), reading a kids ``XL (18/20)`` as ``XL`` only when the
    choices do not offer the range itself; and the same measurement, which is
    Depop's ``36"`` for a tag that reads 36. A step that matches more than one
    choice is ambiguous and matches none.
    """
    text = str(size or "").strip()
    labels = [str(choice) for choice in choices if str(choice or "").strip()]
    if not text or not labels:
        return None
    if text in labels:
        return text
    steps = ((_squashed, _squashed), (_one_size, _one_size), (letter_size, letter_size),
             (_letters_without_range, letter_size), (_measured, _measured))
    for read_size, read_choice in steps:
        wanted = read_size(text)
        if not wanted:
            continue
        found = [label for label in labels if read_choice(label) == wanted]
        if found:
            return found[0] if len(found) == 1 else None
    return None


def _squashed(text: str) -> str:
    return re.sub(r"[\s.\-]+", "", text).casefold()


def _one_size(text: str) -> str | None:
    return "one size" if _squashed(text) in {"os", "onesize", "onesizefitsall", "osfa"} else None


def _letters_without_range(text: str) -> str | None:
    return letter_size(_TAG_RANGE.sub("", text))


def _measured(text: str) -> str | None:
    """One measurement, with or without its label and unit: 36, 36", Waist 36 in."""
    value = _MEASUREMENT_LABEL.sub("", str(text or "").strip())
    value = _MEASUREMENT_UNIT.sub("", value).strip()
    return value if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) else None


# A measurement's label and unit are how a marketplace writes the size, not a
# different size: Depop's men's 36" is the 36 a tag reads.
_MEASUREMENT_LABEL = re.compile(r"^(?:WAIST|NECK|CHEST|INSEAM|LENGTH|US)\s+", re.IGNORECASE)
_MEASUREMENT_UNIT = re.compile(r'\s*(?:IN\.?|["”″])$', re.IGNORECASE)
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
