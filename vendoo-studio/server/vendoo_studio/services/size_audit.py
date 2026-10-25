"""Whether a listing's size is the same on every marketplace form, and what it rests on.

A listing has one general size, and each marketplace form keeps its own copy.
Saving a listing makes the copies and the title follow the general size
(``listing_generate.align_size_fields``), so a listing is out of step when a
save would rewrite any of them. Nothing here changes a listing.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from vendoo_studio.services.listing_generate import (
    _SIZE_SPECIFIC_KEYS,
    align_size_fields,
    sanitize_listing_sizes,
    sanitize_size_value,
)

_TAG_SIZE_LINE = re.compile(r"(?im)^-\s*size:\s*(.+?)\s*$")
_TAG_SOURCE = re.compile(r"\s*\(source:.*$", re.I)
# The photos were looked at and no size could be read from them.
_UNREAD = re.compile(r"^unknown\b", re.I)


def form_sizes(listing: dict) -> dict[str, str]:
    """The size each marketplace form holds, by marketplace, leaving out forms with none."""
    sizes = {}
    for key in _SIZE_SPECIFIC_KEYS:
        block = listing.get(key)
        size = str(block.get("size") or "").strip() if isinstance(block, dict) else ""
        if size:
            sizes[key.removesuffix("_specifics")] = size
    return sizes


def aligned(listing: dict, *, previous_size: str | None = None) -> dict:
    """A copy of ``listing`` with its sizes as a save would leave them."""
    updated = copy.deepcopy(listing)
    sanitize_listing_sizes(updated)
    align_size_fields(updated, previous_size=previous_size)
    return updated


def photo_size(analysis: str | None) -> str | None:
    """The size line of a photo analysis, with where it was read from: ``M (source: tag)``."""
    match = _TAG_SIZE_LINE.search(analysis or "")
    return match.group(1) if match else None


def size_problems(listing: dict, photo: str | None = None) -> str | None:
    """Why a listing's sizing needs a look, in plain words, or None when nothing stands out."""
    general = sanitize_size_value(listing.get("size"))
    forms = form_sizes(listing)
    problems = []
    if not general:
        if not forms:
            return None
        problems.append("no general size")
    else:
        after = aligned(listing)
        rewritten = [name for name, size in forms.items() if form_sizes(after).get(name) != size]
        if rewritten:
            verb = "differs" if len(rewritten) == 1 else "differ"
            problems.append(f"{', '.join(rewritten)} {verb} from the general size")
        if str(after.get("title") or "") != str(listing.get("title") or ""):
            problems.append("the title does not carry the size")
        seen = _TAG_SOURCE.sub("", photo or "").strip()
        if seen and not _UNREAD.match(seen) and _plain(seen) != _plain(general):
            problems.append("the photos read a different size")
    return "; ".join(problems) or None


def _plain(size: Any) -> str:
    """A size as one spelling, so a tag reading ``Large (12/14)`` or ``2X`` matches ``L`` or ``2XL``."""
    text = re.sub(r"[\s.\-]+", "", _TAG_RANGE.sub("", str(size)).upper())
    text = _SIZE_WORDS.get(text, text)
    text = _X_WORD.sub(lambda match: match.group(1) + match.group(2)[0], text)
    if counted := _COUNTED_SIZE.match(text):
        return f"{counted.group(1)}X{counted.group(2) or 'L'}"
    if repeated := _REPEATED_SIZE.match(text):
        xs, letter = len(repeated.group(1)), repeated.group(2)
        return f"{xs}X{letter}" if xs > 1 else "X" * xs + letter
    return text


# The range a tag gives after the size, like ``M (8-10)``, is not the size.
_TAG_RANGE = re.compile(r"(?<=\S)\s*\([^)]*\)\s*$")
_SIZE_WORDS = {
    "EXTRASMALL": "XS", "XSMALL": "XS", "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
    "EXTRALARGE": "XL", "XLARGE": "XL", "XXLARGE": "XXL", "XXXLARGE": "XXXL",
}
# 3X-Large and XX-Small, as 3XL and XXS.
_X_WORD = re.compile(r"^([2-6]?X+)(LARGE|SMALL)$")
# 2XL, 3XS, and plus sizing's 2X, which is 2XL.
_COUNTED_SIZE = re.compile(r"^([2-6])X([SL]?)$")
# XS, XXL, XXXL.
_REPEATED_SIZE = re.compile(r"^(X*)([SL])$")
