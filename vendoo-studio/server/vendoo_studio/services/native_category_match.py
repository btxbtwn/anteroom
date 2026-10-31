"""Match a category path onto one marketplace's own category list.

Each marketplace names the same item its own way: General's "Men's Clothing >
Shirts > T-Shirts" is "Men > Shirts > Tees - Short Sleeve" on Poshmark and
"Men > Tops > T-shirts" on Depop and Mercari. A row matches when its last step
names the same item (the same main noun, and no extra qualifier the category or
the listing does not say) inside the same department. A tie returns nothing, so
the seller picks rather than getting a guess.

The rows come from the same lists the marketplace forms show, so a match is
always one of that form's dropdown options.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, NamedTuple

_SPLIT = re.compile(r"\s*(?:>|›)\s*")
_ALTERNATIVES = re.compile(r"\s*(?:,|&|/|\band\b|\bor\b)\s*", re.I)
_PHRASES = (
    (re.compile(r"\bt[\s-]?shirts?\b", re.I), "tshirt"),
    (re.compile(r"\bathletic shoes?\b", re.I), "sneaker"),
    (re.compile(r"\bbutton[\s-]?(?:down|up|front)\b", re.I), "buttondown"),
)
_SYNONYMS = {
    "tshirt": "tee", "hoody": "hoodie", "trouser": "pant", "denim": "jean",
    "purse": "handbag", "trainer": "sneaker", "cap": "hat",
}
_STOP = {"the", "of", "for", "with", "up", "size", "sizes", "s", "in", "a", "and", "or", "mo", "mos"}
# Words that do not name an item: "Other", "None", "Men's Clothing", "Accessories".
_GENERIC = {
    "other", "none", "more", "all", "clothing", "apparel", "specialty", "fashion", "everything", "else",
    "accessory", "part", "supply", "equipment", "item", "product", "misc", "miscellaneous",
}
# Branches for a narrower kind of item. A row inside one needs the category or listing to say so.
_NICHE = {
    "vintage", "handmade", "uniform", "school", "costume", "maternity", "dancewear", "bridal",
    "wedding", "pet", "dog", "cat", "doll", "toy", "golf", "fishing", "scrub", "traditional",
}
_DEPARTMENTS = {
    "women": "women", "woman": "women", "ladie": "women", "womenswear": "women",
    "men": "men", "man": "men", "menswear": "men",
    "boy": "kids", "girl": "kids", "kid": "kids", "baby": "kids", "toddler": "kids", "kidswear": "kids",
}


def _word(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        word = word[:-2]
    elif len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return _SYNONYMS.get(word, word)


def _normalized(text: Any) -> str:
    text = str(text or "")
    for pattern, replacement in _PHRASES:
        text = pattern.sub(replacement, text)
    return re.sub(r"['’]s?\b", "", text)


def words(text: Any) -> list[str]:
    return [_word(word) for word in re.findall(r"[A-Za-z0-9]+", _normalized(text)) if word.lower() not in _STOP]


def segments(path: Any) -> list[str]:
    return [part.strip() for part in _SPLIT.split(str(path or "")) if part.strip()]


def department(path: Any) -> str:
    for segment in segments(path):
        for word in words(segment):
            if word in _DEPARTMENTS:
                return _DEPARTMENTS[word]
    return ""


def _nouns(part: str) -> list[str]:
    return [
        word for word in words(part)
        if word not in _DEPARTMENTS and word not in _GENERIC and word not in _NICHE and not re.search(r"\d", word)
    ]


@lru_cache(maxsize=65536)
def _alternatives(segment: str) -> tuple[tuple[str, frozenset[str]], ...]:
    """A step's items as (main noun, qualifiers).

    "Tees - Long Sleeve" is tees qualified by long sleeve. "Film & Polaroid
    Cameras" is film cameras and Polaroid cameras.
    """
    main, _, rest = _normalized(segment).partition(" - ")
    parts = [found for found in (_nouns(part) for part in _ALTERNATIVES.split(main)) if found]
    extra = frozenset(words(rest)) - frozenset(_DEPARTMENTS)
    last = parts[-1] if parts else []
    items = []
    for part in parts:
        items.append((part[-1], frozenset(part[:-1]) | extra))
        if len(part) == 1 and len(last) > 1 and part is not last:
            items.append((last[-1], frozenset({part[0]}) | extra))
    return tuple(items)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


class _Path(NamedTuple):
    key: str
    department: str
    words: frozenset[str]
    # The steps naming the item, last first, skipping Other, None, or an age range.
    steps: tuple[str, ...]
    generic_leaf: bool


@lru_cache(maxsize=65536)
def _parsed(path: str) -> _Path:
    steps = segments(path)
    while steps and not _alternatives(steps[-1]):
        steps.pop()
    # The item's own step, then the one right above it when that names an item too.
    named = [step for step in steps[-2:] if _alternatives(step)] if steps else []
    last = segments(path)[-1] if segments(path) else ""
    return _Path(
        key=" > ".join(" ".join(words(step)) for step in segments(path)),
        department=department(path),
        words=frozenset(words(path)),
        steps=tuple(reversed(named)) if named and named[-1] == steps[-1] else (),
        generic_leaf=not _alternatives(last),
    )


def match_row(rows: list[dict], path: str, evidence: str = "") -> dict | None:
    """The one row in `rows` ({"id", "path"}) that names the item `path` names, else None.

    `evidence` is the listing's own words, such as its title and sleeve length.
    They break a tie between rows that differ only by a qualifier.
    """
    if not _text(path):
        return None
    want = _parsed(_text(path))
    parsed = [(row, _parsed(str(row.get("path") or ""))) for row in rows]
    exact = [row for row, found in parsed if found.key == want.key]
    if len(exact) == 1:
        return exact[0]
    hint = frozenset(words(evidence))
    # The item itself first, then the step above it ("Shirts" for "T-Shirts").
    for step in want.steps:
        items = _alternatives(step)
        nouns = {noun for noun, _ in items}
        qualifiers = frozenset().union(*(found for _, found in items))
        ranked = []
        for row, found in parsed:
            if not found.steps:
                continue
            if want.department and found.department and found.department != want.department:
                continue
            if not want.department and found.department:
                continue
            if (found.words & _NICHE) - want.words - hint:
                continue
            same_department = bool(want.department) and found.department == want.department
            row_items = [(noun, quals) for noun, quals in _alternatives(found.steps[0]) if quals <= want.words | hint]
            strong = [(noun, quals) for noun, quals in row_items if noun in nouns]
            # "Button-front" for "Button-Down Shirts", only inside the same department.
            weak = [(noun, quals) for noun, quals in row_items
                    if same_department and noun in qualifiers and noun not in nouns]
            if not strong and not weak:
                continue
            # Qualifiers the row says anywhere: Video in "Video games & consoles > Games".
            shared = len(found.words & qualifiers) if strong else 0
            context = same_department or bool((found.words - _GENERIC) & (want.words - nouns - _GENERIC))
            # Outside clothing the branch has to agree too: "Video Games" is not
            # "Puzzles and games", and a truck's lighting is not studio lighting.
            if not context and (not want.department or (qualifiers and not shared)):
                continue
            score = (
                int(bool(found.department) == bool(want.department)),
                int(bool(strong)),
                len({noun for noun, _ in strong + weak}),
                shared,
                # The same name as written: "Athletic Shoes" over "Sneakers".
                int(_plain(found.steps[0]) == _plain(step)),
                len(want.words & found.words),
                len(found.words & hint),
                -len(found.words - want.words - hint - _GENERIC),
                -int(found.generic_leaf),
            )
            ranked.append((score, row))
        if not ranked:
            continue
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return None
        return ranked[0][1]
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def evidence(listing: dict) -> str:
    """The listing's own words that can settle a tie: its title and sleeve length."""
    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    return " ".join(_text(value) for value in (
        listing.get("title"), listing.get("sleeveLength"), ebay.get("sleeveLength"),
    ) if _text(value))


def native_rows() -> dict[str, list[dict]]:
    """Each direct form's own category options as {"id", "path"}, read from the files the forms read."""
    from vendoo_studio.services import depop_form, mercari_form, poshmark_form
    from vendoo_studio.services.sellwild_listing import sellwild_catalog

    poshmark = next(row for row in poshmark_form.catalog()["fields"] if row["path"] == "direct_category_ids.poshmark")
    depop = depop_form.catalog()
    retired = set(depop.get("retired_category_ids") or [])
    mercari = mercari_form.categories()
    parents = {row["path"].rsplit(" > ", 1)[0] for row in mercari if " > " in row["path"]}
    return {
        "poshmark": [{"id": value, "path": label} for label, value in poshmark["option_values"].items()],
        "depop": [{"id": row["id"], "path": row["path"]} for row in depop["categories"] if row["id"] not in retired],
        "mercari": [dict(row) for row in mercari if row["path"] not in parents],
        "sellwild": [{"id": str(row["id"]), "path": row["label"]} for row in sellwild_catalog()["categories"]],
    }
